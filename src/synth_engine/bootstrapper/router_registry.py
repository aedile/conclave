"""Router registration and exception handler wiring for the Conclave Engine.

Named ``router_registry.py`` (not ``routers.py``) because ``routers/`` already
exists as a package directory in this package — a plain ``routers.py`` file
would shadow that package and break all ``from synth_engine.bootstrapper.routers.*``
imports.

Contains:
- ``_include_routers()`` — wires the ten domain routers into the application.
- ``_register_exception_handlers()`` — registers domain exception handlers with
  operator-friendly RFC 7807 messages via :data:`OPERATOR_ERROR_MAP`, and the
  RFC 7807 catch-all via :mod:`synth_engine.bootstrapper.errors`.

Task: P29-T29.3 — Error Message Audience Differentiation
    Added handlers for BudgetExhaustionError, OOMGuardrailError, VaultSealedError
    using OPERATOR_ERROR_MAP for consistent operator-friendly RFC 7807 responses.
Task: T34.3 — Complete OPERATOR_ERROR_MAP for All Domain Exceptions
    Added handlers for VaultAlreadyUnsealedError, LicenseError, CollisionError,
    CycleDetectionError, PrivilegeEscalationError, ArtifactTamperingError.
    Migrated CycleDetectionError from bespoke inline handler to operator_error_response().
Task: P36 review — Import CycleDetectionError and CollisionError from shared.exceptions (ADR-0037)
Task: T39.1 — Add Authentication Middleware (JWT Bearer Token)
    Registered auth_router for POST /auth/token.
Task: T41.1 — Implement Data Retention Policy
    Registered admin_router for PATCH /admin/jobs/{id}/legal-hold.
Task: T41.2 — Implement GDPR Right-to-Erasure & CCPA Deletion Endpoint
    Registered compliance_router for DELETE /compliance/erasure.
Task: T45.3 — Implement Webhook Callbacks for Task Completion
    Registered webhooks_router for POST/GET/DELETE /webhooks.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from synth_engine.shared.exceptions import (
    ArtifactTamperingError,
    BudgetExhaustionError,
    CollisionError,
    CycleDetectionError,
    LicenseError,
    OOMGuardrailError,
    PrivilegeEscalationError,
    VaultAlreadyUnsealedError,
    VaultSealedError,
)


def _include_routers(app: FastAPI) -> None:
    """Include all APIRouter submodules into the application.

    Imported here (not at module top-level) so that create_app() controls
    registration order relative to exception handlers and middleware.

    Args:
        app: The FastAPI instance to attach routers to.
    """
    from synth_engine.bootstrapper.routers.admin import router as admin_router
    from synth_engine.bootstrapper.routers.auth import router as auth_router
    from synth_engine.bootstrapper.routers.compliance import router as compliance_router
    from synth_engine.bootstrapper.routers.connections import router as connections_router
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router
    from synth_engine.bootstrapper.routers.jobs_streaming import router as jobs_streaming_router
    from synth_engine.bootstrapper.routers.licensing import router as licensing_router
    from synth_engine.bootstrapper.routers.privacy import router as privacy_router
    from synth_engine.bootstrapper.routers.security import router as security_router
    from synth_engine.bootstrapper.routers.settings import router as settings_router
    from synth_engine.bootstrapper.routers.webhooks import router as webhooks_router

    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(compliance_router)
    app.include_router(jobs_router)
    app.include_router(jobs_streaming_router)
    app.include_router(connections_router)
    app.include_router(settings_router)
    app.include_router(licensing_router)
    app.include_router(security_router)
    app.include_router(privacy_router)
    app.include_router(webhooks_router)


def _register_exception_handlers(app: FastAPI) -> None:
    """Register application-level exception handlers.

    Handlers convert known domain exceptions to structured HTTP responses
    before FastAPI's default 500 handler fires.  All handlers delegate to
    :func:`~synth_engine.bootstrapper.errors.operator_error_response` which
    looks up the exception class in :data:`OPERATOR_ERROR_MAP` and returns
    the correct RFC 7807 body with operator-friendly title and detail.

    ADV-022: CycleDetectionError -> HTTP 422 RFC 7807 Problem Details.
    T5.1: Generic Exception -> HTTP 500 RFC 7807 Problem Details (ADV-036+044).
    T6.2: RequestValidationError -> HTTP 422 with NaN/Infinity-safe serialization.
    T29.3: Domain exceptions -> operator-friendly RFC 7807 via OPERATOR_ERROR_MAP.
    T34.3: All 11 SynthEngineError subclasses registered; no domain exception
        falls through to the catch-all 500 handler.

    Security: :exc:`PrivilegeEscalationError` and :exc:`ArtifactTamperingError`
    are registered with FIXED, STATIC detail strings in OPERATOR_ERROR_MAP.
    The raw exception message is logged at WARNING level by
    :func:`~synth_engine.bootstrapper.errors.operator_error_response` but
    never forwarded verbatim in the HTTP response body (ADV-036+044).

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
        """Handle CycleDetectionError with operator-friendly RFC 7807 422 response.

        A cycle in the schema FK graph is a client-side data error (the schema
        is malformed).  Delegates to operator_error_response() for consistency
        with all other domain exception handlers.

        Args:
            request: The incoming HTTP request (required by FastAPI signature).
            exc: The CycleDetectionError raised by the subsetting engine.

        Returns:
            JSONResponse with HTTP 422 and operator-friendly RFC 7807 body.
        """
        return operator_error_response(exc)

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

    @app.exception_handler(VaultAlreadyUnsealedError)
    async def _vault_already_unsealed_error_handler(
        request: Request, exc: VaultAlreadyUnsealedError
    ) -> JSONResponse:
        """Handle VaultAlreadyUnsealedError with operator-friendly RFC 7807 response.

        Returns "Vault Already Unsealed" with an informational message.
        No remediation is needed — the vault is operational.

        Args:
            request: The incoming HTTP request (required by FastAPI signature).
            exc: The VaultAlreadyUnsealedError raised when unseal is attempted twice.

        Returns:
            JSONResponse with HTTP 409 and operator-friendly RFC 7807 body.
        """
        return operator_error_response(exc)

    @app.exception_handler(LicenseError)
    async def _license_error_handler(request: Request, exc: LicenseError) -> JSONResponse:
        """Handle LicenseError with operator-friendly RFC 7807 response.

        Returns "License Validation Failed" with remediation instructions.
        The internal license token details are logged but not exposed via HTTP.

        Args:
            request: The incoming HTTP request (required by FastAPI signature).
            exc: The LicenseError raised by the license validator.

        Returns:
            JSONResponse with HTTP 403 and operator-friendly RFC 7807 body.
        """
        return operator_error_response(exc)

    @app.exception_handler(CollisionError)
    async def _collision_error_handler(request: Request, exc: CollisionError) -> JSONResponse:
        """Handle CollisionError with operator-friendly RFC 7807 response.

        Returns "Masking Collision Detected" with remediation instructions.

        Args:
            request: The incoming HTTP request (required by FastAPI signature).
            exc: The CollisionError raised by the masking registry.

        Returns:
            JSONResponse with HTTP 409 and operator-friendly RFC 7807 body.
        """
        return operator_error_response(exc)

    @app.exception_handler(PrivilegeEscalationError)
    async def _privilege_escalation_error_handler(
        request: Request, exc: PrivilegeEscalationError
    ) -> JSONResponse:
        """Handle PrivilegeEscalationError with a sanitized RFC 7807 403 response.

        Security: the exception message may contain database role names or
        privilege details.  The HTTP response uses the FIXED, STATIC detail
        string from OPERATOR_ERROR_MAP — never str(exc).  The raw message is
        logged at WARNING level by operator_error_response().

        Args:
            request: The incoming HTTP request (required by FastAPI signature).
            exc: The PrivilegeEscalationError raised by the ingestion adapter.

        Returns:
            JSONResponse with HTTP 403 and sanitized operator-friendly RFC 7807 body.
        """
        return operator_error_response(exc)

    @app.exception_handler(ArtifactTamperingError)
    async def _artifact_tampering_error_handler(
        request: Request, exc: ArtifactTamperingError
    ) -> JSONResponse:
        """Handle ArtifactTamperingError with a sanitized RFC 7807 422 response.

        Security: the exception message may contain artifact paths or HMAC
        signing-key hints.  The HTTP response uses the FIXED, STATIC detail
        string from OPERATOR_ERROR_MAP — never str(exc).  The raw message is
        logged at WARNING level by operator_error_response().

        Args:
            request: The incoming HTTP request (required by FastAPI signature).
            exc: The ArtifactTamperingError raised by the HMAC signing module.

        Returns:
            JSONResponse with HTTP 422 and sanitized operator-friendly RFC 7807 body.
        """
        return operator_error_response(exc)
