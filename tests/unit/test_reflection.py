"""Unit tests for the SchemaReflector class.

All tests use mocked SQLAlchemy inspect() calls -- no database required.

Task: P3-T3.2 -- Relational Mapping & Topological Sort
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from sqlalchemy import Engine

from synth_engine.modules.mapping.graph import DirectedAcyclicGraph
from synth_engine.modules.mapping.reflection import SchemaReflector

_INSPECT = "synth_engine.modules.mapping.reflection.inspect"


def _make_engine() -> Engine:
    """Create a mock SQLAlchemy Engine for testing."""
    return MagicMock(spec=Engine)


def _make_inspector(
    tables: list[str],
    columns_by_table: dict[str, list[dict[str, Any]]] | None = None,
    fks_by_table: dict[str, list[dict[str, Any]]] | None = None,
) -> MagicMock:
    """Build a mock SQLAlchemy Inspector.

    Args:
        tables: Table names to return from get_table_names().
        columns_by_table: Column metadata per table.
        fks_by_table: Foreign key metadata per table.

    Returns:
        A configured MagicMock implementing the Inspector interface.
    """
    inspector = MagicMock()
    inspector.get_table_names.return_value = tables

    columns_by_table = columns_by_table or {}
    fks_by_table = fks_by_table or {}

    def _get_columns(table_name: str, schema: str = "public") -> list[dict[str, Any]]:
        return columns_by_table.get(table_name, [])

    def _get_foreign_keys(table_name: str, schema: str = "public") -> list[dict[str, Any]]:
        return fks_by_table.get(table_name, [])

    inspector.get_columns.side_effect = _get_columns
    inspector.get_foreign_keys.side_effect = _get_foreign_keys
    return inspector


class TestSchemaReflectorReflect:
    """Tests for SchemaReflector.reflect() DAG construction."""

    def test_reflect_builds_dag_with_correct_nodes(self) -> None:
        """reflect() returns a DAG whose nodes match the reflected tables."""
        engine = _make_engine()
        tables = ["users", "orders", "products"]
        mock_inspector = _make_inspector(tables)

        with patch(_INSPECT, return_value=mock_inspector):
            reflector = SchemaReflector(engine)
            dag = reflector.reflect()

        assert isinstance(dag, DirectedAcyclicGraph)
        assert dag.nodes() == set(tables)

    def test_reflect_adds_fk_edges(self) -> None:
        """reflect() adds DAG edges for each explicit foreign key relationship."""
        engine = _make_engine()
        tables = ["users", "orders"]
        # orders.user_id -> users.id
        fks_by_table = {
            "orders": [
                {
                    "constrained_columns": ["user_id"],
                    "referred_table": "users",
                    "referred_columns": ["id"],
                }
            ]
        }
        mock_inspector = _make_inspector(tables, fks_by_table=fks_by_table)

        with patch(_INSPECT, return_value=mock_inspector):
            reflector = SchemaReflector(engine)
            dag = reflector.reflect()

        # Edge: users (parent) -> orders (child, holds FK)
        assert ("users", "orders") in dag.edges()

    def test_reflect_multiple_fk_edges(self) -> None:
        """reflect() handles multiple FKs across multiple tables."""
        engine = _make_engine()
        tables = ["organizations", "departments", "employees"]
        fks_by_table = {
            "departments": [
                {
                    "constrained_columns": ["org_id"],
                    "referred_table": "organizations",
                    "referred_columns": ["id"],
                }
            ],
            "employees": [
                {
                    "constrained_columns": ["dept_id"],
                    "referred_table": "departments",
                    "referred_columns": ["id"],
                }
            ],
        }
        mock_inspector = _make_inspector(tables, fks_by_table=fks_by_table)

        with patch(_INSPECT, return_value=mock_inspector):
            reflector = SchemaReflector(engine)
            dag = reflector.reflect()

        assert ("organizations", "departments") in dag.edges()
        assert ("departments", "employees") in dag.edges()

    def test_reflect_empty_schema_returns_empty_dag(self) -> None:
        """reflect() on a schema with no tables returns an empty DAG."""
        engine = _make_engine()
        mock_inspector = _make_inspector([])

        with patch(_INSPECT, return_value=mock_inspector):
            reflector = SchemaReflector(engine)
            dag = reflector.reflect()

        assert isinstance(dag, DirectedAcyclicGraph)
        assert dag.nodes() == set()
        assert dag.edges() == []

    def test_reflect_uses_only_explicit_fk_edges(self) -> None:
        """reflect() uses only FK-defined edges; virtual FKs are not inferred."""
        engine = _make_engine()
        # Tables exist but no FKs defined -- no edges should be created
        tables = ["table_a", "table_b"]
        mock_inspector = _make_inspector(tables)

        with patch(_INSPECT, return_value=mock_inspector):
            reflector = SchemaReflector(engine)
            dag = reflector.reflect()

        assert dag.nodes() == {"table_a", "table_b"}
        assert dag.edges() == []


class TestSchemaReflectorGetTables:
    """Tests for SchemaReflector.get_tables()."""

    def test_get_tables_returns_list(self) -> None:
        """get_tables() returns a list of strings."""
        engine = _make_engine()
        tables = ["alpha", "beta", "gamma"]
        mock_inspector = _make_inspector(tables)

        with patch(_INSPECT, return_value=mock_inspector):
            reflector = SchemaReflector(engine)
            result = reflector.get_tables()

        assert isinstance(result, list)
        assert result == tables

    def test_get_tables_empty_schema(self) -> None:
        """get_tables() returns an empty list for a schema with no tables."""
        engine = _make_engine()
        mock_inspector = _make_inspector([])

        with patch(_INSPECT, return_value=mock_inspector):
            reflector = SchemaReflector(engine)
            assert reflector.get_tables() == []


class TestSchemaReflectorGetColumns:
    """Tests for SchemaReflector.get_columns()."""

    def test_get_columns_returns_list(self) -> None:
        """get_columns() returns a list of column descriptor dicts."""
        engine = _make_engine()
        columns = [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": 1},
            {"name": "name", "type": "VARCHAR", "nullable": True, "primary_key": 0},
        ]
        mock_inspector = _make_inspector(["users"], columns_by_table={"users": columns})

        with patch(_INSPECT, return_value=mock_inspector):
            reflector = SchemaReflector(engine)
            result = reflector.get_columns("users")

        assert isinstance(result, list)
        assert len(result) == 2

    def test_get_columns_returns_primary_key_ge_1(self) -> None:
        """Composite PK columns have primary_key >= 1 (ADV-012 compliance).

        Composite PKs use incrementing integers (1, 2, 3...) not just 0/1.
        The SchemaReflector must pass this through unchanged.
        """
        engine = _make_engine()
        # Composite PK: (order_id=1, product_id=2)
        columns = [
            {"name": "order_id", "type": "INTEGER", "nullable": False, "primary_key": 1},
            {"name": "product_id", "type": "INTEGER", "nullable": False, "primary_key": 2},
            {"name": "quantity", "type": "INTEGER", "nullable": False, "primary_key": 0},
        ]
        mock_inspector = _make_inspector(["order_items"], columns_by_table={"order_items": columns})

        with patch(_INSPECT, return_value=mock_inspector):
            reflector = SchemaReflector(engine)
            result = reflector.get_columns("order_items")

        pk_columns = [col for col in result if col["primary_key"] >= 1]
        assert len(pk_columns) == 2
        pk_positions = {col["name"]: col["primary_key"] for col in pk_columns}
        assert pk_positions["order_id"] == 1
        assert pk_positions["product_id"] == 2

    def test_get_columns_non_pk_has_primary_key_zero(self) -> None:
        """Non-PK columns have primary_key == 0."""
        engine = _make_engine()
        columns = [
            {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": 1},
            {"name": "email", "type": "VARCHAR", "nullable": False, "primary_key": 0},
        ]
        mock_inspector = _make_inspector(["accounts"], columns_by_table={"accounts": columns})

        with patch(_INSPECT, return_value=mock_inspector):
            reflector = SchemaReflector(engine)
            result = reflector.get_columns("accounts")

        non_pk = [col for col in result if col["name"] == "email"]
        assert non_pk[0]["primary_key"] == 0


class TestSchemaReflectorGetForeignKeys:
    """Tests for SchemaReflector.get_foreign_keys()."""

    def test_get_foreign_keys_returns_list(self) -> None:
        """get_foreign_keys() returns a list of FK descriptor dicts."""
        engine = _make_engine()
        fks = [
            {
                "constrained_columns": ["user_id"],
                "referred_table": "users",
                "referred_columns": ["id"],
            }
        ]
        mock_inspector = _make_inspector(["orders"], fks_by_table={"orders": fks})

        with patch(_INSPECT, return_value=mock_inspector):
            reflector = SchemaReflector(engine)
            result = reflector.get_foreign_keys("orders")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["referred_table"] == "users"

    def test_get_foreign_keys_empty_for_no_fk_table(self) -> None:
        """get_foreign_keys() returns empty list for tables with no FKs."""
        engine = _make_engine()
        mock_inspector = _make_inspector(["standalone"])

        with patch(_INSPECT, return_value=mock_inspector):
            reflector = SchemaReflector(engine)
            result = reflector.get_foreign_keys("standalone")

        assert result == []
