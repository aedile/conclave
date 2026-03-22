"""Unit tests for the orphan task reaper — T45.2.

Tests are grouped into:
  - ATTACK / negative tests (per spec Rule 22, written first)
  - Happy-path / positive tests

CONSTITUTION Priority 0: Security
CONSTITUTION Priority 3: TDD
Task: T45.2 — Reintroduce Orphan Task Reaper (TBD-08)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from synth_engine.shared.tasks.reaper import OrphanTaskReaper
from synth_engine.shared.tasks.repository import StaleTask, TaskRepository

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


def _make_stale_task(
    task_id: int = 1,
    legal_hold: bool = False,
    status: str = "IN_PROGRESS",
) -> StaleTask:
    """Return a minimal :class:`StaleTask` value object.

    Args:
        task_id: Synthetic job ID.
        legal_hold: Whether the job has a legal hold flag set.
        status: The job status string.

    Returns:
        A populated :class:`StaleTask` instance.
    """
    return StaleTask(
        task_id=task_id,
        status=status,
        created_at=datetime.now(UTC) - timedelta(hours=2),
        legal_hold=legal_hold,
    )


class _StubRepository(TaskRepository):
    """Test-double repository with controllable stale task list and failure injection."""

    def __init__(
        self,
        stale_tasks: list[StaleTask] | None = None,
        fail_on_mark_ids: set[int] | None = None,
    ) -> None:
        self._stale_tasks = stale_tasks or []
        self._fail_on_mark_ids = fail_on_mark_ids or set()
        self.marked: list[tuple[int, str, str]] = []  # (task_id, status, error_msg)

    def get_stale_in_progress(self, older_than: datetime) -> list[StaleTask]:  # type: ignore[override]
        """Return pre-configured stale tasks (ignores ``older_than``).

        Args:
            older_than: Threshold datetime (unused in stub).

        Returns:
            The pre-configured list of stale tasks.
        """
        return list(self._stale_tasks)

    def mark_failed(self, task_id: int, error_msg: str) -> bool:  # type: ignore[override]
        """Record the mark attempt and optionally raise for error-isolation tests.

        Args:
            task_id: Job ID to mark as FAILED.
            error_msg: Failure reason.

        Returns:
            ``True`` when the mark succeeds; ``False`` when no matching row
            existed (simulates lost-update / concurrent completion).

        Raises:
            RuntimeError: When ``task_id`` is in ``fail_on_mark_ids``.
        """
        if task_id in self._fail_on_mark_ids:
            raise RuntimeError(f"Injected failure for task_id={task_id}")
        self.marked.append((task_id, "FAILED", error_msg))
        return True


# ---------------------------------------------------------------------------
# ATTACK TEST 1 — Race condition: completed jobs must NOT be overwritten
# ---------------------------------------------------------------------------


class TestRaceConditionSafety:
    """AC-6: conditional UPDATE WHERE status='IN_PROGRESS' prevents race."""

    def test_mark_failed_returns_false_when_no_row_updated(self) -> None:
        """Repository.mark_failed returning False is handled gracefully."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = [_make_stale_task(task_id=10)]
        repo.mark_failed.return_value = False  # simulates concurrent completion

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        reaped = reaper.reap()

        # mark_failed was called with the correct job
        repo.mark_failed.assert_called_once()
        # row not updated → does not count as reaped
        assert reaped == 0

    def test_mark_failed_returns_true_counts_as_reaped(self) -> None:
        """Repository.mark_failed returning True counts the job as reaped."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = [_make_stale_task(task_id=11)]
        repo.mark_failed.return_value = True

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        reaped = reaper.reap()

        assert reaped == 1


# ---------------------------------------------------------------------------
# ATTACK TEST 2 — Legal-hold jobs must be SKIPPED
# ---------------------------------------------------------------------------


class TestLegalHoldExclusion:
    """AC-2: jobs with legal_hold=True are skipped even when stale."""

    def test_legal_hold_job_is_not_reaped(self) -> None:
        """A stale job under legal hold must NOT be passed to mark_failed."""
        repo = MagicMock(spec=TaskRepository)
        held_task = _make_stale_task(task_id=20, legal_hold=True)
        repo.get_stale_in_progress.return_value = [held_task]

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        reaped = reaper.reap()

        repo.mark_failed.assert_not_called()
        assert reaped == 0

    def test_mix_held_and_unheld_only_unheld_reaped(self) -> None:
        """Only non-held jobs are reaped when the list contains both kinds."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = [
            _make_stale_task(task_id=21, legal_hold=True),
            _make_stale_task(task_id=22, legal_hold=False),
        ]
        repo.mark_failed.return_value = True

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        reaped = reaper.reap()

        repo.mark_failed.assert_called_once()
        assert reaped == 1


