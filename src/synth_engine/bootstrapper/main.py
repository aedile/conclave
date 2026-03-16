"""FastAPI application factory for the Conclave Engine.

Sole entry point for the HTTP layer.  Assembles the application on demand via
:func:`create_app` — a factory pattern that keeps tests isolated and allows
future multi-tenant configurations.

Each concern is delegated to a focused submodule:

- :mod:`.factories` — Synthesis and DP factory functions.
- :mod:`.middleware` — Middleware stack setup.
- :mod:`.lifecycle` — Lifespan hooks and ops route registration.
- :mod:`.router_registry` — Domain router and exception handler wiring.

The Docker-secrets cluster (``_read_secret``, ``_SECRETS_DIR``,
``_MINIO_ENDPOINT``, ``_EPHEMERAL_BUCKET``, ``MinioStorageBackend``, and
``build_ephemeral_storage_client``) remains in this module so that existing
test patches against ``synth_engine.bootstrapper.main.*`` work unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import make_asgi_app

from synth_engine.bootstrapper.factories import (  # noqa: F401 — re-exported for test patches
    build_dp_wrapper,
    build_synthesis_engine,
)
from synth_engine.bootstrapper.lifecycle import (
    UnsealRequest,  # noqa: F401 — re-exported for test patches
    _lifespan,
    _register_routes,
)
from synth_engine.bootstrapper.middleware import setup_middleware
from synth_engine.bootstrapper.router_registry import (
    _include_routers,
    _register_exception_handlers,
)
from synth_engine.shared.telemetry import configure_telemetry

if TYPE_CHECKING:
    from synth_engine.modules.synthesizer.storage import EphemeralStorageClient

_SERVICE_NAME = "conclave-engine"
_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Docker secrets cluster — kept here so test patches resolve against this
# module's namespace (patch("synth_engine.bootstrapper.main._SECRETS_DIR")).
# ---------------------------------------------------------------------------

#: Default MinIO endpoint for the ephemeral storage bucket.
_MINIO_ENDPOINT = "http://minio-ephemeral:9000"

#: Ephemeral bucket name — backed by tmpfs in Docker Compose.
_EPHEMERAL_BUCKET = "synth-ephemeral"

#: Docker secrets directory — credentials mounted here at runtime.
_SECRETS_DIR = Path("/run/secrets")

# Deferred import so environments without the synthesizer group don't fail.
# Bound at module scope for patch("synth_engine.bootstrapper.main.MinioStorageBackend").
try:
    from synth_engine.modules.synthesizer.storage import MinioStorageBackend
except ImportError:  # pragma: no cover — synthesizer group not installed
    MinioStorageBackend = None  # type: ignore[assignment,misc]  # conditional import fallback: None when synthesizer group absent; type narrowed at call sites


def _read_secret(name: str) -> str:
    """Read a Docker secret from ``_SECRETS_DIR``.

    Args:
        name: Secret filename (e.g. ``"minio_ephemeral_access_key"``).

    Returns:
        Secret value stripped of leading/trailing whitespace.

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

    Reads MinIO credentials from Docker secrets at synthesis-job start time,
    not at application startup, so a missing MinIO service does not break
    the /health endpoint.

    Returns:
        A configured :class:`EphemeralStorageClient` ready to upload/download
        Parquet files.

    Raises:
        RuntimeError: If the Docker secrets are not mounted.
        ValueError: If the secrets are empty or the endpoint URL is invalid.
    """
    from synth_engine.modules.synthesizer.storage import EphemeralStorageClient

    access_key = _read_secret("minio_ephemeral_access_key")
    secret_key = _read_secret("minio_ephemeral_secret_key")

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


# ---------------------------------------------------------------------------
# Rule 8 — Huey task wiring (T4.2c)
# Registers run_synthesis_job and rotate_ale_keys_task with the shared Huey
# instance at worker startup.  Do NOT remove — silent task drops otherwise.
# ---------------------------------------------------------------------------
from synth_engine.modules.synthesizer import tasks as _synthesizer_tasks  # noqa: F401, E402
from synth_engine.shared.security import rotation as _security_rotation  # noqa: F401, E402


def create_app() -> FastAPI:
    """Build and return a fully wired FastAPI application.

    Assembles middleware (LIFO order, outermost-last), Prometheus metrics,
    exception handlers, lifecycle routes, and domain routers.

    Middleware evaluation order (LIFO — last added = outermost):
    1. RequestBodyLimitMiddleware — rejects > 1 MiB or depth > 100.
    2. CSPMiddleware — Content-Security-Policy on every response.
    3. SealGateMiddleware — 423 Locked while vault is sealed.
    4. LicenseGateMiddleware — 402 Payment Required if unlicensed.

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
        lifespan=_lifespan,
    )

    FastAPIInstrumentor.instrument_app(app)
    setup_middleware(app)

    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    _register_exception_handlers(app)
    _register_routes(app)
    _include_routers(app)

    return app


# Note: EpsilonAccountant (T4.4) is wired through the synthesis job pipeline
# (modules/synthesizer/tasks.py), not through bootstrapper DI. No bootstrapper
# wiring is required here.

#: Module-level singleton for ``uvicorn synth_engine.bootstrapper.main:app``.
app = create_app()
