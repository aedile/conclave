"""Integration tests for the orphan task reaper — T45.2.

Exercises the full reaper pipeline with a real in-memory SQLite database:
  - Injects stale IN_PROGRESS jobs and asserts they are marked FAILED.
  - Verifies legal-hold jobs survive.
  - Verifies jobs that complete between query and update are not double-marked.

SQLite datetime note
--------------------
SQLite stores ``datetime`` values as plain strings without timezone information.
SQLAlchemy serialises Python ``datetime`` objects using the space-separated
``str()`` representation (``'2026-03-21 22:22:21.123456'``).

The ``_backdate()`` helper stores timestamps using the same ``str()`` format to
keep string-based ``<`` comparisons correct.  Using ``.isoformat()`` (which
produces a ``T`` separator) would break comparisons because ASCII space (32)
sorts before ``T`` (84) — a ``T``-formatted stored value would appear to be
lexicographically *greater than* a space-formatted cutoff even when the instant
is in the past.

CONSTITUTION Priority 0: Security
CONSTITUTION Priority 3: TDD
Task: T45.2 — Reintroduce Orphan Task Reaper (TBD-08)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, text

from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob
from synth_engine.modules.synthesizer.storage.reaper_repository import SQLAlchemyTaskRepository
from synth_engine.shared.tasks.reaper import OrphanTaskReaper

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


def _make_job(
    *,
    status: str = "IN_PROGRESS",
    legal_hold: bool = False,
    table_name: str = "test_table",
) -> SynthesisJob:
    """Build a minimal SynthesisJob for insertion.

    Args:
        status: Initial job status string.
        legal_hold: Whether the job has a legal hold.
        table_name: Source table name (arbitrary for tests).

    Returns:
        An unsaved :class:`SynthesisJob` instance.
    """
    return SynthesisJob(
        status=status,
        total_epochs=1,
        num_rows=1,
        table_name=table_name,
        parquet_path="/tmp/test.parquet",
        legal_hold=legal_hold,
    )


def _backdate(session: Session, job_id: int, minutes: int) -> None:
    """Move a job's created_at back by ``minutes`` minutes.

    Stores the backdated timestamp as a **space-separated string**
    (``'2026-03-21 22:10:05.123456'``) — the same format SQLAlchemy uses
    internally when binding Python ``datetime`` objects for SQLite.

    Using ``.isoformat()`` (``T``-separator format) would break SQLite string
    comparisons because the repository's cutoff is bound via SQLAlchemy as a
    space-separated value.  ASCII space (32) < ``T`` (84), so a ``T``-stored
    value would sort *after* a space-format cutoff even when the datetime is
    in the past.

    The deprecated Python 3.12+ sqlite3 datetime adapter is deliberately
    avoided here to prevent ``DeprecationWarning`` → ``error`` conversion under
    ``-W error`` in test mode.

    Args:
        session: Active database session.
        job_id: Primary key of the job.
        minutes: Number of minutes to subtract.
    """
    new_dt = (datetime.now(UTC) - timedelta(minutes=minutes)).replace(tzinfo=None)
    # str(datetime) produces '2026-03-21 22:10:05.123456' (space-separated, no TZ)
    new_ts = str(new_dt)
    session.execute(
        text("UPDATE synthesis_job SET created_at = :ts WHERE id = :id"),
        {"ts": new_ts, "id": job_id},
    )
    session.commit()


# ---------------------------------------------------------------------------
# AC-13: Inject stale IN_PROGRESS job → assert FAILED
# ---------------------------------------------------------------------------


class TestStaleJobMarkedFailed:
    """Core integration scenario: stale IN_PROGRESS job → FAILED after reap."""

    def test_stale_in_progress_job_is_marked_failed(self) -> None:
        """A job older than the threshold must be set to FAILED after reap()."""
        engine = _make_engine()
        threshold_minutes = 30
        job_id: int

        with Session(engine) as session:
            job = _make_job(status="IN_PROGRESS")
            session.add(job)
            session.commit()
            session.refresh(job)
            assert job.id is not None
            job_id = job.id
            _backdate(session, job_id, minutes=threshold_minutes + 10)

        repo = SQLAlchemyTaskRepository(engine=engine)
        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=threshold_minutes)

        with patch("synth_engine.shared.tasks.reaper.get_audit_logger"):
            reaped = reaper.reap()

        assert reaped == 1

        with Session(engine) as session:
            updated = session.get(SynthesisJob, job_id)
            assert updated is not None
            assert updated.status == "FAILED"
            assert updated.error_msg == (
                "Reaped: exceeded staleness threshold — possible worker crash"
            )

    def test_recent_in_progress_job_is_not_reaped(self) -> None:
        """A job younger than the threshold must NOT be reaped."""
        engine = _make_engine()
        threshold_minutes = 60
        job_id: int

        with Session(engine) as session:
            job = _make_job(status="IN_PROGRESS")
            session.add(job)
            session.commit()
            session.refresh(job)
            assert job.id is not None
            job_id = job.id
            # Job is brand new — no backdate applied

        repo = SQLAlchemyTaskRepository(engine=engine)
        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=threshold_minutes)

        with patch("synth_engine.shared.tasks.reaper.get_audit_logger"):
            reaped = reaper.reap()

        assert reaped == 0

        with Session(engine) as session:
            unchanged = session.get(SynthesisJob, job_id)
            assert unchanged is not None
            assert unchanged.status == "IN_PROGRESS"


class TestLegalHoldSurvivesReap:
    """AC-2: legal-hold IN_PROGRESS jobs must survive the reap cycle."""

    def test_legal_hold_job_is_not_marked_failed(self) -> None:
        """A stale IN_PROGRESS job with legal_hold=True must remain IN_PROGRESS."""
        engine = _make_engine()
        job_id: int

        with Session(engine) as session:
            job = _make_job(status="IN_PROGRESS", legal_hold=True)
            session.add(job)
            session.commit()
            session.refresh(job)
            assert job.id is not None
            job_id = job.id
            _backdate(session, job_id, minutes=120)

        repo = SQLAlchemyTaskRepository(engine=engine)
        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=30)

        with patch("synth_engine.shared.tasks.reaper.get_audit_logger"):
            reaped = reaper.reap()

        assert reaped == 0

        with Session(engine) as session:
            unchanged = session.get(SynthesisJob, job_id)
            assert unchanged is not None
            assert unchanged.status == "IN_PROGRESS"


class TestConditionalUpdateRaceCondition:
    """AC-6: race condition guard — mark_failed uses WHERE status='IN_PROGRESS'."""

    def test_mark_failed_on_already_completed_job_returns_false(self) -> None:
        """If a job was updated to COMPLETE before mark_failed runs, it returns False."""
        engine = _make_engine()
        job_id: int

        with Session(engine) as session:
            job = _make_job(status="COMPLETE")
            session.add(job)
            session.commit()
            session.refresh(job)
            assert job.id is not None
            job_id = job.id

        repo = SQLAlchemyTaskRepository(engine=engine)
        # Directly attempt to mark a COMPLETE job as FAILED
        result = repo.mark_failed(
            job_id,
            "Reaped: exceeded staleness threshold — possible worker crash",
        )

        assert result is False

        with Session(engine) as session:
            unchanged = session.get(SynthesisJob, job_id)
            assert unchanged is not None
            assert unchanged.status == "COMPLETE"


class TestTerminalAndQueuedJobsExcluded:
    """Only IN_PROGRESS non-held jobs older than threshold qualify."""

    def test_queued_job_is_not_returned_by_repository(self) -> None:
        """get_stale_in_progress must not return QUEUED jobs."""
        engine = _make_engine()

        with Session(engine) as session:
            job = _make_job(status="QUEUED")
            session.add(job)
            session.commit()
            session.refresh(job)
            assert job.id is not None
            _backdate(session, job.id, minutes=120)

        repo = SQLAlchemyTaskRepository(engine=engine)
        cutoff = datetime.now(UTC) - timedelta(minutes=60)
        stale = repo.get_stale_in_progress(older_than=cutoff)

        assert len(stale) == 0

    def test_failed_job_is_not_returned_by_repository(self) -> None:
        """get_stale_in_progress must not return FAILED jobs."""
        engine = _make_engine()

        with Session(engine) as session:
            job = _make_job(status="FAILED")
            session.add(job)
            session.commit()
            session.refresh(job)
            assert job.id is not None
            _backdate(session, job.id, minutes=120)

        repo = SQLAlchemyTaskRepository(engine=engine)
        cutoff = datetime.now(UTC) - timedelta(minutes=60)
        stale = repo.get_stale_in_progress(older_than=cutoff)

        assert len(stale) == 0

    def test_complete_job_is_not_returned_by_repository(self) -> None:
        """get_stale_in_progress must not return COMPLETE jobs."""
        engine = _make_engine()

        with Session(engine) as session:
            job = _make_job(status="COMPLETE")
            session.add(job)
            session.commit()
            session.refresh(job)
            assert job.id is not None
            _backdate(session, job.id, minutes=120)

        repo = SQLAlchemyTaskRepository(engine=engine)
        cutoff = datetime.now(UTC) - timedelta(minutes=60)
        stale = repo.get_stale_in_progress(older_than=cutoff)

        assert len(stale) == 0

    def test_multiple_statuses_only_in_progress_reaped(self) -> None:
        """Mix of statuses: only IN_PROGRESS non-held old jobs are reaped."""
        engine = _make_engine()

        with Session(engine) as session:
            jobs = [
                _make_job(status="IN_PROGRESS"),  # should be reaped
                _make_job(status="QUEUED"),  # should be skipped
                _make_job(status="FAILED"),  # should be skipped
                _make_job(status="COMPLETE"),  # should be skipped
                _make_job(status="IN_PROGRESS", legal_hold=True),  # held — skip
            ]
            for j in jobs:
                session.add(j)
            session.commit()
            for j in jobs:
                session.refresh(j)
                assert j.id is not None
                _backdate(session, j.id, minutes=120)

        repo = SQLAlchemyTaskRepository(engine=engine)
        reaper = OrphanTaskReaper(repository=repo, stale_threshold_minutes=60)

        with patch("synth_engine.shared.tasks.reaper.get_audit_logger"):
            reaped = reaper.reap()

        assert reaped == 1
