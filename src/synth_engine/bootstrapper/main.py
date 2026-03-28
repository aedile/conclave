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

Middleware ordering assertion (T62.4)
--------------------------------------
:func:`_assert_middleware_ordering` verifies the LIFO middleware stack at app
creation time.  Any structural regression (e.g. a reordered ``add_middleware``
call in :mod:`.middleware`) raises ``RuntimeError`` immediately rather than
silently allowing the wrong order to serve production traffic.

The assertion is fail-closed: any ``AttributeError``, ``TypeError``, or
unexpected structure raises, never silently passes.
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

# ---------------------------------------------------------------------------
# Rule 8 — Huey task wiring (T4.2c) + DI factory injection (ADR-0029)
# All wiring logic has been extracted to bootstrapper/wiring.py (T56.2).
# wire_all() is called here at module scope so it fires for Huey workers
# that import main for task discovery without calling create_app().
# ---------------------------------------------------------------------------
wire_all()  # Module-scope: fires on import for Huey workers (see wiring.py docstring)


# ---------------------------------------------------------------------------
# T62.4 — Programmatic middleware ordering assertion
# ---------------------------------------------------------------------------

#: Expected LIFO ordering of domain middleware classes.
#: Index 0 = outermost on request path (added last to the stack).
#: Index 7 = innermost (added first to the stack).
#:
#: In Starlette's ``user_middleware`` list, items are prepended on each
#: ``add_middleware()`` call so the last-added middleware appears at index 0.
#: RFC7807Middleware added by ``_register_exception_handlers`` appears before
#: HTTPSEnforcementMiddleware in the final list (index 0) but is excluded
#: from this contract since it is registered separately.
_EXPECTED_MIDDLEWARE_ORDER: tuple[str, ...] = (
    "HTTPSEnforcementMiddleware",
    "RateLimitGateMiddleware",
    "RequestBodyLimitMiddleware",
    "CSPMiddleware",
    "SealGateMiddleware",
    "LicenseGateMiddleware",
    "AuthenticationGateMiddleware",
    "IdempotencyMiddleware",
)


def _assert_middleware_ordering(app: FastAPI) -> None:
    """Verify the middleware stack matches the expected LIFO order.

    Reads ``app.user_middleware`` (a Starlette internal list where index 0 =
    outermost / last-added) and confirms the eight domain middleware classes
    appear in ``_EXPECTED_MIDDLEWARE_ORDER``.

    This assertion is fail-closed: any unexpected structure (missing attribute,
    wrong type, missing middleware class) raises ``RuntimeError`` immediately.
    It is called at the end of ``create_app()`` so a misconfigured middleware
    stack never silently reaches production traffic.

    Args:
        app: The FastAPI application whose middleware stack is to be validated.

    Raises:
        RuntimeError: If ``app.user_middleware`` is missing, has unexpected
            structure, or does not contain all eight expected classes in the
            correct order.
        AttributeError: Re-raised if ``app.user_middleware`` attribute is
            absent — indicates a Starlette internals change.
    """
    user_middleware = getattr(app, "user_middleware", None)
    if user_middleware is None:
        raise AttributeError(
            "FastAPI app has no 'user_middleware' attribute. "
            "Starlette internals may have changed — review T62.4 implementation. "
            "This is a fail-closed startup assertion."
        )

    # Extract class names from user_middleware, skipping entries without a cls attribute.
    actual_names: list[str] = []
    for entry in user_middleware:
        cls = getattr(entry, "cls", None)
        if cls is not None:
            actual_names.append(cls.__name__)

    # Verify all expected middleware classes are present
    actual_set = set(actual_names)
    for expected_name in _EXPECTED_MIDDLEWARE_ORDER:
        if expected_name not in actual_set:
            raise RuntimeError(
                f"Middleware ordering assertion failed: expected {expected_name!r} "
                f"in middleware stack but it is missing. "
                f"Found: {actual_names}. "
                f"Check bootstrapper/middleware.py for a missing add_middleware() call."
            )

    # Verify positional order: extract indices of the expected classes
    # and confirm they are in the correct monotonically increasing order
    # (since user_middleware index 0 = outermost, and our expected list
    # is also ordered outermost-first).
    indices: list[int] = []
    for expected_name in _EXPECTED_MIDDLEWARE_ORDER:
        idx = next((i for i, name in enumerate(actual_names) if name == expected_name), None)
        if idx is None:
            raise RuntimeError(
                f"Middleware ordering assertion failed: {expected_name!r} not found "
                f"in user_middleware. Found: {actual_names}"
            )
        indices.append(idx)

    for i in range(len(indices) - 1):
        if indices[i] >= indices[i + 1]:
            raise RuntimeError(
                f"Middleware ordering assertion failed: "
                f"{_EXPECTED_MIDDLEWARE_ORDER[i]!r} (index {indices[i]}) must appear "
                f"BEFORE {_EXPECTED_MIDDLEWARE_ORDER[i + 1]!r} (index {indices[i + 1]}) "
                f"in user_middleware (lower index = outer = fires first on request path). "
                f"Actual order: {actual_names}. "
                f"This is a security-critical ordering — review bootstrapper/middleware.py."
            )

    _logger.debug(
        "Middleware ordering assertion passed: %s",
        " -> ".join(_EXPECTED_MIDDLEWARE_ORDER),
    )


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

    Security (T66.2): OpenAPI docs (/docs, /redoc, /openapi.json) are
    disabled in production mode to reduce API reconnaissance surface
    (ADV-P62-01).  In development mode all three endpoints are available.

    Returns:
        A configured FastAPI instance ready to serve requests.
    """
    from synth_engine.shared.settings import get_settings

    configure_telemetry(_SERVICE_NAME)

    # T66.2: Disable OpenAPI docs in production to prevent API reconnaissance.
    # In production: docs_url=None, redoc_url=None, openapi_url=None → 404.
    # In development: default URLs (/docs, /redoc, /openapi.json) are enabled.
    _is_prod = get_settings().is_production()
    _docs_url: str | None = None if _is_prod else "/docs"
    _redoc_url: str | None = None if _is_prod else "/redoc"
    _openapi_url: str | None = None if _is_prod else "/openapi.json"

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
        docs_url=_docs_url,
        redoc_url=_redoc_url,
        openapi_url=_openapi_url,
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

    # T62.4: Fail-closed startup assertion — raises immediately if ordering is wrong.
    # Called AFTER all middleware is registered so the full stack is available.
    _assert_middleware_ordering(app)

    return app


# Note: EpsilonAccountant (T4.4) is wired through the synthesis job pipeline
# (modules/synthesizer/tasks.py), not through bootstrapper DI. No bootstrapper
# wiring is required here.

#: Module-level singleton for ``uvicorn synth_engine.bootstrapper.main:app``.
app = create_app()
