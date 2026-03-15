"""RFC 7807 Problem Details error formatter and FastAPI exception handlers.

Provides :func:`problem_detail` to build RFC 7807-compliant error dicts and
:func:`register_error_handlers` to attach a catch-all exception handler to
a FastAPI application.

All error messages exposed via HTTP are sanitized through
:func:`synth_engine.shared.errors.safe_error_msg` (ADV-036+044).

Implementation note — BaseHTTPMiddleware and exception handlers:
    FastAPI's ``@app.exception_handler(Exception)`` is implemented as an
    ``ExceptionMiddleware`` that is placed INSIDE ``BaseHTTPMiddleware``-based
    middlewares (e.g. ``SealGateMiddleware``).  Exceptions from route handlers
    propagate out of ``BaseHTTPMiddleware.call_next()`` and are handled by
    ``ServerErrorMiddleware`` — but that layer cannot use our custom handler.

    The solution: register the catch-all using ``@app.middleware("http")``
    which is ASGI-level and positioned OUTSIDE the route exception-handler
    chain.  This correctly intercepts exceptions before they reach the
    generic Starlette 500 HTML handler.

Reference: RFC 7807 — Problem Details for HTTP APIs
    https://datatracker.ietf.org/doc/html/rfc7807

Task: P5-T5.1 — Task Orchestration API Core
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from synth_engine.shared.errors import safe_error_msg

_logger = logging.getLogger(__name__)

#: Default RFC 7807 type URI for generic errors without a specific type.
_DEFAULT_TYPE_URI: str = "about:blank"

#: HTTP status-to-title mapping for common server-side status codes.
_STATUS_TITLES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    422: "Unprocessable Entity",
    500: "Internal Server Error",
}


def problem_detail(
    status: int,
    title: str,
    detail: str,
    type_uri: str = _DEFAULT_TYPE_URI,
) -> dict[str, Any]:
    """Build an RFC 7807 Problem Details dictionary.

    The returned dictionary contains all four required RFC 7807 fields:
    ``type``, ``title``, ``status``, ``detail``.

    Args:
        status: HTTP status code for the problem (e.g. 404, 500).
        title: Short, human-readable summary of the problem type.
        detail: Human-readable explanation specific to this occurrence.
            Must already be sanitized by the caller if it originates from
            an exception message.
        type_uri: URI reference identifying the problem type.
            Defaults to ``"about:blank"`` per RFC 7807 §4.2.

    Returns:
        A dictionary with keys ``type``, ``title``, ``status``, ``detail``.
    """
    return {
        "type": type_uri,
        "title": title,
        "status": status,
        "detail": detail,
    }


class RFC7807Middleware(BaseHTTPMiddleware):
    """ASGI middleware that converts unhandled exceptions to RFC 7807 responses.

    Wraps the entire request/response cycle.  If any exception escapes from
    the inner app (including route handlers), it is caught here and converted
    to an HTTP 500 response with an RFC 7807 Problem Details JSON body.

    Error details are sanitized via :func:`~synth_engine.shared.errors.safe_error_msg`
    before exposure (ADV-036+044).

    This middleware must be added LAST (outermost) so it catches exceptions
    from all inner middleware layers and route handlers.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Catch unhandled exceptions and return RFC 7807 Problem Details.

        Args:
            request: Incoming HTTP request.
            call_next: The inner application callable.

        Returns:
            The normal response if no exception occurs, otherwise an HTTP 500
            RFC 7807 JSON response with a sanitized error detail.
        """
        try:
            return await call_next(request)
        except Exception as exc:
            _logger.exception("Unhandled exception on %s %s", request.method, request.url)
            safe_detail = safe_error_msg(str(exc))
            body = problem_detail(
                status=500,
                title=_STATUS_TITLES.get(500, "Internal Server Error"),
                detail=safe_detail,
            )
            return JSONResponse(
                status_code=500,
                content=body,
                headers={"content-type": "application/json"},
            )


def register_error_handlers(app: FastAPI) -> None:
    """Register RFC 7807 catch-all error handling on the FastAPI app.

    Adds :class:`RFC7807Middleware` as an outer ASGI middleware.  This
    approach is used instead of ``@app.exception_handler(Exception)``
    because FastAPI's built-in exception handler registration is bypassed
    by ``BaseHTTPMiddleware`` (e.g. ``SealGateMiddleware``) due to how
    ``call_next()`` re-raises route exceptions.

    This function is idempotent-safe: each call wraps the app in an
    additional middleware layer.  Call exactly once per app instance.

    Args:
        app: The FastAPI application instance to register handlers on.
    """
    app.add_middleware(RFC7807Middleware)
