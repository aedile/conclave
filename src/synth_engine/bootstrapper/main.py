"""FastAPI application factory for the Conclave Engine.

Sole entry point for the HTTP layer.  Assembles the application on demand via
:func:`create_app` — a factory pattern that keeps tests isolated and allows
future multi-tenant configurations.

Each concern is delegated to a focused submodule:

- :mod:`.factories` — Synthesis, DP, and ephemeral-storage factory functions.
- :mod:`.middleware` — Middleware stack setup.
- :mod:`.lifecycle` — Lifespan hooks and ops route registration.
- :mod:`.router_registry` — Domain router and exception handler wiring.
- :mod:`.wiring` — Explicit IoC registration functions (Rule 8).
- :mod:`.openapi_metadata` — Tags metadata and error response schemas (T59.3).

Docker-secrets cluster
----------------------
``_read_secret``, ``_SECRETS_DIR``, ``_MINIO_ENDPOINT``, and
``_EPHEMERAL_BUCKET`` now live in :mod:`.docker_secrets` and are
re-exported here so that existing code referencing
``synth_engine.bootstrapper.main._read_secret`` (including test patches
against ``main._SECRETS_DIR``) continues to resolve correctly.

Task: T60.3 — Move build_ephemeral_storage_client to factories.py
    ``build_ephemeral_storage_client`` now lives in :mod:`.factories`.
    It is re-exported here so that existing test patches against
    ``synth_engine.bootstrapper.main.build_ephemeral_storage_client``
    continue to resolve correctly.

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
from synth_engine.bootstrapper.factories import (  # noqa: F401 — re-exported for test patches
    build_dp_wrapper,
    build_ephemeral_storage_client,
    build_spend_budget_fn,
    build_synthesis_engine,
)
from synth_engine.bootstrapper.lifecycle import (
    UnsealRequest,  # noqa: F401 — re-exported for test patches
    _lifespan,
    _register_routes,
)
from synth_engine.bootstrapper.middleware import setup_middleware
from synth_engine.bootstrapper.openapi_metadata import TAGS_METADATA
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
    pass

_SERVICE_NAME = "conclave-engine"
_logger = logging.getLogger(__name__)

# Deferred import so environments without the synthesizer group don't fail.
# Bound at module scope for patch("synth_engine.bootstrapper.main.MinioStorageBackend").
try:
    from synth_engine.modules.synthesizer.storage.storage import MinioStorageBackend
except ImportError:  # pragma: no cover — synthesizer group not installed
    MinioStorageBackend = None  # type: ignore[assignment,misc]  # conditional import fallback: None when synthesizer group absent; type narrowed at call sites


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

    OpenAPI enrichment (T59.3):
    Tags metadata is injected via ``openapi_tags`` for grouped documentation
    in the /docs UI.  Route-level ``summary`` and ``responses`` with RFC 7807
    schemas are defined in each router module.

    Returns:
        A configured FastAPI instance ready to serve requests.
    """
    configure_telemetry(_SERVICE_NAME)

    app = FastAPI(
        title="Conclave Engine",
        description=(
            "**Air-Gapped Synthetic Data Generation Engine** — v1.0\n\n"
            "Transforms production databases into privacy-safe synthetic replicas "
            "inside your security perimeter, on your hardware, with zero network "
            "calls out.\n\n"
            "All business-logic endpoints are versioned under `/api/v1/`. "
            "Infrastructure endpoints (health, unseal, auth, license) remain at root.\n\n"
            "**Authentication**: All business endpoints require a JWT Bearer token. "
            "Obtain one via `POST /auth/token`.\n\n"
            "**Error format**: All error responses use "
            "[RFC 7807 Problem Details](https://www.rfc-editor.org/rfc/rfc7807)."
        ),
        version=synth_engine.__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_lifespan,
        openapi_tags=TAGS_METADATA,
        contact={
            "name": "Conclave Engine Operations",
            "url": "https://github.com/example/conclave-engine",
        },
        license_info={
            "name": "AGPL-3.0-or-later",
            "url": "https://www.gnu.org/licenses/agpl-3.0.html",
        },
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
