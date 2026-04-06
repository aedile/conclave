"""Abstract task repository interface for the orphan task reaper.

Defines the :class:`TaskRepository` ABC and the :class:`StaleTask` value
object.  The abstract interface lives in ``shared/`` so that
:class:`~synth_engine.shared.tasks.reaper.OrphanTaskReaper` can depend on the
abstraction without violating the ``shared/ → modules/`` boundary rule.

The concrete ``SQLAlchemyTaskRepository`` implementation is placed in
``modules/synthesizer/reaper_repository.py`` where it is free to import
:class:`~synth_engine.modules.synthesizer.jobs.job_models.SynthesisJob`.

Boundary constraints (import-linter enforced):
    This module must NOT import from ``modules/`` or ``bootstrapper/``.

CONSTITUTION Priority 0: Security
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T45.2 — Reintroduce Orphan Task Reaper (TBD-08)
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class StaleTask:
    """Immutable value object representing a stale synthesis job.

    Carries the minimal set of fields the reaper needs to decide whether to
    act on a job and to emit a meaningful audit event.

    Attributes:
        task_id: Integer primary key of the synthesis job.
        status: Current job status string (e.g. ``"IN_PROGRESS"``).
        created_at: UTC timestamp when the job was created.
        legal_hold: Whether the job has a legal hold that exempts it from
            routine cleanup.  Defaults to ``False``.
    """

    task_id: int
    status: str
    created_at: datetime | None = field(default=None)
    legal_hold: bool = field(default=False)
    #: Tenant organization UUID for multi-tenant audit events (T79.2, ADR-0065).
    #: Empty string for backward compatibility with pre-P79 tasks.
    org_id: str = field(default="")


class TaskRepository(abc.ABC):
    """Abstract repository for synthesis-job task operations used by the reaper.

    Concrete implementations (e.g.
    :class:`~synth_engine.modules.synthesizer.storage.reaper_repository.SQLAlchemyTaskRepository`)
    handle DB access.  The abstract layer is kept here so that
    :class:`~synth_engine.shared.tasks.reaper.OrphanTaskReaper` can live in
    ``shared/`` without importing from ``modules/``.
    """

    @abc.abstractmethod
    def get_stale_in_progress(self, older_than: datetime) -> list[StaleTask]:
        """Return IN_PROGRESS jobs that have been running since before *older_than*.

        Implementations MUST:
          - Filter on ``status = 'IN_PROGRESS'`` only (QUEUED, FAILED, COMPLETE
            must never be returned).
          - Filter on ``legal_hold = False`` (held jobs must be excluded).
          - Use a strict ``created_at < older_than`` comparison.

        Args:
            older_than: UTC cutoff datetime.  Only jobs created strictly before
                this instant are candidates.

        Returns:
            A list of :class:`StaleTask` instances ready for reaping.  Returns
            an empty list when there are no stale jobs.
        """

    @abc.abstractmethod
    def mark_failed(self, task_id: int, error_msg: str) -> bool:
        """Conditionally mark *task_id* as FAILED.

        Implementations MUST use a conditional UPDATE::

            UPDATE synthesis_job
            SET status = 'FAILED', error_msg = :msg
            WHERE id = :id AND status = 'IN_PROGRESS'

        This guards against the race condition where a job completes between
        the stale query and the update.

        Args:
            task_id: Integer primary key of the job to mark.
            error_msg: Human-readable failure reason written to ``error_msg``.

        Returns:
            ``True`` when exactly one row was updated (the job was still
            ``IN_PROGRESS`` and is now ``FAILED``).  ``False`` when zero rows
            were updated (job was no longer ``IN_PROGRESS`` — concurrent
            completion race).
        """