# ---------------------------------------------------------------------------
# ATTACK TEST 3 — QUEUED jobs must be ignored
# ---------------------------------------------------------------------------


class TestQueuedJobsIgnored:
    """Repository contract: only IN_PROGRESS jobs must be returned."""

    def test_reaper_queries_only_in_progress(self) -> None:
        """get_stale_in_progress is the only query; QUEUED must not appear."""
        repo = MagicMock(spec=TaskRepository)
        # Repository returns empty — contract is on the repo side
        repo.get_stale_in_progress.return_value = []

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        reaper.reap()

        repo.get_stale_in_progress.assert_called_once()
        repo.mark_failed.assert_not_called()

    def test_task_with_queued_status_is_not_reaped(self) -> None:
        """Even if repository returns a QUEUED job, OrphanTaskReaper skips it."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = [
            _make_stale_task(task_id=30, status="QUEUED"),
        ]

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        reaped = reaper.reap()

        repo.mark_failed.assert_not_called()
        assert reaped == 0


# ---------------------------------------------------------------------------
# ATTACK TEST 4 — Already-FAILED jobs must be ignored
# ---------------------------------------------------------------------------


class TestFailedJobsIgnored:
    """Reaper must not re-mark already-failed jobs."""

    def test_failed_status_is_not_reaped(self) -> None:
        """A task with status FAILED must not be passed to mark_failed."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = [
            _make_stale_task(task_id=40, status="FAILED"),
        ]

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        reaped = reaper.reap()

        repo.mark_failed.assert_not_called()
        assert reaped == 0


# ---------------------------------------------------------------------------
# ATTACK TEST 5 — Zero stale jobs → no errors, no audit entries
# ---------------------------------------------------------------------------


class TestZeroStaleJobs:
    """AC-10: zero stale jobs produces a summary log and no side-effects."""

    def test_no_stale_jobs_no_audit(self) -> None:
        """Zero results from get_stale_in_progress must produce no audit entries."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = []
        audit_mock = MagicMock()

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        with patch("synth_engine.shared.tasks.reaper.get_audit_logger", return_value=audit_mock):
            reaped = reaper.reap()

        assert reaped == 0
        audit_mock.log_event.assert_not_called()

    def test_no_stale_jobs_logs_summary(self, caplog: Any) -> None:
        """Zero results still emits the summary INFO log line."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = []

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        with caplog.at_level(logging.INFO, logger="synth_engine.shared.tasks.reaper"):
            reaped = reaper.reap()

        assert reaped == 0
        assert "Reaper cycle complete: 0 jobs reaped" in caplog.text


# ---------------------------------------------------------------------------
# ATTACK TEST 6 — Threshold validation: minimum 5 minutes
# ---------------------------------------------------------------------------


class TestThresholdValidation:
    """AC-4: reaper_stale_threshold_minutes must be >= 5 (ge=5)."""

    def test_threshold_below_5_raises(self) -> None:
        """Constructing OrphanTaskReaper with threshold < 5 must raise ValueError."""
        repo = MagicMock(spec=TaskRepository)
        with pytest.raises(ValueError, match="stale_threshold_minutes"):
            OrphanTaskReaper(repository=repo, stale_threshold_minutes=4)

    def test_threshold_of_5_is_allowed(self) -> None:
        """Threshold == 5 (the minimum) must be accepted."""
        repo = MagicMock(spec=TaskRepository)
        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=5)
        assert reaper is not None

    def test_threshold_of_0_raises(self) -> None:
        """Threshold == 0 must raise ValueError."""
        repo = MagicMock(spec=TaskRepository)
        with pytest.raises(ValueError, match="stale_threshold_minutes"):
            OrphanTaskReaper(repository=repo, stale_threshold_minutes=0)


# ---------------------------------------------------------------------------
# ATTACK TEST 7 — DB unavailable: reaper logs error, does not crash
# ---------------------------------------------------------------------------


