"""Rate limiting middleware for the Conclave Engine.

Implements application-layer rate limiting as the OUTERMOST middleware in
the stack.  Running outermost means the rate limit check fires BEFORE vault,
license, and authentication gates — providing DoS and brute-force protection
before any expensive downstream processing begins.

Rate limit tiers (per T39.3 specification)
-----------------------------------------
``/unseal``:
    5 requests/minute per client IP.  This endpoint is a high-value target
    (vault unseal) and must be protected against brute-force attacks even
    before authentication is available.

``/auth/token``:
    10 requests/minute per client IP.  Credential stuffing protection.

``/jobs/{id}/download``:
    10 requests/minute per authenticated operator.  Bandwidth protection.
    Matched with ``path.endswith("/download")`` to enforce the specific
    route contract rather than a broader substring match.

All other endpoints:
    60 requests/minute per authenticated operator.

Identity resolution
-------------------
For ``/unseal`` and ``/auth/token`` the client IP address is used as the
rate-limit key because these are pre-authentication endpoints.  The IP is
extracted from the ``X-Forwarded-For`` header (first entry — leftmost IP
is the real client behind a reverse proxy) with fallback to
``request.client.host``.

For all other endpoints the JWT ``sub`` claim is used when a Bearer token
is present.  The token is decoded without signature verification — the
:class:`AuthenticationGateMiddleware` (inner layer) performs the authoritative
signature check.  This avoids double-verification overhead and ensures the
rate-limit key is stable across token refresh cycles.  When no token is
present (unconfigured JWT mode), the client IP is used as the fallback key.

Technology
----------
Uses the ``limits`` library (same underlying engine as ``slowapi``) with a
``FixedWindowRateLimiter`` and ``MemoryStorage`` backend.  The in-memory
backend requires no external dependencies (no Redis), is safe for air-gapped
deployments, and resets on process restart — appropriate for the application
layer where per-instance rate limiting is additive to reverse-proxy limits.

Middleware ordering
-------------------
``RateLimitGateMiddleware`` must be registered LAST in ``setup_middleware()``
(added last = LIFO outermost) so that it is the first middleware the request
encounters:

    RateLimitGateMiddleware → RequestBodyLimitMiddleware → CSPMiddleware
    → SealGateMiddleware → LicenseGateMiddleware → AuthenticationGateMiddleware
    → route handler

Response format
---------------
Rate-limited requests receive HTTP 429 Too Many Requests with an RFC 7807
Problem Details body and a ``Retry-After`` header indicating seconds until
the current window resets.

Configuration
-------------
All four rate limit tiers are configurable via :class:`ConclaveSettings`
fields (``RATE_LIMIT_UNSEAL_PER_MINUTE``, ``RATE_LIMIT_AUTH_PER_MINUTE``,
``RATE_LIMIT_GENERAL_PER_MINUTE``, ``RATE_LIMIT_DOWNLOAD_PER_MINUTE``).

CONSTITUTION Priority 0: Security — brute-force and DoS protection
CONSTITUTION Priority 3: TDD
Task: T39.3 — Add Rate Limiting Middleware
"""

from __future__ import annotations

import hashlib
import logging
import math
import time

import jwt as pyjwt
from fastapi.responses import JSONResponse
from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)

#: Paths where the rate limit key is the client IP (pre-authentication endpoints).
_IP_KEYED_PATHS: frozenset[str] = frozenset({"/unseal", "/auth/token"})

#: Path suffix that triggers the download-specific (lower) rate limit tier.
#: Uses endswith() to enforce the specific /jobs/{id}/download route contract.
_DOWNLOAD_PATH_SUFFIX: str = "/download"


