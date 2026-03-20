"""JWT authentication dependency and middleware for the Conclave Engine.

This module is the **only** place where FastAPI/Starlette authentication
concerns are coupled to the framework-agnostic JWT logic.  The framework
binding lives exclusively in ``bootstrapper/``; ``shared/`` has zero FastAPI
imports.

Security posture
----------------
- JWT algorithm is **pinned** via ``ConclaveSettings.jwt_algorithm``.
  ``alg: none`` is **never** accepted.  Algorithm confusion attacks
  (substituting RS256 for HS256 or vice-versa) are rejected because
  ``jwt.decode()`` only accepts the configured algorithm name.
- Operator credentials are verified against a bcrypt hash stored in
  ``ConclaveSettings.operator_credentials_hash``.  The raw passphrase
  is never stored or logged.  Verification uses ``bcrypt.checkpw()`` which
  provides constant-time comparison.
- Tokens contain ``sub`` (operator ID), ``exp`` (expiry), ``iat``
  (issued-at), and ``scope`` (permissions list).

Unconfigured mode
-----------------
When ``jwt_secret_key`` is empty (the default), the middleware operates in
**pass-through mode**: all requests are allowed without token verification,
and a WARNING is logged on every non-exempt request.  This allows the
application to start and be accessed before JWT credentials are configured,
but production deployments MUST set ``JWT_SECRET_KEY`` to a non-empty
value to enforce authentication.

Middleware ordering
-------------------
``AuthenticationGateMiddleware`` must be registered **INNERMOST** in
``setup_middleware()`` — after :class:`LicenseGateMiddleware` in the
``app.add_middleware()`` call list, which means it fires BEFORE
``LicenseGateMiddleware`` on the request path (LIFO evaluation order):

    RequestBodyLimitMiddleware → CSPMiddleware → SealGateMiddleware
    → LicenseGateMiddleware → AuthenticationGateMiddleware → route handler

Exempt paths
------------
:data:`AUTH_EXEMPT_PATHS` lists all paths that must remain unauthenticated
by definition (pre-auth bootstrapping endpoints).

TODO [CONCLAVE-ADV-EXEMPT]: Three middleware files (vault.py, licensing.py,
auth.py) maintain independent frozensets of exempt paths. Extract a
``COMMON_INFRA_EXEMPT_PATHS`` constant to
``bootstrapper/dependencies/_exempt_paths.py`` and compose from it to
eliminate the maintenance debt. Tracked as ARCH-ADV-1 from T39.1 review.

CONSTITUTION Priority 0: Security — algorithm pinning, no alg:none
CONSTITUTION Priority 3: TDD
Task: T39.1 — Add Authentication Middleware (JWT Bearer Token)
"""

from __future__ import annotations

import logging
import time

import bcrypt as _bcrypt
import jwt as pyjwt
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)

#: Routes that bypass authentication entirely.
#: These are pre-auth by definition — they must be reachable before any
#: credential is issued or the vault is unsealed.
AUTH_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/unseal",
        "/health",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/license/challenge",
        "/license/activate",
        "/security/shred",
        "/security/keys/rotate",
        # Token issuance endpoint — must be pre-auth so operators can log in.
        "/auth/token",
    }
)


