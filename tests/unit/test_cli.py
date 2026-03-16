"""Unit tests for the conclave-subset CLI entrypoint.

Tests validate argument parsing, input validation, engine invocation,
and error-path behaviour using click.testing.CliRunner with mocked
dependencies — no live PostgreSQL required.

CONSTITUTION Priority 0: Security — connection strings are validated and
never echoed in error messages.
CONSTITUTION Priority 3: TDD RED Phase.
Task: P3.5-T3.5.4 — Bootstrapper Wiring & Minimal CLI Entrypoint
Task: P20-T20.1 — ADV-021 FK Traversal Fix
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from synth_engine.bootstrapper.cli import subset

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subset_result(
    tables: list[str] | None = None,
    row_counts: dict[str, int] | None = None,
) -> MagicMock:
    """Create a mock SubsetResult with the given tables and row counts.

    Args:
        tables: List of table names written.
        row_counts: Mapping of table name to row count.

    Returns:
        A MagicMock configured to mimic SubsetResult.
    """
    result = MagicMock()
    result.tables_written = tables or ["persons", "accounts", "transactions"]
    result.row_counts = row_counts or {"persons": 5, "accounts": 10, "transactions": 30}
    return result


def _mock_topology() -> MagicMock:
    """Return a minimal mock topology accepted by SubsettingEngine."""
    topology = MagicMock()
    topology.table_order = ("persons", "accounts", "transactions")
    return topology


# ---------------------------------------------------------------------------
# Valid invocation tests
# ---------------------------------------------------------------------------


class TestCLIValidInvocations:
    """Test the happy-path scenarios for the subset CLI command."""

    def test_help_succeeds(self) -> None:
        """--help exits 0 without errors."""
        runner = CliRunner()
        result = runner.invoke(subset, ["--help"])
        assert result.exit_code == 0
        assert "--source" in result.output
        assert "--target" in result.output
        assert "--seed-table" in result.output
        assert "--seed-query" in result.output
        assert "--mask" in result.output

    def test_valid_args_with_masking_exits_zero(self) -> None:
        """Valid args with --mask calls the engine and exits 0."""
        runner = CliRunner()
        mock_result = _make_subset_result()

        with (
            patch("synth_engine.bootstrapper.cli.create_engine"),
            patch("synth_engine.bootstrapper.cli.EgressWriter"),
            patch("synth_engine.bootstrapper.cli._load_topology", return_value=_mock_topology()),
            patch("synth_engine.bootstrapper.cli.SubsettingEngine") as mock_engine_cls,
            patch(
                "synth_engine.bootstrapper.cli._build_masking_transformer"
            ) as mock_transformer_builder,
        ):
            mock_engine_instance = MagicMock()
            mock_engine_instance.run.return_value = mock_result
            mock_engine_cls.return_value = mock_engine_instance
            mock_transformer_builder.return_value = MagicMock(
                spec=Callable[[str, dict[str, Any]], dict[str, Any]]
            )

            result = runner.invoke(
                subset,
                [
                    "--source",
                    "postgresql+psycopg2://user:pass@localhost/src",  # pragma: allowlist secret
                    "--target",
                    "postgresql+psycopg2://user:pass@localhost/tgt",  # pragma: allowlist secret
                    "--seed-table",
                    "persons",
                    "--seed-query",
                    "SELECT * FROM persons LIMIT 5",
                    "--mask",
                ],
            )

        assert result.exit_code == 0, f"Unexpected exit: {result.output}"
        # Verify the engine was invoked with the correct args
        mock_engine_instance.run.assert_called_once_with(
            seed_table="persons",
            seed_query="SELECT * FROM persons LIMIT 5",
        )

    def test_valid_args_without_masking_exits_zero(self) -> None:
        """Valid args with --no-mask wires no transformer and exits 0."""
        runner = CliRunner()
        mock_result = _make_subset_result()

        with (
            patch("synth_engine.bootstrapper.cli.create_engine"),
            patch("synth_engine.bootstrapper.cli.EgressWriter"),
            patch("synth_engine.bootstrapper.cli._load_topology", return_value=_mock_topology()),
            patch("synth_engine.bootstrapper.cli.SubsettingEngine") as mock_engine_cls,
            patch(
                "synth_engine.bootstrapper.cli._build_masking_transformer"
            ) as mock_transformer_builder,
        ):
            mock_engine_instance = MagicMock()
            mock_engine_instance.run.return_value = mock_result
            mock_engine_cls.return_value = mock_engine_instance

            result = runner.invoke(
                subset,
                [
                    "--source",
                    "postgresql+psycopg2://user:pass@localhost/src",  # pragma: allowlist secret
                    "--target",
                    "postgresql+psycopg2://user:pass@localhost/tgt",  # pragma: allowlist secret
                    "--seed-table",
                    "persons",
                    "--seed-query",
                    "SELECT * FROM persons LIMIT 5",
                    "--no-mask",
                ],
            )

        assert result.exit_code == 0, f"Unexpected exit: {result.output}"
        # With --no-mask the transformer builder must NOT be called
        mock_transformer_builder.assert_not_called()
        # SubsettingEngine must be constructed with row_transformer=None
        _, kwargs = mock_engine_cls.call_args
        assert kwargs.get("row_transformer") is None

    def test_output_includes_row_summary(self) -> None:
        """Output includes the row count summary for each table written."""
        runner = CliRunner()
        mock_result = _make_subset_result(
            tables=["persons", "accounts"],
            row_counts={"persons": 3, "accounts": 6},
        )

        with (
            patch("synth_engine.bootstrapper.cli.create_engine"),
            patch("synth_engine.bootstrapper.cli.EgressWriter"),
            patch("synth_engine.bootstrapper.cli._load_topology", return_value=_mock_topology()),
            patch("synth_engine.bootstrapper.cli.SubsettingEngine") as mock_engine_cls,
            patch("synth_engine.bootstrapper.cli._build_masking_transformer"),
        ):
            mock_engine_instance = MagicMock()
            mock_engine_instance.run.return_value = mock_result
            mock_engine_cls.return_value = mock_engine_instance

            result = runner.invoke(
                subset,
                [
                    "--source",
                    "postgresql+psycopg2://user:pass@localhost/src",  # pragma: allowlist secret
                    "--target",
                    "postgresql+psycopg2://user:pass@localhost/tgt",  # pragma: allowlist secret
                    "--seed-table",
                    "persons",
                    "--seed-query",
                    "SELECT * FROM persons LIMIT 3",
                ],
            )

        assert result.exit_code == 0
        assert "persons" in result.output
        assert "3" in result.output


# ---------------------------------------------------------------------------
# Validation failure tests
# ---------------------------------------------------------------------------


class TestCLIValidationErrors:
    """Test that invalid inputs are caught cleanly with exit code 1."""

    def test_non_select_query_exits_one(self) -> None:
        """--seed-query that is not a SELECT statement exits 1 with clear error."""
        runner = CliRunner()
        result = runner.invoke(
            subset,
            [
                "--source",
                "postgresql+psycopg2://user:pass@localhost/src",  # pragma: allowlist secret
                "--target",
                "postgresql+psycopg2://user:pass@localhost/tgt",  # pragma: allowlist secret
                "--seed-table",
                "persons",
                "--seed-query",
                "DROP TABLE persons",
            ],
        )

        assert result.exit_code == 1
        # Error message must mention SELECT clearly
        assert "SELECT" in result.output.upper()

    def test_delete_query_exits_one(self) -> None:
        """DELETE seed-query is rejected with exit code 1."""
        runner = CliRunner()
        result = runner.invoke(
            subset,
            [
                "--source",
                "postgresql+psycopg2://user:pass@localhost/src",  # pragma: allowlist secret
                "--target",
                "postgresql+psycopg2://user:pass@localhost/tgt",  # pragma: allowlist secret
                "--seed-table",
                "persons",
                "--seed-query",
                "DELETE FROM persons",
            ],
        )

        assert result.exit_code == 1

    def test_invalid_source_connection_string_exits_one(self) -> None:
        """Malformed --source DSN exits 1 with a clear error."""
        runner = CliRunner()
        result = runner.invoke(
            subset,
            [
                "--source",
                "not-a-valid-dsn",
                "--target",
                "postgresql+psycopg2://user:pass@localhost/tgt",  # pragma: allowlist secret
                "--seed-table",
                "persons",
                "--seed-query",
                "SELECT * FROM persons LIMIT 5",
            ],
        )

        assert result.exit_code == 1
        # The error must mention "source" to guide the operator
        assert "source" in result.output.lower()

    def test_invalid_target_connection_string_exits_one(self) -> None:
        """Malformed --target DSN exits 1 with a clear error."""
        runner = CliRunner()
        result = runner.invoke(
            subset,
            [
                "--source",
                "postgresql+psycopg2://user:pass@localhost/src",  # pragma: allowlist secret
                "--target",
                "mysql://localhost/tgt",
                "--seed-table",
                "persons",
                "--seed-query",
                "SELECT * FROM persons LIMIT 5",
            ],
        )

        assert result.exit_code == 1
        assert "target" in result.output.lower()

    def test_missing_required_source_arg(self) -> None:
        """Omitting --source causes click to exit with a usage error."""
        runner = CliRunner()
        result = runner.invoke(
            subset,
            [
                "--target",
                "postgresql+psycopg2://user:pass@localhost/tgt",  # pragma: allowlist secret
                "--seed-table",
                "persons",
                "--seed-query",
                "SELECT * FROM persons LIMIT 5",
            ],
        )

        # Click's built-in missing-arg handling exits with code 2
        assert result.exit_code == 2

    def test_empty_seed_query_exits_one(self) -> None:
        """Empty --seed-query is rejected with exit code 1."""
        runner = CliRunner()
        result = runner.invoke(
            subset,
            [
                "--source",
                "postgresql+psycopg2://user:pass@localhost/src",  # pragma: allowlist secret
                "--target",
                "postgresql+psycopg2://user:pass@localhost/tgt",  # pragma: allowlist secret
                "--seed-table",
                "persons",
                "--seed-query",
                "   ",
            ],
        )

        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------


class TestCLIErrorPaths:
    """Test that runtime exceptions from the engine surface cleanly."""

    def test_engine_exception_exits_one(self) -> None:
        """Exception raised by SubsettingEngine.run() exits 1 with message."""
        runner = CliRunner()

        with (
            patch("synth_engine.bootstrapper.cli.create_engine"),
            patch("synth_engine.bootstrapper.cli.EgressWriter"),
            patch("synth_engine.bootstrapper.cli._load_topology", return_value=_mock_topology()),
            patch("synth_engine.bootstrapper.cli.SubsettingEngine") as mock_engine_cls,
            patch("synth_engine.bootstrapper.cli._build_masking_transformer"),
        ):
            mock_engine_instance = MagicMock()
            mock_engine_instance.run.side_effect = RuntimeError("DB connection refused")
            mock_engine_cls.return_value = mock_engine_instance

            result = runner.invoke(
                subset,
                [
                    "--source",
                    "postgresql+psycopg2://user:pass@localhost/src",  # pragma: allowlist secret
                    "--target",
                    "postgresql+psycopg2://user:pass@localhost/tgt",  # pragma: allowlist secret
                    "--seed-table",
                    "persons",
                    "--seed-query",
                    "SELECT * FROM persons LIMIT 5",
                ],
            )

        assert result.exit_code == 1
        # Must not print a traceback — clean error message only
        assert "Traceback" not in result.output

    def test_connection_string_is_not_echoed_in_error_output(self) -> None:
        """Credential-bearing DSN must never appear in CLI error output."""
        runner = CliRunner()
        # The password "s3cr3t" must not appear in the output
        result = runner.invoke(
            subset,
            [
                "--source",
                "postgresql+psycopg2://admin:s3cr3t@localhost/src",  # pragma: allowlist secret
                "--target",
                "postgresql+psycopg2://user:pass@localhost/tgt",  # pragma: allowlist secret
                "--seed-table",
                "persons",
                "--seed-query",
                "DROP TABLE persons",
            ],
        )

        assert result.exit_code == 1
        assert "s3cr3t" not in result.output


# ---------------------------------------------------------------------------
# Masking transformer builder
# ---------------------------------------------------------------------------


class TestBuildMaskingTransformer:
    """Tests for the _build_masking_transformer() factory function.

    Covers:
    - Factory returns a callable (smoke test).
    - Non-PII tables pass through unchanged (no-mask path).
    - Input dict is never mutated (pure function contract).
    - PII columns in the 'persons' table are replaced with masked values
      (QA finding: lines 100-104 of cli.py had zero coverage before this).
    - None-valued PII columns pass through unchanged (null guard branch).
    """

    def test_build_masking_transformer_returns_callable(self) -> None:
        """_build_masking_transformer() returns a callable."""
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        assert callable(transformer)

    def test_masking_transformer_passthrough_for_unknown_table(self) -> None:
        """Transformer returns row unchanged for tables not in masking config."""
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row = {"id": 1, "amount": 100}
        result = transformer("transactions", row)
        assert result == row

    def test_masking_transformer_does_not_modify_input_dict(self) -> None:
        """Transformer must not mutate the input row dict (pure function contract)."""
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        original_row = {"id": 1, "amount": 100}
        original_copy = dict(original_row)
        transformer("transactions", original_row)
        assert original_row == original_copy

    def test_masking_transformer_masks_pii_columns_for_persons_table(self) -> None:
        """Transformer replaces PII column values for the 'persons' table."""
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row: dict[str, Any] = {
            "id": 1,
            "full_name": "Alice Smith",
            "email": "alice@example.com",
            "ssn": "123-45-6789",
        }
        result = transformer("persons", row)
        assert result["full_name"] != "Alice Smith"
        assert result["email"] != "alice@example.com"
        assert result["ssn"] != "123-45-6789"
        assert result["id"] == 1  # non-PII column unchanged

    def test_masking_transformer_passthrough_for_none_pii_values(self) -> None:
        """Transformer passes through None-valued PII columns unchanged."""
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row: dict[str, Any] = {
            "id": 1,
            "full_name": None,
            "email": None,
            "ssn": None,
        }
        result = transformer("persons", row)
        assert result["full_name"] is None
        assert result["email"] is None
        assert result["ssn"] is None


# ---------------------------------------------------------------------------
# Default --mask flag
# ---------------------------------------------------------------------------


class TestCLIDefaultMaskFlag:
    """Test that --mask is the default behaviour (not --no-mask)."""

    def test_default_mask_flag_is_true(self) -> None:
        """Omitting --mask/--no-mask defaults to masking enabled."""
        runner = CliRunner()
        mock_result = _make_subset_result()

        captured_kwargs: dict[str, Any] = {}

        def capture_init(**kwargs: Any) -> MagicMock:
            captured_kwargs.update(kwargs)
            instance = MagicMock()
            instance.run.return_value = mock_result
            return instance

        with (
            patch("synth_engine.bootstrapper.cli.create_engine"),
            patch("synth_engine.bootstrapper.cli.EgressWriter"),
            patch("synth_engine.bootstrapper.cli._load_topology", return_value=_mock_topology()),
            patch("synth_engine.bootstrapper.cli.SubsettingEngine", side_effect=capture_init),
            patch("synth_engine.bootstrapper.cli._build_masking_transformer") as mock_builder,
        ):
            mock_builder.return_value = lambda t, r: r

            result = runner.invoke(
                subset,
                [
                    "--source",
                    "postgresql+psycopg2://user:pass@localhost/src",  # pragma: allowlist secret
                    "--target",
                    "postgresql+psycopg2://user:pass@localhost/tgt",  # pragma: allowlist secret
                    "--seed-table",
                    "persons",
                    "--seed-query",
                    "SELECT * FROM persons LIMIT 5",
                    # No --mask or --no-mask — default should apply masking
                ],
            )

        assert result.exit_code == 0, f"Unexpected exit: {result.output}"
        mock_builder.assert_called_once()
        assert captured_kwargs.get("row_transformer") is not None


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


# ---------------------------------------------------------------------------
# Pytest mark
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit
