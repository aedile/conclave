"""Subsetting Engine — orchestrates DAG traversal and Saga egress.

The SubsettingEngine is the primary entry point for the subsetting pipeline.
It receives a :class:`~synth_engine.shared.schema_topology.SchemaTopology`
value object (bootstrapper-injected) rather than importing
:class:`~synth_engine.modules.mapping.reflection.SchemaReflector` or
:class:`~synth_engine.modules.mapping.graph.DirectedAcyclicGraph` directly.

This satisfies the bootstrapper-as-value-courier pattern mandated by
ADR-0001, ADR-0012 §Cross-module, and ADR-0013 §5.

Architecture note
-----------------
This module may only import from ``synth_engine.modules.subsetting`` (sibling
files) and ``synth_engine.shared``.  Cross-module imports from masking,
profiler, synthesizer, privacy, or bootstrapper are forbidden by import-linter
contracts.

The optional ``row_transformer`` parameter allows callers (bootstrapper, tests)
to inject a masking callback **without** this module importing from masking.
This is the inversion-of-control pattern that preserves the module boundary.

CONSTITUTION Priority 0: Security — no PII exposure, no external calls.
Task: P3-T3.4 -- Subsetting & Materialization Core
Task: P3-T3.5 -- Execute E2E Subsetting Subsystem Tests
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine

from synth_engine.modules.subsetting.egress import EgressWriter
from synth_engine.modules.subsetting.traversal import DagTraversal
from synth_engine.shared.schema_topology import SchemaTopology


@dataclass
class SubsetResult:
    """Result of a completed subset run.

    Attributes:
        tables_written: Ordered list of table names written to the target,
            in the same order as they were processed.
        row_counts: Mapping of table name to the number of rows written.
    """

    tables_written: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)


class SubsettingEngine:
    """Orchestrates topological DAG traversal and Saga-pattern egress.

    The engine validates its inputs, drives :class:`DagTraversal` to fetch
    rows from the source database, optionally transforms each row via a
    caller-injected ``row_transformer``, and writes them to the target via
    :class:`EgressWriter`.  If any step fails, ``egress.rollback()`` is called
    to restore the target to a clean (empty) state.

    The ``row_transformer`` callback is the inversion-of-control hook that
    allows the bootstrapper (or a test) to wire in masking, hashing, or any
    other per-row transformation **without** this module importing from
    ``modules/masking`` or any other sibling module.  The contract is strict:
    the callback MUST be a pure function of ``(table_name, row)`` → ``row``
    and MUST NOT modify the input dict in place.

    Args:
        source_engine: A SQLAlchemy :class:`~sqlalchemy.Engine` for the source
            (read-only) database.
        topology: Bootstrapper-injected :class:`~synth_engine.shared.schema_topology.SchemaTopology`
            describing table order, columns, and FK relationships.
        egress: An :class:`EgressWriter` targeting the destination database.
        row_transformer: Optional callback applied to each row before it is
            written to the target.  Signature::

                def transformer(table_name: str, row: dict[str, Any]) -> dict[str, Any]: ...

            If ``None`` (the default), rows are written as-is from the source.

    Example::

        engine = SubsettingEngine(
            source_engine=src_engine,
            topology=topology,
            egress=EgressWriter(target_engine=tgt_engine),
            row_transformer=my_masking_fn,
        )
        result = engine.run(
            seed_table="departments",
            seed_query="SELECT * FROM departments LIMIT 10",
        )
    """

    def __init__(
        self,
        source_engine: Engine,
        topology: SchemaTopology,
        egress: EgressWriter,
        row_transformer: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self._engine = source_engine
        self._topology = topology
        self._egress = egress
        self._row_transformer = row_transformer

    def run(self, seed_table: str, seed_query: str) -> SubsetResult:
        """Execute the subset pipeline.

        Validates inputs, traverses the DAG from the seed table, optionally
        transforms each row via the injected ``row_transformer``, writes rows
        to the target, and returns a :class:`SubsetResult`.

        **row_transformer contract**: when a transformer is provided it is
        called once per row as ``transformer(table_name, row_dict)`` and its
        return value is written to the target.  The transformer MUST return a
        ``dict[str, Any]`` — returning ``None`` raises ``TypeError`` and
        triggers the Saga rollback.  Any other exception raised by the
        transformer is treated as a pipeline failure: ``egress.rollback()`` is
        called and the exception is re-raised.

        **Synchronous method** — backed by the blocking psycopg2 driver.
        Callers in async contexts (e.g., FastAPI route handlers or bootstrapper
        orchestrators) **MUST** wrap this call via ``asyncio.to_thread()`` to
        avoid blocking the event loop::

            result = await asyncio.to_thread(engine.run, seed_table, seed_query)

        See ADR-0015 §Async Call-Site Contract and ADR-0012 §Sync/Async Boundary.

        Args:
            seed_table: Starting table name.  Must be present in
                ``topology.table_order``.
            seed_query: Non-empty SQL SELECT that returns the seed rows.
                Must begin with the keyword ``SELECT`` (case-insensitive).

        Returns:
            A :class:`SubsetResult` with tables written and row counts.

        Raises:
            ValueError: If ``seed_query`` is empty/whitespace, if
                ``seed_query`` does not start with ``SELECT``, or if
                ``seed_table`` is not in the topology.
            TypeError: If the ``row_transformer`` returns ``None`` for any row.
            Exception: Any exception from traversal, the row_transformer, or
                egress is re-raised after calling ``egress.rollback()``.
        """
        # --- Input validation ---
        if not seed_query or not seed_query.strip():
            raise ValueError("seed_query must be a non-empty SQL string.")

        normalized = seed_query.strip().upper()
        if not normalized.startswith("SELECT"):
            raise ValueError(f"seed_query must be a SELECT statement; got: {seed_query[:80]!r}")

        if seed_table not in self._topology.table_order:
            raise ValueError(
                f"seed_table '{seed_table}' is not in the schema topology. "
                f"Known tables: {list(self._topology.table_order)}"
            )

        # --- Traversal and egress ---
        traversal = DagTraversal(engine=self._engine, topology=self._topology)
        result = SubsetResult()

        try:
            for table, rows in traversal.traverse(seed_table, seed_query):
                if self._row_transformer is not None:
                    transformed: list[dict[str, Any]] = []
                    for row in rows:
                        result_row = self._row_transformer(table, row)
                        if result_row is None:
                            raise TypeError(
                                f"row_transformer returned None for table {table!r}; "
                                "transformer must return a dict[str, Any]"
                            )
                        transformed.append(result_row)
                    rows = transformed
                self._egress.write(table, rows)
                result.tables_written.append(table)
                result.row_counts[table] = len(rows)
        except Exception:  # Broad catch intentional: any write error must trigger full rollback
            self._egress.rollback()
            raise

        return result
