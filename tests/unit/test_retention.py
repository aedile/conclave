"""Unit tests for data retention policy — T41.1.

Tests verify:
- ConclaveSettings has retention period fields with correct defaults.
- RetentionCleanup.cleanup_expired_jobs() deletes expired jobs.
- RetentionCleanup.cleanup_expired_jobs() retains non-expired jobs.
- Legal-hold flag prevents deletion regardless of TTL.
- Audit events are never deleted within retention period.
- All deletions are logged to the audit trail.
- Legal hold can be toggled via admin endpoint helper.

CONSTITUTION Priority 0: Security — PII-free audit, no data leakage
CONSTITUTION Priority 3: TDD — RED/GREEN/REFACTOR
Task: T41.1 — Implement Data Retention Policy
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    """Backdate a job's created_at by ``days`` days using a bound parameter.

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


# ---------------------------------------------------------------------------
# Settings tests
# ---------------------------------------------------------------------------


class TestRetentionSettings:
    """Tests for ConclaveSettings retention period fields."""

    def test_job_retention_days_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """job_retention_days defaults to 90."""
        monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
        monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
        monkeypatch.delenv("JOB_RETENTION_DAYS", raising=False)

        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.job_retention_days == 90

    def test_audit_retention_days_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """audit_retention_days defaults to 1095 (3 years)."""
        monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
        monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
        monkeypatch.delenv("AUDIT_RETENTION_DAYS", raising=False)

        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.audit_retention_days == 1095

    def test_artifact_retention_days_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """artifact_retention_days defaults to 30."""
        monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
        monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
        monkeypatch.delenv("ARTIFACT_RETENTION_DAYS", raising=False)

        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.artifact_retention_days == 30

    def test_job_retention_days_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JOB_RETENTION_DAYS overrides the default."""
        monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
        monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
        monkeypatch.setenv("JOB_RETENTION_DAYS", "180")

        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.job_retention_days == 180

    def test_audit_retention_days_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AUDIT_RETENTION_DAYS overrides the default."""
        monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
        monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "2555")

        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.audit_retention_days == 2555

    def test_artifact_retention_days_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ARTIFACT_RETENTION_DAYS overrides the default."""
        monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
        monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
        monkeypatch.setenv("ARTIFACT_RETENTION_DAYS", "60")

        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.artifact_retention_days == 60

    def test_job_retention_days_rejects_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Setting JOB_RETENTION_DAYS=0 raises ValidationError."""
        monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
        monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
        monkeypatch.setenv("JOB_RETENTION_DAYS", "0")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises(ValidationError):
            ConclaveSettings()

    def test_retention_days_rejects_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Setting AUDIT_RETENTION_DAYS to a negative value raises ValidationError."""
        monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
        monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "-1")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises(ValidationError):
            ConclaveSettings()


# ---------------------------------------------------------------------------
# SynthesisJob model tests
# ---------------------------------------------------------------------------


class TestSynthesisJobLegalHold:
    """Tests for SynthesisJob.legal_hold field."""

    def test_legal_hold_defaults_false(self) -> None:
        """SynthesisJob.legal_hold defaults to False."""
        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/p.parquet",
            total_epochs=1,
            num_rows=1,
        )
        assert job.legal_hold is False

    def test_legal_hold_can_be_set_true(self) -> None:
        """SynthesisJob.legal_hold can be set to True at construction."""
        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/p.parquet",
            total_epochs=1,
            num_rows=1,
            legal_hold=True,
        )
        assert job.legal_hold is True

    def test_legal_hold_is_not_pii(self) -> None:
        """SynthesisJob.legal_hold is a boolean, not PII — no ALE encryption needed."""
        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/p.parquet",
            total_epochs=1,
            num_rows=1,
            legal_hold=True,
        )
        # The field must be a plain Python bool, not an encrypted wrapper
        assert isinstance(job.legal_hold, bool)

    def test_job_has_created_at_field(self) -> None:
        """SynthesisJob has a created_at field for retention TTL comparisons."""
        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/p.parquet",
            total_epochs=1,
            num_rows=1,
        )
        # created_at should exist and be a datetime (or None before DB insert)
        assert hasattr(job, "created_at")


# ---------------------------------------------------------------------------
# RetentionCleanup unit tests (DB-level with SQLite)
# ---------------------------------------------------------------------------


class TestRetentionCleanupExpiredJobs:
    """Tests for RetentionCleanup.cleanup_expired_jobs() behaviour."""

    def test_expired_job_is_deleted(self) -> None:
        """A job older than job_retention_days is deleted."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/p.parquet",
                total_epochs=1,
                num_rows=1,
                status="COMPLETE",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            _backdate(session, job.id, 100)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 1

        with Session(engine) as session:
            from sqlmodel import select

            jobs = session.exec(select(SynthesisJob)).all()
            assert len(jobs) == 0

    def test_non_expired_job_is_retained(self) -> None:
        """A job younger than job_retention_days is NOT deleted."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/p.parquet",
                total_epochs=1,
                num_rows=1,
                status="COMPLETE",
            )
            session.add(job)
            session.commit()

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

            jobs = session.exec(select(SynthesisJob)).all()
            assert len(jobs) == 1

    def test_legal_hold_prevents_deletion(self) -> None:
        """A job with legal_hold=True is NOT deleted even if expired."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/p.parquet",
                total_epochs=1,
                num_rows=1,
                status="COMPLETE",
                legal_hold=True,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
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

            jobs = session.exec(select(SynthesisJob)).all()
            assert len(jobs) == 1
            assert jobs[0].legal_hold is True

    def test_deletion_emits_audit_event(self) -> None:
        """Each deleted job emits a JOB_RETENTION_PURGE audit event."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()

        with Session(engine) as session:
            for _ in range(2):
                job = SynthesisJob(
                    table_name="t",
                    parquet_path="/tmp/p.parquet",
                    total_epochs=1,
                    num_rows=1,
                    status="COMPLETE",
                )
                session.add(job)
            session.commit()

            # Backdate both jobs
            from sqlalchemy import text

            cutoff = datetime.now(UTC) - timedelta(days=100)
            session.exec(  # type: ignore[call-overload]
                text("UPDATE synthesis_job SET created_at = :ts").bindparams(ts=cutoff.isoformat())
            )
            session.commit()

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 2
        assert audit_mock.log_event.call_count == 2
        for call_args in audit_mock.log_event.call_args_list:
            assert call_args.kwargs["event_type"] == "JOB_RETENTION_PURGE"

    def test_no_audit_event_when_nothing_deleted(self) -> None:
        """No audit event is emitted when no jobs are deleted."""
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
        audit_mock.log_event.assert_not_called()

    def test_mixed_expired_and_held_jobs(self) -> None:
        """Only un-held expired jobs are deleted; held and fresh jobs survive."""
        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = _make_engine()

        with Session(engine) as session:
            # Expired, no hold — will be deleted
            expired_free = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/p.parquet",
                total_epochs=1,
                num_rows=1,
                status="COMPLETE",
                legal_hold=False,
            )
            # Expired, held — must NOT be deleted
            expired_held = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/p.parquet",
                total_epochs=1,
                num_rows=1,
                status="COMPLETE",
                legal_hold=True,
            )
            # Fresh, no hold — must NOT be deleted
            fresh = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/p.parquet",
                total_epochs=1,
                num_rows=1,
                status="COMPLETE",
                legal_hold=False,
            )
            session.add_all([expired_free, expired_held, fresh])
            session.commit()

            # Backdate only the two expired jobs
            for job_ref in [expired_free, expired_held]:
                session.refresh(job_ref)
                _backdate(session, job_ref.id, 150)

        audit_mock = MagicMock()
        with patch(
            "synth_engine.modules.synthesizer.storage.retention.get_audit_logger",
            return_value=audit_mock,
        ):
            cleanup = RetentionCleanup(engine=engine, job_retention_days=90)
            deleted = cleanup.cleanup_expired_jobs()

        assert deleted == 1

        with Session(engine) as session:
            from sqlmodel import select

            remaining = session.exec(select(SynthesisJob)).all()
            assert len(remaining) == 2
            remaining_hold_flags = {j.legal_hold for j in remaining}
            # One held, one fresh (not held) remain
            assert True in remaining_hold_flags
            assert False in remaining_hold_flags


# ---------------------------------------------------------------------------
# _delete_artifact OSError path tests
# ---------------------------------------------------------------------------


class TestDeleteArtifactOSError:
    """Tests for RetentionCleanup._delete_artifact() OSError handling."""

    def test_oserror_does_not_raise(self) -> None:
        """_delete_artifact does not raise when Path.unlink raises PermissionError."""
        from pathlib import Path

        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = MagicMock()
        cleanup = RetentionCleanup(engine=engine, job_retention_days=90)

        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/some_artifact.parquet",
            output_path="/tmp/some_artifact.parquet",
            total_epochs=1,
            num_rows=1,
        )

        with patch.object(Path, "unlink", side_effect=PermissionError("access denied")):
            # Must not raise — best-effort deletion
            cleanup._delete_artifact(job)

    def test_oserror_logs_correct_exception_type_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_delete_artifact logs a WARNING with the correct exception class name."""
        import logging
        from pathlib import Path

        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = MagicMock()
        cleanup = RetentionCleanup(engine=engine, job_retention_days=90)

        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/some_artifact.parquet",
            output_path="/tmp/some_artifact.parquet",
            total_epochs=1,
            num_rows=1,
        )

        with caplog.at_level(
            logging.WARNING, logger="synth_engine.modules.synthesizer.storage.retention"
        ):
            with patch.object(Path, "unlink", side_effect=PermissionError("access denied")):
                cleanup._delete_artifact(job)

        assert any("PermissionError" in record.message for record in caplog.records), (
            f"Expected 'PermissionError' in warning log, got: {[r.message for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# Admin router tests
# ---------------------------------------------------------------------------


class TestLegalHoldEndpoint:
    """Tests for PATCH /admin/jobs/{id}/legal-hold endpoint."""

    def test_set_legal_hold_returns_200(self) -> None:
        """PATCH /admin/jobs/{id}/legal-hold with enable=True returns 200."""
        import os

        os.environ.setdefault("AUDIT_KEY", "aa" * 32)
        os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import StaticPool
        from sqlmodel import Session, SQLModel, create_engine

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.routers.admin import router as admin_router

        app = FastAPI()
        app.include_router(admin_router)

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

        # Create a job
        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/p.parquet",
                total_epochs=1,
                num_rows=1,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        client = TestClient(app)
        response = client.patch(f"/admin/jobs/{job_id}/legal-hold", json={"enable": True})
        assert response.status_code == 200
        assert response.json()["legal_hold"] is True

    def test_clear_legal_hold_returns_200(self) -> None:
        """PATCH /admin/jobs/{id}/legal-hold with enable=False clears the hold."""
        import os

        os.environ.setdefault("AUDIT_KEY", "aa" * 32)
        os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import StaticPool
        from sqlmodel import Session, SQLModel, create_engine

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.routers.admin import router as admin_router

        app = FastAPI()
        app.include_router(admin_router)

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/p.parquet",
                total_epochs=1,
                num_rows=1,
                legal_hold=True,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        client = TestClient(app)
        response = client.patch(f"/admin/jobs/{job_id}/legal-hold", json={"enable": False})
        assert response.status_code == 200
        assert response.json()["legal_hold"] is False

    def test_legal_hold_on_missing_job_returns_404(self) -> None:
        """PATCH /admin/jobs/{id}/legal-hold returns 404 for a missing job."""
        import os

        os.environ.setdefault("AUDIT_KEY", "aa" * 32)
        os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import StaticPool
        from sqlmodel import Session, SQLModel, create_engine

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.routers.admin import router as admin_router

        app = FastAPI()
        app.include_router(admin_router)

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

        client = TestClient(app)
        response = client.patch("/admin/jobs/9999/legal-hold", json={"enable": True})
        assert response.status_code == 404

    def test_set_legal_hold_returns_500_when_audit_fails(self) -> None:
        """Endpoint returns 500 and does NOT commit when audit logging raises (T68.3).

        T68.3 changed this from graceful degradation (200) to blocking (500).
        No destructive operation — including toggling legal hold — may proceed
        without a successful audit trail. If the audit write raises any exception,
        the endpoint returns 500 and the database change is NOT applied.
        """
        import os
        from unittest.mock import MagicMock, patch

        os.environ.setdefault("AUDIT_KEY", "aa" * 32)
        os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import StaticPool
        from sqlmodel import Session, SQLModel, create_engine

        from synth_engine.bootstrapper.dependencies.auth import get_current_operator
        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.routers.admin import router as admin_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        app = FastAPI()
        app.include_router(admin_router)

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override
        # T68.2: Override auth to match job owner_id so ownership check passes.
        app.dependency_overrides[get_current_operator] = lambda: "operator-1"

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/p.parquet",
                total_epochs=1,
                num_rows=1,
                owner_id="operator-1",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        audit_mock = MagicMock()
        audit_mock.log_event.side_effect = RuntimeError("audit service down")

        client = TestClient(app)
        with patch(
            "synth_engine.bootstrapper.routers.admin.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.patch(f"/admin/jobs/{job_id}/legal-hold", json={"enable": True})

        # T68.3: Audit failure BLOCKS the operation — must return 500, not 200.
        assert response.status_code == 500
        # Verify the legal_hold was NOT changed (DB not committed).
        with Session(engine) as verify_session:
            refreshed_job = verify_session.get(SynthesisJob, job_id)
            assert refreshed_job is not None
            assert refreshed_job.legal_hold is False, (
                "legal_hold must NOT be toggled when audit write fails (T68.3)"
            )


# ---------------------------------------------------------------------------
# Audit retention guard tests
# ---------------------------------------------------------------------------


class TestAuditRetentionGuard:
    """Tests verifying audit events are never deleted within retention period."""

    def test_cleanup_does_not_delete_audit_events(self) -> None:
        """RetentionCleanup never deletes AuditEvent-like records within retention period.

        The AuditLogger stores events via Python logging only (to WORM log handler),
        not in a database table. This test verifies that the cleanup task only
        targets synthesis_job and artifact records — not any audit log records.
        """
        import inspect

        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        # RetentionCleanup must have no method that touches an audit events table.
        # Validate via inspection: cleanup_expired_jobs must not reference any
        # audit-deletion query.
        cleanup = RetentionCleanup(engine=MagicMock(), job_retention_days=90)
        source = inspect.getsource(type(cleanup))
        # The cleanup must never execute a DELETE on audit-related tables.
        assert "audit_event" not in source.lower()
        assert "delete.*audit" not in source.lower()

    def test_retention_cleanup_only_targets_synthesis_job(self) -> None:
        """RetentionCleanup.cleanup_expired_jobs operates only on synthesis_job table."""
        import inspect

        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        source = inspect.getsource(RetentionCleanup.cleanup_expired_jobs)
        # Must reference SynthesisJob (the target table)
        assert "SynthesisJob" in source
