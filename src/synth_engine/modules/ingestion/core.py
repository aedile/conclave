"""Subsetting Engine — orchestrates DAG traversal and Saga egress.

The SubsettingEngine is the primary entry point for the subsetting pipeline.
It receives a :class:`~synth_engine.shared.schema_topology.SchemaTopology`
value object (bootstrapper-injected) rather than importing
:class:`~synth_engine.modules.ingestion.reflection.SchemaReflector` or
:class:`~synth_engine.modules.ingestion.graph.DirectedAcyclicGraph` directly.

This satisfies the bootstrapper-as-value-courier pattern mandated by
ADR-0001, ADR-0012 §Cross-module, and ADR-0013 §5.

Architecture note
-----------------
This module may only import from ``synth_engine.modules.ingestion`` (sibling
files) and ``synth_engine.shared``.  Cross-module imports from masking,
profiler, synthesizer, privacy, or bootstrapper are forbidden by import-linter
contracts.

CONSTITUTION Priority 0: Security — no PII exposure, no external calls.
Task: P3-T3.4 -- Subsetting & Materialization Core
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Engine

from synth_engine.modules.ingestion.egress import EgressWriter
from synth_engine.modules.ingestion.traversal import DagTraversal
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
    rows from the source database, and writes them to the target via
    :class:`EgressWriter`.  If any step fails, ``egress.rollback()`` is called
    to restore the target to a clean (empty) state.

    Args:
        source_engine: A SQLAlchemy :class:`~sqlalchemy.Engine` for the source
            (read-only) database.
        topology: Bootstrapper-injected :class:`~synth_engine.shared.schema_topology.SchemaTopology`
            describing table order, columns, and FK relationships.
        egress: An :class:`EgressWriter` targeting the destination database.

    Example::

        engine = SubsettingEngine(
            source_engine=src_engine,
            topology=topology,
            egress=EgressWriter(target_engine=tgt_engine),
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
    ) -> None:
        """Initialise with bootstrapper-injected dependencies.

        Args:
            source_engine: Source database engine (read-only).
            topology: Schema topology value object from the bootstrapper.
            egress: Egress writer for the target database.
        """
        self._engine = source_engine
        self._topology = topology
        self._egress = egress

    def run(self, seed_table: str, seed_query: str) -> SubsetResult:
        """Execute the subset pipeline.

        Validates inputs, traverses the DAG from the seed table, writes rows
        to the target, and returns a :class:`SubsetResult`.

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
            Exception: Any exception from traversal or egress is re-raised
                after calling ``egress.rollback()``.
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
                self._egress.write(table, rows)
                result.tables_written.append(table)
                result.row_counts[table] = len(rows)
        except Exception:
            self._egress.rollback()
            raise

        return result