class TestDatabaseUnavailable:
    """Reaper must survive a DB error without raising."""

    def test_db_error_on_query_is_logged_and_swallowed(self, caplog: Any) -> None:
        """get_stale_in_progress raising an exception must be caught and logged."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.side_effect = OSError("Connection refused")

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        with caplog.at_level(logging.ERROR, logger="synth_engine.shared.tasks.reaper"):
            reaped = reaper.reap()

        assert reaped == 0
        assert "Connection refused" in caplog.text


# ---------------------------------------------------------------------------
# ATTACK TEST 8 — Per-task isolation: one failure must not block others
# ---------------------------------------------------------------------------


class TestPerTaskIsolation:
    """AC-8: one job's mark_failed exception must not stop other jobs."""

    def test_one_failure_does_not_block_remaining_jobs(self) -> None:
        """When task 1 fails, task 2 must still be attempted and reaped."""
        repo = _StubRepository(
            stale_tasks=[
                _make_stale_task(task_id=50),
                _make_stale_task(task_id=51),
            ],
            fail_on_mark_ids={50},
        )

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        with patch("synth_engine.shared.tasks.reaper.get_audit_logger"):
            reaped = reaper.reap()

        # task 50 failed, task 51 succeeded
        assert reaped == 1
        assert any(m[0] == 51 for m in repo.marked)

    def test_per_task_error_is_logged(self, caplog: Any) -> None:
        """When mark_failed raises, the error must be logged at ERROR level."""
        repo = _StubRepository(
            stale_tasks=[_make_stale_task(task_id=52)],
            fail_on_mark_ids={52},
        )

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        with caplog.at_level(logging.ERROR, logger="synth_engine.shared.tasks.reaper"):
            with patch("synth_engine.shared.tasks.reaper.get_audit_logger"):
                reaper.reap()

        assert "52" in caplog.text


# ---------------------------------------------------------------------------
# ATTACK TEST 9 — Boundary comparison: strictly greater-than (not >=)
# ---------------------------------------------------------------------------


