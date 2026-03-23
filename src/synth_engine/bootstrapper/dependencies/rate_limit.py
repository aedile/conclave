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

Technology (T48.1)
------------------
Uses Redis ``INCR`` + ``EXPIRE`` via a pipeline for distributed, atomic
counting.  This ensures rate limits are shared across all workers/pods in a
multi-process deployment.

The synchronous ``_redis_hit()`` call is dispatched via ``asyncio.to_thread()``
so the event loop is never blocked on network I/O to Redis.

Redis key format: ``ratelimit:{window_seconds}:{identity_key}``
The ``ratelimit:`` prefix isolates these keys from:
- Idempotency middleware (``idempotency:`` prefix)
- Huey task queue (``huey.`` prefix)

Graceful degradation (T48.1)
-----------------------------
When Redis is unavailable (``redis.RedisError`` from the pipeline), the
middleware falls back to a per-instance ``FixedWindowRateLimiter`` backed
by ``MemoryStorage``.  A WARNING is logged (with hashed key, not raw
identity) and the request is handled by the in-memory limiter.  This means
per-instance limits still apply, but distributed counting is suspended until
Redis recovers.

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

Allowed requests receive an ``X-RateLimit-Remaining`` header indicating
how many requests remain in the current window.

Configuration
-------------
All four rate limit tiers are configurable via :class:`ConclaveSettings`
fields (``RATE_LIMIT_UNSEAL_PER_MINUTE``, ``RATE_LIMIT_AUTH_PER_MINUTE``,
``RATE_LIMIT_GENERAL_PER_MINUTE``, ``RATE_LIMIT_DOWNLOAD_PER_MINUTE``).

CONSTITUTION Priority 0: Security — brute-force and DoS protection
CONSTITUTION Priority 3: TDD
Task: T39.3 — Add Rate Limiting Middleware
Task: T48.1 — Redis-Backed Rate Limiting
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time

import jwt as pyjwt
import redis as redis_lib
from fastapi.responses import JSONResponse
from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from synth_engine.bootstrapper.dependencies.redis import get_redis_client
from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)

#: Paths where the rate limit key is the client IP (pre-authentication endpoints).
_IP_KEYED_PATHS: frozenset[str] = frozenset({"/unseal", "/auth/token"})

#: Path suffix that triggers the download-specific (lower) rate limit tier.
#: Uses endswith() to enforce the specific /jobs/{id}/download route contract.
_DOWNLOAD_PATH_SUFFIX: str = "/download"

#: Redis key prefix that isolates rate limit keys from other middleware keys.
#: - Idempotency middleware uses 'idempotency:' prefix
#: - Huey task queue uses 'huey.' prefix
#: This ensures no collision between middleware namespaces (T48.1 attack mitigation).
_REDIS_KEY_PREFIX: str = "ratelimit:"

#: Window duration in seconds for the per-minute rate limit.
_WINDOW_SECONDS: int = 60


