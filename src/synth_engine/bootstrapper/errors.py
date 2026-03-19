"""RFC 7807 Problem Details error formatter and FastAPI exception handlers.

Provides :func:`problem_detail` to build RFC 7807-compliant error dicts and
:func:`register_error_handlers` to attach a catch-all exception handler to
a FastAPI application.

All error messages exposed via HTTP are sanitized through
:func:`synth_engine.shared.errors.safe_error_msg` (ADV-036+044).

Implementation note — Pure ASGI middleware (T19.1):
    ``RFC7807Middleware`` is implemented as a pure ASGI middleware class rather
    than extending Starlette's ``BaseHTTPMiddleware``.

    ``BaseHTTPMiddleware`` buffers the **entire response body** before returning
    to the outer stack.  This breaks SSE (Server-Sent Events) streaming because
    the SSE generator yields chunks incrementally — buffering defeats the purpose
    and causes the client to receive all events at once (or not at all) when the
    stream terminates.

    Pure ASGI middleware calls ``await self.app(scope, receive, send)`` directly
    with a wrapped ``send`` callable, which allows each SSE chunk to pass through
    to the client as soon as it is yielded.

    The wrapped ``send`` callable tracks whether response headers have already
    been sent (``headers_sent`` flag).  If an exception is raised after headers
    have been sent, we cannot send a new 500 response — the connection is already
    committed to a different status code.  In that case the exception is re-raised
    and will be handled by Starlette's ``ServerErrorMiddleware``.

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

Implementation note — Operator-friendly error messages (T29.3):
    Domain exceptions carry technical messages intended for developer logs
    (e.g. ``"DP budget exhausted: epsilon_spent=1.234 >= allocated_epsilon=1.0"``).
    These must NOT be forwarded verbatim to HTTP clients — operators need
    plain-language titles and actionable remediation instructions instead.

    :data:`OPERATOR_ERROR_MAP` maps each domain exception class to a
    presentation-layer tuple of ``(title, detail, status_code, type_uri)``.
    The mapping is consulted by the exception handlers registered in
    :mod:`synth_engine.bootstrapper.router_registry`.

    Security-sensitive exceptions (:exc:`PrivilegeEscalationError`,
    :exc:`ArtifactTamperingError`) are included in the map but use fixed,
    static detail strings that contain no security-sensitive context.
    Their exception messages are logged at WARNING level but never forwarded
    verbatim to HTTP clients (ADV-036+044).

Reference: RFC 7807 — Problem Details for HTTP APIs
    https://datatracker.ietf.org/doc/html/rfc7807

Task: P5-T5.1 — Task Orchestration API Core
Task: P6-T6.2 — NIST SP 800-88 Erasure, OWASP validation, LLM Fuzz Testing
    (Added RequestValidationError NaN/Infinity sanitization)
Task: T19.1 — Middleware & Engine Singleton Fixes
    (Converted from BaseHTTPMiddleware to pure ASGI middleware)
Task: P29-T29.3 — Error Message Audience Differentiation
    (Added OPERATOR_ERROR_MAP for operator-friendly RFC 7807 responses)
Task: T34.3 — Complete OPERATOR_ERROR_MAP for All Domain Exceptions
    (Added mappings for VaultAlreadyUnsealedError, LicenseError, CollisionError,
    CycleDetectionError, PrivilegeEscalationError, ArtifactTamperingError)
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, TypedDict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from synth_engine.modules.mapping.graph import CycleDetectionError
from synth_engine.modules.masking.registry import CollisionError
from synth_engine.shared.errors import safe_error_msg
from synth_engine.shared.exceptions import (
    ArtifactTamperingError,
    BudgetExhaustionError,
    LicenseError,
    OOMGuardrailError,
    PrivilegeEscalationError,
    VaultAlreadyUnsealedError,
    VaultConfigError,
    VaultEmptyPassphraseError,
    VaultSealedError,
)

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


class OperatorErrorEntry(TypedDict):
    """Presentation-layer mapping for a domain exception.

    Each entry in :data:`OPERATOR_ERROR_MAP` must supply all four fields so
    that any exception handler can build a complete RFC 7807 response without
    falling back to defaults.

    Attributes:
        title: Short, plain-language summary shown as the error heading
            in the frontend ``RFC7807Toast`` component.
        detail: Operator-facing explanation with a concrete remediation
            action.  Must NOT contain raw exception messages, epsilon values,
            internal paths, or any other developer-only technical details.
        status_code: HTTP status code for this error class.
        type_uri: RFC 7807 ``type`` field — either ``"about:blank"`` or a
            URI identifying the specific problem type.
    """

    title: str
    detail: str
    status_code: int
    type_uri: str


#: Operator-friendly RFC 7807 presentation mapping for all domain exceptions.
#:
#: Keys are exception *classes* (not instances).  Values are
#: :class:`OperatorErrorEntry` dicts consumed by exception handlers in
#: :mod:`synth_engine.bootstrapper.router_registry` and the ``/unseal`` route
#: in :mod:`synth_engine.bootstrapper.lifecycle`.
#:
#: Security rule: :exc:`PrivilegeEscalationError` and
#: :exc:`ArtifactTamperingError` are mapped with FIXED, STATIC detail strings
#: that contain no security-sensitive context (no role names, no artifact paths,
#: no HMAC hints).  The raw exception message is logged at WARNING level but
#: must never appear verbatim in the HTTP response body (ADV-036+044).
#: The ``detail`` field in these entries is a safe, sanitized constant — not
#: derived from ``str(exc)``.
OPERATOR_ERROR_MAP: dict[type[Exception], OperatorErrorEntry] = {
    BudgetExhaustionError: OperatorErrorEntry(
        title="Privacy Budget Exceeded",
        detail=(
            "The privacy budget for this dataset has been exhausted. "
            "Reset the privacy budget via POST /privacy/budget/reset "
            "or contact your administrator."
        ),
        status_code=409,
        type_uri="about:blank",
    ),
    OOMGuardrailError: OperatorErrorEntry(
        title="Memory Limit Exceeded",
        detail=(
            "The synthesis job was rejected because the estimated memory "
            "requirement exceeds available system memory. "
            "Reduce the dataset size or the number of rows and retry."
        ),
        status_code=422,
        type_uri="about:blank",
    ),
    VaultSealedError: OperatorErrorEntry(
        title="Vault Is Sealed",
        detail=(
            "Unseal the vault before performing data operations. POST /unseal with your passphrase."
        ),
        status_code=423,
        type_uri="about:blank",
    ),
    VaultEmptyPassphraseError: OperatorErrorEntry(
        title="Empty Passphrase",
        detail="Enter a non-empty passphrase to unseal the vault.",
        status_code=400,
        type_uri="about:blank",
    ),
    VaultConfigError: OperatorErrorEntry(
        title="Vault Configuration Error",
        detail=(
            "The vault cannot be unsealed due to a configuration error. "
            "Ensure the VAULT_SEAL_SALT environment variable is set and "
            "meets the 16-byte minimum length requirement."
        ),
        status_code=400,
        type_uri="about:blank",
    ),
    # HTTP 400 (not 409) is intentional: "already unsealed" means the operator's
    # desired state is already achieved — a bad request, not a resource conflict.
    # This matches the bespoke inline handler in bootstrapper/lifecycle.py which
    # also returns 400 for VaultAlreadyUnsealedError on POST /unseal.
    VaultAlreadyUnsealedError: OperatorErrorEntry(
        title="Vault Already Unsealed",
        detail=(
            "The vault is already unsealed. No action is required. "
            "To re-seal and rotate the key, call POST /seal first."
        ),
        status_code=400,
        type_uri="about:blank",
    ),
    LicenseError: OperatorErrorEntry(
        title="License Validation Failed",
        detail=(
            "The engine license could not be validated. "
            "Ensure a valid license token is configured and has not expired. "
            "Contact your administrator to renew or reconfigure the license."
        ),
        status_code=403,
        type_uri="about:blank",
    ),
    CollisionError: OperatorErrorEntry(
        title="Masking Collision Detected",
        detail=(
            "A collision was detected during deterministic masking. "
            "This indicates an unexpected state in the masking registry. "
            "Retry the operation or contact your administrator if the problem persists."
        ),
        status_code=409,
        type_uri="about:blank",
    ),
    CycleDetectionError: OperatorErrorEntry(
        title="Cycle Detected in Schema Graph",
        detail=(
            "A circular dependency was detected in the database schema foreign-key graph. "
            "Provide explicit cycle-breaking rules before ingestion can proceed."
        ),
        status_code=422,
        type_uri="about:blank",
    ),
    # Security-sensitive exceptions: detail is a fixed static string.
    # The raw exception message (which may contain credential hints or internal
    # paths) is logged at WARNING level but MUST NOT appear in HTTP responses.
    PrivilegeEscalationError: OperatorErrorEntry(
        title="Insufficient Database Privileges",
        detail=(
            "The ingestion database user has write privileges on the source database. "
            "Configure a read-only database user for ingestion and retry."
        ),
        status_code=403,
        type_uri="about:blank",
    ),
    ArtifactTamperingError: OperatorErrorEntry(
        title="Model Artifact Integrity Failure",
        detail=(
            "A model artifact failed integrity verification. "
            "The artifact may have been modified or corrupted. "
            "Delete the affected artifact and re-run the synthesis job."
        ),
        status_code=422,
        type_uri="about:blank",
    ),
}


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

    Looks up the exception class in :data:`OPERATOR_ERROR_MAP` and returns
    an appropriate operator-friendly response.  The internal exception message
    is logged at WARNING level but never included in the HTTP response body.

    Args:
        exc: The domain exception to convert.  Must be present in
            :data:`OPERATOR_ERROR_MAP`; callers are responsible for checking
            membership before calling this function.

    Returns:
        JSONResponse with the operator-friendly RFC 7807 body and the
        mapped HTTP status code.  If the exception class is not present
        in :data:`OPERATOR_ERROR_MAP`, a `KeyError` is raised by the dict
        lookup — callers must ensure membership before calling this function.
    """
    entry = OPERATOR_ERROR_MAP[type(exc)]
    _logger.warning(
        "Domain exception %s: %s",
        type(exc).__name__,
        str(exc),
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


class RFC7807Middleware:
    """Pure ASGI middleware that converts unhandled exceptions to RFC 7807 responses.

    Wraps the entire request/response cycle using the raw ASGI protocol.
    Unlike ``BaseHTTPMiddleware``, this implementation does NOT buffer the
    response body, which allows SSE (Server-Sent Events) streaming to work
    correctly — each chunk is forwarded to the client immediately.

    If any exception escapes from the inner app (including route handlers),
    it is caught here and converted to an HTTP 500 response with an RFC 7807
    Problem Details JSON body, provided headers have not yet been sent.

    If the exception occurs after response headers have been sent (i.e., the
    status line and headers are already committed), the exception is re-raised
    so that Starlette's ``ServerErrorMiddleware`` can handle it.  This is the
    correct behaviour: we cannot overwrite a response that is already in flight.

    Error details are sanitized via :func:`~synth_engine.shared.errors.safe_error_msg`
    before exposure (ADV-036+044).

    This middleware must be added LAST (outermost) so it catches exceptions
    from all inner middleware layers and route handlers.

    Non-HTTP scope types (e.g., ``lifespan``, ``websocket``) are passed through
    to the inner app without modification.

    Args:
        app: The inner ASGI application to wrap.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle an ASGI request, catching unhandled exceptions.

        For ``http`` scopes: wraps the inner app call in a try/except.
        On exception: if headers have not yet been sent, returns an RFC 7807
        500 JSON response.  If headers were already sent, re-raises.

        For non-``http`` scopes: passes directly to the inner app unchanged.

        Args:
            scope: The ASGI connection scope (type, path, headers, etc.).
            receive: The ASGI receive callable.
            send: The ASGI send callable.

        Raises:
            Exception: Any unhandled exception from the inner app is re-raised
                when response headers have already been sent.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers_sent = False

        async def _send_wrapper(message: Any) -> None:
            nonlocal headers_sent
            if message.get("type") == "http.response.start":
                headers_sent = True
            await send(message)

        try:
            await self.app(scope, receive, _send_wrapper)
        # Broad catch intentional: ASGI middleware must convert all unhandled errors to RFC 7807
        except Exception as exc:
            if headers_sent:
                # Cannot send a new response — headers already committed.
                raise

            _logger.exception(
                "Unhandled exception on %s %s",
                scope.get("method", "UNKNOWN"),
                scope.get("path", "/"),
            )
            safe_detail = safe_error_msg(str(exc))
            body = problem_detail(
                status=500,
                title=_STATUS_TITLES.get(500, "Internal Server Error"),
                detail=safe_detail,
            )
            body_bytes = json.dumps(body).encode("utf-8")

            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        [b"content-type", b"application/json"],
                        [b"content-length", str(len(body_bytes)).encode("ascii")],
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": body_bytes,
                    "more_body": False,
                }
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
