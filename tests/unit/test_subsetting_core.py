"""Unit tests for SubsettingEngine — orchestrator of DAG traversal and egress.

All tests use mocked dependencies; no database required.

Task: P3-T3.4 -- Subsetting & Materialization Core
Task: P3.5-T3.5.3 -- SchemaTopology immutability (MappingProxyType)
Task: T49.2 -- Assertion Hardening: negative cases for egress failure and DB disconnect
Architecture: SubsettingEngine receives SchemaTopology via constructor injection
per ADR-0001, ADR-0012 §Cross-module, and ADR-0013 §5.  It must NOT import
SchemaReflector, DirectedAcyclicGraph, or PostgresIngestionAdapter directly.

Advisory: ADV-T49.2 — Circular FK handling not implemented in DagTraversal.
    DagTraversal.traverse() iterates topology.table_order (a pre-computed
    topological sort).  Circular FK relationships would cause an infinite loop
    or incorrect results in the DAG *builder* (modules/mapping/graph.py), not
    in traversal itself.  SubsettingEngine has no circular FK guard because it
    relies on the invariant that SchemaTopology always encodes a valid DAG.
    Recommendation: add a cycle-detection assertion in SchemaTopology.__post_init__
    or in the DAG builder so that circular FKs are caught at topology-construction
    time rather than silently producing wrong output at traversal time.
    This advisory is logged here per T49.2 task requirements.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from synth_engine.modules.subsetting.core import SubsetResult, SubsettingEngine
from synth_engine.modules.subsetting.egress import EgressWriter
from synth_engine.shared.schema_topology import (
    ColumnInfo,
    ForeignKeyInfo,
    SchemaTopology,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_topology(tables: list[str]) -> SchemaTopology:
    """Build a minimal SchemaTopology with the given table order and no FKs.

    Args:
        tables: Table names in topological order.

    Returns:
        A frozen SchemaTopology value object.
    """
    columns: dict[str, tuple[ColumnInfo, ...]] = {
        t: (ColumnInfo(name="id", type="INTEGER", primary_key=1, nullable=False),) for t in tables
    }
    return SchemaTopology(
        table_order=tuple(tables),
        columns=columns,
        foreign_keys=dict.fromkeys(tables, ()),
    )


def _make_engine() -> MagicMock:
    """Return a MagicMock acting as a SQLAlchemy Engine."""
    from sqlalchemy import Engine

    return MagicMock(spec=Engine)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSubsettingEngineValidation:
    """Validation tests — SubsettingEngine rejects bad inputs before traversal."""

    def test_subset_empty_seed_raises_value_error(self) -> None:
        """run() raises ValueError when seed_query is an empty string."""
        topology = _make_topology(["departments"])
        egress = MagicMock(spec=EgressWriter)
        engine = _make_engine()

        se = SubsettingEngine(source_engine=engine, topology=topology, egress=egress)

        with pytest.raises(ValueError, match="seed_query"):
            se.run(seed_table="departments", seed_query="")

    def test_subset_whitespace_only_seed_raises_value_error(self) -> None:
        """run() raises ValueError when seed_query is whitespace-only."""
        topology = _make_topology(["departments"])
        egress = MagicMock(spec=EgressWriter)
        engine = _make_engine()

        se = SubsettingEngine(source_engine=engine, topology=topology, egress=egress)

        with pytest.raises(ValueError, match="seed_query"):
            se.run(seed_table="departments", seed_query="   ")

    def test_subset_table_not_in_topology_raises_value_error(self) -> None:
        """run() raises ValueError when seed_table is not in topology.table_order."""
        topology = _make_topology(["employees"])
        egress = MagicMock(spec=EgressWriter)
        engine = _make_engine()

        se = SubsettingEngine(source_engine=engine, topology=topology, egress=egress)

        with pytest.raises(ValueError, match="departments"):
            se.run(seed_table="departments", seed_query="SELECT * FROM departments LIMIT 1")

    def test_run_rejects_non_select_seed_query(self) -> None:
        """run() raises ValueError when seed_query is a DELETE statement."""
        topology = _make_topology(["foo"])
        egress = MagicMock(spec=EgressWriter)
        engine = _make_engine()

        se = SubsettingEngine(source_engine=engine, topology=topology, egress=egress)

        with pytest.raises(ValueError, match="SELECT"):
            se.run(seed_table="foo", seed_query="DELETE FROM foo")

    def test_run_rejects_insert_seed_query(self) -> None:
        """run() raises ValueError when seed_query is an INSERT statement."""
        topology = _make_topology(["foo"])
        egress = MagicMock(spec=EgressWriter)
        engine = _make_engine()

        se = SubsettingEngine(source_engine=engine, topology=topology, egress=egress)

        with pytest.raises(ValueError, match="SELECT"):
            se.run(seed_table="foo", seed_query="INSERT INTO foo VALUES (1)")


class TestSubsettingEngineOrchestration:
    """Orchestration tests — SubsettingEngine coordinates traversal and egress."""

    def test_subset_calls_transversal_with_topology(self) -> None:
        """run() instantiates DagTraversal and calls traverse() with the right args."""
        topology = _make_topology(["departments"])
        egress = MagicMock(spec=EgressWriter)
        engine = _make_engine()

        mock_traversal = MagicMock()
        mock_traversal.traverse.return_value = iter([("departments", [{"id": 1}])])

        with patch(
            "synth_engine.modules.subsetting.core.DagTraversal",
            return_value=mock_traversal,
        ) as mock_cls:
            se = SubsettingEngine(source_engine=engine, topology=topology, egress=egress)
            se.run(seed_table="departments", seed_query="SELECT * FROM departments LIMIT 1")

        mock_cls.assert_called_once_with(engine=engine, topology=topology)
        mock_traversal.traverse.assert_called_once_with(
            "departments", "SELECT * FROM departments LIMIT 1"
        )

    def test_subset_calls_egress_with_rows(self) -> None:
        """run() calls egress.write() for each (table, rows) pair from traversal."""
        topology = _make_topology(["departments", "employees"])
        egress = MagicMock(spec=EgressWriter)
        engine = _make_engine()

        dept_rows = [{"id": 1, "name": "Engineering"}]
        emp_rows = [{"id": 10, "dept_id": 1}]

        mock_traversal = MagicMock()
        mock_traversal.traverse.return_value = iter(
            [("departments", dept_rows), ("employees", emp_rows)]
        )

        with patch(
            "synth_engine.modules.subsetting.core.DagTraversal",
            return_value=mock_traversal,
        ):
            se = SubsettingEngine(source_engine=engine, topology=topology, egress=egress)
            result = se.run(
                seed_table="departments",
                seed_query="SELECT * FROM departments LIMIT 1",
            )

        egress.write.assert_has_calls(
            [
                call("departments", dept_rows),
                call("employees", emp_rows),
            ]
        )
        assert result.tables_written == ["departments", "employees"]
        assert result.row_counts == {"departments": 1, "employees": 1}

    def test_subset_triggers_rollback_on_egress_failure(self) -> None:
        """run() calls egress.rollback() and re-raises when egress.write() fails."""
        topology = _make_topology(["departments"])
        egress = MagicMock(spec=EgressWriter)
        egress.write.side_effect = RuntimeError("disk full")
        engine = _make_engine()

        mock_traversal = MagicMock()
        mock_traversal.traverse.return_value = iter([("departments", [{"id": 1}])])

        with patch(
            "synth_engine.modules.subsetting.core.DagTraversal",
            return_value=mock_traversal,
        ):
            se = SubsettingEngine(source_engine=engine, topology=topology, egress=egress)

            with pytest.raises(RuntimeError, match="disk full"):
                se.run(
                    seed_table="departments",
                    seed_query="SELECT * FROM departments LIMIT 1",
                )

        egress.rollback.assert_called_once()

    def test_subset_returns_subset_result(self) -> None:
        """run() returns a SubsetResult with specific tables_written and row_counts values.

        Hardened from T49.2: previously only checked isinstance(result, SubsetResult).
        Now asserts the specific field values to catch a mutation that returns an
        empty SubsetResult or wrong counts.
        """
        topology = _make_topology(["departments"])
        egress = MagicMock(spec=EgressWriter)
        engine = _make_engine()

        mock_traversal = MagicMock()
        mock_traversal.traverse.return_value = iter([("departments", [{"id": 1}, {"id": 2}])])

        with patch(
            "synth_engine.modules.subsetting.core.DagTraversal",
            return_value=mock_traversal,
        ):
            se = SubsettingEngine(source_engine=engine, topology=topology, egress=egress)
            result = se.run(
                seed_table="departments",
                seed_query="SELECT * FROM departments LIMIT 2",
            )

        assert isinstance(result, SubsetResult)
        assert result.tables_written == ["departments"], (
            f"expected tables_written=['departments'], got {result.tables_written!r}"
        )
        assert result.row_counts == {"departments": 2}, (
            f"expected row_counts={{'departments': 2}}, got {result.row_counts!r}"
        )

    def test_transformer_none_return_raises_type_error(self) -> None:
        """run() raises TypeError when row_transformer returns None for a row.

        The engine must guard against a transformer that silently returns None
        rather than passing it downstream to egress, which would corrupt the
        target database.  rollback() must also be called.
        """
        topology = _make_topology(["departments"])
        egress = MagicMock(spec=EgressWriter)
        engine = _make_engine()

        mock_traversal = MagicMock()
        mock_traversal.traverse.return_value = iter([("departments", [{"id": 1}])])

        def _bad_transformer(table: str, row: dict) -> None:  # type: ignore[return]
            """Transformer that returns None — violates the callback contract."""
            return None

        with patch(
            "synth_engine.modules.subsetting.core.DagTraversal",
            return_value=mock_traversal,
        ):
            se = SubsettingEngine(
                source_engine=engine,
                topology=topology,
                egress=egress,
                row_transformer=_bad_transformer,  # type: ignore[arg-type]
            )

            with pytest.raises(TypeError, match="None"):
                se.run(
                    seed_table="departments",
                    seed_query="SELECT * FROM departments LIMIT 1",
                )

        egress.rollback.assert_called_once()

    def test_transformer_failure_triggers_rollback(self) -> None:
        """run() calls egress.rollback() and re-raises when row_transformer raises.

        Any exception from the transformer must trigger the Saga rollback so
        the target database is left clean.
        """
        topology = _make_topology(["departments"])
        egress = MagicMock(spec=EgressWriter)
        engine = _make_engine()

        mock_traversal = MagicMock()
        mock_traversal.traverse.return_value = iter([("departments", [{"id": 1}])])

        def _exploding_transformer(table: str, row: dict) -> dict:  # type: ignore[type-arg]
            """Transformer that raises — simulates a masking pipeline failure."""
            raise RuntimeError("transform failed")

        with patch(
            "synth_engine.modules.subsetting.core.DagTraversal",
            return_value=mock_traversal,
        ):
            se = SubsettingEngine(
                source_engine=engine,
                topology=topology,
                egress=egress,
                row_transformer=_exploding_transformer,
            )

            with pytest.raises(RuntimeError, match="transform failed"):
                se.run(
                    seed_table="departments",
                    seed_query="SELECT * FROM departments LIMIT 1",
                )

        egress.rollback.assert_called_once()

    # -----------------------------------------------------------------------
    # Negative cases (T49.2)
    # -----------------------------------------------------------------------

    def test_mid_stream_egress_failure_after_first_write_triggers_rollback(self) -> None:
        """Egress failure on the SECOND write still triggers rollback (T49.2).

        This tests the mid-stream failure path: the first table writes
        successfully, but the second table's egress.write() raises.  The engine
        must catch the exception, call rollback() exactly once, and re-raise the
        original exception — leaving no partial state in the target.

        This is distinct from test_subset_triggers_rollback_on_egress_failure
        (which fails on the very first write).  Here we verify that a partial
        write is also fully rolled back.
        """
        topology = _make_topology(["departments", "employees"])
        egress = MagicMock(spec=EgressWriter)

        # First call succeeds, second call raises
        egress.write.side_effect = [None, OSError("network failure mid-stream")]
        engine = _make_engine()

        mock_traversal = MagicMock()
        mock_traversal.traverse.return_value = iter(
            [
                ("departments", [{"id": 1}]),
                ("employees", [{"id": 10, "dept_id": 1}]),
            ]
        )

        with patch(
            "synth_engine.modules.subsetting.core.DagTraversal",
            return_value=mock_traversal,
        ):
            se = SubsettingEngine(source_engine=engine, topology=topology, egress=egress)

            with pytest.raises(OSError, match="network failure mid-stream"):
                se.run(
                    seed_table="departments",
                    seed_query="SELECT * FROM departments LIMIT 1",
                )

        # rollback must be called exactly once despite one successful write
        egress.rollback.assert_called_once()
        # first write must have been attempted
        assert egress.write.call_count == 2, (
            f"Expected 2 write calls (one success, one failure), got {egress.write.call_count}"
        )

    def test_db_disconnect_during_traversal_triggers_rollback(self) -> None:
        """DB disconnect during traversal triggers rollback and re-raises (T49.2).

        When DagTraversal.traverse() raises an OperationalError (simulating a
        DB connection loss mid-traversal), SubsettingEngine must:
        1. Call egress.rollback() exactly once.
        2. Re-raise the original exception unchanged.

        The Saga pattern guarantees that any traversal failure leaves the target
        in a clean state.  Recovery semantics: the caller must restart the full
        run from scratch — there is no partial-resume capability.
        """
        from sqlalchemy.exc import OperationalError

        topology = _make_topology(["departments", "employees"])
        egress = MagicMock(spec=EgressWriter)
        engine = _make_engine()

        mock_traversal = MagicMock()

        # Simulate a generator that yields one batch then raises on the next iteration
        def _disconnecting_traversal(
            seed_table: str, seed_query: str
        ) -> Iterator[tuple[str, list[dict[str, Any]]]]:
            yield "departments", [{"id": 1}]
            raise OperationalError(
                "could not connect to server: Connection refused",
                params=None,
                orig=Exception("connection refused"),
            )

        mock_traversal.traverse.side_effect = _disconnecting_traversal

        with patch(
            "synth_engine.modules.subsetting.core.DagTraversal",
            return_value=mock_traversal,
        ):
            se = SubsettingEngine(source_engine=engine, topology=topology, egress=egress)

            with pytest.raises(OperationalError):
                se.run(
                    seed_table="departments",
                    seed_query="SELECT * FROM departments LIMIT 1",
                )

        # Saga guarantee: rollback must fire even after a partial traversal
        egress.rollback.assert_called_once()

    def test_db_disconnect_before_any_rows_triggers_rollback(self) -> None:
        """DB disconnect before any rows are fetched triggers rollback (T49.2).

        When DagTraversal.traverse() raises immediately (e.g., initial seed
        query fails due to connection loss), rollback must still be called.
        """
        from sqlalchemy.exc import OperationalError

        topology = _make_topology(["departments"])
        egress = MagicMock(spec=EgressWriter)
        engine = _make_engine()

        mock_traversal = MagicMock()
        mock_traversal.traverse.side_effect = OperationalError(
            "SSL connection has been closed unexpectedly",
            params=None,
            orig=Exception("SSL error"),
        )

        with patch(
            "synth_engine.modules.subsetting.core.DagTraversal",
            return_value=mock_traversal,
        ):
            se = SubsettingEngine(source_engine=engine, topology=topology, egress=egress)

            with pytest.raises(OperationalError):
                se.run(
                    seed_table="departments",
                    seed_query="SELECT * FROM departments LIMIT 1",
                )

        egress.rollback.assert_called_once()


class TestSchemaTopologyImmutability:
    """Tests for SchemaTopology MappingProxyType runtime immutability.

    Task: P3.5-T3.5.3 -- SchemaTopology immutability fix.
    Verifies that the frozen=True dataclass combined with MappingProxyType
    wrapping in __post_init__ prevents nested dict mutation at runtime.
    """

    def test_columns_is_mapping_proxy(self) -> None:
        """SchemaTopology.columns is a MappingProxyType — not a plain dict."""
        import types

        topology = _make_topology(["users"])
        assert isinstance(topology.columns, types.MappingProxyType)

    def test_foreign_keys_is_mapping_proxy(self) -> None:
        """SchemaTopology.foreign_keys is a MappingProxyType — not a plain dict."""
        import types

        topology = _make_topology(["users"])
        assert isinstance(topology.foreign_keys, types.MappingProxyType)

    def test_columns_append_raises_type_error(self) -> None:
        """Assigning a new outer key to topology.columns raises TypeError.

        MappingProxyType prevents item assignment on the outer mapping.
        Assigning a new key to the proxy itself raises TypeError.
        """
        topology = _make_topology(["users"])
        with pytest.raises(TypeError):
            topology.columns["evil_table"] = ()  # type: ignore[index]

    def test_foreign_keys_mutation_raises_type_error(self) -> None:
        """topology.foreign_keys mutation attempt raises TypeError.

        MappingProxyType prevents item assignment on the outer mapping.
        """
        topology = _make_topology(["users"])
        with pytest.raises(TypeError):
            topology.foreign_keys["evil_table"] = ()  # type: ignore[index]

    def test_columns_read_access_works(self) -> None:
        """MappingProxyType does not break existing read access patterns."""
        topology = _make_topology(["users", "orders"])
        # Key lookup
        assert "users" in topology.columns
        # Iteration
        keys = list(topology.columns.keys())
        assert set(keys) == {"users", "orders"}
        # Value access
        user_cols = topology.columns["users"]
        assert len(user_cols) == 1
        assert user_cols[0].name == "id"

    def test_foreign_keys_read_access_works(self) -> None:
        """MappingProxyType foreign_keys allows all read operations."""
        topology = SchemaTopology(
            table_order=("accounts", "transactions"),
            columns={
                "accounts": (ColumnInfo(name="id", type="INTEGER", primary_key=1, nullable=False),),
                "transactions": (
                    ColumnInfo(name="id", type="INTEGER", primary_key=1, nullable=False),
                    ColumnInfo(name="account_id", type="INTEGER", primary_key=0, nullable=False),
                ),
            },
            foreign_keys={
                "accounts": (),
                "transactions": (
                    ForeignKeyInfo(
                        constrained_columns=("account_id",),
                        referred_table="accounts",
                        referred_columns=("id",),
                    ),
                ),
            },
        )
        # Read access must work
        assert "transactions" in topology.foreign_keys
        fks = topology.foreign_keys["transactions"]
        assert len(fks) == 1
        assert fks[0].referred_table == "accounts"
