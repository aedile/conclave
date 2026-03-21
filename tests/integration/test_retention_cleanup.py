"""Integration tests for data retention cleanup — T41.1.

These tests exercise the full retention pipeline with a real in-memory SQLite
database. They verify the correct end-to-end behavior of:

- Expired jobs being purged.
- Non-expired jobs being retained.
- Legal-held jobs surviving beyond TTL.
- Audit events emitted per deletion.
- Artifact file cleanup for expired jobs with output_path set.

CONSTITUTION Priority 0: Security — data is deleted, not leaked
CONSTITUTION Priority 3: TDD
Task: T41.1 — Implement Data Retention Policy
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from synth_engine.modules.synthesizer.job_models import SynthesisJob

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine() -> Any:
    """Create an in-memory SQLite engine for testing.

    Returns:
        SQLAlchemy engine backed by in-memory SQLite.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _backdate_job(session: Session, job_id: int, days: int) -> None:
    """Move a job's created_at timestamp backward by ``days`` days.

    Uses a bound parameter to avoid SQL injection (S608).

    Args:
        session: Active database session.
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


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestRetentionCleanupIntegration:
    """Full pipeline retention cleanup tests using in-memory SQLite."""

    def test_expired_jobs_deleted_and_fresh_jobs_retained(self) -> None:
        """End-to-end: expired jobs deleted, fresh jobs retained."""
        from synth_engine.modules.synthesizer.retention import RetentionCleanup

        engine = _make_engine()

        with Session(engine) as session:
            old = SynthesisJob(
                table_name="old_table",
                parquet_path="/tmp/old.parquet",
                total_epochs=1,
                num_rows=5,
                status="COMPLETE",
            )
            fresh = SynthesisJob(
                table_name="fresh_table",
                parquet_path="/tmp/fresh.parquet",
                total_epochs=1,
                num_rows=5,
                status="COMPLETE",
            )
            session.add_all([old, fresh])
            session.commit()
            session.refresh(old)
            _backdate_job(session, old.id, 100)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 1
        audit_mock.log_event.assert_called_once()
        call_kwargs = audit_mock.log_event.call_args.kwargs
        assert call_kwargs["event_type"] == "JOB_RETENTION_PURGE"

        with Session(engine) as session:
            remaining = session.exec(select(SynthesisJob)).all()
            assert len(remaining) == 1
            assert remaining[0].table_name == "fresh_table"

    def test_legal_hold_survives_beyond_ttl(self) -> None:
        """A legal-held job is not deleted even when 500 days old."""
        from synth_engine.modules.synthesizer.retention import RetentionCleanup

        engine = _make_engine()

        with Session(engine) as session:
            held = SynthesisJob(
                table_name="held_table",
                parquet_path="/tmp/held.parquet",
                total_epochs=1,
                num_rows=5,
                status="COMPLETE",
                legal_hold=True,
            )
            session.add(held)
            session.commit()
            session.refresh(held)
            _backdate_job(session, held.id, 500)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 0
        audit_mock.log_event.assert_not_called()

        with Session(engine) as session:
            jobs = session.exec(select(SynthesisJob)).all()
            assert len(jobs) == 1
            assert jobs[0].legal_hold is True

    def test_artifact_file_deleted_for_expired_job(self, tmp_path: Path) -> None:
        """Artifact file on disk is removed when its job is purged."""
        from synth_engine.modules.synthesizer.retention import RetentionCleanup

        engine = _make_engine()

        artifact = tmp_path / "artifact.parquet"
        artifact.write_text("parquet-data")

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path=str(tmp_path / "source.parquet"),
                total_epochs=1,
                num_rows=5,
                status="COMPLETE",
                output_path=str(artifact),
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            _backdate_job(session, job.id, 100)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 1
        assert not artifact.exists(), "Artifact file should have been deleted"

    def test_artifact_file_missing_does_not_raise(self, tmp_path: Path) -> None:
        """Cleanup succeeds even if the artifact file was already removed."""
        from synth_engine.modules.synthesizer.retention import RetentionCleanup

        engine = _make_engine()

        missing_path = str(tmp_path / "nonexistent.parquet")

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path=str(tmp_path / "source.parquet"),
                total_epochs=1,
                num_rows=5,
                status="COMPLETE",
                output_path=missing_path,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            _backdate_job(session, job.id, 100)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            # Must not raise
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 1

    def test_audit_log_includes_job_id_and_table_name(self) -> None:
        """Audit event details include job_id and table_name (no PII)."""
        from synth_engine.modules.synthesizer.retention import RetentionCleanup

        engine = _make_engine()

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="patients",
                parquet_path="/tmp/p.parquet",
                total_epochs=1,
                num_rows=5,
                status="COMPLETE",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            recorded_id = job.id
            _backdate_job(session, recorded_id, 100)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            cleanup.cleanup_expired_jobs()

        call_kwargs = audit_mock.log_event.call_args.kwargs
        assert call_kwargs["details"]["job_id"] == str(recorded_id)
        assert call_kwargs["details"]["table_name"] == "patients"
