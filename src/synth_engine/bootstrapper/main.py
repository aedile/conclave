"""FastAPI application factory for the Conclave Engine.

Sole entry point for the HTTP layer.  Assembles the application on demand via
:func:`create_app` — a factory pattern that keeps tests isolated and allows
future multi-tenant configurations.

Each concern is delegated to a focused submodule:

- :mod:`.factories` — Synthesis and DP factory functions.
- :mod:`.middleware` — Middleware stack setup.
- :mod:`.lifecycle` — Lifespan hooks and ops route registration.
- :mod:`.router_registry` — Domain router and exception handler wiring.

Docker-secrets cluster
----------------------
``_read_secret``, ``_SECRETS_DIR``, ``_MINIO_ENDPOINT``, and
``_EPHEMERAL_BUCKET`` now live in :mod:`.docker_secrets` and are
re-exported here so that existing code referencing
``synth_engine.bootstrapper.main._read_secret`` (including test patches
against ``main._SECRETS_DIR``) continues to resolve correctly.

Webhook IoC wiring (Rule 8 — T45.3, P45 review F3)
---------------------------------------------------
``_build_webhook_delivery_fn`` constructs the concrete delivery callback that
:func:`~synth_engine.modules.synthesizer.job_orchestration.set_webhook_delivery_fn`
expects.  The callback is registered at module load time (after the imports
block), not inside ``create_app()``, so the wiring fires regardless of whether
``create_app()`` is called (e.g. in Huey worker processes).

The callback:
1. Opens a synchronous DB session.
2. Looks up the ``SynthesisJob`` to retrieve ``owner_id``.
3. Queries active ``WebhookRegistration`` rows for that owner.
4. Calls :func:`~synth_engine.modules.synthesizer.webhook_delivery.deliver_webhook`
   for each active registration.
5. Saves the :class:`~synth_engine.bootstrapper.schemas.webhooks.WebhookDelivery`
   delivery log rows.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import make_asgi_app
from sqlmodel import Session, select

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
from synth_engine.bootstrapper.schemas.webhooks import (
    WebhookDelivery,
    WebhookRegistration,
)
from synth_engine.modules.synthesizer.job_models import SynthesisJob
from synth_engine.modules.synthesizer.webhook_delivery import deliver_webhook
from synth_engine.shared.db import get_engine
from synth_engine.shared.settings import get_settings
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
# Webhook delivery IoC callback — Rule 8 / T45.3 / P45 review F3
# ---------------------------------------------------------------------------


def _build_webhook_delivery_fn() -> Any:
    """Build the concrete webhook delivery callback for IoC injection.

    Returns a closure that, when called with ``(job_id, status)``:
    1. Opens a synchronous DB session using the settings ``database_url``.
    2. Looks up the ``SynthesisJob`` to resolve the ``owner_id``.
    3. Queries all active ``WebhookRegistration`` rows for that owner.
    4. Filters registrations by subscribed event type.
    5. Calls :func:`deliver_webhook` for each qualifying registration.
    6. Persists a :class:`WebhookDelivery` audit row for each attempt.

    Errors are caught and logged so webhook delivery never affects the job
    lifecycle outcome.

    Returns:
        A callable ``(job_id: int, status: str) -> None``.
    """
    settings = get_settings()
    database_url = settings.database_url
    timeout_seconds = settings.webhook_delivery_timeout_seconds

    def _deliver(job_id: int, status: str) -> None:
        """Deliver webhook events for ``job_id`` terminal status.

        Args:
            job_id: Integer PK of the synthesis job.
            status: Terminal status string (``"COMPLETE"`` or ``"FAILED"``).
        """
        if not database_url:
            _logger.warning(
                "Webhook delivery skipped for job %d: DATABASE_URL not configured.", job_id
            )
            return

        event_type = "job.completed" if status == "COMPLETE" else "job.failed"

        try:
            engine = get_engine(database_url)
            with Session(engine) as session:
                job = session.get(SynthesisJob, job_id)
                if job is None:
                    _logger.warning(
                        "Webhook delivery: SynthesisJob %d not found — skipping.", job_id
                    )
                    return

                owner_id = job.owner_id

                # Query active registrations for this owner
                stmt = select(WebhookRegistration).where(
                    WebhookRegistration.owner_id == owner_id,
                    WebhookRegistration.active.is_(True),  # type: ignore[attr-defined]
                )
                registrations = session.exec(stmt).all()

                payload: dict[str, Any] = {"job_id": str(job_id), "status": status}

                for reg in registrations:
                    # Check if this registration subscribes to this event type
                    subscribed_events: list[str] = (
                        json.loads(reg.events) if isinstance(reg.events, str) else reg.events
                    )
                    if event_type not in subscribed_events:
                        continue

                    result = deliver_webhook(
                        registration=reg,
                        job_id=job_id,
                        event_type=event_type,
                        payload=payload,
                        timeout_seconds=timeout_seconds,
                    )

                    # Persist delivery audit row
                    delivery = WebhookDelivery(
                        registration_id=reg.id,
                        job_id=job_id,
                        event_type=event_type,
                        delivery_id=result.delivery_id,
                        attempt_number=result.attempt_number,
                        status=result.status,
                        response_code=result.response_code,
                        error_message=result.error_message,
                    )
                    session.add(delivery)

                session.commit()

        except Exception:
            _logger.exception(
                "Webhook delivery failed unexpectedly for job %d (%s).", job_id, status
            )

    return _deliver


# ---------------------------------------------------------------------------
# Rule 8 — Huey task wiring (T4.2c) + DI factory injection (ADR-0029)
# Registers run_synthesis_job, rotate_ale_keys_task, periodic_cleanup_expired_jobs,
# periodic_cleanup_expired_artifacts, and periodic_reap_orphan_tasks with the
# shared Huey instance at worker startup.  Do NOT remove — silent task drops
# otherwise.
# set_dp_wrapper_factory injects build_dp_wrapper so tasks.py never imports
# from bootstrapper directly (correct DI direction: bootstrapper → modules).
# set_spend_budget_fn injects the async→sync spend_budget wrapper (T22.3).
# retention_tasks import (ADR-D3) registers the nightly retention periodic tasks.
# reaper_tasks import (T45.2) registers the 15-minute orphan task reaper.
# set_webhook_delivery_fn wires the concrete delivery callback (T45.3, Rule 8).
# ---------------------------------------------------------------------------
from synth_engine.modules.synthesizer import reaper_tasks as _reaper_tasks  # noqa: F401, E402
from synth_engine.modules.synthesizer import retention_tasks as _retention_tasks  # noqa: F401, E402
from synth_engine.modules.synthesizer import tasks as _synthesizer_tasks  # noqa: E402
from synth_engine.modules.synthesizer.job_orchestration import (  # noqa: E402
    set_webhook_delivery_fn as _set_webhook_delivery_fn,
)
from synth_engine.shared.security import rotation as _security_rotation  # noqa: F401, E402

_synthesizer_tasks.set_dp_wrapper_factory(build_dp_wrapper)  # DI: ADR-0029
_synthesizer_tasks.set_spend_budget_fn(build_spend_budget_fn())  # DI: T22.3
_set_webhook_delivery_fn(_build_webhook_delivery_fn())  # DI: T45.3 Rule 8


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
