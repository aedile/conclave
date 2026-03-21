"""Concrete SQLAlchemy implementation of TaskRepository for the orphan reaper.

Queries the ``synthesis_job`` table for stale IN_PROGRESS records and applies
a conditional UPDATE to mark them FAILED.

Race-condition guard
--------------------
:meth:`SQLAlchemyTaskRepository.mark_failed` uses a conditional UPDATE::

    UPDATE synthesis_job
    SET status = 'FAILED', error_msg = :msg
    WHERE id = :id AND status = 'IN_PROGRESS'

If the job was completed by the worker between the stale query and this UPDATE,
zero rows are modified.  The method returns ``False`` in that case, and the
reaper skips counting the job as reaped.  This prevents overwriting a successful
completion result.

Repository query
----------------
:meth:`get_stale_in_progress` selects only rows where:
  - ``status = 'IN_PROGRESS'``
  - ``legal_hold = False``
  - ``created_at < :cutoff`` (strict less-than — ``>`` from reaper perspective)

SQLite timezone note
--------------------
SQLite stores ``datetime`` values as plain strings without timezone information.
SQLModel's ``created_at = Field(default_factory=lambda: datetime.now(UTC))``
produces a naive datetime in SQLite (the TZ is stripped on storage).  This
repository normalises the ``older_than`` cutoff to naive UTC before the
SQLAlchemy comparison so that string-based SQLite comparisons work correctly.
PostgreSQL stores timezone information natively and handles both aware and naive
comparisons without issue.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

CONSTITUTION Priority 0: Security — conditional UPDATE prevents silent overwrite
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T45.2 — Reintroduce Orphan Task Reaper (TBD-08)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CursorResult, Engine, update
from sqlmodel import Session, col, select

from synth_engine.modules.synthesizer.job_models import SynthesisJob
from synth_engine.shared.tasks.repository import StaleTask, TaskRepository

#: Status string for IN_PROGRESS jobs targeted by the reaper.
_IN_PROGRESS: str = "IN_PROGRESS"


def _naive_utc(dt: datetime) -> datetime:
    """Return *dt* as a timezone-naive datetime (UTC implied).

    SQLite stores datetimes as plain strings without timezone info.
    Stripping the tzinfo from a UTC-aware datetime allows SQLAlchemy's
    string-based comparison to work correctly against SQLite-stored values.
    PostgreSQL handles both aware and naive comparisons natively.

    Args:
        dt: A timezone-aware or naive UTC :class:`datetime`.

    Returns:
        The same instant as a timezone-naive :class:`datetime`.
    """
    return dt.replace(tzinfo=None)


class SQLAlchemyTaskRepository(TaskRepository):
    """Concrete :class:`~synth_engine.shared.tasks.repository.TaskRepository`.

    Uses a synchronous SQLAlchemy :class:`~sqlalchemy.Engine` obtained from
    :func:`~synth_engine.shared.db.get_engine` — never an async engine — to
    remain compatible with the synchronous Huey worker context.

    Args:
        engine: Synchronous SQLAlchemy engine bound to the ``synthesis_job``
            table.  Obtain via :func:`~synth_engine.shared.db.get_engine`.
    """

    def __init__(self, *, engine: Engine) -> None:
        self._engine = engine

    def get_stale_in_progress(self, older_than: datetime) -> list[StaleTask]:
        """Return stale IN_PROGRESS, non-legal-hold synthesis jobs.

        Only jobs whose ``created_at`` is strictly earlier than ``older_than``
        are returned.  QUEUED, FAILED, COMPLETE, and legal-hold jobs are
        excluded at the query level.

        The ``older_than`` cutoff is normalised to a naive datetime before the
        SQLAlchemy comparison to ensure correct string-based ordering in SQLite.
        PostgreSQL handles timezone-aware comparisons natively.

        Args:
            older_than: UTC cutoff (aware or naive).  Jobs created at this
                exact instant are NOT included (strict ``<`` comparison).

        Returns:
            List of :class:`~synth_engine.shared.tasks.repository.StaleTask`
            value objects, one per qualifying row.
        """
        naive_cutoff = _naive_utc(older_than)
        stmt = select(SynthesisJob).where(
            SynthesisJob.status == _IN_PROGRESS,
            col(SynthesisJob.legal_hold).is_(False),
            col(SynthesisJob.created_at) < naive_cutoff,
        )
        with Session(self._engine) as session:
            rows = session.exec(stmt).all()

        return [
            StaleTask(
                task_id=row.id,  # type: ignore[arg-type]  # id is int after DB insert
                status=row.status,
                created_at=row.created_at,
                legal_hold=row.legal_hold,
            )
            for row in rows
        ]

    def mark_failed(self, task_id: int, error_msg: str) -> bool:
        """Conditionally update a job to FAILED using a WHERE-guarded UPDATE.

        Only updates the row when ``status = 'IN_PROGRESS'``, preventing the
        race condition where a job completes between the stale query and this
        call.

        Args:
            task_id: Integer primary key of the target job.
            error_msg: Failure reason written to ``error_msg`` column.

        Returns:
            ``True`` when exactly one row was updated (job was still
            ``IN_PROGRESS``).  ``False`` when zero rows were updated (job
            completed concurrently or was already in a terminal state).
        """
        stmt = (
            update(SynthesisJob)
            .where(
                col(SynthesisJob.id) == task_id,
                col(SynthesisJob.status) == _IN_PROGRESS,
            )
            .values(status="FAILED", error_msg=error_msg)
        )
        with Session(self._engine) as session:
            result: CursorResult[Any] = session.execute(stmt)  # type: ignore[assignment]
            session.commit()

        rowcount: int = result.rowcount
        return rowcount == 1
