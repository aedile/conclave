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
- ``require_scope()`` enforces scope-based authorization by verifying that
  the JWT ``scope`` claim is a *list* containing the required scope string.
  Bare-string scope claims are unconditionally rejected (array injection
  attack vector).

Unconfigured mode
-----------------
When ``jwt_secret_key`` is empty (the default), the middleware operates in
**pass-through mode** in non-production environments: all requests are allowed
without token verification, and a WARNING is logged on every non-exempt
request.  This allows development and testing before JWT credentials are
configured.

**SECURITY (T57.1)**: In production mode (``conclave_env == "production"``),
pass-through is unconditionally disabled.  A production deployment with an
empty JWT secret key is a misconfiguration that MUST be rejected — returning
HTTP 401 — not silently allowed.  ``require_scope()`` and
``AuthenticationGateMiddleware`` apply the same production hard-fail logic.

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
by definition (pre-auth bootstrapping endpoints).  It is composed from
:data:`~synth_engine.bootstrapper.dependencies._exempt_paths.COMMON_INFRA_EXEMPT_PATHS`
plus the ``/auth/token`` endpoint (resolved: ADV-T39.1-01).

CONSTITUTION Priority 0: Security — algorithm pinning, no alg:none
CONSTITUTION Priority 3: TDD
Task: T39.1 — Add Authentication Middleware (JWT Bearer Token)
Task: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
Task: T47.1 — Scope-based auth for security endpoints
Task: T47.3 — Scope-based auth for settings write endpoints
Task: T57.1 — JWT Authentication Hard-Fail in Production
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import bcrypt as _bcrypt
import jwt as pyjwt
from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from synth_engine.bootstrapper.dependencies._exempt_paths import COMMON_INFRA_EXEMPT_PATHS
from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)

#: Routes that bypass authentication entirely.
#: These are pre-auth by definition — they must be reachable before any
#: credential is issued or the vault is unsealed.
#: Composed from COMMON_INFRA_EXEMPT_PATHS plus the token issuance endpoint
#: so operators can log in before any token is available (ADV-T39.1-01).
AUTH_EXEMPT_PATHS: frozenset[str] = COMMON_INFRA_EXEMPT_PATHS | frozenset({"/auth/token"})


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
    return pyjwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )


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
            settings.jwt_secret_key.get_secret_value(),
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


