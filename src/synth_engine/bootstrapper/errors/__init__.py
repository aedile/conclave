"""RFC 7807 Problem Details error handling for the bootstrapper.

This package is split into focused submodules:

- :mod:`~synth_engine.bootstrapper.errors.formatter` — RFC 7807 dict builders,
  JSON sanitization, operator error response builder, and validation error handler.
- :mod:`~synth_engine.bootstrapper.errors.middleware` — Pure ASGI ``RFC7807Middleware``.
- :mod:`~synth_engine.bootstrapper.errors.mapping` — ``OPERATOR_ERROR_MAP`` and
  ``OperatorErrorEntry`` TypedDict.

All public symbols are re-exported from this ``__init__.py`` so that all
existing ``from synth_engine.bootstrapper.errors import ...`` statements
continue to work without modification.

All error messages exposed via HTTP are sanitized through
:func:`synth_engine.shared.errors.safe_error_msg` (ADV-036+044).

Reference: RFC 7807 — Problem Details for HTTP APIs
    https://datatracker.ietf.org/doc/html/rfc7807

Task: P5-T5.1 — Task Orchestration API Core
Task: T19.1 — Middleware & Engine Singleton Fixes
Task: P29-T29.3 — Error Message Audience Differentiation
Task: T34.3 — Complete OPERATOR_ERROR_MAP for All Domain Exceptions
Task: T36.2 — Split bootstrapper/errors.py Into Focused Modules
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from synth_engine.bootstrapper.errors.formatter import (
    _validation_error_handler,
    operator_error_response,
    problem_detail,
)
from synth_engine.bootstrapper.errors.mapping import (
    OPERATOR_ERROR_MAP,
    OperatorErrorEntry,
)
from synth_engine.bootstrapper.errors.middleware import RFC7807Middleware

__all__ = [
    "OPERATOR_ERROR_MAP",
    "OperatorErrorEntry",
    "RFC7807Middleware",
    "operator_error_response",
    "problem_detail",
    "register_error_handlers",
]


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
