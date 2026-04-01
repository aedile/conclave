"""Explicit IoC registration functions for the Conclave Engine bootstrapper.

This module centralises all module-scope side-effect wiring that must fire
whenever ``synth_engine.bootstrapper.main`` is imported — including in Huey
worker processes, which import ``main`` for task discovery but never call
:func:`~synth_engine.bootstrapper.main.create_app`.

Huey worker constraint
----------------------
:func:`wire_all` is called at **module scope** in ``main.py``, not inside
``create_app()``.  This is intentional: Huey workers import ``main`` to
register the Huey tasks (run_synthesis_job, retention tasks, reaper tasks)
and they need the IoC callbacks — DP wrapper factory, spend-budget function,
webhook delivery function — to be live before any task executes.  Moving
these calls inside ``create_app()`` would break Huey workers because they
never call ``create_app()``.

Side-effect task-registration imports
--------------------------------------
Importing this module registers several Huey tasks as a side effect:

- :mod:`synth_engine.modules.synthesizer.storage.reaper_tasks` — orphan task reaper
- :mod:`synth_engine.modules.synthesizer.storage.retention_tasks` — nightly retention
- :mod:`synth_engine.shared.security.rotation` — ALE key rotation

These must be imported before the Huey worker starts polling or tasks will be
silently dropped.

Rule 8 compliance
-----------------
Each ``wire_*`` function corresponds to one Rule 8 IoC hook (from
:mod:`synth_engine.modules.synthesizer.jobs.job_orchestration`):

- :func:`wire_dp_wrapper_factory` — injects the DP wrapper factory (ADR-0029)
- :func:`wire_spend_budget_fn` — injects the async→sync budget wrapper (T22.3)
- :func:`wire_webhook_delivery_fn` — injects the webhook delivery callback
  (T45.3 / P45 review F3)

Idempotency
-----------
Each registration function may be called multiple times without ill effect.
The underlying ``set_*`` functions in :mod:`job_orchestration` overwrite the
module-level global unconditionally, so repeated calls simply re-register the
same factory — a safe no-op in practice.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
from prometheus_client import Counter
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from synth_engine.bootstrapper.dependencies.redis import get_redis_client as _get_redis_client
from synth_engine.bootstrapper.factories import build_dp_wrapper, build_spend_budget_fn
from synth_engine.bootstrapper.schemas.webhooks import WebhookDelivery, WebhookRegistration
from synth_engine.modules.synthesizer.jobs import (
    tasks as _synthesizer_tasks,  # noqa: F401 — side-effect: registers run_synthesis_job Huey task
)
from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob
from synth_engine.modules.synthesizer.jobs.job_orchestration import (
    set_dp_wrapper_factory as _set_dp_wrapper_factory,
)
from synth_engine.modules.synthesizer.jobs.job_orchestration import (
    set_spend_budget_fn as _set_spend_budget_fn,
)
from synth_engine.modules.synthesizer.jobs.job_orchestration import (
    set_webhook_delivery_fn,
)
from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook
from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
    set_circuit_breaker_redis_client as _set_circuit_breaker_redis_client,
)
from synth_engine.modules.synthesizer.storage import (
    reaper_tasks as _reaper_tasks,  # noqa: F401 — side-effect: registers Huey task
)
from synth_engine.modules.synthesizer.storage import (
    retention_tasks as _retention_tasks,  # noqa: F401 — side-effect: registers Huey task
)
from synth_engine.shared.db import get_engine
from synth_engine.shared.errors import safe_error_msg
from synth_engine.shared.security import (
    rotation as _security_rotation,  # noqa: F401 — side-effect: registers rotation task
)
from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ADV-P58-02 — Prometheus counter for unexpected webhook delivery errors.
# Incremented when an unexpected exception (not SQLAlchemy/network/OS) reaches
# the broad catch in _deliver().  CRITICAL-level log + this counter together
# make the failure visible in both operator logs and Prometheus dashboards.
# ---------------------------------------------------------------------------
UNEXPECTED_WEBHOOK_ERRORS_TOTAL: Counter = Counter(
    "unexpected_webhook_errors_total",
    "Unexpected exceptions in webhook delivery callback",
)


def _dispatch_to_registrations(
    session: Session,
    job_id: int,
    event_type: str,
    payload: dict[str, Any],
    timeout_seconds: int,
    owner_id: str,
) -> None:
    """Deliver and persist audit rows for all qualifying registrations.

    Args:
        session: Open SQLModel Session.
        job_id: Synthesis job integer PK.
        event_type: Event string (e.g. ``"job.completed"``).
        payload: Dict payload to deliver.
        timeout_seconds: HTTP timeout per attempt.
        owner_id: The job owner — used to filter registrations.
    """
    stmt = select(WebhookRegistration).where(
        WebhookRegistration.owner_id == owner_id,
        WebhookRegistration.active.is_(True),  # type: ignore[attr-defined]
    )
    registrations = session.exec(stmt).all()
    for reg in registrations:
        subscribed: list[str] = (
            json.loads(reg.events) if isinstance(reg.events, str) else reg.events
        )
        if event_type not in subscribed:
            continue
        result = deliver_webhook(
            registration=reg,
            job_id=job_id,
            event_type=event_type,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        _raw_err = result.error_message
        _safe_err: str | None = safe_error_msg(_raw_err or "")[:500] if _raw_err else None
        session.add(
            WebhookDelivery(
                registration_id=reg.id,
                job_id=job_id,
                event_type=event_type,
                delivery_id=result.delivery_id,
                attempt_number=result.attempt_number,
                status=result.status,
                response_code=result.response_code,
                error_message=_safe_err,
            )
        )
    session.commit()


def _build_webhook_delivery_fn() -> Callable[[int, str], None]:
    """Build the concrete webhook delivery callback for IoC injection.

    Returns a closure that, when called with ``(job_id, status)``:
    opens a DB session, resolves the job owner, dispatches to qualifying
    webhook registrations, and persists audit rows.  Errors are caught and
    logged so delivery never affects the job lifecycle outcome.

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
                payload: dict[str, Any] = {"job_id": str(job_id), "status": status}
                _dispatch_to_registrations(
                    session, job_id, event_type, payload, timeout_seconds, job.owner_id
                )
        except (SQLAlchemyError, ConnectionError, OSError, httpx.HTTPError) as exc:
            _logger.exception(
                "Webhook delivery failed for job %d (%s): %s", job_id, status, type(exc).__name__
            )
        except Exception as exc:  # broad catch intentional: protect job lifecycle
            UNEXPECTED_WEBHOOK_ERRORS_TOTAL.inc()
            _logger.critical(
                "Unexpected error in webhook delivery (job_id=%d): %s", job_id, type(exc).__name__
            )

    return _deliver