def _extract_client_ip(request: Request) -> str:
    """Extract the client IP address from the request.

    Prefers the first (leftmost) entry in the ``X-Forwarded-For`` header,
    which represents the real client IP in a standard reverse-proxy deployment.
    Falls back to ``request.client.host`` when the header is absent.  Returns
    ``"unknown"`` when neither source is available (e.g. test clients without
    a bound socket).

    Args:
        request: Incoming HTTP request.

    Returns:
        Client IP address string, or ``"unknown"`` if unavailable.
    """
    forwarded_for: str | None = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For may be a comma-separated list; the leftmost IP is the
        # real client (each proxy appends its own IP to the right).
        return forwarded_for.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def _extract_operator_id(request: Request) -> str | None:
    """Extract the operator identity from the JWT Bearer token.

    Decodes the ``sub`` claim without signature verification.  Signature
    integrity is the responsibility of :class:`AuthenticationGateMiddleware`
    (the inner auth gate).  Rate limiting only needs a stable identity key.

    Returns ``None`` when:
    - No ``Authorization`` header is present.
    - The header is not in ``Bearer <token>`` format.
    - The token cannot be decoded (malformed, missing ``sub`` claim).

    Args:
        request: Incoming HTTP request.

    Returns:
        The ``sub`` claim string, or ``None`` if the token is absent or
        undecodable.
    """
    auth_header: str | None = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer ") :]
    try:
        # options={"verify_signature": False} decodes without key validation.
        # The AuthGate (inner layer) performs the authoritative signature check.
        payload: dict[str, object] = pyjwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["HS256", "HS384", "HS512"],
        )
        sub = payload.get("sub")
        return str(sub) if sub is not None else None
    except pyjwt.InvalidTokenError:
        return None


def _build_429_response(retry_after_seconds: int) -> JSONResponse:
    """Build an RFC 7807 Problem Details 429 Too Many Requests response.

    Includes a ``Retry-After`` header per the HTTP specification (RFC 6585)
    indicating the number of seconds until the rate limit window resets.

    Args:
        retry_after_seconds: Seconds until the rate limit window resets.
            Must be a non-negative integer.

    Returns:
        JSONResponse with HTTP 429, RFC 7807 body, and Retry-After header.
    """
    return JSONResponse(
        status_code=429,
        content={
            "type": "about:blank",
            "status": 429,
            "title": "Too Many Requests",
            "detail": (f"Rate limit exceeded. Retry after {retry_after_seconds} second(s)."),
        },
        headers={"Retry-After": str(retry_after_seconds)},
    )