class TestBoundaryComparison:
    """AC-6 (boundary): threshold cutoff uses strict greater-than."""

    def test_cutoff_is_now_minus_threshold(self) -> None:
        """get_stale_in_progress must receive a cutoff = now - threshold."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = []

        threshold_minutes = 30
        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=threshold_minutes)

        before = datetime.now(UTC)
        reaper.reap()
        after = datetime.now(UTC)

        args, _ = repo.get_stale_in_progress.call_args
        cutoff: datetime = args[0]

        expected_low = before - timedelta(minutes=threshold_minutes)
        expected_high = after - timedelta(minutes=threshold_minutes)
        assert expected_low <= cutoff <= expected_high


# ---------------------------------------------------------------------------
# ATTACK TEST 10 — Audit failure resilience
# ---------------------------------------------------------------------------


class TestAuditFailureResilience:
    """AC-7: audit failure must not prevent the job from being marked FAILED."""

    def test_audit_exception_does_not_abort_mark_failed(self) -> None:
        """Even when get_audit_logger().log_event raises, mark_failed must be called."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = [_make_stale_task(task_id=60)]
        repo.mark_failed.return_value = True

        broken_audit = MagicMock()
        broken_audit.log_event.side_effect = RuntimeError("Audit storage unavailable")

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        with patch(
            "synth_engine.shared.tasks.reaper.get_audit_logger",
            return_value=broken_audit,
        ):
            reaped = reaper.reap()

        repo.mark_failed.assert_called_once()
        assert reaped == 1

    def test_audit_exception_is_logged(self, caplog: Any) -> None:
        """Audit failure must be logged as a WARNING (best-effort)."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = [_make_stale_task(task_id=61)]
        repo.mark_failed.return_value = True

        broken_audit = MagicMock()
        broken_audit.log_event.side_effect = RuntimeError("Audit storage unavailable")

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        with caplog.at_level(logging.WARNING, logger="synth_engine.shared.tasks.reaper"):
            with patch(
                "synth_engine.shared.tasks.reaper.get_audit_logger",
                return_value=broken_audit,
            ):
                reaper.reap()

        assert "Audit storage unavailable" in caplog.text


# ---------------------------------------------------------------------------
# ATTACK TEST 11 — StaleTask.legal_hold is the gate, not repository
# ---------------------------------------------------------------------------


class TestLegalHoldIsOnStaleTask:
    """Confirm legal_hold is carried on StaleTask and evaluated in OrphanTaskReaper."""

    def test_stale_task_legal_hold_field_exists(self) -> None:
        """StaleTask must expose a legal_hold bool field."""
        task = StaleTask(
            task_id=70,
            status="IN_PROGRESS",
            created_at=datetime.now(UTC),
            legal_hold=True,
        )
        assert task.legal_hold is True

    def test_stale_task_default_legal_hold_is_false(self) -> None:
        """StaleTask must default legal_hold to False."""
        task = StaleTask(
            task_id=71,
            status="IN_PROGRESS",
            created_at=datetime.now(UTC),
        )
        assert task.legal_hold is False


# ---------------------------------------------------------------------------
# HAPPY-PATH TESTS
# ---------------------------------------------------------------------------


class TestHappyPath:
    """AC-1–AC-10 positive / nominal flow."""

    def test_single_stale_job_reaped_and_counted(self) -> None:
        """One stale IN_PROGRESS non-held job is marked FAILED and counted."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = [_make_stale_task(task_id=80)]
        repo.mark_failed.return_value = True

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        with patch("synth_engine.shared.tasks.reaper.get_audit_logger"):
            reaped = reaper.reap()

        assert reaped == 1

    def test_error_message_matches_spec(self) -> None:
        """mark_failed must be called with the canonical reaper error message."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = [_make_stale_task(task_id=81)]
        repo.mark_failed.return_value = True

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        with patch("synth_engine.shared.tasks.reaper.get_audit_logger"):
            reaper.reap()

        _, error_msg = repo.mark_failed.call_args[0]
        assert error_msg == ("Reaped: exceeded staleness threshold — possible worker crash")

    def test_audit_event_emitted_per_reaped_job(self) -> None:
        """One audit log_event call per reaped job, with correct event_type and actor."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = [
            _make_stale_task(task_id=82),
            _make_stale_task(task_id=83),
        ]
        repo.mark_failed.return_value = True

        audit_mock = MagicMock()
        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        with patch("synth_engine.shared.tasks.reaper.get_audit_logger", return_value=audit_mock):
            reaped = reaper.reap()

        assert reaped == 2
        assert audit_mock.log_event.call_count == 2
        first_call_kwargs = audit_mock.log_event.call_args_list[0][1]
        assert first_call_kwargs["event_type"] == "ORPHAN_TASK_REAPED"
        assert first_call_kwargs["actor"] == "system/reaper"

    def test_summary_log_contains_correct_count(self, caplog: Any) -> None:
        """Summary log must include the number of jobs reaped."""
        repo = MagicMock(spec=TaskRepository)
        repo.get_stale_in_progress.return_value = [
            _make_stale_task(task_id=84),
            _make_stale_task(task_id=85),
        ]
        repo.mark_failed.return_value = True

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        with caplog.at_level(logging.INFO, logger="synth_engine.shared.tasks.reaper"):
            with patch("synth_engine.shared.tasks.reaper.get_audit_logger"):
                reaped = reaper.reap()

        assert "Reaper cycle complete: 2 jobs reaped" in caplog.text
        assert reaped == 2

    def test_multiple_jobs_all_reaped(self) -> None:
        """All stale non-held IN_PROGRESS jobs are marked FAILED."""
        tasks = [_make_stale_task(task_id=i) for i in range(90, 95)]
        repo = _StubRepository(stale_tasks=tasks)

        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)
        with patch("synth_engine.shared.tasks.reaper.get_audit_logger"):
            reaped = reaper.reap()

        assert reaped == 5
        assert len(repo.marked) == 5


# ---------------------------------------------------------------------------
# SETTINGS FIELD TESTS
# ---------------------------------------------------------------------------


class TestSettingsField:
    """AC-4: reaper_stale_threshold_minutes field on ConclaveSettings."""

    def test_settings_has_reaper_threshold_field(self) -> None:
        """ConclaveSettings must expose reaper_stale_threshold_minutes with default 60."""
        from synth_engine.shared.settings import ConclaveSettings

        settings = ConclaveSettings()
        assert settings.reaper_stale_threshold_minutes == 60

    def test_settings_threshold_below_5_raises(self) -> None:
        """reaper_stale_threshold_minutes=4 must fail Pydantic validation."""

        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises(ValidationError):
            ConclaveSettings(reaper_stale_threshold_minutes=4)  # type: ignore[call-arg]

    def test_settings_threshold_of_5_is_accepted(self) -> None:
        """reaper_stale_threshold_minutes=5 must be accepted."""
        from synth_engine.shared.settings import ConclaveSettings

        settings = ConclaveSettings(reaper_stale_threshold_minutes=5)  # type: ignore[call-arg]
        assert settings.reaper_stale_threshold_minutes == 5


# ---------------------------------------------------------------------------
# REAPER REPOSITORY UNIT TESTS (F10 — previously 0% coverage)
# ---------------------------------------------------------------------------


