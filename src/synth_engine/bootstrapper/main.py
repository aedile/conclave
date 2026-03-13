"""FastAPI application factory for the Conclave Engine.

This module is the sole entry point for the HTTP layer.  It assembles the
application on demand via create_app() — a factory pattern that ensures
each call produces an independent instance, keeping tests isolated and
allowing future multi-tenant configurations.

Task 2.4 additions:
  - SealGateMiddleware: blocks all non-exempt routes while the vault is
    sealed (423 Locked).
  - /unseal POST endpoint: accepts operator passphrase, derives the KEK,
    and transitions the vault to the UNSEALED state.
  - Prometheus metrics mounted at /metrics via prometheus_client.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import make_asgi_app
from pydantic import BaseModel

from synth_engine.bootstrapper.dependencies.vault import SealGateMiddleware
from synth_engine.shared.security.vault import VaultState
from synth_engine.shared.telemetry import configure_telemetry

_SERVICE_NAME = "conclave-engine"
_logger = logging.getLogger(__name__)


class UnsealRequest(BaseModel):
    """Request body for the /unseal endpoint.

    Attributes:
        passphrase: Operator-provided passphrase used to derive the KEK.
    """

    passphrase: str


def create_app() -> FastAPI:
    """Build and return a fully wired FastAPI application.

    Attaches:
    - OpenTelemetry instrumentation
    - SealGateMiddleware (blocks sealed-state access)
    - Prometheus metrics at /metrics

    Then registers the /health liveness probe, /unseal ops endpoint,
    and mounts the Prometheus ASGI app.

    Returns:
        A configured FastAPI instance ready to serve requests.
    """
    configure_telemetry(_SERVICE_NAME)

    app = FastAPI(
        title="Conclave Engine",
        description="Air-Gapped Synthetic Data Generation Engine",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    FastAPIInstrumentor.instrument_app(app)

    # Seal gate must be added before routes are registered so that every
    # request passes through it.  Middleware is evaluated in LIFO order by
    # Starlette, so the gate is the outermost layer.
    app.add_middleware(SealGateMiddleware)

    # Mount Prometheus metrics endpoint (internal network only; no auth required
    # because /metrics is unreachable from outside the Docker bridge network).
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    _register_routes(app)

    return app


def _register_routes(app: FastAPI) -> None:
    """Attach all core routes to the application.

    Args:
        app: The FastAPI instance to register routes on.
    """

    @app.get("/health", tags=["ops"])
    async def health_check() -> JSONResponse:
        """Liveness probe for container orchestrators and load balancers.

        Returns:
            JSON body ``{"status": "ok"}`` with HTTP 200.
        """
        return JSONResponse(content={"status": "ok"})

    @app.post("/unseal", tags=["ops"])
    async def unseal_vault(body: UnsealRequest) -> JSONResponse:
        """Unseal the vault by deriving the KEK from the operator passphrase.

        Reads ``VAULT_SEAL_SALT`` from the environment, runs PBKDF2-HMAC-
        SHA256 (600k iterations) in a thread pool to avoid blocking the event
        loop, stores the result in ephemeral memory, and logs an audit event.

        Args:
            body: JSON body containing the operator passphrase.

        Returns:
            ``{"status": "unsealed"}`` with HTTP 200 on success.
            ``{"detail": "<reason>"}`` with HTTP 400 on failure.
        """
        try:
            await asyncio.to_thread(VaultState.unseal, body.passphrase)
        except ValueError as exc:
            return JSONResponse(content={"detail": str(exc)}, status_code=400)

        # Emit audit event — best-effort; failure must not prevent unsealing
        try:
            from synth_engine.shared.security.audit import get_audit_logger

            audit = get_audit_logger()
            audit.log_event(
                event_type="VAULT_UNSEAL",
                actor="operator",
                resource="vault",
                action="unseal",
                details={},
            )
        except (ValueError, RuntimeError):
            # AUDIT_KEY not configured in this environment — log but continue
            _logger.warning("AUDIT_KEY not configured; vault unseal event was not audited.")

        return JSONResponse(content={"status": "unsealed"})


#: Module-level application instance for use by uvicorn.
#: ``uvicorn synth_engine.bootstrapper.main:app`` picks up this singleton.
app = create_app()