def _extract_client_ip(request: Request) -> str:
    """Extract the client IP address from the request.

    Prefers the first (leftmost) entry in the ``X-Forwarded-For`` header,
    which represents the real client IP in a standard reverse-proxy deployment.
    Falls back to ``request.client.host`` when the header is absent.  Returns
    ``"unknown"`` when neither source is available (e.g. test clients without
    a bound socket).

    The leftmost-IP trust model is consistent with standard proxy conventions
    and is preserved in the Redis-backed implementation (T48.1).

    Args:
        request: Incoming HTTP request.

    Returns:
        Client IP address string, or ``"unknown"`` if unavailable.
    """
    forwarded_for: str | None = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For may be a comma-separated list; the leftmost IP is the
        # real client (each proxy appends its own IP to the right).
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
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

    Uses Redis ``INCR`` + ``EXPIRE`` via a pipeline for distributed counting
    across multiple workers/pods.  The synchronous Redis call is dispatched
    via ``asyncio.to_thread()`` so the event loop is never blocked on I/O.
    Degrades gracefully to in-memory counting when Redis is unavailable,
    with a WARNING log.

    Must be registered as the OUTERMOST middleware in ``setup_middleware()``
    (added last in LIFO ordering) to protect against DoS and brute-force
    attacks before any downstream processing.

    When ``None`` is passed for any tier, the value is read from
    :func:`~synth_engine.shared.settings.get_settings` at construction
    time.  Explicit values override settings — this allows tests to inject
    low limits without environment variable manipulation.

    Args:
        app: The next ASGI application in the stack.
        redis_client: Sync Redis client to use for distributed counting.
            When ``None`` (default), the shared client from
            :func:`~synth_engine.bootstrapper.dependencies.redis.get_redis_client`
            is used.  Inject a mock in tests.
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
        _redis: Sync Redis client for distributed counting.
        _fallback_storage: In-memory storage for graceful degradation.
        _fallback_limiter: Fixed-window rate limiter backed by in-memory storage.
        _unseal_limit: Parsed rate limit item for the /unseal endpoint.
        _auth_limit: Parsed rate limit item for the /auth/token endpoint.
        _general_limit: Parsed rate limit item for all other endpoints.
        _download_limit: Parsed rate limit item for download endpoints.
    """

    def __init__(
        self,
        app: object,
        *,
        redis_client: redis_lib.Redis | None = None,
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

        # Use injected Redis client when provided (enables test injection and
        # connection pool reuse from bootstrapper/dependencies/redis.py).
        # Only call get_redis_client() when no client is explicitly provided.
        if redis_client is not None:
            self._redis: redis_lib.Redis = redis_client
        else:
            self._redis = get_redis_client()

        # Fallback in-memory limiter — used when Redis is unavailable.
        self._fallback_storage = MemoryStorage()
        self._fallback_limiter: FixedWindowRateLimiter = FixedWindowRateLimiter(
            self._fallback_storage
        )

        self._unseal_limit: RateLimitItem = parse(f"{_unseal}/minute")
        self._auth_limit: RateLimitItem = parse(f"{_auth}/minute")
        self._general_limit: RateLimitItem = parse(f"{_general}/minute")
        self._download_limit: RateLimitItem = parse(f"{_download}/minute")

    def _redis_hit(self, limit_str: str, identity_key: str) -> tuple[int, bool]:
        """Atomically increment the Redis counter and check the limit.

        Uses a Redis pipeline to issue ``INCR`` and ``EXPIRE`` as a single
        atomic batch, preventing the scenario where a key exists without a TTL
        (which would permanently block the identity).

        Redis key format: ``ratelimit:{window_seconds}:{identity_key}``

        Args:
            limit_str: Rate limit string in ``N/period`` format (e.g.
                ``"5/minute"``).  Used to derive the limit count and key.
            identity_key: The identity bucket (e.g. ``"ip:10.0.0.1"`` or
                ``"op:operator-123"``).

        Returns:
            A tuple of ``(count, allowed)`` where ``count`` is the current
            request count in the window and ``allowed`` is ``True`` when
            ``count <= limit``.  Propagates ``redis.RedisError`` to the
            caller for graceful degradation handling in :meth:`dispatch`.
        """
        # Parse limit count from "N/period" format (e.g. "5/minute" -> limit=5)
        limit_count = int(limit_str.split("/")[0])
        redis_key = f"{_REDIS_KEY_PREFIX}{_WINDOW_SECONDS}:{identity_key}"

        with self._redis.pipeline() as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, _WINDOW_SECONDS)
            results = pipe.execute()

        count: int = int(results[0])
        allowed: bool = count <= limit_count
        return count, allowed

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
            stats = self._fallback_limiter.get_window_stats(limit, key)
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

        Attempts Redis-backed distributed counting first via
        ``asyncio.to_thread()`` to avoid blocking the event loop on the
        synchronous Redis pipeline call.  On ``redis.RedisError``
        (connection failure, timeout, auth error), falls back to in-memory
        counting with a WARNING log.  The hashed identity key is logged instead
        of the raw value to prevent PII leakage (CONSTITUTION Priority 0).

        Requests that exceed the applicable rate limit receive a 429 RFC 7807
        response with ``Retry-After`` and ``X-RateLimit-Remaining: 0`` headers.
        Allowed requests receive an ``X-RateLimit-Remaining`` header.

        Args:
            request: Incoming HTTP request.
            call_next: ASGI callable for the next middleware or route handler.

        Returns:
            A 429 JSONResponse (RFC 7807) if the rate limit is exceeded,
            otherwise the downstream response with rate limit headers added.
        """
        limit, key = self._resolve_limit_and_key(request)
        limit_str = f"{limit.amount}/{limit.multiples or 1}minute"

        # Attempt Redis-backed counting first.
        # asyncio.to_thread() prevents blocking the event loop on the sync
        # Redis pipeline call (FINDING fix from P48 review).
        count: int
        allowed: bool
        try:
            count, allowed = await asyncio.to_thread(self._redis_hit, limit_str, key)
        except redis_lib.RedisError as e:
            # Graceful degradation: Redis unavailable — fall back to in-memory.
            # Hash the key before logging (CONSTITUTION Priority 0: no PII in logs).
            hashed_key = hashlib.sha256(key.encode()).hexdigest()[:12]
            _logger.warning(
                "rate_limit: redis fallback for key=%s path=%s: %s",
                hashed_key,
                request.url.path,
                e,
            )
            allowed = self._fallback_limiter.hit(limit, key)
            count = limit.amount if not allowed else 0

        if not allowed:
            retry_after = self._compute_retry_after(limit, key)
            # Hash the key before logging (CONSTITUTION Priority 0: no PII in logs).
            hashed_key = hashlib.sha256(key.encode()).hexdigest()[:12]
            _logger.warning(
                "rate_limit: exceeded for key=%s path=%s retry_after=%ds",
                hashed_key,
                request.url.path,
                retry_after,
            )
            error_response: Response = _build_429_response(retry_after)
            error_response.headers["X-RateLimit-Remaining"] = "0"
            return error_response

        downstream: Response = await call_next(request)
        remaining = max(0, limit.amount - count)
        downstream.headers["X-RateLimit-Remaining"] = str(remaining)
        return downstream
