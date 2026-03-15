"""FastAPI dependencies for vault seal gate enforcement.

This module is the **only** place where FastAPI concerns (middleware,
HTTP exceptions) are coupled to the framework-agnostic vault state in
:mod:`synth_engine.shared.security.vault`.

The separation maintains the Modular Monolith architectural contract:
``shared/`` has zero FastAPI imports; framework binding lives exclusively
in ``bootstrapper/``.

CONSTITUTION Priority 0: Security
Task: P2-T2.4 — Vault Observability
Task: P5-T5.2 — Offline License Activation Protocol (added license endpoints)
"""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from synth_engine.shared.security.vault import VaultState

#: Routes that are accessible even when the vault is sealed.
#: License endpoints are included so that operators can activate the software
#: without first unsealing the vault (challenge/response is a pre-boot flow).
EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/unseal",
        "/health",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/license/challenge",
        "/license/activate",
    }
)


class SealGateMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that blocks all non-exempt routes while sealed.

    Any request whose path is not in :data:`EXEMPT_PATHS` receives a
    **423 Locked** response while :attr:`VaultState._is_sealed` is
    ``True``.  Once unsealed, all requests pass through normally.

    The 423 status code is the canonical "Locked" response defined in
    RFC 4918 (WebDAV) and is the most semantically accurate choice for
    "this resource is currently locked / not yet activated".
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Gate every non-exempt request behind the vault seal check.

        Args:
            request: Incoming HTTP request.
            call_next: ASGI callable for the next middleware or route handler.

        Returns:
            A 423 JSONResponse if sealed and the path is not exempt,
            otherwise the normal downstream response.
        """
        if VaultState.is_sealed() and request.url.path not in EXEMPT_PATHS:
            return JSONResponse(
                content={"detail": "Service sealed. POST /unseal to activate."},
                status_code=423,
            )
        return await call_next(request)


async def require_unsealed() -> None:
    """FastAPI dependency that raises 423 if the vault is sealed.

    Use this on individual routes that need an explicit seal-check
    independent of the global middleware (e.g., internal sub-routes
    that bypass middleware).

    Raises:
        HTTPException: 423 if the vault is currently sealed.
    """
    if VaultState.is_sealed():
        raise HTTPException(
            status_code=423,
            detail="Service sealed. POST /unseal to activate.",
        )
