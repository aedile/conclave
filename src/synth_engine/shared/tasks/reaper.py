"""Orphan Task Reaper for the Conclave Engine.

Detects long-running IN_PROGRESS tasks that have exceeded their expected
execution window and marks them as FAILED, freeing resources and alerting
operators via structured logging.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """Minimal representation of a background task for reaper logic.

    Attributes:
        id: Unique task identifier.
        status: Current lifecycle status — one of "IN_PROGRESS", "FAILED",
            or "COMPLETE".
        started_at: Timezone-aware UTC datetime when the task began executing.
        locked_by: Optional identifier of the worker that holds the task lock.
    """

    id: str
    status: str
    started_at: datetime
    locked_by: str | None = field(default=None)


class TaskRepository(ABC):
    """Abstract interface for task persistence operations used by the reaper.

    Concrete implementations plug in the actual database layer (Task 2.2).
    The abstract interface keeps the reaper testable without a live database.
    """

    @abstractmethod
    def get_stale_tasks(self, older_than: datetime) -> list[Task]:
        """Return all IN_PROGRESS tasks that started before the given threshold.

        Args:
            older_than: Timezone-aware UTC datetime cutoff.  Tasks with
                ``started_at`` earlier than this value are considered stale.

        Returns:
            A list of Task objects eligible for reaping.
        """
        ...

    @abstractmethod
    def fail_task(self, task_id: str) -> None:
        """Transition a task's status to FAILED.

        Args:
            task_id: The unique identifier of the task to fail.
        """
        ...


class OrphanTaskReaper:
    """Identifies and terminates orphaned long-running tasks.

    An orphaned task is one whose status has been IN_PROGRESS for longer
    than the configured stale threshold, indicating the worker that claimed
    it likely crashed or lost connectivity.
    """

    def __init__(
        self,
        repository: TaskRepository,
        stale_threshold_minutes: int = 60,
    ) -> None:
        """Initialise the reaper with a repository and staleness threshold.

        Args:
            repository: Concrete TaskRepository used to query and update tasks.
            stale_threshold_minutes: Number of minutes after which an
                IN_PROGRESS task is considered orphaned.
        """
        self._repo = repository
        self._threshold_minutes = stale_threshold_minutes

    def reap(self) -> int:
        """Find all stale tasks and mark them as FAILED.

        Returns:
            The number of tasks that were reaped in this invocation.
        """
        from datetime import timedelta

        cutoff = datetime.now(tz=UTC) - timedelta(minutes=self._threshold_minutes)
        stale_tasks = self._repo.get_stale_tasks(older_than=cutoff)

        for task in stale_tasks:
            self._repo.fail_task(task.id)

        count = len(stale_tasks)
        logger.info("Reaped %d orphaned tasks", count)
        return count
