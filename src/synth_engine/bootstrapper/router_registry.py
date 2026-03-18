"""Router registration and exception handler wiring for the Conclave Engine.

Named ``router_registry.py`` (not ``routers.py``) because ``routers/`` already
exists as a package directory in this package — a plain ``routers.py`` file
would shadow that package and break all ``from synth_engine.bootstrapper.routers.*``
imports.

Contains:
- ``_include_routers()`` — wires the seven domain routers into the application.
- ``_register_exception_handlers()`` — registers the CycleDetectionError handler,
  domain exception handlers with operator-friendly RFC 7807 messages, and the
  RFC 7807 catch-all via :mod:`synth_engine.bootstrapper.errors`.

Task: P29-T29.3 — Error Message Audience Differentiation
    Added handlers for BudgetExhaustionError, OOMGuardrailError, VaultSealedError
    using OPERATOR_ERROR_MAP for consistent operator-friendly RFC 7807 responses.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from synth_engine.modules.mapping import CycleDetectionError
from synth_engine.shared.exceptions import (
    BudgetExhaustionError,
    OOMGuardrailError,
    VaultSealedError,
)


def _include_routers(app: FastAPI) -> None:
    """Include all APIRouter submodules into the application.

    Imported here (not at module top-level) so that create_app() controls
    registration order relative to exception handlers and middleware.

    Args:
        app: The FastAPI instance to attach routers to.
    """
    from synth_engine.bootstrapper.routers.connections import router as connections_router
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router
    from synth_engine.bootstrapper.routers.jobs_streaming import router as jobs_streaming_router
    from synth_engine.bootstrapper.routers.licensing import router as licensing_router
    from synth_engine.bootstrapper.routers.privacy import router as privacy_router
    from synth_engine.bootstrapper.routers.security import router as security_router
    from synth_engine.bootstrapper.routers.settings import router as settings_router

    app.include_router(jobs_router)
    app.include_router(jobs_streaming_router)
    app.include_router(connections_router)
    app.include_router(settings_router)
    app.include_router(licensing_router)
    app.include_router(security_router)
    app.include_router(privacy_router)


def _register_exception_handlers(app: FastAPI) -> None:
    """Register application-level exception handlers.

    Handlers convert known domain exceptions to structured HTTP responses
    before FastAPI's default 500 handler fires.

    ADV-022: CycleDetectionError -> HTTP 422 RFC 7807 Problem Details.
    T5.1: Generic Exception -> HTTP 500 RFC 7807 Problem Details (ADV-036+044).
    T6.2: RequestValidationError -> HTTP 422 with NaN/Infinity-safe serialization.
    T29.3: Domain exceptions -> operator-friendly RFC 7807 via OPERATOR_ERROR_MAP.

    Security: PrivilegeEscalationError and ArtifactTamperingError are NOT handled
    here — they are security-sensitive and must only appear in internal logs.
    The catch-all RFC7807Middleware returns a generic 500 for them.

    Args:
        app: The FastAPI instance to register handlers on.
    """
    # Generic catch-all RFC 7807 handler (T5.1) — must be registered BEFORE
    # domain-specific handlers so that specific handlers take precedence.
    from synth_engine.bootstrapper.errors import operator_error_response, register_error_handlers

    register_error_handlers(app)

    @app.exception_handler(CycleDetectionError)
    async def _cycle_detection_error_handler(
        request: Request, exc: CycleDetectionError
    ) -> JSONResponse:
        """Handle CycleDetectionError with HTTP 422 RFC 7807 Problem Details.

        A cycle in the schema FK graph is a client-side data error (the schema
        is malformed), not a server-side failure.  HTTP 422 Unprocessable
        Entity is the correct status code.  The RFC 7807 response body gives
        operators a structured, machine-readable error description.

        Args:
            request: The incoming HTTP request (required by FastAPI signature).
            exc: The CycleDetectionError raised by the subsetting engine.

        Returns:
            JSONResponse with HTTP 422 and RFC 7807 Problem Details body.
        """
        return JSONResponse(
            status_code=422,
            content={
                "type": "about:blank",
                "title": "Cycle Detected in Schema Graph",
                "status": 422,
                "detail": str(exc),
            },
        )

    @app.exception_handler(BudgetExhaustionError)
    async def _budget_exhaustion_error_handler(
        request: Request, exc: BudgetExhaustionError
    ) -> JSONResponse:
        """Handle BudgetExhaustionError with operator-friendly RFC 7807 response.

        Returns "Privacy Budget Exceeded" with remediation instructions.
        The technical epsilon/delta details are logged but not exposed via HTTP.

        Args:
            request: The incoming HTTP request (required by FastAPI signature).
            exc: The BudgetExhaustionError raised by the privacy accountant.

        Returns:
            JSONResponse with HTTP 409 and operator-friendly RFC 7807 body.
        """
        return operator_error_response(exc)

    @app.exception_handler(OOMGuardrailError)
    async def _oom_guardrail_error_handler(
        request: Request, exc: OOMGuardrailError
    ) -> JSONResponse:
        """Handle OOMGuardrailError with operator-friendly RFC 7807 response.

        Returns "Memory Limit Exceeded" with a suggestion to reduce the dataset.
        The technical memory estimates are logged but not exposed via HTTP.

        Args:
            request: The incoming HTTP request (required by FastAPI signature).
            exc: The OOMGuardrailError raised by the memory guardrail.

        Returns:
            JSONResponse with HTTP 422 and operator-friendly RFC 7807 body.
        """
        return operator_error_response(exc)

    @app.exception_handler(VaultSealedError)
    async def _vault_sealed_error_handler(request: Request, exc: VaultSealedError) -> JSONResponse:
        """Handle VaultSealedError with operator-friendly RFC 7807 response.

        Returns "Vault Is Sealed" with an instruction to call POST /unseal.

        Args:
            request: The incoming HTTP request (required by FastAPI signature).
            exc: The VaultSealedError raised when vault is sealed.

        Returns:
            JSONResponse with HTTP 423 and operator-friendly RFC 7807 body.
        """
        return operator_error_response(exc)
