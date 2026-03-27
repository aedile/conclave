"""JWT authentication dependencies for the Conclave Engine.

JWT utility functions and FastAPI per-route dependencies.  The Starlette
middleware (``AuthenticationGateMiddleware``) lives in :mod:`auth_middleware`
and is re-exported here for backward compatibility.

Security posture
----------------
- JWT algorithm is **pinned** via ``ConclaveSettings.jwt_algorithm``.
  ``alg: none`` is **never** accepted.
- Operator credentials verified against a bcrypt hash via ``bcrypt.checkpw()``
  (constant-time comparison).
- ``require_scope()`` rejects bare-string scope claims (array injection
  attack vector).

Unconfigured mode
-----------------
When ``jwt_secret_key`` is empty, non-production environments operate in
pass-through mode (WARNING logged).  Production always enforces auth (T57.1).

Exempt paths
------------
:data:`AUTH_EXEMPT_PATHS` — paths that bypass authentication entirely.
Composed from ``COMMON_INFRA_EXEMPT_PATHS`` plus ``/auth/token``.

Task: T60.1 — Extract AuthenticationGateMiddleware to auth_middleware.py
    ``AuthenticationGateMiddleware`` re-exported here for backward compat.

CONSTITUTION Priority 0: Security — algorithm pinning, no alg:none
CONSTITUTION Priority 3: TDD
Task: T39.1, T39.2, T47.1, T47.3, T57.1, T63.4
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import bcrypt as _bcrypt
import jwt as pyjwt
from fastapi import Depends, HTTPException
from starlette.requests import Request

from synth_engine.bootstrapper.dependencies._exempt_paths import COMMON_INFRA_EXEMPT_PATHS
from synth_engine.bootstrapper.dependencies.auth_middleware import (
    AuthenticationGateMiddleware as AuthenticationGateMiddleware,  # re-exported
)
from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)

#: Routes that bypass authentication entirely.
#: Composed from COMMON_INFRA_EXEMPT_PATHS plus ``/auth/token`` so operators
#: can log in before any token is available (ADV-T39.1-01).
AUTH_EXEMPT_PATHS: frozenset[str] = COMMON_INFRA_EXEMPT_PATHS | frozenset({"/auth/token"})


class AuthenticationError(Exception):
    """Raised when JWT authentication fails.

    Covers: expired tokens, invalid signatures, malformed tokens, algorithm
    confusion, and algorithm rejection.

    Args:
        message: Human-readable description of the failure reason.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)


def create_token(*, sub: str, scope: list[str]) -> str:
    """Create a signed JWT token for the given operator.

    Token claims: ``sub`` (operator ID), ``iat`` (issued-at), ``exp``
    (expiry = now + ``jwt_expiry_seconds``), ``scope`` (permissions list).

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

    Algorithm is **pinned** to ``ConclaveSettings.jwt_algorithm``.
    Tokens claiming ``alg: none`` or any other algorithm are rejected.

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

    Uses ``bcrypt.checkpw()`` for constant-time comparison against
    ``ConclaveSettings.operator_credentials_hash``.  Returns ``False``
    when no credentials hash is configured.

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
    except Exception as exc:
        # Broad catch: any bcrypt error (e.g. invalid hash format) → deny.
        # Log at DEBUG only: exception type and message are safe (no passphrase
        # in the frame — bcrypt.checkpw args are encoded bytes, not logged).
        # NEVER log at INFO or above — that would surface errors in production
        # log aggregators and create a bcrypt error oracle via log channels.
        # CONSTITUTION Priority 0: passphrase must never appear in logs.
        _logger.debug(
            "Credential verification failed due to bcrypt error: %s",
            type(exc).__name__,
            exc_info=True,
        )
        return False


def get_current_operator(request: Request) -> str:
    """Extract and return the operator's sub claim from the JWT bearer token.

    FastAPI dependency for resource endpoints.  Extracts the
    ``Authorization`` header, verifies the bearer token, and returns the
    ``sub`` claim.

    **Pass-through mode (non-production only)**: Returns ``""`` when
    ``jwt_secret_key`` is empty and NOT in production mode.

    **Production hard-fail (T57.1)**: Returns 401 when ``jwt_secret_key``
    is empty AND in production mode.  An empty JWT secret in production is
    a misconfiguration — pass-through is never permitted.

    Args:
        request: The incoming HTTP request (injected by FastAPI).

    Returns:
        The ``sub`` claim from the verified JWT token, or ``""`` in
        unconfigured/pass-through mode (non-production only).

    Raises:
        HTTPException: 401 if the Authorization header is absent, malformed,
            or the token is invalid/expired.  Also raised in production mode
            when ``jwt_secret_key`` is empty.
    """
    settings = get_settings()
    jwt_secret = settings.jwt_secret_key.get_secret_value()

    if not jwt_secret:
        if settings.is_production():
            # T57.1: Production hard-fail — do NOT reveal configuration state.
            raise HTTPException(
                status_code=401,
                detail="Authentication required. Provide a valid Bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Non-production pass-through: return sentinel "".
        # T58.2: Populate jwt_claims with empty dict so require_scope works.
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
        # T63.4: Log the actual exception at DEBUG — do NOT put str(exc) in the
        # 401 response body.  AuthenticationError messages may include internal
        # JWT details (algorithm, claim names) that could aid an attacker.
        # Static message prevents oracle attacks via response body differences.
        # CONSTITUTION Priority 0: no internal error detail in auth responses.
        _logger.debug(
            "JWT authentication failed: %s",
            type(exc).__name__,
            exc_info=True,
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
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
    # T58.2: Cache decoded claims so require_scope avoids a second verify_token call.
    request.state.jwt_claims = claims
    return sub


def require_scope(scope: str) -> Callable[..., str]:
    """Return a FastAPI dependency that enforces the given scope.

    Resolves after ``get_current_operator`` has verified the token.
    Checks that the JWT ``scope`` claim is a *list* containing the required
    scope string.  Bare-string scope claims are rejected (array injection
    attack vector).

    **Pass-through mode**: scope check bypassed when ``jwt_secret_key`` is
    empty in non-production (consistent with ``get_current_operator``).

    **Production hard-fail (T57.1)**: 401 when ``jwt_secret_key`` is empty
    in production.

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

        Args:
            request: The incoming HTTP request (auto-injected by FastAPI).
            operator: Resolved operator sub claim from ``get_current_operator``.

        Returns:
            The operator sub claim on success.

        Raises:
            HTTPException: 401 if JWT is unconfigured in production, or if
                ``request.state.jwt_claims`` is absent (middleware reorder guard).
                403 if the scope claim is absent, not a list, or missing the
                required scope.
        """
        settings = get_settings()
        jwt_secret = settings.jwt_secret_key.get_secret_value()

        if not jwt_secret:
            if settings.is_production():
                raise HTTPException(
                    status_code=401,
                    detail="Authentication required. Provide a valid Bearer token.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return operator

        # T58.2: Read claims from request.state.jwt_claims (set by get_current_operator).
        if not hasattr(request.state, "jwt_claims"):
            raise HTTPException(
                status_code=401,
                detail="Authentication required. Provide a valid Bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        claims = request.state.jwt_claims
        raw_scope = claims.get("scope")

        # SECURITY: scope MUST be a list — bare string is injection vector.
        if not isinstance(raw_scope, list):
            _logger.warning(
                "Scope claim is not a list (type=%s). Rejecting request.",
                type(raw_scope).__name__,
            )
            raise HTTPException(
                status_code=403,
                detail="Forbidden. Required scope not present.",
            )

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
