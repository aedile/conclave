"""Unit tests for CLI topology loading and schema reflection.

Tests cover the _load_topology() function (ADV-021 PK detection fix) and
the SchemaReflector.get_pk_constraint() method it relies on.

Covers:
  - _load_topology sets PK columns from get_pk_constraint(), not col dict
  - get_pk_constraint() is called for every table in the schema
  - Composite PKs — all PK columns are marked with primary_key >= 1
  - Tables with no PK — all columns have primary_key == 0
  - get_pk_constraint() returning empty dict (missing key safe default)
  - SchemaReflector exposes a get_pk_constraint() public method
  - get_pk_constraint() delegates to SQLAlchemy Inspector

CONSTITUTION Priority 3: TDD RED Phase.
Task: P20-T20.1 — ADV-021 FK Traversal Fix
Task: P26-T26.6 — Split from test_cli.py for maintainability
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# ADV-021 FK Traversal Fix tests
# T19.4 retrospective: tests must exercise the actual CLI topology-building
# code path (_load_topology function), not just SubsettingEngine directly.
# ---------------------------------------------------------------------------


class TestLoadTopologyPrimaryKeyFix:
    """T20.1 / ADV-021 — _load_topology must use get_pk_constraint() for PK detection.

    The bug: col.get('primary_key', 0) always returns 0 because SQLAlchemy's
    Inspector.get_columns() may not include a 'primary_key' key for all
    database backends (notably PostgreSQL via psycopg2/asyncpg).

    The fix: use Inspector.get_pk_constraint(table_name) to get the actual
    list of primary key column names, then set primary_key=1 for columns
    in that list and primary_key=0 for all others.

    Per T19.4 retrospective: tests MUST exercise _load_topology directly,
    not bypass it via SubsettingEngine mocks.
    """

    # Dummy source DSN — no real DB is contacted (create_engine is mocked).
    _SRC_DSN = "postgresql+psycopg2://user:pass@localhost/src"  # pragma: allowlist secret

    def _make_mock_reflector(
        self,
        tables: list[str],
        columns_by_table: dict[str, list[dict[str, Any]]],
        pk_constraints_by_table: dict[str, dict[str, Any]],
        fks_by_table: dict[str, list[dict[str, Any]]] | None = None,
    ) -> MagicMock:
        """Build a mock SchemaReflector with configurable column/PK data.

        Args:
            tables: List of table names to expose.
            columns_by_table: Map from table name to list of column dicts
                (each with 'name', 'type', 'nullable' keys; NO 'primary_key').
            pk_constraints_by_table: Map from table name to PK constraint dict
                with 'constrained_columns' list.
            fks_by_table: Map from table name to list of FK dicts.

        Returns:
            Configured MagicMock standing in for SchemaReflector.
        """
        from synth_engine.modules.mapping.graph import DirectedAcyclicGraph

        mock_reflector = MagicMock()

        # Build a simple DAG for topological_sort
        dag = DirectedAcyclicGraph()
        for table in tables:
            dag.add_node(table)

        mock_reflector.reflect.return_value = dag
        mock_reflector.get_columns.side_effect = lambda t, **kw: columns_by_table.get(t, [])
        mock_reflector.get_pk_constraint.side_effect = lambda t, **kw: pk_constraints_by_table.get(
            t, {"constrained_columns": []}
        )
        mock_reflector.get_foreign_keys.side_effect = lambda t, **kw: (fks_by_table or {}).get(
            t, []
        )

        return mock_reflector

    def test_load_topology_sets_primary_key_from_pk_constraint(self) -> None:
        """_load_topology must set primary_key=1 for PK columns via get_pk_constraint().

        ADV-021: col.get('primary_key', 0) always returns 0 when SQLAlchemy does
        not include 'primary_key' in column dicts.  The fix uses get_pk_constraint()
        to correctly identify PK columns.
        """
        from synth_engine.bootstrapper.cli import _load_topology

        tables = ["persons"]
        # Columns WITHOUT 'primary_key' key — simulates PostgreSQL inspector output
        columns_by_table = {
            "persons": [
                {"name": "id", "type": "INTEGER", "nullable": False},
                {"name": "name", "type": "VARCHAR", "nullable": True},
            ]
        }
        pk_constraints_by_table = {
            "persons": {"constrained_columns": ["id"]},
        }

        mock_reflector = self._make_mock_reflector(
            tables=tables,
            columns_by_table=columns_by_table,
            pk_constraints_by_table=pk_constraints_by_table,
        )

        with (
            patch("synth_engine.bootstrapper.cli.create_engine"),
            patch(
                "synth_engine.bootstrapper.cli.SchemaReflector",
                return_value=mock_reflector,
            ),
        ):
            topology = _load_topology(self._SRC_DSN)

        persons_cols = {col.name: col for col in topology.columns["persons"]}
        assert persons_cols["id"].primary_key >= 1, (
            "ADV-021: 'id' column must have primary_key >= 1 after fix. "
            f"Got primary_key={persons_cols['id'].primary_key}. "
            "Fix: use get_pk_constraint() to set primary_key on ColumnInfo."
        )
        assert persons_cols["name"].primary_key == 0, (
            "Non-PK column 'name' must have primary_key=0. "
            f"Got primary_key={persons_cols['name'].primary_key}."
        )

    def test_load_topology_calls_get_pk_constraint_for_each_table(self) -> None:
        """_load_topology must call get_pk_constraint() for every table it processes.

        ADV-021: if get_pk_constraint() is never called, the fix is not applied.
        This test verifies the method is actually invoked in the topology-building
        code path — not just that the code compiles correctly.
        """
        from synth_engine.bootstrapper.cli import _load_topology

        tables = ["persons", "accounts"]
        columns_by_table = {
            "persons": [{"name": "id", "type": "INTEGER", "nullable": False}],
            "accounts": [{"name": "acct_id", "type": "INTEGER", "nullable": False}],
        }
        pk_constraints_by_table = {
            "persons": {"constrained_columns": ["id"]},
            "accounts": {"constrained_columns": ["acct_id"]},
        }

        mock_reflector = self._make_mock_reflector(
            tables=tables,
            columns_by_table=columns_by_table,
            pk_constraints_by_table=pk_constraints_by_table,
        )

        with (
            patch("synth_engine.bootstrapper.cli.create_engine"),
            patch(
                "synth_engine.bootstrapper.cli.SchemaReflector",
                return_value=mock_reflector,
            ),
        ):
            _load_topology(self._SRC_DSN)

        # get_pk_constraint must be called once per table
        assert mock_reflector.get_pk_constraint.call_count == len(tables), (
            f"get_pk_constraint() must be called once per table ({len(tables)} tables). "
            f"Was called {mock_reflector.get_pk_constraint.call_count} time(s). "
            "ADV-021 fix requires calling get_pk_constraint() in _load_topology."
        )

    def test_load_topology_composite_pk_columns_all_marked(self) -> None:
        """_load_topology must mark all columns of a composite PK with primary_key >= 1.

        ADV-012 compliance: composite PKs use incrementing integers (1, 2, ...).
        Both columns in a composite PK must have primary_key >= 1.
        """
        from synth_engine.bootstrapper.cli import _load_topology

        tables = ["order_items"]
        columns_by_table = {
            "order_items": [
                {"name": "order_id", "type": "INTEGER", "nullable": False},
                {"name": "item_id", "type": "INTEGER", "nullable": False},
                {"name": "quantity", "type": "INTEGER", "nullable": True},
            ]
        }
        pk_constraints_by_table = {
            "order_items": {"constrained_columns": ["order_id", "item_id"]},
        }

        mock_reflector = self._make_mock_reflector(
            tables=tables,
            columns_by_table=columns_by_table,
            pk_constraints_by_table=pk_constraints_by_table,
        )

        with (
            patch("synth_engine.bootstrapper.cli.create_engine"),
            patch(
                "synth_engine.bootstrapper.cli.SchemaReflector",
                return_value=mock_reflector,
            ),
        ):
            topology = _load_topology(self._SRC_DSN)

        cols = {col.name: col for col in topology.columns["order_items"]}
        assert cols["order_id"].primary_key >= 1, (
            "composite PK column 'order_id' must have primary_key >= 1"
        )
        assert cols["item_id"].primary_key >= 1, (
            "composite PK column 'item_id' must have primary_key >= 1"
        )
        assert cols["quantity"].primary_key == 0, (
            "non-PK column 'quantity' must have primary_key == 0"
        )

    def test_load_topology_table_with_no_pk_all_columns_zero(self) -> None:
        """_load_topology must set primary_key=0 for all columns when no PK exists.

        A table without a primary key constraint must have all columns with
        primary_key=0.  This is the safe default.
        """
        from synth_engine.bootstrapper.cli import _load_topology

        tables = ["log_entries"]
        columns_by_table = {
            "log_entries": [
                {"name": "ts", "type": "TIMESTAMP", "nullable": True},
                {"name": "msg", "type": "TEXT", "nullable": True},
            ]
        }
        pk_constraints_by_table = {
            "log_entries": {"constrained_columns": []},
        }

        mock_reflector = self._make_mock_reflector(
            tables=tables,
            columns_by_table=columns_by_table,
            pk_constraints_by_table=pk_constraints_by_table,
        )

        with (
            patch("synth_engine.bootstrapper.cli.create_engine"),
            patch(
                "synth_engine.bootstrapper.cli.SchemaReflector",
                return_value=mock_reflector,
            ),
        ):
            topology = _load_topology(self._SRC_DSN)

        cols = {col.name: col for col in topology.columns["log_entries"]}
        assert cols["ts"].primary_key == 0
        assert cols["msg"].primary_key == 0

    def test_load_topology_get_pk_constraint_missing_key_safe_default(self) -> None:
        """_load_topology must handle get_pk_constraint() returning empty dict gracefully.

        If get_pk_constraint() returns {} (no 'constrained_columns' key),
        the code must not raise — it must treat the table as having no PK.
        """
        from synth_engine.bootstrapper.cli import _load_topology

        tables = ["strange_table"]
        columns_by_table = {
            "strange_table": [
                {"name": "col1", "type": "TEXT", "nullable": True},
            ]
        }
        # Returns empty dict — simulates a backend that omits constrained_columns
        pk_constraints_by_table = {
            "strange_table": {},
        }

        mock_reflector = self._make_mock_reflector(
            tables=tables,
            columns_by_table=columns_by_table,
            pk_constraints_by_table=pk_constraints_by_table,
        )

        with (
            patch("synth_engine.bootstrapper.cli.create_engine"),
            patch(
                "synth_engine.bootstrapper.cli.SchemaReflector",
                return_value=mock_reflector,
            ),
        ):
            # Must not raise
            topology = _load_topology(self._SRC_DSN)

        cols = {col.name: col for col in topology.columns["strange_table"]}
        assert cols["col1"].primary_key == 0


class TestSchemaReflectorGetPkConstraint:
    """T20.1 / ADV-021 — SchemaReflector must expose get_pk_constraint().

    The CLI's _load_topology now calls reflector.get_pk_constraint(table).
    SchemaReflector must expose this method, wrapping the SQLAlchemy inspector.
    """

    def test_schema_reflector_has_get_pk_constraint_method(self) -> None:
        """SchemaReflector must have a get_pk_constraint() public method.

        ADV-021: _load_topology calls reflector.get_pk_constraint(table).
        If SchemaReflector does not expose this method, the fix cannot work.
        """
        from synth_engine.modules.mapping.reflection import SchemaReflector

        assert hasattr(SchemaReflector, "get_pk_constraint"), (
            "SchemaReflector must have a get_pk_constraint() method. "
            "ADV-021 fix: _load_topology calls this method to identify PK columns."
        )

    def test_get_pk_constraint_calls_inspector(self) -> None:
        """get_pk_constraint() must delegate to SQLAlchemy Inspector.get_pk_constraint().

        The method must call self._inspector.get_pk_constraint() with the table
        name and schema, then return the result.
        """
        from unittest.mock import MagicMock, patch

        from sqlalchemy import create_engine

        mock_inspector = MagicMock()
        mock_inspector.get_table_names.return_value = []
        mock_inspector.get_pk_constraint.return_value = {"constrained_columns": ["id"]}

        engine = create_engine("sqlite:///:memory:")

        with patch("synth_engine.modules.mapping.reflection.inspect", return_value=mock_inspector):
            from synth_engine.modules.mapping.reflection import SchemaReflector

            reflector = SchemaReflector(engine=engine)

        result = reflector.get_pk_constraint("mytable", schema="public")

        mock_inspector.get_pk_constraint.assert_called_once_with("mytable", schema="public")
        assert result == {"constrained_columns": ["id"]}
