"""Orphan task reaper — pure business logic.

Detects stale IN_PROGRESS synthesis jobs (likely victims of SIGKILL or OOM
kill) and marks them FAILED so they do not accumulate indefinitely.

Design
------
:class:`OrphanTaskReaper` depends only on the abstract
:class:`~synth_engine.shared.tasks.repository.TaskRepository` interface.  It
has no direct DB or SQLAlchemy dependency, keeping this module boundary-safe
inside ``shared/``.

Per-task isolation
------------------
A failure while marking one job does not abort the loop.  Each exception is
caught, logged at ERROR level, and the cycle continues with the next candidate.

Audit logging
-------------
A ``ORPHAN_TASK_REAPED`` audit event is emitted for each successfully reaped
job.  Audit logging is best-effort: a logging failure is caught, logged as a
WARNING, and does NOT prevent the job from being marked FAILED.

This pattern mirrors ``modules/synthesizer/retention.py``.

Boundary constraints (import-linter enforced):
    This module must NOT import from ``modules/`` or ``bootstrapper/``.

CONSTITUTION Priority 0: Security — reaped jobs audited, PII-free log entries
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T45.2 — Reintroduce Orphan Task Reaper (TBD-08)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from synth_engine.shared.security.audit import get_audit_logger
from synth_engine.shared.tasks.repository import StaleTask, TaskRepository

_logger = logging.getLogger(__name__)

#: Canonical error message written to reaped jobs (AC-5).
_REAPER_ERROR_MSG: str = "Reaped: exceeded staleness threshold — possible worker crash"

#: Minimum allowed staleness threshold (minutes).  Prevents accidental kill-all.
_MIN_THRESHOLD_MINUTES: int = 5


class OrphanTaskReaper:
    """Scans for stale IN_PROGRESS synthesis jobs and marks them FAILED.

    Args:
        repository: Concrete implementation of
            :class:`~synth_engine.shared.tasks.repository.TaskRepository`.
        stale_threshold_minutes: Number of minutes after which an IN_PROGRESS
            job is considered orphaned.  Must be >= 5 to prevent accidental
            kill-all.

    Raises:
        ValueError: If ``stale_threshold_minutes`` is less than 5.
    """

    def __init__(
        self,
        *,
        repository: TaskRepository,
        stale_threshold_minutes: int,
    ) -> None:
        if stale_threshold_minutes < _MIN_THRESHOLD_MINUTES:
            raise ValueError(
                f"stale_threshold_minutes must be >= {_MIN_THRESHOLD_MINUTES}; "
                f"got {stale_threshold_minutes}.  "
                "Use a larger value to prevent accidental mass-reaping."
            )
        self._repo = repository
        self._threshold = stale_threshold_minutes

    def reap(self) -> int:
        """Execute one reaper cycle.

        Queries the repository for stale IN_PROGRESS jobs, skips those under
        legal hold, conditionally marks the remainder FAILED, emits audit
        events (best-effort), and logs a summary.

        If the repository raises an exception during the initial query, the
        error is logged and the method returns ``0`` without crashing.

        Returns:
            The number of jobs successfully marked FAILED in this cycle.
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=self._threshold)

        try:
            candidates: list[StaleTask] = self._repo.get_stale_in_progress(cutoff)
        except Exception as exc:
            _logger.error(
                "Reaper: failed to query stale jobs — skipping cycle. Error: %s",
                exc,
            )
            return 0

        reaped = 0
        for task in candidates:
            if task.legal_hold:
                _logger.debug("Reaper: skipping job %d — legal hold active.", task.task_id)
                continue

            if task.status != "IN_PROGRESS":
                _logger.debug(
                    "Reaper: skipping job %d — unexpected status '%s'.",
                    task.task_id,
                    task.status,
                )
                continue

            try:
                updated = self._repo.mark_failed(task.task_id, _REAPER_ERROR_MSG)
            except Exception as exc:
                _logger.error(
                    "Reaper: failed to mark job %d as FAILED — skipping. Error: %s",
                    task.task_id,
                    exc,
                )
                continue

            if not updated:
                _logger.debug(
                    "Reaper: job %d no longer IN_PROGRESS (concurrent completion).",
                    task.task_id,
                )
                continue

            reaped += 1
            self._emit_audit(task.task_id)

        _logger.info("Reaper cycle complete: %d jobs reaped.", reaped)
        return reaped

    def _emit_audit(self, task_id: int) -> None:
        """Emit a best-effort ORPHAN_TASK_REAPED audit event.

        Failures are caught and logged as a WARNING.  A failed audit does NOT
        roll back the job's FAILED status — the mark has already been committed.

        Args:
            task_id: Integer primary key of the reaped job.
        """
        try:
            get_audit_logger().log_event(
                event_type="ORPHAN_TASK_REAPED",
                actor="system/reaper",
                resource=f"synthesis_job/{task_id}",
                action="mark_failed",
                details={"job_id": str(task_id), "reason": _REAPER_ERROR_MSG},
            )
        except Exception as exc:
            _logger.warning(
                "Reaper: audit log failed for job %d (best-effort). Error: %s",
                task_id,
                exc,
            )