def wire_dp_wrapper_factory() -> None:
    """Register the DP wrapper factory with the synthesizer task module.

    Injects :func:`~synth_engine.bootstrapper.factories.build_dp_wrapper`
    as the factory that constructs
    :class:`~synth_engine.modules.privacy.dp_engine.DPTrainingWrapper`
    instances at synthesis-job start time.

    Calling this function multiple times is safe — each call overwrites the
    previous registration with an identical factory (idempotent).
    """
    _set_dp_wrapper_factory(build_dp_wrapper)
    _logger.debug("IoC: dp_wrapper_factory wired (ADR-0029).")


def wire_spend_budget_fn() -> None:
    """Register the sync spend-budget callable with the synthesizer task module.

    Injects the sync wrapper returned by
    :func:`~synth_engine.bootstrapper.factories.build_spend_budget_fn` so that
    Huey worker tasks can call it without entering an async event loop (T22.3).

    Calling this function multiple times is safe — each call overwrites the
    previous registration with a new equivalent wrapper (idempotent in effect).
    """
    _set_spend_budget_fn(build_spend_budget_fn())
    _logger.debug("IoC: spend_budget_fn wired (T22.3).")


def wire_webhook_delivery_fn() -> None:
    """Register the webhook delivery callback with the job orchestration module.

    Injects the concrete delivery callback built by
    :func:`_build_webhook_delivery_fn` so that
    :func:`~synth_engine.modules.synthesizer.jobs.job_orchestration` can trigger
    webhook delivery without importing from ``bootstrapper/`` (correct DI
    direction: bootstrapper → modules).

    Calling this function multiple times is safe — each call overwrites the
    previous registration (T45.3 / Rule 8 / P45 review F3).
    """
    set_webhook_delivery_fn(_build_webhook_delivery_fn())
    _logger.debug("IoC: webhook_delivery_fn wired (T45.3, Rule 8).")


def wire_circuit_breaker_redis_client() -> None:
    """Inject the shared Redis client into the webhook circuit breaker (T75.1).

    Registers the shared Redis client singleton with
    :func:`~synth_engine.modules.synthesizer.jobs.webhook_delivery.set_circuit_breaker_redis_client`
    so the Redis-backed circuit breaker can share state across workers.

    Called before :func:`wire_webhook_delivery_fn` so the circuit breaker is
    ready before any task tries to deliver webhooks.
    """
    _set_circuit_breaker_redis_client(_get_redis_client())
    _logger.debug("IoC: circuit_breaker_redis_client wired (T75.1).")


def wire_all() -> None:
    """Register all IoC callbacks required by the Conclave Engine at startup.

    Calls :func:`wire_dp_wrapper_factory`, :func:`wire_spend_budget_fn`,
    :func:`wire_circuit_breaker_redis_client`, and
    :func:`wire_webhook_delivery_fn` in order.  Must be called at module scope
    (not inside ``create_app()``) so the wiring fires for Huey worker processes
    that import ``main`` for task discovery without calling ``create_app()``.

    This function is idempotent: calling it twice registers the same callbacks
    twice, which is safe because each registration overwrites the previous one.
    """
    wire_dp_wrapper_factory()
    wire_spend_budget_fn()
    wire_circuit_breaker_redis_client()
    wire_webhook_delivery_fn()
    _logger.debug("IoC: all wiring complete (wire_all).")
