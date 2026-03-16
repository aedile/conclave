"""DAG traversal engine for the subsetting pipeline.

Walks a :class:`~synth_engine.shared.schema_topology.SchemaTopology` in
topological order (parents before children) and fetches rows reachable from
an initial seed query via explicit FK relationships.

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
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.sql.expression import quoted_name

from synth_engine.shared.schema_topology import SchemaTopology


class DagTraversal:
    """Traverses a schema DAG in topological order, fetching reachable rows.

    Given a seed query that returns rows from a starting table, ``traverse()``
    follows FK relationships to fetch all directly reachable parent and child
    rows — yielding each ``(table, rows)`` pair in topological order so that
    parents always precede their children.

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
        """Initialise with source engine and bootstrapper-injected topology.

        Args:
            engine: Source database engine (read-only usage).
            topology: Bootstrapper-injected SchemaTopology value object.
        """
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
            ``(table_name, rows)`` pairs in topological order.  Each ``rows``
            element is a plain ``dict[str, Any]`` mapping column name to value.

        Note:
            If the seed query returns 0 rows, nothing is yielded.
        """
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

        Args:
            table: The table to fetch rows for.
            fetched: Dict of already-fetched rows keyed by table name.

        Returns:
            List of matching row dicts, or an empty list if not reachable.
        """
        rows: list[dict[str, Any]] = []

        # --- Child direction: ``table`` has an FK referencing a fetched parent ---
        fks = self._topology.foreign_keys.get(table, ())
        for fk in fks:
            parent_table = fk.referred_table
            if parent_table not in fetched:
                continue

            parent_rows = fetched[parent_table]
            parent_pk_values = self._extract_pk_values(parent_table, parent_rows)
            if not parent_pk_values:
                continue

            # fk.constrained_columns[0]: the FK column on this (child) table
            # fk.referred_columns[0]: the PK column on the parent table
            child_fk_col = fk.constrained_columns[0]
            new_rows = self._fetch_by_fk_values(table, child_fk_col, parent_pk_values)
            rows.extend(new_rows)

        if rows:
            return rows

        # --- Parent direction: something already fetched has an FK to ``table`` ---
        for child_table, child_rows in fetched.items():
            child_fks = self._topology.foreign_keys.get(child_table, ())
            for fk in child_fks:
                if fk.referred_table != table:
                    continue

                # Collect the FK values from the child rows
                child_fk_col = fk.constrained_columns[0]
                parent_pk_col = fk.referred_columns[0]
                fk_values = list(
                    {row[child_fk_col] for row in child_rows if row.get(child_fk_col) is not None}
                )
                if not fk_values:
                    continue

                new_rows = self._fetch_by_fk_values(table, parent_pk_col, fk_values)
                rows.extend(new_rows)

        return rows

    def _extract_pk_values(
        self,
        table: str,
        rows: list[dict[str, Any]],
    ) -> list[Any]:
        """Extract primary key values from a list of rows.

        Args:
            table: Table name (used to look up PK column from topology).
            rows: Row dicts from which to extract PK values.

        Returns:
            Deduplicated list of PK values from the rows.
        """
        cols = self._topology.columns.get(table, ())
        pk_cols = [c.name for c in cols if c.primary_key >= 1]
        if not pk_cols:
            return []

        # Simple case: single-column PK (composite PK support deferred)
        pk_col = pk_cols[0]
        return list({row[pk_col] for row in rows if row.get(pk_col) is not None})

    def _fetch_by_fk_values(
        self,
        table: str,
        column: str,
        values: list[Any],
    ) -> list[dict[str, Any]]:
        """Fetch rows from ``table`` where ``column`` is IN ``values``.

        Uses a parameterised ``text()`` query with a named bind parameter —
        no f-string interpolation of data values.  Table and column names are
        quoted with ``quoted_name`` to prevent identifier injection from
        schema metadata.

        Args:
            table: Table to query.
            column: Column to filter on (typically a PK or FK column).
            values: Values to match in the IN clause.

        Returns:
            List of matching row dicts.
        """
        if not values:
            return []

        quoted_table = str(quoted_name(table, quote=True))
        quoted_col = str(quoted_name(column, quote=True))

        # Build a parameterised IN clause: WHERE col IN (:v0, :v1, ...)
        params: dict[str, Any] = {f"v{i}": v for i, v in enumerate(values)}
        placeholders = ", ".join(f":v{i}" for i in range(len(values)))

        # nosec B608 — table/column names are from SchemaTopology (bootstrapper-controlled) and are SQLAlchemy-quoted above; values are parameterised
        stmt = text(
            f"SELECT * FROM {quoted_table} WHERE {quoted_col} IN ({placeholders})"  # nosec B608 — see comment above  # noqa: S608
        )

        with self._engine.connect() as conn:
            result = conn.execute(stmt, params)
            return [dict(row) for row in result.mappings()]
