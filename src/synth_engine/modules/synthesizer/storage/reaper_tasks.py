"""Huey periodic task registration for the orphan task reaper — T45.2.

Wires :class:`~synth_engine.shared.tasks.reaper.OrphanTaskReaper` to the Huey
scheduler so that stale IN_PROGRESS synthesis jobs are swept every 15 minutes:

- ``periodic_reap_orphan_tasks``: runs at ``*/15`` (every 15 minutes), detects
  IN_PROGRESS jobs older than ``reaper_stale_threshold_minutes`` and marks them
  FAILED with error message
  ``"Reaped: exceeded staleness threshold — possible worker crash"``.

The task uses ``@huey.lock_task()`` to prevent overlapping concurrent runs.
If a reaper cycle is still running when the next schedule fires, the new
invocation exits immediately.

Bootstrapper wiring note (Rule 8)
----------------------------------
``bootstrapper/main.py`` imports this module at startup so the Huey worker
process discovers the periodic task::

    from synth_engine.modules.synthesizer.storage import reaper_tasks as _reaper_tasks  # noqa: F401

No additional DI injection is needed — the task reads settings directly from
:func:`~synth_engine.shared.settings.get_settings` and constructs a
:class:`~synth_engine.modules.synthesizer.storage.reaper_repository.SQLAlchemyTaskRepository`
on each invocation.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

CONSTITUTION Priority 0: Security — stale jobs audited, PII-free log entries
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T45.2 — Reintroduce Orphan Task Reaper (TBD-08)
"""

from __future__ import annotations

import logging

from huey import crontab

from synth_engine.shared.task_queue import huey

_logger = logging.getLogger(__name__)

_REAPER_LOCK = "orphan-task-reaper"


@huey.periodic_task(crontab(minute="*/15"))  # type: ignore[untyped-decorator]
@huey.lock_task(_REAPER_LOCK)  # type: ignore[untyped-decorator]
def periodic_reap_orphan_tasks() -> int:
    """Periodic task: reap stale IN_PROGRESS synthesis jobs every 15 minutes.

    Constructs a
    :class:`~synth_engine.modules.synthesizer.storage.reaper_repository.SQLAlchemyTaskRepository`
    and an :class:`~synth_engine.shared.tasks.reaper.OrphanTaskReaper` from
    current settings and calls
    :meth:`~synth_engine.shared.tasks.reaper.OrphanTaskReaper.reap`.

    Only IN_PROGRESS jobs older than ``reaper_stale_threshold_minutes`` that
    are not on legal hold are targeted.  Audit events are emitted for each
    reaped job (best-effort).

    Protected by ``@huey.lock_task`` — concurrent invocations skip execution
    rather than running in parallel (AC-9).

    Returns:
        The number of jobs marked FAILED in this reaper cycle.
    """
    from synth_engine.modules.synthesizer.storage.reaper_repository import (
        SQLAlchemyTaskRepository,
    )
    from synth_engine.shared.db import get_engine
    from synth_engine.shared.settings import get_settings
    from synth_engine.shared.tasks.reaper import OrphanTaskReaper

    settings = get_settings()
    database_url = settings.database_url
    if not database_url:
        _logger.error("Reaper task: DATABASE_URL not configured — aborting.")
        return 0

    db_engine = get_engine(database_url)
    repo = SQLAlchemyTaskRepository(engine=db_engine)
    reaper = OrphanTaskReaper(
        repository=repo,
        stale_threshold_minutes=settings.reaper_stale_threshold_minutes,
    )
    reaped = reaper.reap()
    return reaped
