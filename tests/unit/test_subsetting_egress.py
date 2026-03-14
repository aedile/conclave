"""Unit tests for EgressWriter — Saga-pattern egress with rollback support.

All tests mock the SQLAlchemy engine; no live PostgreSQL required.

Task: P3-T3.4 -- Subsetting & Materialization Core
Task: P3.5-T3.5.4 -- Remove EgressWriter.commit() no-op (semantic trap)
Task: P3.5-T3.5.5 -- Advisory sweep (ADV-029: rollback logs table names AND row counts)
Security: TRUNCATE with CASCADE is the rollback strategy (ADR-0015).
Saga invariant: if ANY write fails, rollback() wipes all written tables.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy.exc
from sqlalchemy import Engine

from synth_engine.modules.subsetting.egress import EgressWriter

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

    def test_write_accumulates_row_counts_across_batches(self) -> None:
        """write() called twice for the same table accumulates total row count."""
        engine = _make_engine()
        _make_conn_ctx(engine)

        writer = EgressWriter(target_engine=engine)
        writer.write("departments", [{"id": 1}])
        writer.write("departments", [{"id": 2}])

        # Table appears once in the list but its count accumulates
        assert writer.written_tables.count("departments") == 1
        assert writer._written_tables["departments"] == 2

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
        writer._written_tables = {"departments": 3, "employees": 7}

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

    def test_rollback_logs_warning_with_table_names_and_row_counts(self) -> None:
        """rollback() logs at WARNING level including table names AND row counts.

        The Saga compensating action should be visible in logs so that
        operators can diagnose partial subset failures without inspecting
        the database.  The log message must name both tables written and
        include their respective row counts (ADV-029).
        """
        engine = _make_engine()
        _make_conn_ctx(engine)

        writer = EgressWriter(target_engine=engine)
        writer.write("departments", [{"id": 1}])
        writer.write("employees", [{"id": 10, "dept_id": 1}, {"id": 11, "dept_id": 1}])

        with patch("synth_engine.modules.subsetting.egress.logger") as mock_logger:
            writer.rollback()

        mock_logger.warning.assert_called_once()
        warning_args = mock_logger.warning.call_args
        # The format args must reference both table names and their row counts.
        all_args = " ".join(str(a) for a in warning_args.args)
        assert "departments" in all_args, f"'departments' not found in warning: {warning_args}"
        assert "employees" in all_args, f"'employees' not found in warning: {warning_args}"
        # Row counts must appear in the warning message arguments.
        assert "1" in all_args, f"row count '1' not found in warning: {warning_args}"
        assert "2" in all_args, f"row count '2' not found in warning: {warning_args}"


class TestEgressWriterNoCommitMethod:
    """EgressWriter.commit() was removed — it was a semantic trap.

    The method was a public no-op on a database-facing class, implying
    transactional semantics that do not exist.  Each write() call
    auto-commits its own batch.  The context manager no longer calls
    commit() on clean exit.
    """

    def test_commit_method_does_not_exist(self) -> None:
        """EgressWriter must NOT have a commit() method (it was removed).

        A public commit() on a DB-facing class is a semantic trap: callers
        may believe uncommitted writes exist and that calling commit() is
        required to persist them.  Writes are auto-committed per-batch in
        write(); no commit hook is needed or correct.
        """
        engine = _make_engine()
        writer = EgressWriter(target_engine=engine)
        assert not hasattr(writer, "commit"), (
            "EgressWriter.commit() was removed (T3.5.4). "
            "Do not re-add it — writes auto-commit in write()."
        )


class TestEgressWriterContextManager:
    """EgressWriter as a context manager — clean on success, rollback on failure."""

    def test_context_manager_does_not_call_rollback_on_success(self) -> None:
        """Clean exit from 'with EgressWriter()' does NOT call rollback."""
        engine = _make_engine()
        writer = EgressWriter(target_engine=engine)
        writer.rollback = MagicMock()

        with writer:
            pass

        writer.rollback.assert_not_called()

    def test_context_manager_calls_rollback_on_exception(self) -> None:
        """Exception raised inside 'with EgressWriter()' triggers rollback."""
        engine = _make_engine()
        writer = EgressWriter(target_engine=engine)
        writer.rollback = MagicMock()

        with pytest.raises(RuntimeError, match="test error"):
            with writer:
                raise RuntimeError("test error")

        writer.rollback.assert_called_once()

    def test_context_manager_reraises_exception(self) -> None:
        """The original exception propagates out of the context manager."""
        engine = _make_engine()
        writer = EgressWriter(target_engine=engine)
        writer.rollback = MagicMock()

        with pytest.raises(ValueError, match="bad data"):
            with writer:
                raise ValueError("bad data")
