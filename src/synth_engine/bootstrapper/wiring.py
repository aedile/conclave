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

- :mod:`synth_engine.modules.synthesizer.reaper_tasks` — orphan task reaper
- :mod:`synth_engine.modules.synthesizer.retention_tasks` — nightly retention
- :mod:`synth_engine.shared.security.rotation` — ALE key rotation

These must be imported before the Huey worker starts polling or tasks will be
silently dropped.

Rule 8 compliance
-----------------
Each ``wire_*`` function corresponds to one Rule 8 IoC hook (from
:mod:`synth_engine.modules.synthesizer.job_orchestration`):

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

from sqlmodel import Session, select

from synth_engine.bootstrapper.factories import build_dp_wrapper, build_spend_budget_fn
from synth_engine.bootstrapper.schemas.webhooks import WebhookDelivery, WebhookRegistration
from synth_engine.modules.synthesizer import (
    reaper_tasks as _reaper_tasks,  # noqa: F401 — side-effect: registers Huey task
)
from synth_engine.modules.synthesizer import (
    retention_tasks as _retention_tasks,  # noqa: F401 — side-effect: registers Huey task
)
from synth_engine.modules.synthesizer import tasks as _synthesizer_tasks
from synth_engine.modules.synthesizer.job_models import SynthesisJob
from synth_engine.modules.synthesizer.job_orchestration import set_webhook_delivery_fn
from synth_engine.modules.synthesizer.webhook_delivery import deliver_webhook
from synth_engine.shared.db import get_engine
from synth_engine.shared.security import (
    rotation as _security_rotation,  # noqa: F401 — side-effect: registers rotation task
)
from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)


def _build_webhook_delivery_fn() -> Callable[[int, str], None]:
    """Build the concrete webhook delivery callback for IoC injection.

    Returns a closure that, when called with ``(job_id, status)``:

    1. Opens a synchronous DB session using the settings ``database_url``.
    2. Looks up the ``SynthesisJob`` to resolve the ``owner_id``.
    3. Queries all active ``WebhookRegistration`` rows for that owner.
    4. Filters registrations by subscribed event type.
    5. Calls :func:`~synth_engine.modules.synthesizer.webhook_delivery.deliver_webhook`
       for each qualifying registration.
    6. Persists a :class:`~synth_engine.bootstrapper.schemas.webhooks.WebhookDelivery`
       audit row for each attempt.

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


def wire_dp_wrapper_factory() -> None:
    """Register the DP wrapper factory with the synthesizer task module.

    Injects :func:`~synth_engine.bootstrapper.factories.build_dp_wrapper`
    as the factory that constructs
    :class:`~synth_engine.modules.privacy.dp_engine.DPTrainingWrapper`
    instances at synthesis-job start time.

    Calling this function multiple times is safe — each call overwrites the
    previous registration with an identical factory (idempotent).
    """
    _synthesizer_tasks.set_dp_wrapper_factory(build_dp_wrapper)
    _logger.debug("IoC: dp_wrapper_factory wired (ADR-0029).")


def wire_spend_budget_fn() -> None:
    """Register the sync spend-budget callable with the synthesizer task module.

    Injects the sync wrapper returned by
    :func:`~synth_engine.bootstrapper.factories.build_spend_budget_fn` so that
    Huey worker tasks can call it without entering an async event loop (T22.3).

    Calling this function multiple times is safe — each call overwrites the
    previous registration with a new equivalent wrapper (idempotent in effect).
    """
    _synthesizer_tasks.set_spend_budget_fn(build_spend_budget_fn())
    _logger.debug("IoC: spend_budget_fn wired (T22.3).")


def wire_webhook_delivery_fn() -> None:
    """Register the webhook delivery callback with the job orchestration module.

    Injects the concrete delivery callback built by
    :func:`_build_webhook_delivery_fn` so that
    :func:`~synth_engine.modules.synthesizer.job_orchestration` can trigger
    webhook delivery without importing from ``bootstrapper/`` (correct DI
    direction: bootstrapper → modules).

    Calling this function multiple times is safe — each call overwrites the
    previous registration (T45.3 / Rule 8 / P45 review F3).
    """
    set_webhook_delivery_fn(_build_webhook_delivery_fn())
    _logger.debug("IoC: webhook_delivery_fn wired (T45.3, Rule 8).")


def wire_all() -> None:
    """Register all IoC callbacks required by the Conclave Engine at startup.

    Calls :func:`wire_dp_wrapper_factory`, :func:`wire_spend_budget_fn`, and
    :func:`wire_webhook_delivery_fn` in order.  Must be called at module scope
    (not inside ``create_app()``) so the wiring fires for Huey worker processes
    that import ``main`` for task discovery without calling ``create_app()``.

    This function is idempotent: calling it twice registers the same callbacks
    twice, which is safe because each registration overwrites the previous one.
    """
    wire_dp_wrapper_factory()
    wire_spend_budget_fn()
    wire_webhook_delivery_fn()
    _logger.debug("IoC: all wiring complete (wire_all).")