def get_current_operator(request: Request) -> str:
    """Extract and return the operator's sub claim from the JWT bearer token.

    This is a FastAPI dependency for resource endpoints that need to know
    which operator is making the request.  It extracts the ``Authorization``
    header, verifies the bearer token, and returns the ``sub`` claim string.

    **Pass-through mode (non-production only)**: When ``jwt_secret_key`` is
    empty and the deployment is NOT in production mode, a sentinel value of
    ``""`` is returned.  This maintains backward compatibility for
    development and test environments where JWT is not yet configured.

    **Production hard-fail (T57.1)**: When ``jwt_secret_key`` is empty AND
    the deployment is in production mode (``settings.is_production()``),
    HTTP 401 is raised unconditionally.  An empty JWT secret in production
    is a misconfiguration — pass-through is never permitted in production.
    The 401 detail message does not reveal configuration state.

    Args:
        request: The incoming HTTP request (injected by FastAPI).

    Returns:
        The ``sub`` claim from the verified JWT token, or ``""`` when
        operating in unconfigured/pass-through mode (non-production only).

    Raises:
        HTTPException: 401 Unauthorized if the Authorization header is
            absent, malformed, or contains an invalid/expired token, or
            if the ``sub`` claim is present but empty.  Also raised in
            production mode when ``jwt_secret_key`` is empty.
    """
    settings = get_settings()
    jwt_secret = settings.jwt_secret_key.get_secret_value()

    if not jwt_secret:
        if settings.is_production():
            # T57.1: Production hard-fail — empty JWT secret is a misconfiguration.
            # Do NOT reveal that the key is unconfigured (info disclosure).
            raise HTTPException(
                status_code=401,
                detail="Authentication required. Provide a valid Bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Non-production pass-through: return sentinel "".
        # This matches the default owner_id for pre-T39.2 resources.
        # T58.2: Populate jwt_claims with empty dict so require_scope does not
        # get AttributeError when reading request.state.jwt_claims.
        request.state.jwt_claims = {}
        return ""

    auth_header: str | None = request.headers.get("Authorization")
    if auth_header is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide a valid Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format. Expected: 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header[len("Bearer ") :]

    try:
        claims = verify_token(token)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(
            status_code=401,
            detail="Token is missing required sub claim.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not sub:
        raise HTTPException(
            status_code=401,
            detail="Token sub claim must not be empty.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # T58.2: Cache decoded claims on request.state so require_scope can read
    # them without a second call to verify_token (eliminates double-decode).
    request.state.jwt_claims = claims
    return sub


def require_scope(scope: str) -> Callable[..., str]:
    """Return a FastAPI dependency that enforces the given scope.

    The returned dependency resolves after ``get_current_operator`` has
    already verified the token (authentication).  It then checks that the
    JWT ``scope`` claim:

    1. Is a ``list`` (bare-string claims are rejected — array injection
       attack vector where ``"security:admin" in "security:admin"`` would
       be ``True`` for substring checks but is an illegitimate claim shape).
    2. Contains the required scope string via exact list membership.

    **Pass-through mode (non-production only)**: when ``jwt_secret_key`` is
    empty and not in production mode, the scope check is bypassed entirely —
    consistent with ``get_current_operator`` pass-through behavior.

    **Production hard-fail (T57.1)**: In production mode with an empty JWT
    secret, the scope check raises HTTP 401 before examining any claims.

    FastAPI injection: the returned ``_check_scope`` function declares
    ``request: Request`` without ``Depends()`` — FastAPI recognises
    ``Request`` as a special type and injects the current request
    automatically.  ``operator`` is resolved via ``Depends(get_current_operator)``
    to enforce authentication before authorization.

    Args:
        scope: The required scope string, e.g. ``"security:admin"``.

    Returns:
        A FastAPI-compatible dependency callable.

    Example::

        @router.post("/security/shred")
        async def shred_vault(
            current_operator: Annotated[str, Depends(require_scope("security:admin"))],
        ) -> JSONResponse: ...
    """

    def _check_scope(
        request: Request,
        operator: str = Depends(get_current_operator),
    ) -> str:
        """Verify the JWT scope claim contains the required scope string.

        FastAPI injects ``request`` directly (special Request type) and
        resolves ``operator`` via the ``get_current_operator`` dependency.

        Args:
            request: The incoming HTTP request (auto-injected by FastAPI).
            operator: Resolved operator sub claim from ``get_current_operator``.

        Returns:
            The operator sub claim on success.

        Raises:
            HTTPException: 401 if JWT is unconfigured in production mode, or if
                request.state.jwt_claims is absent (middleware reorder guard).
                403 Forbidden if the scope claim is absent, not a list,
                or does not contain the required scope.
        """
        settings = get_settings()
        jwt_secret = settings.jwt_secret_key.get_secret_value()

        if not jwt_secret:
            if settings.is_production():
                # T57.1: Production hard-fail — consistent with get_current_operator.
                raise HTTPException(
                    status_code=401,
                    detail="Authentication required. Provide a valid Bearer token.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            # Non-production pass-through: no JWT configured → skip scope check.
            return operator

        # T58.2: Read claims from request.state.jwt_claims (set by
        # get_current_operator during token verification).  This eliminates
        # the second call to verify_token that the previous implementation
        # performed by re-parsing the Authorization header.
        #
        # If jwt_claims is absent (e.g. middleware reorder or direct call
        # bypassing get_current_operator), raise 401 rather than AttributeError.
        if not hasattr(request.state, "jwt_claims"):
            raise HTTPException(
                status_code=401,
                detail="Authentication required. Provide a valid Bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        claims = request.state.jwt_claims
        raw_scope = claims.get("scope")

        # SECURITY: scope MUST be a list.  A bare string is an injection
        # vector — ``"security:admin" in "security:admin"`` is True for
        # string substring checks but scope claims must be lists.
        if not isinstance(raw_scope, list):
            _logger.warning(
                "Scope claim is not a list (type=%s). Rejecting request.",
                type(raw_scope).__name__,
            )
            raise HTTPException(
                status_code=403,
                detail="Forbidden. Required scope not present.",
            )

        # Exact list membership only — no substring or prefix matching.
        if scope not in raw_scope:
            _logger.warning(
                "Scope '%s' not in token scopes %r. Rejecting request.",
                scope,
                raw_scope,
            )
            raise HTTPException(
                status_code=403,
                detail="Forbidden. Required scope not present.",
            )

        return operator

    return _check_scope


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

    **Pass-through mode (non-production only)**: When ``jwt_secret_key`` is
    empty and the deployment is NOT in production mode, requests are allowed
    through with a WARNING log.  This permits development and testing before
    JWT is configured.

    **Production hard-fail (T57.1)**: In production mode with an empty JWT
    secret, ALL non-exempt requests receive a 401 response.  A production
    deployment with no JWT secret is a misconfiguration that must be
    rejected, not silently allowed.

    The middleware slots INNERMOST in the middleware stack — after
    :class:`~synth_engine.bootstrapper.dependencies.vault.SealGateMiddleware`
    and :class:`~synth_engine.bootstrapper.dependencies.licensing.LicenseGateMiddleware`
    have already allowed the request through.

    Security properties:
    - Algorithm is pinned via settings; ``alg: none`` is unconditionally rejected.
    - Token validation uses constant-time comparison (delegated to PyJWT).
    - Failure detail messages do not leak key material or internal state.
    - Pass-through mode only in non-production; production always enforces auth.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Gate every non-exempt request behind JWT token verification.

        In non-production with empty ``jwt_secret_key``, requests pass through
        with a warning (unconfigured mode).  In production with an empty
        ``jwt_secret_key``, all non-exempt requests receive 401 (T57.1).

        Args:
            request: Incoming HTTP request.
            call_next: ASGI callable for the next middleware or route handler.

        Returns:
            A 401 JSONResponse (RFC 7807) if the token is absent, invalid,
            or if the deployment is production with no JWT secret configured.
            Otherwise returns the normal downstream response.
        """
        if request.url.path in AUTH_EXEMPT_PATHS:
            return await call_next(request)

        settings = get_settings()
        jwt_secret = settings.jwt_secret_key.get_secret_value()

        if not jwt_secret:
            if settings.is_production():
                # T57.1: Production hard-fail — empty JWT secret is a misconfiguration.
                # Do not reveal configuration state in the response detail.
                return _build_401_response(
                    "Authentication required. "
                    "Provide a valid Bearer token in the Authorization header."
                )
            # Non-production pass-through: skip authentication with a warning.
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
