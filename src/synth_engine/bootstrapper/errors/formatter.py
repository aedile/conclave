"""RFC 7807 Problem Details dict builders and JSON sanitization utilities.

Provides :func:`problem_detail` to build RFC 7807-compliant error dicts,
:func:`operator_error_response` to build operator-friendly :class:`JSONResponse`
objects from domain exceptions, and :func:`_sanitize_for_json` to strip
non-finite float values before JSON serialization.

Implementation note — RequestValidationError with NaN/Infinity inputs:
    FastAPI's default ``RequestValidationError`` handler serializes the raw
    input values into the error response (e.g. as ``{"detail": [{"input": NaN}]}``).
    Python's stdlib ``json.dumps`` does NOT support ``NaN`` or ``Infinity``
    and raises ``ValueError`` when asked to serialize them (RFC 8259 §6:
    "Numeric values that cannot be represented as sequences of digits ... are
    NOT permitted").

    Our custom ``RequestValidationError`` handler passes all error dicts
    through :func:`_sanitize_for_json` which replaces non-finite float values
    with the string ``"<non-finite float>"`` before serialization.  This
    ensures all validation errors produce a proper 400 or 422 response.

Implementation note — Operator-friendly error messages (T29.3):
    Domain exceptions carry technical messages intended for developer logs.
    These must NOT be forwarded verbatim to HTTP clients.
    :func:`operator_error_response` consults ``OPERATOR_ERROR_MAP``
    and returns a plain-language operator-friendly response.

Implementation note — Server-side log sanitization (ADV-P34-01):
    Domain exception messages may contain filesystem paths, SQL identifiers,
    or other internal details that must not appear in server logs.
    :func:`operator_error_response` passes the exception message through
    :func:`~synth_engine.shared.errors.safe_error_msg` before logging it
    at WARNING level, ensuring the log entry cannot leak sensitive paths or
    schema information to log aggregation systems.

Reference: RFC 7807 — Problem Details for HTTP APIs
    https://datatracker.ietf.org/doc/html/rfc7807

Task: P5-T5.1 — Task Orchestration API Core
Task: P6-T6.2 — NIST SP 800-88 Erasure, OWASP validation, LLM Fuzz Testing
Task: P29-T29.3 — Error Message Audience Differentiation
Task: T36.2 — Split bootstrapper/errors.py Into Focused Modules
Task: T37.2 — Drain ADV-P34-01: sanitize exc message in server-side WARNING log
"""

from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from synth_engine.bootstrapper.errors.mapping import OPERATOR_ERROR_MAP
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


def operator_error_response(exc: Exception) -> JSONResponse:
    """Build an RFC 7807 JSONResponse for a known domain exception.

    Looks up the exception class in ``OPERATOR_ERROR_MAP`` and returns an
    appropriate operator-friendly response.  The internal exception message
    is sanitized via :func:`~synth_engine.shared.errors.safe_error_msg` and
    logged at WARNING level, but never included in the HTTP response body.

    Sanitizing the server-side log entry prevents filesystem paths, SQL
    identifiers, and internal Python module names in exception messages from
    reaching log aggregation systems (ADV-P34-01).

    Args:
        exc: The domain exception to convert.  Must be present in
            ``OPERATOR_ERROR_MAP``; callers are responsible for checking
            membership before calling this function.

    Returns:
        JSONResponse with the operator-friendly RFC 7807 body and the
        mapped HTTP status code.  If the exception class is not present
        in ``OPERATOR_ERROR_MAP``, a ``KeyError`` is raised by the dict
        lookup — callers must ensure membership before calling this function.
    """
    entry = OPERATOR_ERROR_MAP[type(exc)]
    _logger.warning(
        "Domain exception %s: %s",
        type(exc).__name__,
        safe_error_msg(str(exc)),
    )
    return JSONResponse(
        status_code=entry["status_code"],
        content=problem_detail(
            status=entry["status_code"],
            title=entry["title"],
            detail=entry["detail"],
            type_uri=entry["type_uri"],
        ),
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
