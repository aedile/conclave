"""Unit tests for the conclave-subset CLI entrypoint.

Tests validate argument parsing, input validation, engine invocation,
and error-path behaviour using click.testing.CliRunner with mocked
dependencies — no live PostgreSQL required.

CONSTITUTION Priority 0: Security — connection strings are validated and
never echoed in error messages.
CONSTITUTION Priority 3: TDD RED Phase.
Task: P3.5-T3.5.4 — Bootstrapper Wiring & Minimal CLI Entrypoint
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from synth_engine.cli import subset


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
            patch("synth_engine.cli.create_engine") as mock_create_engine,
            patch("synth_engine.cli.EgressWriter") as mock_egress_cls,
            patch("synth_engine.cli.SubsettingEngine") as mock_engine_cls,
            patch("synth_engine.cli._build_masking_transformer") as mock_transformer_builder,
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
                    "postgresql+psycopg2://user:pass@localhost/src",
                    "--target",
                    "postgresql+psycopg2://user:pass@localhost/tgt",
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
            patch("synth_engine.cli.create_engine"),
            patch("synth_engine.cli.EgressWriter"),
            patch("synth_engine.cli.SubsettingEngine") as mock_engine_cls,
            patch("synth_engine.cli._build_masking_transformer") as mock_transformer_builder,
        ):
            mock_engine_instance = MagicMock()
            mock_engine_instance.run.return_value = mock_result
            mock_engine_cls.return_value = mock_engine_instance

            result = runner.invoke(
                subset,
                [
                    "--source",
                    "postgresql+psycopg2://user:pass@localhost/src",
                    "--target",
                    "postgresql+psycopg2://user:pass@localhost/tgt",
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
            patch("synth_engine.cli.create_engine"),
            patch("synth_engine.cli.EgressWriter"),
            patch("synth_engine.cli.SubsettingEngine") as mock_engine_cls,
            patch("synth_engine.cli._build_masking_transformer"),
        ):
            mock_engine_instance = MagicMock()
            mock_engine_instance.run.return_value = mock_result
            mock_engine_cls.return_value = mock_engine_instance

            result = runner.invoke(
                subset,
                [
                    "--source",
                    "postgresql+psycopg2://user:pass@localhost/src",
                    "--target",
                    "postgresql+psycopg2://user:pass@localhost/tgt",
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
                "postgresql+psycopg2://user:pass@localhost/src",
                "--target",
                "postgresql+psycopg2://user:pass@localhost/tgt",
                "--seed-table",
                "persons",
                "--seed-query",
                "DROP TABLE persons",
            ],
        )

        assert result.exit_code == 1
        # Error message must be informative but must NOT echo the query verbatim
        # in a way that leaks dangerous SQL — just a clear description.
        assert "SELECT" in result.output.upper()

    def test_delete_query_exits_one(self) -> None:
        """DELETE seed-query is rejected with exit code 1."""
        runner = CliRunner()
        result = runner.invoke(
            subset,
            [
                "--source",
                "postgresql+psycopg2://user:pass@localhost/src",
                "--target",
                "postgresql+psycopg2://user:pass@localhost/tgt",
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
                "postgresql+psycopg2://user:pass@localhost/tgt",
                "--seed-table",
                "persons",
                "--seed-query",
                "SELECT * FROM persons LIMIT 5",
            ],
        )

        assert result.exit_code == 1
        # The invalid DSN must NOT appear verbatim in the error output
        # (connection strings may contain credentials)
        assert "source" in result.output.lower()

    def test_invalid_target_connection_string_exits_one(self) -> None:
        """Malformed --target DSN exits 1 with a clear error."""
        runner = CliRunner()
        result = runner.invoke(
            subset,
            [
                "--source",
                "postgresql+psycopg2://user:pass@localhost/src",
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
                "postgresql+psycopg2://user:pass@localhost/tgt",
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
                "postgresql+psycopg2://user:pass@localhost/src",
                "--target",
                "postgresql+psycopg2://user:pass@localhost/tgt",
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
            patch("synth_engine.cli.create_engine"),
            patch("synth_engine.cli.EgressWriter"),
            patch("synth_engine.cli.SubsettingEngine") as mock_engine_cls,
            patch("synth_engine.cli._build_masking_transformer"),
        ):
            mock_engine_instance = MagicMock()
            mock_engine_instance.run.side_effect = RuntimeError("DB connection refused")
            mock_engine_cls.return_value = mock_engine_instance

            result = runner.invoke(
                subset,
                [
                    "--source",
                    "postgresql+psycopg2://user:pass@localhost/src",
                    "--target",
                    "postgresql+psycopg2://user:pass@localhost/tgt",
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
                "postgresql+psycopg2://admin:s3cr3t@localhost/src",
                "--target",
                "postgresql+psycopg2://user:pass@localhost/tgt",
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
    """Tests for the _build_masking_transformer() factory function."""

    def test_build_masking_transformer_returns_callable(self) -> None:
        """_build_masking_transformer() returns a callable."""
        from synth_engine.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        assert callable(transformer)

    def test_masking_transformer_passthrough_for_unknown_table(self) -> None:
        """Transformer returns row unchanged for tables not in masking config."""
        from synth_engine.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row = {"id": 1, "amount": 100}
        result = transformer("transactions", row)
        assert result == row

    def test_masking_transformer_does_not_modify_input_dict(self) -> None:
        """Transformer must not mutate the input row dict (pure function contract)."""
        from synth_engine.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        original_row = {"id": 1, "amount": 100}
        original_copy = dict(original_row)
        transformer("transactions", original_row)
        assert original_row == original_copy


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
            patch("synth_engine.cli.create_engine"),
            patch("synth_engine.cli.EgressWriter"),
            patch("synth_engine.cli.SubsettingEngine", side_effect=capture_init),
            patch("synth_engine.cli._build_masking_transformer") as mock_builder,
        ):
            mock_builder.return_value = lambda t, r: r

            result = runner.invoke(
                subset,
                [
                    "--source",
                    "postgresql+psycopg2://user:pass@localhost/src",
                    "--target",
                    "postgresql+psycopg2://user:pass@localhost/tgt",
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
# Pytest mark
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit
