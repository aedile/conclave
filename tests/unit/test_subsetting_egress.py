"""Unit tests for EgressWriter — Saga-pattern egress with rollback support.

All tests mock the SQLAlchemy engine; no live PostgreSQL required.

Task: P3-T3.4 -- Subsetting & Materialization Core
Security: TRUNCATE with CASCADE is the rollback strategy (ADR-0015).
Saga invariant: if ANY write fails, rollback() wipes all written tables.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import sqlalchemy.exc
from sqlalchemy import Engine

from synth_engine.modules.ingestion.egress import EgressWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine() -> MagicMock:
    return MagicMock(spec=Engine)


def _make_conn_ctx(engine: MagicMock) -> MagicMock:
    """Configure engine.connect() to return a mock connection context.

    Args:
        engine: The mock engine to configure.

    Returns:
        The mock connection object.
    """
    mock_conn = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    engine.connect.return_value = mock_ctx
    return mock_conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEgressWriterWrite:
    """EgressWriter.write() — row insertion into target database."""

    def test_write_rows_inserts_to_target(self) -> None:
        """write() executes an INSERT for each row in the batch."""
        engine = _make_engine()
        conn = _make_conn_ctx(engine)

        writer = EgressWriter(target_engine=engine)
        rows = [{"id": 1, "name": "Engineering"}, {"id": 2, "name": "Sales"}]

        writer.write("departments", rows)

        # One execute() call per row (INSERT executed row-by-row)
        assert conn.execute.call_count == len(rows)

    def test_write_empty_rows_is_noop(self) -> None:
        """write() with an empty row list does not call execute."""
        engine = _make_engine()
        conn = _make_conn_ctx(engine)

        writer = EgressWriter(target_engine=engine)
        writer.write("departments", [])

        conn.execute.assert_not_called()

    def test_write_tracks_written_tables(self) -> None:
        """write() records which tables have been written for rollback tracking."""
        engine = _make_engine()
        _make_conn_ctx(engine)

        writer = EgressWriter(target_engine=engine)
        writer.write("departments", [{"id": 1}])
        writer.write("employees", [{"id": 10, "dept_id": 1}])

        assert writer.written_tables == ["departments", "employees"]

    def test_write_does_not_duplicate_table_tracking(self) -> None:
        """write() called twice for the same table tracks the table only once."""
        engine = _make_engine()
        _make_conn_ctx(engine)

        writer = EgressWriter(target_engine=engine)
        writer.write("departments", [{"id": 1}])
        writer.write("departments", [{"id": 2}])

        assert writer.written_tables.count("departments") == 1

    def test_write_propagates_sqlalchemy_error(self) -> None:
        """write() does not swallow SQLAlchemyError — it propagates to the caller.

        The Saga contract requires that any write failure be visible to
        SubsettingEngine so it can invoke rollback().  Silencing the exception
        here would break the Saga invariant.
        """
        engine = _make_engine()
        conn = _make_conn_ctx(engine)
        conn.execute.side_effect = sqlalchemy.exc.SQLAlchemyError("DB write failed")

        writer = EgressWriter(target_engine=engine)

        with pytest.raises(sqlalchemy.exc.SQLAlchemyError, match="DB write failed"):
            writer.write("departments", [{"id": 1, "name": "Engineering"}])


class TestEgressWriterRollback:
    """EgressWriter.rollback() — TRUNCATE in reverse order, CASCADE."""

    def test_rollback_truncates_written_tables_in_reverse_order(self) -> None:
        """rollback() truncates all written tables in reverse (children first)."""
        engine = _make_engine()

        truncate_calls: list[str] = []

        def capture_execute(stmt: object, *args: object, **kwargs: object) -> None:
            stmt_str = str(stmt)
            truncate_calls.append(stmt_str)

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = capture_execute

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        engine.connect.return_value = mock_ctx

        writer = EgressWriter(target_engine=engine)
        # Simulate writing departments (parent) first, then employees (child)
        writer._written_tables = ["departments", "employees"]

        writer.rollback()

        # employees (child) must be truncated before departments (parent)
        dept_idx = next((i for i, s in enumerate(truncate_calls) if "departments" in s), None)
        emp_idx = next((i for i, s in enumerate(truncate_calls) if "employees" in s), None)
        assert emp_idx is not None, "employees not truncated"
        assert dept_idx is not None, "departments not truncated"
        assert emp_idx < dept_idx, "employees must be truncated before departments"

    def test_rollback_no_written_tables_is_noop(self) -> None:
        """rollback() with no written tables does not connect or execute."""
        engine = _make_engine()
        writer = EgressWriter(target_engine=engine)

        writer.rollback()  # Must not raise or connect

        engine.connect.assert_not_called()


class TestEgressWriterCommit:
    """EgressWriter.commit() — no-op finalisation hook."""

    def test_commit_is_noop(self) -> None:
        """commit() on a fresh writer does not call engine.connect() and raises no exception.

        commit() is an intentional no-op (individual write() calls commit per
        batch).  It exists as an explicit hook for the context manager and for
        future transactional extension.  Callers must be able to invoke it
        safely without any side-effects.
        """
        engine = _make_engine()
        writer = EgressWriter(target_engine=engine)

        writer.commit()  # Must not raise

        engine.connect.assert_not_called()


class TestEgressWriterContextManager:
    """EgressWriter as a context manager — commit on success, rollback on failure."""

    def test_context_manager_commits_on_success(self) -> None:
        """Clean exit from 'with EgressWriter()' calls commit, not rollback."""
        engine = _make_engine()
        writer = EgressWriter(target_engine=engine)
        writer.commit = MagicMock()
        writer.rollback = MagicMock()

        with writer:
            pass

        writer.commit.assert_called_once()
        writer.rollback.assert_not_called()

    def test_context_manager_calls_rollback_on_exception(self) -> None:
        """Exception raised inside 'with EgressWriter()' triggers rollback."""
        engine = _make_engine()
        writer = EgressWriter(target_engine=engine)
        writer.commit = MagicMock()
        writer.rollback = MagicMock()

        with pytest.raises(RuntimeError, match="test error"):
            with writer:
                raise RuntimeError("test error")

        writer.rollback.assert_called_once()
        writer.commit.assert_not_called()

    def test_context_manager_reraises_exception(self) -> None:
        """The original exception propagates out of the context manager."""
        engine = _make_engine()
        writer = EgressWriter(target_engine=engine)
        writer.rollback = MagicMock()

        with pytest.raises(ValueError, match="bad data"):
            with writer:
                raise ValueError("bad data")
