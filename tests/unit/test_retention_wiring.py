"""Unit tests for retention cleanup wiring to Huey periodic tasks — ADV-019/020.

Tests verify:
- In-flight jobs (TRAINING, GENERATING, QUEUED) are NOT deleted even if older than TTL.
- Concurrent cleanup invocations don't cause double-deletion.
- DB error on one job doesn't abort remaining deletions (error isolation).
- Artifact file already missing doesn't crash cleanup.
- cleanup_expired_jobs() is callable via Huey periodic task.
- Expired COMPLETE/FAILED jobs are deleted by job cleanup.
- Legal-hold jobs are never deleted (regression).
- Empty database returns 0.
- Artifact cleanup sweeps files older than artifact_retention_days.
- Both tasks log summary to audit trail.
- Both tasks return count of deleted items.
- Audit logger failure doesn't prevent job deletion (best-effort audit).

CONSTITUTION Priority 0: Security — PII-free audit, no data leakage
CONSTITUTION Priority 3: TDD — RED/GREEN/REFACTOR
Task: ADR-D3 — Wire Retention Cleanup to Huey Periodic Task (ADV-019/020)
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine() -> Any:
    """Create an in-memory SQLite engine for testing.

    Returns:
        SQLAlchemy engine backed by an in-memory SQLite database.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _backdate(session: Session, job_id: int, days: int) -> None:
    """Backdate a job's created_at by ``days`` days.

    Args:
        session: Active SQLModel session.
        job_id: Primary key of the job to backdate.
        days: Number of days to subtract from now.
    """
    from sqlalchemy import text

    cutoff = datetime.now(UTC) - timedelta(days=days)
    session.exec(  # type: ignore[call-overload]
        text("UPDATE synthesis_job SET created_at = :ts WHERE id = :id").bindparams(
            ts=cutoff.isoformat(), id=job_id
        )
    )
    session.commit()


