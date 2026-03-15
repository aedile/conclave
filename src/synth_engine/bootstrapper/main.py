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

Task 3.5.4 additions:
  - CycleDetectionError exception handler: returns HTTP 422 RFC 7807
    Problem Details (ADV-022).

Task 4.2b additions (ADV-037 drain):
  - build_synthesis_engine(): lazy factory for SynthesisEngine.
  - build_ephemeral_storage_client(): lazy factory for EphemeralStorageClient
    backed by MinioStorageBackend.  Reads MinIO credentials from Docker
    secrets at /run/secrets/ (minio_ephemeral_access_key,
    minio_ephemeral_secret_key).

Task 4.2c additions (Rule 8 — Huey task wiring):
  - Import side-effect registers run_synthesis_job with the shared Huey
    instance so the Huey worker process discovers the task at startup.
    See: https://huey.readthedocs.io/en/latest/consumer.html#importing-tasks

Task 5.1 additions:
  - Jobs, Connections, Settings routers included via app.include_router().
  - RFC 7807 catch-all error handler registered via bootstrapper/errors.py.

Task 5.2 additions:
  - LicenseGateMiddleware: blocks non-exempt routes until the software is
    activated (402 Payment Required).
  - /license/challenge GET endpoint: returns hardware-bound challenge payload
    with QR code for offline activation.
  - /license/activate POST endpoint: accepts RS256 JWT, validates signature
    and hardware_id binding, transitions LicenseState to LICENSED.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import make_asgi_app
from pydantic import BaseModel

from synth_engine.bootstrapper.dependencies.licensing import LicenseGateMiddleware
from synth_engine.bootstrapper.dependencies.vault import SealGateMiddleware
from synth_engine.modules.mapping import CycleDetectionError
from synth_engine.shared.security.vault import VaultState
from synth_engine.shared.telemetry import configure_telemetry

if TYPE_CHECKING:
    from synth_engine.modules.synthesizer.engine import SynthesisEngine
    from synth_engine.modules.synthesizer.storage import EphemeralStorageClient

_SERVICE_NAME = "conclave-engine"
_logger = logging.getLogger(__name__)

#: Default MinIO endpoint for the ephemeral storage bucket.
_MINIO_ENDPOINT = "http://minio-ephemeral:9000"

#: Ephemeral bucket name — backed by tmpfs in Docker Compose.
_EPHEMERAL_BUCKET = "synth-ephemeral"

#: Docker secrets directory — credentials are mounted here at runtime.
_SECRETS_DIR = Path("/run/secrets")

# ---------------------------------------------------------------------------
# MinioStorageBackend — deferred module-level import so that environments
# without the synthesizer dependency group do not fail at import time.
# The name is bound at module scope so unit tests can patch it with:
#   patch('synth_engine.bootstrapper.main.MinioStorageBackend')
# ---------------------------------------------------------------------------
try:
    from synth_engine.modules.synthesizer.storage import MinioStorageBackend
except ImportError:  # pragma: no cover — synthesizer group not installed
    MinioStorageBackend = None  # type: ignore[assignment,misc]


def _read_secret(name: str) -> str:
    """Read a Docker secret from the /run/secrets/ directory.

    Secrets are mounted as files by Docker Compose (``secrets:`` block).
    The file content is stripped of leading and trailing whitespace/newlines.

    Args:
        name: Secret file name (e.g. ``"minio_ephemeral_access_key"``).

    Returns:
        The secret value as a stripped string.

    Raises:
        RuntimeError: If the secret file does not exist or cannot be read.
    """
    secret_path = _SECRETS_DIR / name
    try:
        return secret_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(
            f"Docker secret '{name}' not found at {secret_path}. "
            "Ensure the secret is mounted at /run/secrets/ by Docker Compose."
        ) from exc


def build_ephemeral_storage_client() -> EphemeralStorageClient:
    """Build an EphemeralStorageClient backed by MinioStorageBackend.

    Reads MinIO credentials from Docker secrets at runtime:
      - ``/run/secrets/minio_ephemeral_access_key``
      - ``/run/secrets/minio_ephemeral_secret_key``

    This factory is called lazily at synthesis job start time, not at
    application startup — this avoids failing the health check when the
    MinIO service is not yet running.

    Returns:
        A configured :class:`EphemeralStorageClient` instance ready to
        upload and download Parquet files.

    Raises:
        RuntimeError: If the Docker secrets are not mounted.
        ValueError: If the secrets are empty or the endpoint URL is invalid.
    """
    from synth_engine.modules.synthesizer.storage import EphemeralStorageClient

    access_key = _read_secret("minio_ephemeral_access_key")
    secret_key = _read_secret("minio_ephemeral_secret_key")

    # MinioStorageBackend is bound at module scope (patchable in tests).
    # Use the module-level name so unit tests can intercept the constructor.
    backend_cls: Any = MinioStorageBackend
    backend = backend_cls(
        endpoint_url=_MINIO_ENDPOINT,
        access_key=access_key,
        secret_key=secret_key,
    )
    _logger.info(
        "EphemeralStorageClient initialised (bucket=%s, endpoint=%s).",
        _EPHEMERAL_BUCKET,
        _MINIO_ENDPOINT,
    )
    return EphemeralStorageClient(bucket=_EPHEMERAL_BUCKET, backend=backend)