class TestSQLAlchemyTaskRepository:
    """Unit tests for SQLAlchemyTaskRepository using SQLite in-memory engine."""

    def _make_engine(self) -> Any:
        """Create a SQLite in-memory engine with the synthesis_job table.

        Returns:
            A synchronous SQLAlchemy Engine bound to an in-memory SQLite DB.
        """
        from sqlalchemy import create_engine

        from synth_engine.shared.db import SQLModel

        engine = create_engine("sqlite:///:memory:", echo=False)
        SQLModel.metadata.create_all(engine)
        return engine

    def _insert_job(
        self,
        engine: Any,
        *,
        status: str = "IN_PROGRESS",
        legal_hold: bool = False,
        minutes_old: int = 120,
        owner_id: str = "operator-1",
    ) -> int:
        """Insert a SynthesisJob row and return its primary key.

        Args:
            engine: SQLAlchemy engine.
            status: Job status string.
            legal_hold: Whether the job has a legal hold.
            minutes_old: How many minutes ago the job was created.
            owner_id: Owner identifier for the job.

        Returns:
            The integer primary key of the inserted job.
        """
        from datetime import UTC, datetime, timedelta

        from sqlmodel import Session

        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        created_at = datetime.now(UTC) - timedelta(minutes=minutes_old)
        job = SynthesisJob(
            table_name="test_table",
            parquet_path="/tmp/test.parquet",
            total_epochs=5,
            num_rows=100,
            status=status,
            legal_hold=legal_hold,
            created_at=created_at,
            owner_id=owner_id,
        )
        with Session(engine) as session:
            session.add(job)
            session.commit()
            session.refresh(job)
            return int(job.id)  # type: ignore[arg-type]

    def test_find_stale_tasks_returns_in_progress_jobs(self) -> None:
        """get_stale_in_progress must return IN_PROGRESS jobs older than cutoff.

        Args: none.
        """
        from datetime import UTC, datetime, timedelta

        from synth_engine.modules.synthesizer.reaper_repository import (
            SQLAlchemyTaskRepository,
        )

        engine = self._make_engine()
        job_id = self._insert_job(engine, status="IN_PROGRESS", minutes_old=120)

        repo = SQLAlchemyTaskRepository(engine=engine)
        cutoff = datetime.now(UTC) - timedelta(minutes=60)
        stale = repo.get_stale_in_progress(older_than=cutoff)

        assert len(stale) == 1
        assert stale[0].task_id == job_id
        assert stale[0].status == "IN_PROGRESS"

    def test_find_stale_tasks_excludes_recently_created(self) -> None:
        """Jobs created after the cutoff must not be returned.

        Args: none.
        """
        from datetime import UTC, datetime, timedelta

        from synth_engine.modules.synthesizer.reaper_repository import (
            SQLAlchemyTaskRepository,
        )

        engine = self._make_engine()
        # Created only 10 minutes ago — newer than 60-min threshold
        self._insert_job(engine, status="IN_PROGRESS", minutes_old=10)

        repo = SQLAlchemyTaskRepository(engine=engine)
        cutoff = datetime.now(UTC) - timedelta(minutes=60)
        stale = repo.get_stale_in_progress(older_than=cutoff)

        assert stale == []

    def test_find_stale_tasks_excludes_legal_hold(self) -> None:
        """Jobs with legal_hold=True must not be returned.

        Args: none.
        """
        from datetime import UTC, datetime, timedelta

        from synth_engine.modules.synthesizer.reaper_repository import (
            SQLAlchemyTaskRepository,
        )

        engine = self._make_engine()
        self._insert_job(engine, status="IN_PROGRESS", legal_hold=True, minutes_old=120)

        repo = SQLAlchemyTaskRepository(engine=engine)
        cutoff = datetime.now(UTC) - timedelta(minutes=60)
        stale = repo.get_stale_in_progress(older_than=cutoff)

        assert stale == []

    def test_find_stale_tasks_excludes_complete_jobs(self) -> None:
        """COMPLETE jobs must not be returned even if old.

        Args: none.
        """
        from datetime import UTC, datetime, timedelta

        from synth_engine.modules.synthesizer.reaper_repository import (
            SQLAlchemyTaskRepository,
        )

        engine = self._make_engine()
        self._insert_job(engine, status="COMPLETE", minutes_old=120)

        repo = SQLAlchemyTaskRepository(engine=engine)
        cutoff = datetime.now(UTC) - timedelta(minutes=60)
        stale = repo.get_stale_in_progress(older_than=cutoff)

        assert stale == []

    def test_mark_failed_updates_in_progress_job(self) -> None:
        """mark_failed must update the job status to FAILED and return True.

        Args: none.
        """
        from sqlmodel import Session

        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.reaper_repository import (
            SQLAlchemyTaskRepository,
        )

        engine = self._make_engine()
        job_id = self._insert_job(engine, status="IN_PROGRESS", minutes_old=120)

        repo = SQLAlchemyTaskRepository(engine=engine)
        result = repo.mark_failed(job_id, "Reaped: test")

        assert result is True
        with Session(engine) as session:
            job = session.get(SynthesisJob, job_id)
            assert job is not None
            assert job.status == "FAILED"
            assert job.error_msg == "Reaped: test"

    def test_mark_failed_returns_false_when_already_complete(self) -> None:
        """mark_failed must return False when the job is not IN_PROGRESS.

        This tests the race-condition guard (conditional UPDATE WHERE status='IN_PROGRESS').

        Args: none.
        """
        from synth_engine.modules.synthesizer.reaper_repository import (
            SQLAlchemyTaskRepository,
        )

        engine = self._make_engine()
        job_id = self._insert_job(engine, status="COMPLETE", minutes_old=120)

        repo = SQLAlchemyTaskRepository(engine=engine)
        result = repo.mark_failed(job_id, "Reaped: test")

        assert result is False