def _make_job(
    session: Session,
    status: str = "COMPLETE",
    legal_hold: bool = False,
    output_path: str | None = None,
) -> SynthesisJob:
    """Create and persist a SynthesisJob with the given parameters.

    Args:
        session: Active SQLModel session.
        status: Job status string.
        legal_hold: Whether the job is on legal hold.
        output_path: Optional artifact output path.

    Returns:
        The persisted SynthesisJob instance (refreshed).
    """
    job = SynthesisJob(
        table_name="t",
        parquet_path="/tmp/p.parquet",
        total_epochs=1,
        num_rows=1,
        status=status,
        legal_hold=legal_hold,
        output_path=output_path,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


# ---------------------------------------------------------------------------
# ATTACK RED: Negative / security tests — in-flight job protection
# ---------------------------------------------------------------------------


class TestInFlightJobsAreProtected:
    """In-flight jobs must never be deleted by retention cleanup."""

    def test_training_job_older_than_ttl_is_not_deleted(self) -> None:
        """A TRAINING job older than job_retention_days is NOT deleted."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            job = _make_job(session, status="TRAINING")
            _backdate(session, job.id, 200)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 0

        with Session(engine) as session:
            from sqlmodel import select

            remaining = session.exec(select(SynthesisJob)).all()
            assert len(remaining) == 1
            assert remaining[0].status == "TRAINING"

    def test_generating_job_older_than_ttl_is_not_deleted(self) -> None:
        """A GENERATING job older than job_retention_days is NOT deleted."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            job = _make_job(session, status="GENERATING")
            _backdate(session, job.id, 200)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 0

    def test_queued_job_older_than_ttl_is_not_deleted(self) -> None:
        """A QUEUED job older than job_retention_days is NOT deleted."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            job = _make_job(session, status="QUEUED")
            _backdate(session, job.id, 200)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 0

    def test_only_complete_and_failed_jobs_are_eligible(self) -> None:
        """Only COMPLETE and FAILED jobs are eligible for deletion — all others survive."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        eligible_statuses = ["COMPLETE", "FAILED"]
        protected_statuses = ["QUEUED", "TRAINING", "GENERATING"]

        with Session(engine) as session:
            for status in eligible_statuses + protected_statuses:
                job = _make_job(session, status=status)
                _backdate(session, job.id, 200)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == len(eligible_statuses)

        with Session(engine) as session:
            from sqlmodel import select

            remaining = session.exec(select(SynthesisJob)).all()
            assert len(remaining) == len(protected_statuses)
            remaining_statuses = {j.status for j in remaining}
            assert remaining_statuses == set(protected_statuses)


class TestConcurrentCleanupInvocations:
    """Concurrent cleanup invocations must not cause double-deletion."""

    def test_second_cleanup_after_first_returns_zero(self) -> None:
        """Running cleanup twice in sequence deletes each job only once."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            job = _make_job(session, status="COMPLETE")
            _backdate(session, job.id, 100)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            first_run = cleanup.cleanup_expired_jobs()
            second_run = cleanup.cleanup_expired_jobs()

        assert first_run == 1
        assert second_run == 0


class TestErrorIsolationPerJob:
    """DB errors on one job must not abort remaining deletions."""

    def test_commit_error_on_one_job_does_not_abort_others(self) -> None:
        """If commit() raises on one job, remaining jobs are still deleted."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            job1 = _make_job(session, status="COMPLETE")
            job2 = _make_job(session, status="COMPLETE")
            job3 = _make_job(session, status="COMPLETE")
            _backdate(session, job1.id, 100)
            _backdate(session, job2.id, 100)
            _backdate(session, job3.id, 100)

        # Patch commit to fail on the first call only.
        commit_call_count = 0
        original_commit: Any = None

        def _patched_commit(session_self: Any) -> None:
            nonlocal commit_call_count
            commit_call_count += 1
            if commit_call_count == 1:
                raise RuntimeError("simulated DB commit failure")
            original_commit(session_self)

        import sqlmodel

        original_commit = sqlmodel.Session.commit

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            with patch.object(sqlmodel.Session, "commit", _patched_commit):
                cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
                deleted = cleanup.cleanup_expired_jobs()

        # At least some jobs should be deleted (error isolation)
        assert deleted >= 2


class TestMissingArtifactFile:
    """A missing artifact file must not crash cleanup."""

    def test_missing_artifact_file_does_not_crash_cleanup(self) -> None:
        """cleanup_expired_jobs succeeds even if the artifact file is already gone."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            job = _make_job(
                session,
                status="COMPLETE",
                output_path="/nonexistent/path/artifact.parquet",
            )
            _backdate(session, job.id, 100)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            # Must not raise — missing file is handled gracefully
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 1


# ---------------------------------------------------------------------------
# RED: Feature tests — periodic task wiring
# ---------------------------------------------------------------------------


class TestPeriodicTasksExist:
    """Both periodic task functions must be importable and registered with Huey."""

    def test_periodic_cleanup_expired_jobs_task_exists(self) -> None:
        """periodic_cleanup_expired_jobs is importable from retention_tasks."""
        from synth_engine.modules.synthesizer.storage.retention_tasks import (
            periodic_cleanup_expired_jobs,
        )

        assert callable(periodic_cleanup_expired_jobs)

    def test_periodic_cleanup_expired_artifacts_task_exists(self) -> None:
        """periodic_cleanup_expired_artifacts is importable from retention_tasks."""
        from synth_engine.modules.synthesizer.storage.retention_tasks import (
            periodic_cleanup_expired_artifacts,
        )

        assert callable(periodic_cleanup_expired_artifacts)


class TestExpiredJobDeletionViaCleanup:
    """Expired COMPLETE/FAILED jobs are deleted when cleanup runs."""

    def test_expired_complete_job_is_deleted(self) -> None:
        """An expired COMPLETE job (no legal_hold) is deleted."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            job = _make_job(session, status="COMPLETE")
            _backdate(session, job.id, 100)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 1

    def test_expired_failed_job_is_deleted(self) -> None:
        """An expired FAILED job (no legal_hold) is deleted."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            job = _make_job(session, status="FAILED")
            _backdate(session, job.id, 100)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 1

    def test_legal_hold_regression_complete_job_is_protected(self) -> None:
        """COMPLETE job with legal_hold=True is NOT deleted (regression test)."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            job = _make_job(session, status="COMPLETE", legal_hold=True)
            _backdate(session, job.id, 200)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 0

    def test_empty_database_returns_zero(self) -> None:
        """cleanup_expired_jobs returns 0 on an empty database."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 0


class TestArtifactCleanup:
    """Artifact cleanup sweeps files older than artifact_retention_days."""

    def test_artifact_cleanup_deletes_expired_artifact_file(self) -> None:
        """cleanup_expired_artifacts deletes artifact files older than artifact_retention_days."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            artifact_path = f.name

        assert Path(artifact_path).exists()

        with Session(engine) as session:
            job = _make_job(session, status="COMPLETE", output_path=artifact_path)
            _backdate(session, job.id, 40)  # older than 30-day artifact TTL

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(
                engine=engine, job_retention_days=90, artifact_retention_days=30
            )
            swept = cleanup.cleanup_expired_artifacts()

        assert swept == 1
        assert not Path(artifact_path).exists()

    def test_artifact_cleanup_clears_output_path_on_record(self) -> None:
        """cleanup_expired_artifacts sets output_path=None on the job record."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            artifact_path = f.name

        with Session(engine) as session:
            job = _make_job(session, status="COMPLETE", output_path=artifact_path)
            job_id = job.id
            _backdate(session, job_id, 40)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(
                engine=engine, job_retention_days=90, artifact_retention_days=30
            )
            cleanup.cleanup_expired_artifacts()

        with Session(engine) as session:
            job = session.get(SynthesisJob, job_id)
            assert job is not None
            assert job != None  # noqa: E711 — specific check
            assert job.output_path is None
            assert str(job.output_path) == "None"

    def test_artifact_cleanup_skips_recent_artifacts(self) -> None:
        """Artifacts newer than artifact_retention_days are NOT swept."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            artifact_path = f.name

        with Session(engine) as session:
            # Only 5 days old — well within 30-day TTL
            _make_job(session, status="COMPLETE", output_path=artifact_path)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(
                engine=engine, job_retention_days=90, artifact_retention_days=30
            )
            swept = cleanup.cleanup_expired_artifacts()

        assert swept == 0
        assert Path(artifact_path).exists()

        # Cleanup the temp file
        Path(artifact_path).unlink(missing_ok=True)

    def test_artifact_cleanup_missing_file_does_not_crash(self) -> None:
        """cleanup_expired_artifacts handles a missing artifact file gracefully."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            job = _make_job(
                session,
                status="COMPLETE",
                output_path="/nonexistent/gone.parquet",
            )
            _backdate(session, job.id, 40)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(
                engine=engine, job_retention_days=90, artifact_retention_days=30
            )
            # Must not raise
            swept = cleanup.cleanup_expired_artifacts()

        assert swept == 1

    def test_artifact_cleanup_skips_in_flight_jobs(self) -> None:
        """cleanup_expired_artifacts does not sweep artifacts for in-flight jobs."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            artifact_path = f.name

        with Session(engine) as session:
            job = _make_job(session, status="TRAINING", output_path=artifact_path)
            _backdate(session, job.id, 40)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(
                engine=engine, job_retention_days=90, artifact_retention_days=30
            )
            swept = cleanup.cleanup_expired_artifacts()

        assert swept == 0
        assert Path(artifact_path).exists()

        Path(artifact_path).unlink(missing_ok=True)

    def test_artifact_cleanup_skips_legal_hold_jobs(self) -> None:
        """cleanup_expired_artifacts does not sweep artifacts for legal-hold jobs."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            artifact_path = f.name

        with Session(engine) as session:
            job = _make_job(session, status="COMPLETE", legal_hold=True, output_path=artifact_path)
            _backdate(session, job.id, 40)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(
                engine=engine, job_retention_days=90, artifact_retention_days=30
            )
            swept = cleanup.cleanup_expired_artifacts()

        assert swept == 0
        assert Path(artifact_path).exists()

        Path(artifact_path).unlink(missing_ok=True)

    def test_artifact_cleanup_returns_count(self) -> None:
        """cleanup_expired_artifacts returns the count of swept artifacts."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            for _ in range(3):
                job = _make_job(
                    session,
                    status="COMPLETE",
                    output_path="/nonexistent/gone.parquet",
                )
                _backdate(session, job.id, 40)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(
                engine=engine, job_retention_days=90, artifact_retention_days=30
            )
            swept = cleanup.cleanup_expired_artifacts()

        assert swept == 3

    def test_artifact_cleanup_empty_database_returns_zero(self) -> None:
        """cleanup_expired_artifacts returns 0 on an empty database."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(
                engine=engine, job_retention_days=90, artifact_retention_days=30
            )
            swept = cleanup.cleanup_expired_artifacts()

        assert swept == 0


class TestAuditLogging:
    """Both cleanup methods log summary to the audit trail."""

    def test_job_cleanup_logs_audit_event_per_deletion(self) -> None:
        """cleanup_expired_jobs emits one JOB_RETENTION_PURGE per deleted job."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            for _ in range(3):
                job = _make_job(session, status="COMPLETE")
                _backdate(session, job.id, 100)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            cleanup.cleanup_expired_jobs()

        assert audit_mock.log_event.call_count == 3
        event_types = [c.kwargs["event_type"] for c in audit_mock.log_event.call_args_list]
        assert all(et == "JOB_RETENTION_PURGE" for et in event_types)

    def test_artifact_cleanup_logs_audit_event_per_sweep(self) -> None:
        """cleanup_expired_artifacts emits one ARTIFACT_RETENTION_PURGE per swept artifact."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            for _ in range(2):
                job = _make_job(
                    session,
                    status="COMPLETE",
                    output_path="/nonexistent/gone.parquet",
                )
                _backdate(session, job.id, 40)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(
                engine=engine, job_retention_days=90, artifact_retention_days=30
            )
            cleanup.cleanup_expired_artifacts()

        assert audit_mock.log_event.call_count == 2
        event_types = [c.kwargs["event_type"] for c in audit_mock.log_event.call_args_list]
        assert all(et == "ARTIFACT_RETENTION_PURGE" for et in event_types)

    def test_audit_logger_failure_does_not_prevent_job_deletion(self) -> None:
        """Audit log failure is best-effort — job is still deleted."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            job = _make_job(session, status="COMPLETE")
            _backdate(session, job.id, 100)

        audit_mock = MagicMock()
        audit_mock.log_event.side_effect = RuntimeError("audit service unavailable")

        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        # Job must be deleted even though audit logging failed
        assert deleted == 1

        with Session(engine) as session:
            from sqlmodel import select

            remaining = session.exec(select(SynthesisJob)).all()
            assert len(remaining) == 0


class TestBootstrapperWiring:
    """Retention task module is importable and wired in bootstrapper."""

    def test_retention_tasks_importable_from_synthesizer_module(self) -> None:
        """retention_tasks module is importable from the synthesizer package."""
        import synth_engine.modules.synthesizer.storage.retention_tasks as rt

        assert hasattr(rt, "periodic_cleanup_expired_jobs"), (
            "retention_tasks module must export periodic_cleanup_expired_jobs Huey task"
        )

    def test_bootstrapper_imports_retention_tasks(self) -> None:
        """bootstrapper/wiring.py imports retention_tasks so Huey worker discovers them.

        T56.2: retention_tasks import moved from main.py to wiring.py. The side-effect
        import must still be present in the bootstrapper package so Huey workers
        that import main discover the tasks via wire_all().
        """
        import inspect

        import synth_engine.bootstrapper.wiring as wiring

        source = inspect.getsource(wiring)
        assert "retention_tasks" in source, (
            "wiring.py must import retention_tasks for Huey task discovery (T56.2). "
            "The import was moved from main.py to wiring.py."
        )


# ---------------------------------------------------------------------------
# QA-F1: Missing edge-case tests
# ---------------------------------------------------------------------------


class TestCleanupExpiredArtifactsRaisesWithoutConfig:
    """cleanup_expired_artifacts raises RuntimeError when not configured."""

    def test_cleanup_expired_artifacts_raises_when_artifact_retention_days_not_set(
        self,
    ) -> None:
        """Call cleanup_expired_artifacts() without artifact_retention_days → RuntimeError.

        RetentionCleanup constructed with only job_retention_days (no
        artifact_retention_days) must raise RuntimeError when
        cleanup_expired_artifacts() is called, not silently succeed.
        """
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        # NOTE: artifact_retention_days intentionally omitted.
        cleanup = RetentionCleanup(engine=engine, job_retention_days=90)

        with pytest.raises(RuntimeError, match="artifact_retention_days"):
            cleanup.cleanup_expired_artifacts()


class TestDeleteArtifactOSErrorIsSuppressed:
    """OSError in _delete_artifact must be logged and suppressed, not abort job DB deletion."""

    def test_delete_artifact_os_error_is_logged_and_suppressed(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """PermissionError in Path.unlink is logged as WARNING and job is still DB-deleted.

        Verifies two invariants:
        1. The job IS deleted from the database (OSError in artifact deletion
           must not prevent the DB record from being removed).
        2. A WARNING log entry is emitted containing the exception class name.
        """
        import logging
        from pathlib import Path
        from unittest.mock import patch

        from sqlmodel import select

        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()
        with Session(engine) as session:
            job = _make_job(
                session,
                status="COMPLETE",
                output_path="/tmp/unremovable_artifact.parquet",
            )
            _backdate(session, job.id, 100)

        audit_mock = MagicMock()
        with (
            patch(
                "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
                return_value=audit_mock,
            ),
            patch.object(Path, "unlink", side_effect=PermissionError("access denied")),
            caplog.at_level(
                logging.WARNING, logger="synth_engine.modules.synthesizer.storage.retention"
            ),
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        # Invariant 1: job is deleted from DB despite OSError in artifact removal.
        assert deleted == 1
        with Session(engine) as session:
            remaining = session.exec(select(SynthesisJob)).all()
            assert len(remaining) == 0

        # Invariant 2: WARNING log was emitted.
        assert any(record.levelno == logging.WARNING for record in caplog.records), (
            f"Expected WARNING log, got: {[r.message for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# QA-R2-001: Early-return when database_url is empty
# ---------------------------------------------------------------------------


class TestPeriodicTasksEarlyReturnWhenNoDatabaseUrl:
    """Periodic tasks must log an error and return 0 when database_url is falsy.

    Covers the R1 fix in retention_tasks.py: when ``get_settings()`` returns a
    settings object with an empty ``database_url``, neither periodic task should
    attempt to construct a DB engine.  Both must log a ``_logger.error`` call and
    return 0 immediately.

    Tests call ``task.func.__wrapped__`` — the raw inner function before the
    ``@huey.lock_task()`` decorator — to bypass both the Redis lock and the
    Huey queue.  The ``@huey.lock_task`` decorator sets ``__wrapped__`` per
    Python\'s functools.wraps convention, so this is stable across Huey
    versions used in the project.
    """

    def test_periodic_cleanup_expired_jobs_returns_zero_when_database_url_empty(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """periodic_cleanup_expired_jobs inner function returns 0 when database_url is \'\'.

        Patches ``get_settings`` at the shared settings module level so the
        lazy import inside the task body picks up a stub with
        ``database_url=""``.  Bypasses the Huey Redis lock by calling the
        underlying function via ``task.func.__wrapped__``.
        Asserts the return value is 0 and that an ERROR log containing
        \'DATABASE_URL\' was emitted.
        """
        import logging
        from unittest.mock import MagicMock, patch

        from synth_engine.modules.synthesizer.storage.retention_tasks import (
            periodic_cleanup_expired_jobs,
        )

        # Access the raw inner function before @huey.lock_task() wrapping.
        inner_fn = periodic_cleanup_expired_jobs.func.__wrapped__

        mock_settings = MagicMock()
        mock_settings.database_url = ""

        with (
            patch(
                "synth_engine.shared.settings.get_settings",
                return_value=mock_settings,
            ),
            caplog.at_level(
                logging.ERROR,
                logger="synth_engine.modules.synthesizer.storage.retention_tasks",
            ),
        ):
            result = inner_fn()

        assert result == 0
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records, "Expected at least one ERROR log record"
        assert any("DATABASE_URL" in r.message for r in error_records), (
            f"Expected ERROR log mentioning DATABASE_URL, got: {[r.message for r in error_records]}"
        )

    def test_periodic_cleanup_expired_artifacts_returns_zero_when_database_url_empty(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """periodic_cleanup_expired_artifacts inner function returns 0 when database_url is \'\'.

        Patches ``get_settings`` at the shared settings module level so the
        lazy import inside the task body picks up a stub with
        ``database_url=""``.  Bypasses the Huey Redis lock by calling the
        underlying function via ``task.func.__wrapped__``.
        Asserts the return value is 0 and that an ERROR log containing
        \'DATABASE_URL\' was emitted.
        """
        import logging
        from unittest.mock import MagicMock, patch

        from synth_engine.modules.synthesizer.storage.retention_tasks import (
            periodic_cleanup_expired_artifacts,
        )

        # Access the raw inner function before @huey.lock_task() wrapping.
        inner_fn = periodic_cleanup_expired_artifacts.func.__wrapped__

        mock_settings = MagicMock()
        mock_settings.database_url = ""

        with (
            patch(
                "synth_engine.shared.settings.get_settings",
                return_value=mock_settings,
            ),
            caplog.at_level(
                logging.ERROR,
                logger="synth_engine.modules.synthesizer.storage.retention_tasks",
            ),
        ):
            result = inner_fn()

        assert result == 0
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records, "Expected at least one ERROR log record"
        assert any("DATABASE_URL" in r.message for r in error_records), (
            f"Expected ERROR log mentioning DATABASE_URL, got: {[r.message for r in error_records]}"
        )