class RateLimitGateMiddleware(BaseHTTPMiddleware):
    """ASGI middleware enforcing per-IP and per-operator rate limits.

    Must be registered as the OUTERMOST middleware in ``setup_middleware()``
    (added last in LIFO ordering) to protect against DoS and brute-force
    attacks before any downstream processing.

    When ``None`` is passed for any tier, the value is read from
    :func:`~synth_engine.shared.settings.get_settings` at construction
    time.  Explicit values override settings — this allows tests to inject
    low limits without environment variable manipulation.

    Args:
        app: The next ASGI application in the stack.
        unseal_limit: Requests per minute allowed on /unseal per IP.
            Defaults to ``ConclaveSettings.rate_limit_unseal_per_minute``.
        auth_limit: Requests per minute allowed on /auth/token per IP.
            Defaults to ``ConclaveSettings.rate_limit_auth_per_minute``.
        general_limit: Requests per minute allowed per operator on all
            other endpoints.  Defaults to
            ``ConclaveSettings.rate_limit_general_per_minute``.
        download_limit: Requests per minute allowed per operator on
            download endpoints.  Defaults to
            ``ConclaveSettings.rate_limit_download_per_minute``.

    Attributes:
        _limiter: Fixed-window rate limiter backed by in-memory storage.
        _unseal_limit: Parsed rate limit item for the /unseal endpoint.
        _auth_limit: Parsed rate limit item for the /auth/token endpoint.
        _general_limit: Parsed rate limit item for all other endpoints.
        _download_limit: Parsed rate limit item for download endpoints.
    """

    def __init__(
        self,
        app: object,
        *,
        unseal_limit: int | None = None,
        auth_limit: int | None = None,
        general_limit: int | None = None,
        download_limit: int | None = None,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        settings = get_settings()
        _unseal = (
            unseal_limit if unseal_limit is not None else settings.rate_limit_unseal_per_minute
        )
        _auth = auth_limit if auth_limit is not None else settings.rate_limit_auth_per_minute
        _general = (
            general_limit if general_limit is not None else settings.rate_limit_general_per_minute
        )
        _download = (
            download_limit
            if download_limit is not None
            else settings.rate_limit_download_per_minute
        )

        self._storage = MemoryStorage()
        self._limiter: FixedWindowRateLimiter = FixedWindowRateLimiter(self._storage)
        self._unseal_limit: RateLimitItem = parse(f"{_unseal}/minute")
        self._auth_limit: RateLimitItem = parse(f"{_auth}/minute")
        self._general_limit: RateLimitItem = parse(f"{_general}/minute")
        self._download_limit: RateLimitItem = parse(f"{_download}/minute")

    def _resolve_limit_and_key(self, request: Request) -> tuple[RateLimitItem, str]:
        """Determine the applicable rate limit tier and identity key.

        Routing logic:
        - ``/unseal`` → unseal_limit keyed by client IP.
        - ``/auth/token`` → auth_limit keyed by client IP.
        - Paths ending with ``/download`` → download_limit keyed by operator
          sub (or IP fallback).  Uses endswith() to match the specific
          /jobs/{id}/download route contract.
        - All other paths → general_limit keyed by operator sub (or IP
          fallback).

        Args:
            request: Incoming HTTP request.

        Returns:
            A tuple of (rate_limit_item, key_string) for the limiter.
        """
        path = request.url.path

        if path == "/unseal":
            return self._unseal_limit, f"ip:{_extract_client_ip(request)}"

        if path == "/auth/token":
            return self._auth_limit, f"ip:{_extract_client_ip(request)}"

        # For authenticated endpoints, prefer the operator identity so each
        # operator gets an independent bucket.  Fall back to IP for
        # unauthenticated/unconfigured requests.
        operator_id = _extract_operator_id(request)
        key = f"op:{operator_id}" if operator_id else f"ip:{_extract_client_ip(request)}"

        if path.endswith(_DOWNLOAD_PATH_SUFFIX):
            return self._download_limit, key

        return self._general_limit, key

    def _compute_retry_after(self, limit: RateLimitItem, key: str) -> int:
        """Compute the number of seconds until the rate limit window resets.

        Args:
            limit: The rate limit item whose window to inspect.
            key: The rate limit bucket key.

        Returns:
            Non-negative integer seconds until reset.
        """
        try:
            stats = self._limiter.get_window_stats(limit, key)
            # Cast reset_time to float: limits library does not ship py.typed,
            # so stats attributes are typed as Any in the pre-commit mypy env.
            reset_time: float = float(stats.reset_time)
            seconds = math.ceil(reset_time - time.time())
            return max(0, seconds)
        except Exception as e:
            # Defensive fallback: MemoryStorage is documented not to raise, but
            # this guard protects against future storage backend substitutions.
            # Hash the key before logging to prevent raw client IPs or operator
            # identifiers from appearing in log files (CONSTITUTION Priority 0).
            hashed_key = hashlib.sha256(key.encode()).hexdigest()[:12]
            _logger.warning("rate_limit: window stats unavailable for key=%s: %s", hashed_key, e)
            return 60

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Gate every request through the appropriate rate limit tier.

        Requests that exceed the applicable rate limit receive a 429 RFC 7807
        response with a ``Retry-After`` header.  All other requests are passed
        to the next middleware or route handler unchanged.

        Args:
            request: Incoming HTTP request.
            call_next: ASGI callable for the next middleware or route handler.

        Returns:
            A 429 JSONResponse (RFC 7807) if the rate limit is exceeded,
            otherwise the downstream response.
        """
        limit, key = self._resolve_limit_and_key(request)
        allowed = self._limiter.hit(limit, key)

        if not allowed:
            retry_after = self._compute_retry_after(limit, key)
            # Hash the key before logging to avoid emitting raw client IPs or
            # operator identifiers in log files (CONSTITUTION Priority 0: privacy).
            hashed_key = hashlib.sha256(key.encode()).hexdigest()[:12]
            _logger.warning(
                "rate_limit: exceeded for key=%s path=%s retry_after=%ds",
                hashed_key,
                request.url.path,
                retry_after,
            )
            return _build_429_response(retry_after)

        return await call_next(request)
