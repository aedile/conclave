"""Huey periodic task wrappers for data retention cleanup — ADR-D3.

Wires :class:`RetentionCleanup` to the Huey scheduler so that expired job
records and artifact files are swept automatically on a nightly cadence:

- ``periodic_cleanup_expired_jobs``: runs at 02:00 UTC daily, deletes
  synthesis_job records older than ``job_retention_days`` that are in a
  terminal state (COMPLETE or FAILED) and not on legal hold.
- ``periodic_cleanup_expired_artifacts``: runs at 03:00 UTC daily, sweeps
  output artifact files for jobs older than ``artifact_retention_days``.

Both tasks use ``@huey.lock_task()`` to prevent overlapping concurrent
invocations.  If the task is still running when the next schedule fires, the
new invocation exits immediately without duplicating work.

Bootstrapper wiring note (Rule 8)
----------------------------------
``bootstrapper/main.py`` imports this module at startup so the Huey worker
process discovers the periodic tasks::

    from synth_engine.modules.synthesizer import retention_tasks as _retention_tasks  # noqa: F401

No additional DI injection is needed — both tasks read settings directly from
:func:`~synth_engine.shared.settings.get_settings` and construct a
:class:`RetentionCleanup` instance on each invocation.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

CONSTITUTION Priority 0: Security — deletions audited, PII-free log entries
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: ADR-D3 — Wire Retention Cleanup to Huey Periodic Task (ADV-019/020)
"""

from __future__ import annotations

import logging

from huey import crontab

from synth_engine.shared.task_queue import huey

_logger = logging.getLogger(__name__)

_JOB_CLEANUP_LOCK = "retention-job-cleanup"
_ARTIFACT_CLEANUP_LOCK = "retention-artifact-cleanup"


@huey.periodic_task(crontab(hour="2", minute="0"))  # type: ignore[untyped-decorator]
@huey.lock_task(_JOB_CLEANUP_LOCK)  # type: ignore[untyped-decorator]
def periodic_cleanup_expired_jobs() -> int:
    """Periodic task: clean up expired synthesis job records at 02:00 UTC daily.

    Constructs a :class:`~synth_engine.modules.synthesizer.retention.RetentionCleanup`
    instance from current settings and calls
    :meth:`~synth_engine.modules.synthesizer.retention.RetentionCleanup.cleanup_expired_jobs`.

    Only jobs in terminal states (COMPLETE or FAILED) that are not on legal
    hold and are older than ``job_retention_days`` are deleted.

    Protected by ``@huey.lock_task`` — concurrent invocations skip execution
    rather than running in parallel.

    Returns:
        The number of job records deleted.
    """
    from synth_engine.modules.synthesizer.retention import RetentionCleanup
    from synth_engine.shared.db import get_engine
    from synth_engine.shared.settings import get_settings

    settings = get_settings()
    database_url = settings.database_url
    if not database_url:
        _logger.error("Retention task: DATABASE_URL not configured — aborting.")
        return 0
    db_engine = get_engine(database_url)

    cleanup = RetentionCleanup(
        engine=db_engine,
        job_retention_days=settings.job_retention_days,
    )
    deleted = cleanup.cleanup_expired_jobs()
    _logger.info("Retention task: deleted %d expired job records.", deleted)
    return deleted


@huey.periodic_task(crontab(hour="3", minute="0"))  # type: ignore[untyped-decorator]
@huey.lock_task(_ARTIFACT_CLEANUP_LOCK)  # type: ignore[untyped-decorator]
def periodic_cleanup_expired_artifacts() -> int:
    """Periodic task: sweep expired artifact files at 03:00 UTC daily.

    Constructs a :class:`~synth_engine.modules.synthesizer.retention.RetentionCleanup`
    instance from current settings and calls
    :meth:`~synth_engine.modules.synthesizer.retention.RetentionCleanup.cleanup_expired_artifacts`.

    Only artifact files belonging to terminal, non-held jobs older than
    ``artifact_retention_days`` are removed.  The ``output_path`` column is
    set to ``NULL`` on the corresponding job record after deletion.

    Protected by ``@huey.lock_task`` — concurrent invocations skip execution
    rather than running in parallel.

    Returns:
        The number of artifact files swept.
    """
    from synth_engine.modules.synthesizer.retention import RetentionCleanup
    from synth_engine.shared.db import get_engine
    from synth_engine.shared.settings import get_settings

    settings = get_settings()
    database_url = settings.database_url
    if not database_url:
        _logger.error("Retention task: DATABASE_URL not configured — aborting.")
        return 0
    db_engine = get_engine(database_url)

    cleanup = RetentionCleanup(
        engine=db_engine,
        job_retention_days=settings.job_retention_days,
        artifact_retention_days=settings.artifact_retention_days,
    )
    swept = cleanup.cleanup_expired_artifacts()
    _logger.info("Retention task: swept %d expired artifact files.", swept)
    return swept
