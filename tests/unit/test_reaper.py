"""Unit tests for the OrphanTaskReaper.

Tests verify that stale IN_PROGRESS tasks are correctly identified and
marked as failed, while recent tasks and non-IN_PROGRESS tasks are skipped.
Also tests that fail_task exceptions are caught per-task so the loop continues.

CONSTITUTION Priority 3: TDD RED/GREEN Phase
Task: P2-T2.1 — Module Bootstrapper, OTEL, Idempotency, Orphan Task Reaper
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, call

import pytest


def _utc_now() -> datetime:
    """Return the current time in UTC as a timezone-aware datetime."""
    return datetime.now(tz=UTC)


@pytest.fixture
def stale_task_factory():  # type: ignore[no-untyped-def]
    """Factory for creating Task instances that are older than any threshold."""
    from synth_engine.shared.tasks.reaper import Task

    def _make(task_id: str, minutes_ago: int = 120) -> Task:
        return Task(
            id=task_id,
            status="IN_PROGRESS",
            started_at=_utc_now() - timedelta(minutes=minutes_ago),
        )

    return _make


@pytest.fixture
def recent_task_factory():  # type: ignore[no-untyped-def]
    """Factory for creating Task instances that are within any threshold."""
    from synth_engine.shared.tasks.reaper import Task

    def _make(task_id: str, minutes_ago: int = 30) -> Task:
        return Task(
            id=task_id,
            status="IN_PROGRESS",
            started_at=_utc_now() - timedelta(minutes=minutes_ago),
        )

    return _make


def test_reap_stale_tasks_marks_them_failed(stale_task_factory) -> None:  # type: ignore[no-untyped-def]
    """reap() calls fail_task for each stale IN_PROGRESS task and returns the count.

    Two tasks older than the 60-minute threshold must both be failed,
    and reap() must return 2.
    """
    from synth_engine.shared.tasks.reaper import OrphanTaskReaper

    task_a = stale_task_factory("task-001")
    task_b = stale_task_factory("task-002")

    repo = MagicMock()
    repo.get_stale_tasks.return_value = [task_a, task_b]

    reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
    count = reaper.reap()

    assert count == 2
    repo.fail_task.assert_has_calls([call("task-001"), call("task-002")], any_order=True)


def test_reap_skips_recent_tasks(recent_task_factory) -> None:  # type: ignore[no-untyped-def]
    """reap() does not fail tasks started within the threshold window.

    A task started 30 minutes ago with a 60-minute threshold must not
    be reaped.
    """
    from synth_engine.shared.tasks.reaper import OrphanTaskReaper

    repo = MagicMock()
    repo.get_stale_tasks.return_value = []  # Repository filters stale tasks

    reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
    count = reaper.reap()

    assert count == 0
    repo.fail_task.assert_not_called()


def test_reap_returns_zero_when_nothing_stale() -> None:
    """reap() returns 0 when there are no stale tasks.

    An empty stale task list must result in no fail_task calls and
    a return value of 0.
    """
    from synth_engine.shared.tasks.reaper import OrphanTaskReaper

    repo = MagicMock()
    repo.get_stale_tasks.return_value = []

    reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
    count = reaper.reap()

    assert count == 0
    repo.fail_task.assert_not_called()


def test_task_dataclass_fields() -> None:
    """Task dataclass exposes id, status, started_at, and optional locked_by.

    These fields form the minimal contract for orphan detection logic.
    """
    from synth_engine.shared.tasks.reaper import Task

    now = _utc_now()
    task = Task(id="t-1", status="IN_PROGRESS", started_at=now)

    assert task.id == "t-1"
    assert task.status == "IN_PROGRESS"
    assert task.started_at == now
    assert task.locked_by is None


def test_task_dataclass_with_locked_by() -> None:
    """Task dataclass accepts an optional locked_by field.

    lock ownership is optional metadata used by distributed lock implementations.
    """
    from synth_engine.shared.tasks.reaper import Task

    now = _utc_now()
    task = Task(id="t-2", status="IN_PROGRESS", started_at=now, locked_by="worker-7")

    assert task.locked_by == "worker-7"


def test_reap_logs_info_on_completion(stale_task_factory, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    """reap() emits an INFO log with the count of reaped tasks.

    Operators rely on this log line to confirm reaper health in production.
    """
    import logging

    from synth_engine.shared.tasks.reaper import OrphanTaskReaper

    task = stale_task_factory("task-999")
    repo = MagicMock()
    repo.get_stale_tasks.return_value = [task]

    reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)

    with caplog.at_level(logging.INFO, logger="synth_engine.shared.tasks.reaper"):
        reaper.reap()

    assert "Reaped 1 orphaned tasks" in caplog.text


def test_reap_continues_after_fail_task_exception(  # type: ignore[no-untyped-def]
    stale_task_factory, caplog: pytest.LogCaptureFixture
) -> None:
    """reap() continues to the next task when fail_task() raises an exception.

    A single fail_task failure must not abort processing of subsequent tasks.
    The error must be logged and the final count reflects only the tasks
    for which reaping was attempted (not necessarily succeeded).
    """
    import logging

    from synth_engine.shared.tasks.reaper import OrphanTaskReaper

    task_a = stale_task_factory("task-fail-001")
    task_b = stale_task_factory("task-ok-002")

    repo = MagicMock()
    repo.get_stale_tasks.return_value = [task_a, task_b]
    # First call raises; second call succeeds
    repo.fail_task.side_effect = [Exception("db timeout"), None]

    reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)

    with caplog.at_level(logging.ERROR, logger="synth_engine.shared.tasks.reaper"):
        count = reaper.reap()

    # Both tasks were attempted; count reflects total stale tasks
    assert count == 2
    # fail_task was called for both tasks
    assert repo.fail_task.call_count == 2
    # Error was logged for the failing task
    assert "task-fail-001" in caplog.text
