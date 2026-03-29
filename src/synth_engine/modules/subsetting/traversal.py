"""DAG traversal engine for the subsetting pipeline.

Walks a :class:`~synth_engine.shared.schema_topology.SchemaTopology` in
topological order (parents before children) and fetches rows reachable from
an initial seed query via explicit FK relationships.

Composite PK/FK support (T70.1)
---------------------------------
- :meth:`_extract_pk_values` returns ``list[tuple[Any, ...]]`` for all PKs
  (single-column PKs produce 1-tuples for uniformity).
- :meth:`_fetch_by_composite_fk_values` builds AND-ed equality predicates:
  ``WHERE (a = :v0a AND b = :v0b) OR (a = :v1a AND b = :v1b)``
- FKs with > 4 columns raise ``ValueError`` at traversal time.
- Column-count mismatch between constrained and referred raises ``ValueError``.
- Tables with 0 PK columns emit a WARNING and are skipped (return ``[]``).
- Row deduplication uses the full row identity dict to prevent duplicates when
  multiple FK paths lead to the same row.

Architecture note
-----------------
This module may only import from ``synth_engine.shared`` and the Python
standard library.  Cross-module imports from masking, profiler, synthesizer,
privacy, or bootstrapper are forbidden by import-linter contracts.

Security note
-------------
All SQL executed here uses SQLAlchemy's parameterised ``text()`` API with
named bind parameters.  Table and column identifiers come from
:class:`~synth_engine.shared.schema_topology.SchemaTopology` (bootstrapper-
injected, not user input) and are quoted with ``quoted_name`` as an extra
safety layer.  No f-string SQL with user-controlled data is used.

Per ADR-0013 §5 and ADR-0015.
CONSTITUTION Priority 0: Security — no PII exposure, parameterised SQL only.
Task: P3-T3.4 -- Subsetting & Materialization Core
Task: T70.1 -- Composite PK/FK support
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.sql.expression import quoted_name

from synth_engine.shared.schema_topology import ForeignKeyInfo, SchemaTopology

_logger = logging.getLogger(__name__)

#: Maximum supported composite FK width.  FKs wider than this are rejected.
_MAX_COMPOSITE_WIDTH: int = 4

#: Batch size limit for IN-clause chunks to avoid exceeding DB parameter limits.
_IN_CLAUSE_CHUNK_SIZE: int = 1000


class DagTraversal:
    """Traverses a schema DAG in topological order, fetching reachable rows.

    Given a seed query that returns rows from a starting table, ``traverse()``
    follows FK relationships to fetch all directly reachable parent and child
    rows — yielding each ``(table, rows)`` pair in topological order so that
    parents always precede their children.

    Composite PK/FK support: all PK extraction and FK fetching use tuples of
    column values so multi-column keys are handled correctly.

    Args:
        engine: A SQLAlchemy :class:`~sqlalchemy.Engine` connected to the
            **source** database.
        topology: The immutable schema topology value object (bootstrapper-
            injected) describing table order, columns, and FK relationships.

    Example::

        traversal = DagTraversal(engine=src_engine, topology=topology)
        for table, rows in traversal.traverse("departments", seed_sql):
            egress.write(table, rows)
    """

    def __init__(self, engine: Engine, topology: SchemaTopology) -> None:
        self._engine = engine
        self._topology = topology

    def traverse(
        self,
        seed_table: str,
        seed_query: str,
    ) -> Iterator[tuple[str, list[dict[str, Any]]]]:
        """Yield ``(table, rows)`` for all rows reachable from the seed query.

        Execution order follows ``topology.table_order`` (topological, parents
        first).  Tables unreachable from the seed (no FK path) are skipped.

        The seed query is executed as-is (the caller is responsible for
        composing a safe, parameterised query for the seed table).  All
        subsequent FK-following queries use SQLAlchemy ``text()`` with named
        bind parameters — no f-string interpolation of data values.

        Args:
            seed_table: The table the seed query targets.  Must be in
                ``topology.table_order``.
            seed_query: A raw SQL SELECT that returns the seed rows.

        Yields:
            (str, list[dict[str, Any]]): ``(table_name, rows)`` pairs in
                topological order.  Each ``rows`` element is a plain
                ``dict[str, Any]`` mapping column name to value.

        Raises:
            ValueError: If any FK encountered has > 4 columns or a mismatch
                between constrained and referred column counts.

        Note:
            If the seed query returns 0 rows, nothing is yielded.
        """
        # ------------------------------------------------------------------
        # Step 0: Validate all FKs eagerly before executing any query.
        # This ensures structural errors (column count mismatch, >4 column FKs)
        # are surfaced immediately regardless of whether rows are present.
        # ------------------------------------------------------------------
        for _table, _fks in self._topology.foreign_keys.items():
            for _fk in _fks:
                self._validate_fk(_fk)

        # ------------------------------------------------------------------
        # Step 1: Execute seed query
        # ------------------------------------------------------------------
        seed_rows = self._execute_seed(seed_query)

        if not seed_rows:
            return

        # fetched[table] = list of row dicts collected so far
        fetched: dict[str, list[dict[str, Any]]] = {seed_table: seed_rows}

        # ------------------------------------------------------------------
        # Step 2: Walk topology in order
        # ------------------------------------------------------------------
        for table in self._topology.table_order:
            if table == seed_table:
                # Seed table rows already known — nothing to fetch
                continue

            rows = self._fetch_table(table, fetched)
            if rows:
                fetched[table] = rows

        # ------------------------------------------------------------------
        # Step 3: Yield in topological order (parents before children)
        # ------------------------------------------------------------------
        for table in self._topology.table_order:
            if table in fetched:
                yield table, fetched[table]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _execute_seed(self, seed_query: str) -> list[dict[str, Any]]:
        """Execute the seed query and return its rows as a list of dicts.

        The seed query is supplied by the caller (SubsettingEngine) from
        bootstrapper-controlled configuration and is not constructed from
        user input.

        Args:
            seed_query: A SQL SELECT statement returning seed rows.

        Returns:
            List of row dicts from the seed query result.
        """
        with self._engine.connect() as conn:
            result = conn.execute(text(seed_query))  # nosec B608 — seed_query is supplied by SubsettingEngine from bootstrapper-controlled configuration; not constructed from user input
            return [dict(row) for row in result.mappings()]

    def _validate_fk(self, fk: ForeignKeyInfo) -> None:
        """Validate that a FK has matching column counts and is within width limit.

        Args:
            fk: The ForeignKeyInfo to validate.

        Raises:
            ValueError: If constrained/referred column counts differ.
            ValueError: If the FK has more than :data:`_MAX_COMPOSITE_WIDTH` columns.
        """
        n_constrained = len(fk.constrained_columns)
        n_referred = len(fk.referred_columns)

        if n_constrained != n_referred:
            raise ValueError(
                f"FK from constrained columns {fk.constrained_columns!r} "
                f"to referred columns {fk.referred_columns!r} has mismatched "
                f"column counts ({n_constrained} constrained vs {n_referred} referred). "
                "Each constrained column must map to exactly one referred column."
            )

        if n_constrained > _MAX_COMPOSITE_WIDTH:
            raise ValueError(
                f"FK with {n_constrained} columns exceeds the maximum supported "
                f"composite width of {_MAX_COMPOSITE_WIDTH} columns. "
                "Redesign the schema to use a narrower composite key."
            )

    def _fetch_table(
        self,
        table: str,
        fetched: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Fetch rows from ``table`` that are reachable from already-fetched rows.

        Checks two FK directions:
        1. **Parent direction**: ``table`` is referenced by a child table
           already fetched (i.e., ``table`` is a parent whose PKs appear in a
           child table's FK columns).
        2. **Child direction**: ``table`` holds an FK referencing a parent
           table already fetched (i.e., ``table`` is a child of something
           already collected).

        Deduplicates rows by their full row identity to prevent duplicates when
        multiple FK paths lead to the same row.

        Args:
            table: The table to fetch rows for.
            fetched: Dict of already-fetched rows keyed by table name.

        Returns:
            Deduplicated list of matching row dicts, or an empty list if not reachable.

        Raises:
            ValueError: If any FK has > 4 columns or mismatched column counts.
        """
        # Use a set of frozen row dicts for deduplication
        seen: set[tuple[tuple[str, Any], ...]] = set()
        rows: list[dict[str, Any]] = []

        def _add_rows(new_rows: list[dict[str, Any]]) -> None:
            for row in new_rows:
                row_key = tuple(sorted(row.items()))
                if row_key not in seen:
                    seen.add(row_key)
                    rows.append(row)

        # --- Child direction: ``table`` has an FK referencing a fetched parent ---
        fks = self._topology.foreign_keys.get(table, ())
        for fk in fks:
            self._validate_fk(fk)
            parent_table = fk.referred_table
            if parent_table not in fetched:
                continue

            parent_rows = fetched[parent_table]
            parent_pk_values = self._extract_pk_values(parent_table, parent_rows)
            if not parent_pk_values:
                continue

            new_rows = self._fetch_by_composite_fk_values(
                table, fk.constrained_columns, parent_pk_values
            )
            _add_rows(new_rows)

        if rows:
            return rows

        # --- Parent direction: something already fetched has an FK to ``table`` ---
        for child_table, child_rows in fetched.items():
            child_fks = self._topology.foreign_keys.get(child_table, ())
            for fk in child_fks:
                self._validate_fk(fk)
                if fk.referred_table != table:
                    continue

                # Collect the FK value tuples from the child rows
                constrained_cols = fk.constrained_columns
                referred_cols = fk.referred_columns
                fk_value_set: set[tuple[Any, ...]] = set()
                for row in child_rows:
                    val_tuple = tuple(row.get(c) for c in constrained_cols)
                    if any(v is not None for v in val_tuple):
                        fk_value_set.add(val_tuple)

                fk_values = list(fk_value_set)
                if not fk_values:
                    continue

                new_rows = self._fetch_by_composite_fk_values(
                    table, referred_cols, fk_values
                )
                _add_rows(new_rows)

        return rows

    def _extract_pk_values(
        self,
        table: str,
        rows: list[dict[str, Any]],
    ) -> list[tuple[Any, ...]]:
        """Extract primary key value tuples from a list of rows.

        Returns a list of tuples — one tuple per distinct row.  Single-column
        PKs produce 1-tuples for API uniformity with composite PKs.

        Tables with no PK columns emit a WARNING and return ``[]``.

        Args:
            table: Table name (used to look up PK columns from topology).
            rows: Row dicts from which to extract PK values.

        Returns:
            Deduplicated list of PK value tuples.  Single-column PKs return
            1-tuples.  Composite PKs return N-tuples.  Empty list if no PKs.
        """
        cols = self._topology.columns.get(table, ())
        pk_cols = [c.name for c in cols if c.primary_key >= 1]
        if not pk_cols:
            _logger.warning(
                "Table %r has no primary key columns — skipping PK extraction "
                "(T70.1: zero-PK tables are not traversable via FK joins).",
                table,
            )
            return []

        # Extract tuples of PK values; deduplicate via a set
        pk_set: set[tuple[Any, ...]] = set()
        for row in rows:
            val_tuple = tuple(row.get(c) for c in pk_cols)
            if any(v is not None for v in val_tuple):
                pk_set.add(val_tuple)

        return list(pk_set)

    def _fetch_by_composite_fk_values(
        self,
        table: str,
        columns: tuple[str, ...],
        values: list[tuple[Any, ...]],
    ) -> list[dict[str, Any]]:
        """Fetch rows from ``table`` matching composite key tuples.

        For a single-column key, builds:
            ``WHERE col IN (:v0_0, :v1_0, ...)``

        For a multi-column composite key, builds AND-ed OR predicates:
            ``WHERE ("a" = :v0_0 AND "b" = :v0_1) OR ("a" = :v1_0 AND "b" = :v1_1)``

        Uses parameterised ``text()`` queries — no f-string interpolation of
        data values.  Table and column identifiers are quoted with ``quoted_name``.

        Large value sets are chunked into batches of :data:`_IN_CLAUSE_CHUNK_SIZE`
        to avoid exceeding database parameter limits.

        Args:
            table: Table to query.
            columns: FK/PK column names on ``table`` to filter on.
            values: List of value tuples matching ``columns`` in order.

        Returns:
            Deduplicated list of matching row dicts.
        """
        if not values:
            return []

        quoted_table = str(quoted_name(table, quote=True))
        n_cols = len(columns)
        quoted_cols = [str(quoted_name(c, quote=True)) for c in columns]

        all_rows: list[dict[str, Any]] = []
        seen: set[tuple[tuple[str, Any], ...]] = set()

        # Process in chunks to respect DB parameter limits
        for chunk_start in range(0, len(values), _IN_CLAUSE_CHUNK_SIZE):
            chunk = values[chunk_start : chunk_start + _IN_CLAUSE_CHUNK_SIZE]

            if n_cols == 1:
                # Optimised path: single-column IN clause
                params: dict[str, Any] = {f"v{i}_0": t[0] for i, t in enumerate(chunk)}
                placeholders = ", ".join(f":v{i}_0" for i in range(len(chunk)))
                # nosec B608 — table/column from SchemaTopology (bootstrapper-controlled); quoted above; values parameterised
                sql = f"SELECT * FROM {quoted_table} WHERE {quoted_cols[0]} IN ({placeholders})"  # nosec B608 — see comment above  # noqa: S608
            else:
                # Composite path: AND-ed OR predicates
                params = {}
                or_clauses: list[str] = []
                for i, val_tuple in enumerate(chunk):
                    and_parts: list[str] = []
                    for j, (col, val) in enumerate(zip(quoted_cols, val_tuple, strict=False)):
                        param_name = f"v{i}_{j}"
                        params[param_name] = val
                        and_parts.append(f"{col} = :{param_name}")
                    or_clauses.append("(" + " AND ".join(and_parts) + ")")
                where_clause = " OR ".join(or_clauses)
                # nosec B608 — table/columns from SchemaTopology (bootstrapper-controlled); quoted above; values parameterised
                sql = f"SELECT * FROM {quoted_table} WHERE {where_clause}"  # nosec B608 — see comment above  # noqa: S608

            stmt = text(sql)
            with self._engine.connect() as conn:
                result = conn.execute(stmt, params)
                for row in result.mappings():
                    row_dict = dict(row)
                    row_key = tuple(sorted(row_dict.items()))
                    if row_key not in seen:
                        seen.add(row_key)
                        all_rows.append(row_dict)

        return all_rows
