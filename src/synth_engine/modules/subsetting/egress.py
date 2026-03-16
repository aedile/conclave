"""Saga-pattern egress writer for the subsetting pipeline.

Writes subsetted rows to a target PostgreSQL database and provides a
TRUNCATE-based rollback to satisfy the Saga invariant: if ANY write fails,
ALL previously written data is wiped from the target so it is left empty.

Architecture note
-----------------
This module may only import from ``synth_engine.shared`` and the Python
standard library.  Cross-module imports from masking, profiler, synthesizer,
privacy, or bootstrapper are forbidden by import-linter contracts.

Security note
-------------
Table names originate from :class:`~synth_engine.shared.schema_topology.SchemaTopology`
(bootstrapper-injected, not user input).  Even so, identifiers are quoted with
``sqlalchemy.sql.expression.quoted_name`` rather than interpolated directly
into SQL strings.  All data values travel via SQLAlchemy's parameterised
INSERT interface.

Per ADR-0015: Subsetting Traversal and Saga Rollback Design.
CONSTITUTION Priority 0: Security — parameterised SQL only, no PII exposure.
Task: P3-T3.4 -- Subsetting & Materialization Core
Task: P3.5-T3.5.4 -- Remove EgressWriter.commit() no-op (semantic trap)
Task: P3.5-T3.5.5 -- Advisory sweep (ADV-029: track row counts per table)
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from sqlalchemy import Engine, text
from sqlalchemy.sql.expression import quoted_name

logger = logging.getLogger(__name__)


class EgressWriter:
    """Writes subsetted rows to a target database with Saga-pattern rollback.

    The Saga invariant: if any ``write()`` call fails (or if an exception
    propagates out of the ``with EgressWriter():`` block), ``rollback()``
    TRUNCATEs all tables that were written in reverse order so that FK
    constraints are satisfied.  The target database is left empty after a
    failed subset run.

    Note: each ``write()`` call commits immediately; the context manager
    provides rollback safety on failure, not deferred transactional commit.

    Usage::

        with EgressWriter(target_engine=engine) as writer:
            writer.write("departments", dept_rows)
            writer.write("employees", emp_rows)
        # clean exit — all writes already committed per-batch

    Args:
        target_engine: A SQLAlchemy :class:`~sqlalchemy.Engine` connected to
            the target (output) database.
    """

    def __init__(self, target_engine: Engine) -> None:
        """Initialise with the target database engine.

        Args:
            target_engine: A connected SQLAlchemy :class:`~sqlalchemy.Engine`.
        """
        self._engine = target_engine
        self._written_tables: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def written_tables(self) -> list[str]:
        """Return the list of tables written so far, in insertion order.

        Returns:
            Ordered list of table names; each unique table appears once.
        """
        return list(self._written_tables.keys())

    def write(self, table: str, rows: list[dict[str, Any]]) -> None:
        """Insert rows into the target table.

        If ``rows`` is empty the method is a no-op.  Each call that inserts at
        least one row accumulates the row count for that table for potential
        rollback reporting.

        Args:
            table: Unquoted target table name.
            rows: List of row dicts mapping column name to value.  All values
                are passed to SQLAlchemy as bind parameters — never
                interpolated into SQL text.

        Raises:
            sqlalchemy.exc.SQLAlchemyError: If the INSERT fails.
        """
        if not rows:
            return

        # Accumulate row counts for rollback diagnostics (ADV-029).
        self._written_tables[table] = self._written_tables.get(table, 0) + len(rows)

        columns = list(rows[0].keys())
        # Build column list using quoted identifiers to prevent SQL injection
        # from schema metadata (belt-and-suspenders alongside topology validation).
        quoted_cols = ", ".join(str(quoted_name(col, quote=True)) for col in columns)
        placeholders = ", ".join(f":{col}" for col in columns)
        quoted_table = str(quoted_name(table, quote=True))

        # nosec B608 — table/column names are from SchemaTopology (bootstrapper-controlled) and are SQLAlchemy-quoted above
        stmt = text(
            f"INSERT INTO {quoted_table} ({quoted_cols}) VALUES ({placeholders})"  # nosec B608 — see comment above; values are parameterised  # noqa: S608
        )

        with self._engine.connect() as conn:
            for row in rows:
                conn.execute(stmt, row)
            conn.commit()

    def rollback(self) -> None:
        """TRUNCATE all written tables in reverse order (children before parents).

        Uses ``CASCADE`` to satisfy FK constraints during truncation.  Clears
        the written-tables tracking dict so subsequent rollback calls are
        idempotent.

        Logs a WARNING before truncation that includes both table names and
        the number of rows written to each, so that operators can diagnose
        partial subset failures from logs alone, without inspecting the
        database.

        This is the Saga compensating action: after any failure, the target DB
        is left in a clean (empty) state.
        """
        if not self._written_tables:
            return

        # Reverse order: children (written last) are truncated first so that
        # FK constraints referencing parent tables are already gone.
        tables_to_truncate = dict(reversed(list(self._written_tables.items())))
        logger.warning(
            "Saga rollback: truncating %d tables: %s",
            len(tables_to_truncate),
            dict(tables_to_truncate),
        )
        self._written_tables = {}

        with self._engine.connect() as conn:
            for table in tables_to_truncate:
                quoted_table = str(quoted_name(table, quote=True))
                # TRUNCATE ... CASCADE is the safe rollback for FK-constrained schemas.
                # Table names come from SchemaTopology (bootstrapper-controlled);
                # they are also quoted above as belt-and-suspenders.
                conn.execute(
                    text(f"TRUNCATE TABLE {quoted_table} CASCADE")  # nosec B608 — table name is from SchemaTopology (bootstrapper-controlled) and is SQLAlchemy-quoted
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> EgressWriter:
        """Enter the context manager.

        Returns:
            This EgressWriter instance.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object,
    ) -> Literal[False]:
        """Exit the context manager.

        On clean exit, does nothing — each ``write()`` call already commits
        its own batch immediately, so there is no deferred work to finalise.
        On exception, calls :meth:`rollback` to restore the target to a clean
        state.  Returns ``False`` to allow exceptions to propagate.

        Note: the previous ``commit()`` no-op has been removed because it was
        a semantic trap — a public method named ``commit()`` on a
        database-facing class implies transactional semantics that do not exist
        here.  Writes are auto-committed per-batch in :meth:`write`.

        Args:
            exc_type: Exception type, or None.
            _exc_val: Exception value, or None. Unused; only exc_type is inspected.
            _exc_tb: Traceback, or None. Unused; only exc_type is inspected.

        Returns:
            ``False`` — exceptions are never suppressed.
        """
        if exc_type is not None:
            self.rollback()
        return False