class AuthenticationError(Exception):
    """Raised when JWT authentication fails.

    This is the single exception type for all authentication failures:
    expired tokens, invalid signatures, malformed tokens, algorithm
    confusion, and algorithm rejection.  Construct with a human-readable
    message describing the failure reason.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)


def create_token(*, sub: str, scope: list[str]) -> str:
    """Create a signed JWT token for the given operator.

    Token claims follow the JWT standard:
    - ``sub``: operator identity string
    - ``iat``: issued-at timestamp (Unix seconds)
    - ``exp``: expiry timestamp (Unix seconds, now + jwt_expiry_seconds)
    - ``scope``: list of permission strings

    The algorithm and expiry are read from :func:`get_settings`.

    Args:
        sub: Subject — the operator identifier to embed in the token.
        scope: List of permission strings granted to the operator.

    Returns:
        Compact JWT string (header.payload.signature).
    """
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": sub,
        "iat": now,
        "exp": now + settings.jwt_expiry_seconds,
        "scope": scope,
    }
    return pyjwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def verify_token(token: str) -> dict[str, object]:
    """Verify a JWT token and return its claims.

    The algorithm is **pinned** to ``ConclaveSettings.jwt_algorithm``.
    Tokens claiming ``alg: none`` or any algorithm other than the
    configured one are rejected unconditionally.

    Args:
        token: Compact JWT string to verify.

    Returns:
        Decoded claims dictionary on success.

    Raises:
        AuthenticationError: If the token is expired, has an invalid
            signature, is malformed, or uses a disallowed algorithm.
    """
    settings = get_settings()
    try:
        claims: dict[str, object] = pyjwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "iat"]},
        )
        return claims
    except pyjwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired") from exc
    except pyjwt.InvalidAlgorithmError as exc:
        raise AuthenticationError("Token algorithm is not accepted") from exc
    except pyjwt.InvalidTokenError as exc:
        raise AuthenticationError(f"Token is invalid: {type(exc).__name__}") from exc


def verify_operator_credentials(passphrase: str) -> bool:
    """Verify operator passphrase against the configured bcrypt hash.

    The passphrase is checked against ``ConclaveSettings.operator_credentials_hash``
    using ``bcrypt.checkpw()`` for constant-time comparison.

    If no credentials hash is configured (empty string), always returns
    ``False`` — unconfigured credentials mean no operator is registered.

    Single-operator model: the system uses one operator identity whose
    passphrase is hashed in ``OPERATOR_CREDENTIALS_HASH``.  Multi-operator
    support will require a separate operator registry (post-T39.1 backlog).

    Args:
        passphrase: Plain-text passphrase to check.

    Returns:
        ``True`` if the passphrase matches the stored hash, ``False`` otherwise.
    """
    settings = get_settings()

    stored_hash = settings.operator_credentials_hash
    if not stored_hash:
        return False

    try:
        result: bool = _bcrypt.checkpw(
            passphrase.encode("utf-8"),
            stored_hash.encode("utf-8"),
        )
        return result
    except Exception:
        # Broad catch: any bcrypt error (e.g. invalid hash format) → deny
        _logger.warning("Credential verification failed due to unexpected error", exc_info=True)
        return False


def _build_401_response(detail: str) -> JSONResponse:
    """Build an RFC 7807 Problem Details 401 response.

    Args:
        detail: Human-readable explanation of why authentication failed.

    Returns:
        JSONResponse with HTTP 401 and RFC 7807 body.
    """
    return JSONResponse(
        status_code=401,
        content={
            "type": "about:blank",
            "status": 401,
            "title": "Unauthorized",
            "detail": detail,
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


class AuthenticationGateMiddleware(BaseHTTPMiddleware):
    """Starlette middleware enforcing JWT Bearer token authentication.

    Every request whose path is not in :data:`AUTH_EXEMPT_PATHS` must
    carry a valid ``Authorization: Bearer <token>`` header.  Invalid,
    expired, or absent tokens receive a **401 Unauthorized** RFC 7807
    response.

    When ``jwt_secret_key`` is empty (unconfigured), the middleware operates
    in pass-through mode: all requests are allowed with a WARNING log.
    Production deployments MUST set ``JWT_SECRET_KEY`` to a non-empty value.

    The middleware slots INNERMOST in the middleware stack — after
    :class:`~synth_engine.bootstrapper.dependencies.vault.SealGateMiddleware`
    and :class:`~synth_engine.bootstrapper.dependencies.licensing.LicenseGateMiddleware`
    have already allowed the request through.

    Security properties:
    - Algorithm is pinned via settings; ``alg: none`` is unconditionally rejected.
    - Token validation uses constant-time comparison (delegated to PyJWT).
    - Failure detail messages do not leak key material or internal state.
    - When jwt_secret_key is empty, pass-through mode is used with a WARNING.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Gate every non-exempt request behind JWT token verification.

        When ``jwt_secret_key`` is empty, all requests pass through with a
        warning (unconfigured mode).  This allows startup and access before
        credentials are set up, but is NOT suitable for production.

        Args:
            request: Incoming HTTP request.
            call_next: ASGI callable for the next middleware or route handler.

        Returns:
            A 401 JSONResponse (RFC 7807) if the token is absent or invalid,
            otherwise the normal downstream response.
        """
        if request.url.path in AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # Pass-through mode: when no JWT secret is configured, skip authentication.
        # This allows development and testing without JWT credentials configured.
        # SECURITY: Production deployments MUST set JWT_SECRET_KEY.
        settings = get_settings()
        if not settings.jwt_secret_key:
            _logger.warning(
                "JWT authentication not configured (JWT_SECRET_KEY is empty). "
                "Request to %s is allowed in unconfigured mode. "
                "Set JWT_SECRET_KEY in production.",
                request.url.path,
            )
            return await call_next(request)

        auth_header: str | None = request.headers.get("Authorization")

        if auth_header is None:
            return _build_401_response(
                "Authentication required. Provide a valid Bearer token in the Authorization header."
            )

        if not auth_header.startswith("Bearer "):
            return _build_401_response(
                "Invalid Authorization header format. Expected: 'Bearer <token>'."
            )

        token = auth_header[len("Bearer ") :]

        try:
            verify_token(token)
        except AuthenticationError as exc:
            _logger.warning("Authentication failed: %s", exc)
            return _build_401_response(str(exc))

        return await call_next(request)