def build_synthesis_engine(epochs: int = 300) -> SynthesisEngine:
    """Build a SynthesisEngine with the given epoch count.

    This factory is called lazily at synthesis job start time, not at
    application startup.  Callers receive a stateless engine instance;
    model artifacts are returned from :meth:`SynthesisEngine.train` and
    must be persisted by the caller.

    Args:
        epochs: Number of CTGAN training epochs.  Defaults to 300 (SDV
            default).  Use a lower value (2-5) for integration-test runs.

    Returns:
        A configured :class:`SynthesisEngine` instance.
    """
    from synth_engine.modules.synthesizer.engine import SynthesisEngine as _SynthesisEngine

    _logger.info("SynthesisEngine initialised (epochs=%d).", epochs)
    return _SynthesisEngine(epochs=epochs)


# TODO(T4.3b): Add build_dp_wrapper() factory here.
# DPTrainingWrapper (modules/privacy/dp_engine.py) must be wired through the
# bootstrapper before it can be passed to SynthesisEngine.train(dp_wrapper=...).
# Concrete SDV/Opacus integration is deferred per ADR-0017 risk note —
# SDV's CTGANSynthesizer.fit() does not expose its optimizer/model/dataloader
# for Opacus wrapping.  When SDV adds training hooks, wire here.

# TODO(T4.4): Add build_privacy_accountant() factory or DI binding here.
# PrivacyLedger and spend_budget() (modules/privacy/accountant.py) must be
# wired through the bootstrapper to connect to the async database engine.
# The async engine URL should come from the same DATABASE_URL env var used
# by the sync engine, with the driver swapped to postgresql+asyncpg://.


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
    - SealGateMiddleware (blocks sealed-state access, 423 Locked)
    - LicenseGateMiddleware (blocks unlicensed access, 402 Payment Required)
    - Prometheus metrics at /metrics
    - CycleDetectionError exception handler (ADV-022)
    - RFC 7807 catch-all exception handler (T5.1)
    - Jobs, Connections, Settings routers (T5.1)
    - License challenge/activate router (T5.2)

    Then registers the /health liveness probe, /unseal ops endpoint,
    and mounts the Prometheus ASGI app.

    Middleware evaluation order (LIFO — last added = outermost):
    1. SealGateMiddleware — outermost; returns 423 if vault is sealed.
    2. LicenseGateMiddleware — inner; returns 402 if not licensed.

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

    # Middleware is evaluated in LIFO (Last In, First Out) order.
    # LicenseGateMiddleware is added FIRST so that SealGateMiddleware wraps it
    # (seal check fires before license check — correct priority ordering).
    app.add_middleware(LicenseGateMiddleware)
    app.add_middleware(SealGateMiddleware)

    # Mount Prometheus metrics endpoint (internal network only; no auth required
    # because /metrics is unreachable from outside the Docker bridge network).
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    _register_exception_handlers(app)
    _register_routes(app)
    _include_routers(app)

    return app


def _include_routers(app: FastAPI) -> None:
    """Include all APIRouter submodules into the application.

    Imported here (not at module top-level) so that create_app() controls
    registration order relative to exception handlers and middleware.

    Args:
        app: The FastAPI instance to attach routers to.
    """
    from synth_engine.bootstrapper.routers.connections import router as connections_router
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router
    from synth_engine.bootstrapper.routers.settings import router as settings_router
    from synth_engine.bootstrapper.routers.system import router as system_router

    app.include_router(jobs_router)
    app.include_router(connections_router)
    app.include_router(settings_router)
    app.include_router(system_router)


def _register_exception_handlers(app: FastAPI) -> None:
    """Register application-level exception handlers.

    Handlers convert known domain exceptions to structured HTTP responses
    before FastAPI's default 500 handler fires.

    ADV-022: CycleDetectionError -> HTTP 422 RFC 7807 Problem Details.
    T5.1: Generic Exception -> HTTP 500 RFC 7807 Problem Details (ADV-036+044).

    Args:
        app: The FastAPI instance to register handlers on.
    """
    # Generic catch-all RFC 7807 handler (T5.1) — must be registered BEFORE
    # domain-specific handlers so that specific handlers take precedence.
    from synth_engine.bootstrapper.errors import register_error_handlers

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


# ---------------------------------------------------------------------------
# Rule 8 — Huey task wiring (T4.2c)
# ---------------------------------------------------------------------------
# This import is a deliberate side effect: importing the tasks module
# registers ``run_synthesis_job`` with the shared Huey instance so that
# the Huey worker process discovers the task at process start.
# Do NOT remove this import — the worker will silently drop synthesis jobs
# if the task is not registered.
from synth_engine.modules.synthesizer import tasks as _synthesizer_tasks  # noqa: F401, E402

#: Module-level application instance for use by uvicorn.
#: ``uvicorn synth_engine.bootstrapper.main:app`` picks up this singleton.
app = create_app()