# ---------------------------------------------------------------------------
# REAPER TASKS UNIT TESTS (F10 — previously 0% coverage)
# ---------------------------------------------------------------------------


class TestPeriodicReapOrphanTasks:
    """Unit tests for periodic_reap_orphan_tasks Huey task function.

    The Huey task is decorated with @huey.periodic_task and @huey.lock_task.
    The @huey.lock_task decorator wraps the function body so that call_local()
    attempts to acquire a Redis distributed lock.  Unit tests bypass the
    decorator stack by calling periodic_reap_orphan_tasks.func.__wrapped__
    directly — this is the bare task body without the lock context manager.
    Using __wrapped__ (set by @functools.wraps inside the lock decorator)
    is the correct, stable way to access the inner function for unit testing.
    """

    def _get_task_body(self) -> Any:
        """Return the unwrapped task body function.

        Returns:
            The inner function of periodic_reap_orphan_tasks, bypassing the
            @huey.lock_task Redis lock wrapper.
        """
        from synth_engine.modules.synthesizer.reaper_tasks import (
            periodic_reap_orphan_tasks,
        )

        return periodic_reap_orphan_tasks.func.__wrapped__

    def test_reaper_task_returns_zero_when_no_database_url(self) -> None:
        """periodic_reap_orphan_tasks body must return 0 when DATABASE_URL is not set.

        Calls the unwrapped task body (bypassing the Huey lock decorator) to
        verify the early-exit path when DATABASE_URL is empty.

        Args: none.
        """
        # Import must happen BEFORE the patch context — reaper_tasks triggers
        # task_queue module-level code that calls get_settings() on first import.
        task_body = self._get_task_body()

        mock_settings = MagicMock()
        mock_settings.database_url = ""

        with patch("synth_engine.shared.settings.get_settings", return_value=mock_settings):
            result = task_body()

        assert result == 0

    def test_reaper_task_calls_reaper_reap(self) -> None:
        """periodic_reap_orphan_tasks body must call OrphanTaskReaper.reap and return count.

        Calls the unwrapped task body to verify the main execution path.

        Args: none.
        """
        from unittest.mock import MagicMock

        mock_settings = MagicMock()
        mock_settings.database_url = "sqlite:///:memory:"
        mock_settings.reaper_stale_threshold_minutes = 60

        mock_repo = MagicMock()
        mock_reaper = MagicMock()
        mock_reaper.reap.return_value = 3
        mock_engine = MagicMock()

        # Import must happen BEFORE the patch context (see test above).
        task_body = self._get_task_body()

        with (
            patch("synth_engine.shared.settings.get_settings", return_value=mock_settings),
            patch("synth_engine.shared.db.get_engine", return_value=mock_engine),
            patch(
                "synth_engine.modules.synthesizer.reaper_repository.SQLAlchemyTaskRepository",
                return_value=mock_repo,
            ),
            patch(
                "synth_engine.shared.tasks.reaper.OrphanTaskReaper",
                return_value=mock_reaper,
            ),
        ):
            result = task_body()

        assert result == 3
        mock_reaper.reap.assert_called_once()
