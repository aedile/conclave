"""Router registration and exception handler wiring for the Conclave Engine.

Named ``router_registry.py`` (not ``routers.py``) because ``routers/`` already
exists as a package directory in this package — a plain ``routers.py`` file
would shadow that package and break all ``from synth_engine.bootstrapper.routers.*``
imports.

Contains:
- ``_include_routers()`` — wires domain routers into the application under
  the ``/api/v1/`` versioned prefix (business routes) or at root (infra routes).
- ``_register_exception_handlers()`` — registers domain exception handlers with
  operator-friendly RFC 7807 messages via :data:`OPERATOR_ERROR_MAP`, and the
  RFC 7807 catch-all via :mod:`synth_engine.bootstrapper.errors`.

API versioning (T59.1)
----------------------
All business-logic routes are registered under ``/api/v1/`` via a parent
``APIRouter(prefix="/api/v1")``.  This prefix is applied at registration time,
not inside each router file, so individual routers keep their existing prefixes
(e.g. ``/jobs``) and the combined path becomes ``/api/v1/jobs``.

Infrastructure routes stay at root to keep middleware exempt-path matching
simple and backward-compatible:

- ``auth_router`` (``/auth/token``) — pre-auth bootstrapping; in AUTH_EXEMPT_PATHS.
- ``security_router`` (``/security/shred``, ``/security/keys/rotate``) —
  ``/security/shred`` is in SEAL_EXEMPT_PATHS; must remain at root.
- ``licensing_router`` (``/license/challenge``, ``/license/activate``) —
  in COMMON_INFRA_EXEMPT_PATHS; must remain at root.
- ``health_router`` (``/health``, ``/ready``, ``/health/vault``) —
  in COMMON_INFRA_EXEMPT_PATHS; must remain at root.

**Security invariant**: No ``/api/v1/`` path may appear in any exempt-paths
set (COMMON_INFRA_EXEMPT_PATHS, SEAL_EXEMPT_PATHS, AUTH_EXEMPT_PATHS).
This is enforced by ``tests/integration/test_api_versioning_attack.py``.

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
Task: T48.3 — Readiness Probe & External Dependency Health Checks
    Registered health_router for GET /ready.
    Registered webhooks_router for POST/GET/DELETE /webhooks.
Task: T59.1 — API Versioning
    All business-logic routes moved to /api/v1/ prefix via parent APIRouter.
    Infrastructure routes (auth, security, licensing, health) remain at root.
Task: P58 — Replace 9 identical exception handlers with data-driven loop.
    _OPERATOR_ERROR_HANDLERS derived from OPERATOR_ERROR_MAP.keys() so it
    never diverges from the map.  A single async handler function is registered
    for each type via loop.  Security contract preserved: PrivilegeEscalationError
    and ArtifactTamperingError still delegate to operator_error_response() which
    uses STATIC detail strings from OPERATOR_ERROR_MAP — never str(exc).
Task: P66 review — Fix handler registration gap.
    _OPERATOR_ERROR_HANDLERS is now derived from OPERATOR_ERROR_MAP.keys()
    instead of a hand-curated list, eliminating the divergence permanently.
    Previously, 6 exceptions (AuditWriteError, EpsilonMeasurementError,
    LedgerNotFoundError, DatasetTooLargeError, VaultEmptyPassphraseError,
    VaultConfigError) were in OPERATOR_ERROR_MAP but not in
    _OPERATOR_ERROR_HANDLERS, causing them to fall through to the 500 catch-all
    and potentially leak internal exception details in HTTP responses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from synth_engine.bootstrapper.errors.mapping import OPERATOR_ERROR_MAP
from synth_engine.shared.exceptions import SynthEngineError

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Data-driven exception handler registration (P58 / P66 review)
#
# Derived directly from OPERATOR_ERROR_MAP.keys() so that adding a new
# exception to the map automatically registers its HTTP handler.  This
# eliminates the historic divergence where the map and the handler list
# could silently drift apart.
#
# Security note: PrivilegeEscalationError, ArtifactTamperingError, and
# LedgerNotFoundError are intentionally included here.
# operator_error_response() looks up each exception in OPERATOR_ERROR_MAP
# and returns the STATIC detail string defined there — it never uses
# str(exc).  The raw exception message is written to the server-side
# WARNING log inside operator_error_response() and is never forwarded
# in the HTTP response body (ADV-036+044).
# ---------------------------------------------------------------------------
_OPERATOR_ERROR_HANDLERS: list[type[SynthEngineError]] = list(
    OPERATOR_ERROR_MAP.keys()  # type: ignore[arg-type]
)


def _include_routers(app: FastAPI) -> None:
    """Include all APIRouter submodules into the application.

    Business-logic routers are registered under a parent ``/api/v1`` versioned
    router.  Infrastructure routers (auth, security, licensing, health) are
    registered at root so that exempt-path matching in middleware is unaffected.

    Imported here (not at module top-level) so that create_app() controls
    registration order relative to exception handlers and middleware.

    Args:
        app: The FastAPI instance to attach routers to.
    """
    from synth_engine.bootstrapper.routers.admin import router as admin_router
    from synth_engine.bootstrapper.routers.admin_users import router as admin_users_router
    from synth_engine.bootstrapper.routers.auth import router as auth_router
    from synth_engine.bootstrapper.routers.compliance import router as compliance_router
    from synth_engine.bootstrapper.routers.connections import router as connections_router
    from synth_engine.bootstrapper.routers.health import router as health_router
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router
    from synth_engine.bootstrapper.routers.jobs_streaming import router as jobs_streaming_router
    from synth_engine.bootstrapper.routers.licensing import router as licensing_router
    from synth_engine.bootstrapper.routers.privacy import router as privacy_router
    from synth_engine.bootstrapper.routers.security import router as security_router
    from synth_engine.bootstrapper.routers.settings import router as settings_router
    from synth_engine.bootstrapper.routers.webhooks import router as webhooks_router

    # ------------------------------------------------------------------
    # Business-logic routes — versioned under /api/v1/
    # ------------------------------------------------------------------
    # The parent router applies the /api/v1 prefix to all included routers.
    # Each child router keeps its own prefix (e.g. /jobs) so the final path
    # is /api/v1/jobs, /api/v1/connections, etc.
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(jobs_router)
    api_v1.include_router(jobs_streaming_router)
    api_v1.include_router(connections_router)
    api_v1.include_router(settings_router)
    api_v1.include_router(webhooks_router)
    api_v1.include_router(privacy_router)
    api_v1.include_router(admin_router)
    api_v1.include_router(admin_users_router)
    api_v1.include_router(compliance_router)
    app.include_router(api_v1)

    # ------------------------------------------------------------------
    # Infrastructure routes — remain at root (no /api/v1/ prefix)
    # ------------------------------------------------------------------
    # These paths are in COMMON_INFRA_EXEMPT_PATHS, SEAL_EXEMPT_PATHS, or
    # AUTH_EXEMPT_PATHS.  Moving them to /api/v1/ would break all middleware
    # exempt-path matching.
    app.include_router(auth_router)  # /auth/token — AUTH_EXEMPT_PATHS
    app.include_router(security_router)  # /security/shred — SEAL_EXEMPT_PATHS
    app.include_router(licensing_router)  # /license/challenge, /license/activate — COMMON_INFRA
    app.include_router(health_router)  # /health, /ready, /health/vault — COMMON_INFRA


def _register_exception_handlers(app: FastAPI) -> None:
    """Register application-level exception handlers.

    Handlers convert known domain exceptions to structured HTTP responses
    before FastAPI's default 500 handler fires.  All handlers delegate to
    :func:`~synth_engine.bootstrapper.errors.operator_error_response` which
    looks up the exception class in :data:`OPERATOR_ERROR_MAP` and returns
    the correct RFC 7807 body with operator-friendly title and detail.

    Domain exception types are registered via a data-driven loop over
    :data:`_OPERATOR_ERROR_HANDLERS` — derived from
    :data:`~synth_engine.bootstrapper.errors.mapping.OPERATOR_ERROR_MAP`.keys()
    so that adding a new exception to the map automatically registers its
    handler without any manual list maintenance.

    ADV-022: CycleDetectionError -> HTTP 422 RFC 7807 Problem Details.
    T5.1: Generic Exception -> HTTP 500 RFC 7807 Problem Details (ADV-036+044).
    T6.2: RequestValidationError -> HTTP 422 with NaN/Infinity-safe serialization.
    T29.3: Domain exceptions -> operator-friendly RFC 7807 via OPERATOR_ERROR_MAP.
    T34.3: All SynthEngineError subclasses registered; no domain exception
        falls through to the catch-all 500 handler.
    P66 review: Handler list derived from OPERATOR_ERROR_MAP.keys() —
        adding an entry to the map now automatically registers its handler.

    Security: :exc:`PrivilegeEscalationError`, :exc:`ArtifactTamperingError`,
    and :exc:`LedgerNotFoundError` are registered with FIXED, STATIC detail
    strings in OPERATOR_ERROR_MAP.  The raw exception message is logged at
    WARNING level by
    :func:`~synth_engine.bootstrapper.errors.operator_error_response` but
    never forwarded verbatim in the HTTP response body (ADV-036+044).

    Args:
        app: The FastAPI instance to register handlers on.
    """
    # Generic catch-all RFC 7807 handler (T5.1) — must be registered BEFORE
    # domain-specific handlers so that specific handlers take precedence.
    from synth_engine.bootstrapper.errors import operator_error_response, register_error_handlers

    register_error_handlers(app)

    # Register all domain exception handlers via a data-driven loop.
    # Each handler is a trivial async wrapper that delegates to
    # operator_error_response(exc).  The default-argument capture
    # `_exc_type=exc_type` is required to avoid the classic Python
    # loop-closure variable capture bug.
    for exc_type in _OPERATOR_ERROR_HANDLERS:

        async def _domain_handler(
            request: Request,
            exc: SynthEngineError,
            _exc_type: type[SynthEngineError] = exc_type,
        ) -> JSONResponse:
            """Handle a domain exception with an operator-friendly RFC 7807 response.

            Delegates to operator_error_response() which looks up the exception
            class in OPERATOR_ERROR_MAP and returns the correct RFC 7807 body.
            Security-critical exceptions (PrivilegeEscalationError,
            ArtifactTamperingError, LedgerNotFoundError) use STATIC detail
            strings from the map — the raw exception message is never forwarded
            in the HTTP response.

            Args:
                request: The incoming HTTP request (required by FastAPI signature).
                exc: The domain exception raised by the engine.
                _exc_type: Captured loop variable — the specific exception type
                    this handler was registered for (unused at runtime; the
                    capture prevents the closure bug).

            Returns:
                JSONResponse with RFC 7807 body and appropriate HTTP status code.
            """
            return operator_error_response(exc)

        app.exception_handler(exc_type)(_domain_handler)
