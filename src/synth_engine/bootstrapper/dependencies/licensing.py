"""FastAPI dependencies for license gate enforcement.

This module is the **only** place where FastAPI concerns (middleware,
HTTP exceptions) are coupled to the framework-agnostic license state in
:mod:`synth_engine.shared.security.licensing`.

The separation maintains the Modular Monolith architectural contract:
``shared/`` has zero FastAPI imports; framework binding lives exclusively
in ``bootstrapper/``.

CONSTITUTION Priority 0: Security
Task: P5-T5.2 — Offline License Activation Protocol
"""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from synth_engine.bootstrapper.dependencies._exempt_paths import (
    COMMON_INFRA_EXEMPT_PATHS as LICENSE_EXEMPT_PATHS,
)
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.shared.security.licensing import LicenseState


class LicenseGateMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that blocks non-exempt routes when not licensed.

    Any request whose path is not in :data:`LICENSE_EXEMPT_PATHS` receives
    a **402 Payment Required** response while :attr:`LicenseState._is_licensed`
    is ``False``.  Once the software is activated, all requests pass through
    normally.

    HTTP 402 is the most semantically accurate status for "a license is
    required to use this feature" per the HTTP specification
    (RFC 9110 §15.5.3: "the server refuses the request until the client
    makes a payment").

    The response body uses RFC 7807 Problem Details format for consistency
    with all other error responses in the application.

    Note: :class:`~synth_engine.bootstrapper.dependencies.vault.SealGateMiddleware`
    is evaluated first (outermost), so a sealed vault returns 423 before
    this middleware's 402 check fires.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Gate every non-exempt request behind the license check.

        Args:
            request: Incoming HTTP request.
            call_next: ASGI callable for the next middleware or route handler.

        Returns:
            A 402 JSONResponse (RFC 7807) if unlicensed and the path is not
            exempt, otherwise the normal downstream response.
        """
        if not LicenseState.is_licensed() and request.url.path not in LICENSE_EXEMPT_PATHS:
            return JSONResponse(
                content=problem_detail(
                    status=402,
                    title="License Required",
                    detail=(
                        "Software is not licensed. GET /license/challenge to begin activation."
                    ),
                ),
                status_code=402,
            )
        return await call_next(request)
