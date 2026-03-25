"""FastAPI application factory for the Conclave Engine.

Sole entry point for the HTTP layer.  Assembles the application on demand via
:func:`create_app` — a factory pattern that keeps tests isolated and allows
future multi-tenant configurations.

Each concern is delegated to a focused submodule:

- :mod:`.factories` — Synthesis and DP factory functions.
- :mod:`.middleware` — Middleware stack setup.
- :mod:`.lifecycle` — Lifespan hooks and ops route registration.
- :mod:`.router_registry` — Domain router and exception handler wiring.
- :mod:`.wiring` — Explicit IoC registration functions (Rule 8).

Docker-secrets cluster
----------------------
``_read_secret``, ``_SECRETS_DIR``, ``_MINIO_ENDPOINT``, and
``_EPHEMERAL_BUCKET`` now live in :mod:`.docker_secrets` and are
re-exported here so that existing code referencing
``synth_engine.bootstrapper.main._read_secret`` (including test patches
against ``main._SECRETS_DIR``) continues to resolve correctly.

IoC wiring (Rule 8 — T45.3, P45 review F3)
--------------------------------------------
All IoC registration is delegated to :mod:`.wiring`.  :func:`wire_all` is
called at module scope (not inside ``create_app()``) so the wiring fires
regardless of whether ``create_app()`` is called — e.g. in Huey worker
processes that import ``main`` for task discovery only.

See :mod:`synth_engine.bootstrapper.wiring` for the full constraint
documentation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import make_asgi_app

import synth_engine
from synth_engine.bootstrapper.docker_secrets import (  # noqa: F401 — re-exported for test patches
    _SECRETS_DIR,
    _read_secret,
)
from synth_engine.bootstrapper.docker_secrets import (
    EPHEMERAL_BUCKET as _EPHEMERAL_BUCKET,
)
from synth_engine.bootstrapper.docker_secrets import (
    MINIO_ENDPOINT as _MINIO_ENDPOINT,
)
from synth_engine.bootstrapper.factories import (  # noqa: F401 — re-exported for test patches
    build_dp_wrapper,
    build_spend_budget_fn,
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
from synth_engine.bootstrapper.wiring import (  # noqa: F401 — re-exported for test patches
    _build_webhook_delivery_fn,
    wire_all,
)
from synth_engine.shared.telemetry import configure_telemetry

if TYPE_CHECKING:
    from synth_engine.modules.synthesizer.storage import EphemeralStorageClient

_SERVICE_NAME = "conclave-engine"
_logger = logging.getLogger(__name__)

# Deferred import so environments without the synthesizer group don't fail.
# Bound at module scope for patch("synth_engine.bootstrapper.main.MinioStorageBackend").
try:
    from synth_engine.modules.synthesizer.storage import MinioStorageBackend
except ImportError:  # pragma: no cover — synthesizer group not installed
    MinioStorageBackend = None  # type: ignore[assignment,misc]  # conditional import fallback: None when synthesizer group absent; type narrowed at call sites


def build_ephemeral_storage_client() -> EphemeralStorageClient:
    """Build an EphemeralStorageClient backed by MinioStorageBackend.

    Reads MinIO credentials from Docker secrets at synthesis-job start time,
    not at application startup, so a missing MinIO service does not break
    the /health endpoint.

    Returns:
        A configured :class:`EphemeralStorageClient` ready to upload/download
        Parquet files.
    """
    from synth_engine.modules.synthesizer.storage import EphemeralStorageClient

    access_key = _read_secret("minio_ephemeral_access_key")
    secret_key = _read_secret("minio_ephemeral_secret_key")

    assert MinioStorageBackend is not None, (  # pragma: no cover
        "MinioStorageBackend unavailable — install the synthesizer dependency group"
    )
    backend = MinioStorageBackend(
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
# Rule 8 — Huey task wiring (T4.2c) + DI factory injection (ADR-0029)
# All wiring logic has been extracted to bootstrapper/wiring.py (T56.2).
# wire_all() is called here at module scope so it fires for Huey workers
# that import main for task discovery without calling create_app().
# ---------------------------------------------------------------------------
wire_all()  # Module-scope: fires on import for Huey workers (see wiring.py docstring)


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
        version=synth_engine.__version__,
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
