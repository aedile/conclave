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

Implementation note — RequestValidationError with NaN/Infinity inputs:
    FastAPI's default ``RequestValidationError`` handler serializes the raw
    input values into the error response (e.g. as ``{"detail": [{"input": NaN}]}``).
    Python's stdlib ``json.dumps`` does NOT support ``NaN`` or ``Infinity``
    and raises ``ValueError`` when asked to serialize them (RFC 8259 §6:
    "Numeric values that cannot be represented as sequences of digits ... are
    NOT permitted").

    This produces a 500 Internal Server Error for requests containing
    ``NaN`` or ``Infinity`` rather than the expected 422.

    Our custom ``RequestValidationError`` handler passes all error dicts
    through :func:`_sanitize_for_json` which replaces non-finite float values
    with the string ``"<non-finite float>"`` before serialization.  This
    ensures all validation errors produce a proper 400 or 422 response.

Reference: RFC 7807 — Problem Details for HTTP APIs
    https://datatracker.ietf.org/doc/html/rfc7807

Task: P5-T5.1 — Task Orchestration API Core
Task: P6-T6.2 — NIST SP 800-88 Erasure, OWASP validation, LLM Fuzz Testing
    (Added RequestValidationError NaN/Infinity sanitization)
"""

from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
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

#: Sentinel string replacing non-finite float values in error responses.
_NON_FINITE_SENTINEL: str = "<non-finite float>"


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively replace non-JSON-serializable float values in an object.

    Python's stdlib ``json.dumps`` does not support ``NaN``, ``Infinity``,
    or ``-Infinity`` (RFC 8259 §6).  When these appear in Pydantic validation
    error dicts (e.g. as the ``input`` field reflecting the raw request value),
    serialization raises ``ValueError``.

    This function walks the object graph and replaces any non-finite float
    with the sentinel string :data:`_NON_FINITE_SENTINEL`.

    Args:
        obj: The object to sanitize.  May be a dict, list, float, or any
            other JSON-serializable type.

    Returns:
        A sanitized copy of ``obj`` where all non-finite floats have been
        replaced with :data:`_NON_FINITE_SENTINEL`.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return _NON_FINITE_SENTINEL
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(_sanitize_for_json(item) for item in obj)
    return obj


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


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle RequestValidationError with NaN/Infinity-safe serialization.

    FastAPI's default validation error handler serializes the raw input
    values in the error response.  When the request body contains
    ``NaN`` or ``Infinity`` (non-standard JSON values), Python's
    ``json.dumps`` raises ``ValueError`` because these values are not
    valid JSON (RFC 8259 §6).

    This handler replaces all non-finite float values with
    :data:`_NON_FINITE_SENTINEL` before serialization, ensuring
    that the response is always a well-formed JSON document.

    Args:
        request: The incoming HTTP request (required by FastAPI signature).
        exc: The RequestValidationError raised by FastAPI/Pydantic.

    Returns:
        JSONResponse with HTTP 422 and sanitized Pydantic error details.
    """
    errors = exc.errors()
    sanitized_errors = _sanitize_for_json(errors)
    _logger.warning(
        "Request validation error on %s %s: %d error(s).",
        request.method,
        request.url.path,
        len(errors),
    )
    return JSONResponse(
        status_code=422,
        content={"detail": sanitized_errors},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register RFC 7807 catch-all error handling on the FastAPI app.

    Adds :class:`RFC7807Middleware` as an outer ASGI middleware and
    registers a custom ``RequestValidationError`` handler that safely
    serializes validation errors even when the request input contains
    non-finite float values (NaN, Infinity, -Infinity).

    This function is idempotent-safe: each call wraps the app in an
    additional middleware layer.  Call exactly once per app instance.

    Args:
        app: The FastAPI application instance to register handlers on.
    """
    app.add_middleware(RFC7807Middleware)

    # Register a safe RequestValidationError handler that sanitizes
    # non-finite float values before JSON serialization (P6-T6.2).
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]  # FastAPI add_exception_handler expects ExceptionHandler; our handler matches the protocol but mypy cannot verify the async overload
