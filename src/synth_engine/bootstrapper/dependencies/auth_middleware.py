"""Starlette middleware for JWT Bearer token gate enforcement.

Contains:
- ``_build_401_response`` — private helper that constructs RFC 7807 401 responses.
- ``AuthenticationGateMiddleware`` — the per-request auth gate middleware.

This module is the **canonical** location for ``AuthenticationGateMiddleware``.
Token verification is delegated to ``verify_token`` and
``AuthenticationError`` from :mod:`auth`, imported inside the dispatch
method to avoid a circular import (``auth.py`` re-exports this class).

Circular import resolution
--------------------------
``auth.py`` re-exports ``AuthenticationGateMiddleware`` from this module.
If this module imported from ``auth.py`` at module scope, that would create
a circular dependency.  To break the circle:

- ``AUTH_EXEMPT_PATHS`` is referenced via a deferred import inside
  ``dispatch()`` rather than at module scope.  This resolves correctly at
  call time after both modules are fully loaded.
- ``verify_token`` and ``AuthenticationError`` are also imported inside
  ``dispatch()`` for the same reason.

Task: T60.1 — Extract AuthenticationGateMiddleware from auth.py
    Previously ``AuthenticationGateMiddleware`` and ``_build_401_response``
    lived in ``dependencies/auth.py`` alongside JWT utility functions and
    FastAPI dependencies.  The middleware is architecturally distinct from
    the per-route dependencies; this module separates them.

    Backward compatibility: ``auth.py`` re-exports
    ``AuthenticationGateMiddleware`` from this module so that all existing
    imports (``from synth_engine.bootstrapper.dependencies.auth import
    AuthenticationGateMiddleware``) continue to resolve correctly.

Middleware ordering
-------------------
``AuthenticationGateMiddleware`` must be registered **INNERMOST** in
``setup_middleware()`` — after :class:`LicenseGateMiddleware` in the
``app.add_middleware()`` call list, which means it fires BEFORE
``LicenseGateMiddleware`` on the request path (LIFO evaluation order):

    RequestBodyLimitMiddleware → CSPMiddleware → SealGateMiddleware
    → LicenseGateMiddleware → AuthenticationGateMiddleware → route handler

Security posture
----------------
- Algorithm pinning and token validation is delegated to ``verify_token``
  in :mod:`auth`.  This module only concerns itself with the HTTP layer:
  extracting the Authorization header, calling verify_token, and returning
  the appropriate 401 response on failure.
- Failure detail messages do not leak key material or internal state.
  On ``AuthenticationError``, only the static string "Invalid credentials"
  is returned; exception detail is logged at DEBUG with ``exc_info=True``
  so the full traceback is available for operators but never exposed to
  callers.  (Arch review finding, Phase 63.)
- Pass-through mode is only permitted in non-production environments.

CONSTITUTION Priority 0: Security
Task: T39.1 — Add Authentication Middleware (JWT Bearer Token)
Task: T57.1 — JWT Authentication Hard-Fail in Production
"""

from __future__ import annotations

import logging

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)


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

        Deferred imports are used for ``AUTH_EXEMPT_PATHS``,
        ``verify_token``, and ``AuthenticationError`` to avoid the circular
        import that would occur if they were imported at module scope
        (``auth.py`` re-exports ``AuthenticationGateMiddleware`` from here).

        Args:
            request: Incoming HTTP request.
            call_next: ASGI callable for the next middleware or route handler.

        Returns:
            A 401 JSONResponse (RFC 7807) if the token is absent, invalid,
            or if the deployment is production with no JWT secret configured.
            Otherwise returns the normal downstream response.
        """
        # Deferred to break circular import: auth.py re-exports this class
        # from auth_middleware.py, so importing auth at module scope here
        # would create a cycle.
        from synth_engine.bootstrapper.dependencies.auth import (
            AUTH_EXEMPT_PATHS,
            AuthenticationError,
            verify_token,
        )

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
            # Log at DEBUG with full traceback for operators; do NOT pass exc detail
            # to the response — that would leak JWT error internals (e.g. algorithm
            # name, decode error details) to the caller.  Arch review finding, Phase 63.
            _logger.debug(
                "Authentication failed: %s",
                type(exc).__name__,
                exc_info=True,
            )
            return _build_401_response("Invalid credentials")

        return await call_next(request)
