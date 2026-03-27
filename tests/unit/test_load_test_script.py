"""Unit tests for scripts/load_test.py (T59.2).

Validates script structure, argument parsing, error handling, and metric
calculations without requiring a live PostgreSQL connection.

Attack tests verify:
- epsilon ≤ 0 is rejected with exit code 1
- epsilon > 10.0 is rejected with exit code 1
- row_count < 1 is rejected
- Missing DATABASE_URL and no --db-url → exit code 2
- DSN is never echoed to stdout/stderr

Feature tests verify:
- _parse_args() accepts valid configurations
- _detect_nan_inf() correctly identifies NaN and Inf values
- PeakRSSMonitor captures and returns RSS bytes
- Stage timing produces rows_per_second > 0 given positive row count and duration
- PerTableResult converged=False when NaN detected
- PerTableResult converged=True when no NaN/Inf detected

CONSTITUTION Priority 3: TDD
Task: T59.2 — Load Test Script
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Module loader — scripts/ is not a package on sys.path by default
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

#: Placeholder DSN — fictional, never echoed to stdout
_FICTIONAL_DSN = "postgresql://synth:synth@localhost:5432/synth_db"  # pragma: allowlist secret


def _import_load_test_module() -> Any:
    """Dynamically import scripts/load_test.py.

    Returns:
        The imported module object.
    """
    script_path = SCRIPTS_DIR / "load_test.py"
    spec = importlib.util.spec_from_file_location("load_test", script_path)
    assert spec is not None, f"Could not locate {script_path}"
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("load_test", module)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def mod() -> Any:
    """Return the imported load_test module."""
    return _import_load_test_module()


# ===========================================================================
# ATTACK RED — Negative/rejection tests (written first per Rule 22)
# ===========================================================================


class TestLoadTestArgValidationAttack:
    """Attack tests: argument parsing must reject invalid inputs."""

    def test_epsilon_zero_rejected(self, mod: Any) -> None:
        """epsilon = 0 must be rejected with a validation error.

        epsilon = 0 is not a valid differential privacy budget (must be > 0).

        Arrange: _parse_args with epsilon=0.
        Assert: SystemExit raised (argparse error).
        """
        with pytest.raises(SystemExit) as exc_info:
            mod._parse_args(["--db-url", _FICTIONAL_DSN, "--epsilon", "0.0"])
        assert exc_info.value.code != 0, "epsilon=0 must cause non-zero exit"

    def test_epsilon_negative_rejected(self, mod: Any) -> None:
        """epsilon < 0 must be rejected.

        Arrange: _parse_args with epsilon=-1.0.
        Assert: SystemExit raised with non-zero code.
        """
        with pytest.raises(SystemExit) as exc_info:
            mod._parse_args(["--db-url", _FICTIONAL_DSN, "--epsilon", "-1.0"])
        assert exc_info.value.code != 0, "epsilon < 0 must cause non-zero exit"

    def test_epsilon_exceeds_max_rejected(self, mod: Any) -> None:
        """epsilon > 10.0 must be rejected.

        Arrange: _parse_args with epsilon=10.1.
        Assert: SystemExit raised with non-zero code.
        """
        with pytest.raises(SystemExit) as exc_info:
            mod._parse_args(["--db-url", _FICTIONAL_DSN, "--epsilon", "10.1"])
        assert exc_info.value.code != 0, "epsilon > 10.0 must cause non-zero exit"

    def test_row_count_zero_rejected(self, mod: Any) -> None:
        """row_count = 0 must be rejected.

        Arrange: _parse_args with row-count=0.
        Assert: SystemExit raised.
        """
        with pytest.raises(SystemExit) as exc_info:
            mod._parse_args(["--db-url", _FICTIONAL_DSN, "--row-count", "0"])
        assert exc_info.value.code != 0, "row-count=0 must cause non-zero exit"

    def test_row_count_negative_rejected(self, mod: Any) -> None:
        """row_count < 0 must be rejected.

        Arrange: _parse_args with row-count=-100.
        Assert: SystemExit raised.
        """
        with pytest.raises(SystemExit) as exc_info:
            mod._parse_args(["--db-url", _FICTIONAL_DSN, "--row-count", "-100"])
        assert exc_info.value.code != 0, "row-count < 0 must cause non-zero exit"

    def test_missing_db_url_exits_with_error(self, mod: Any) -> None:
        """Missing database URL must cause a non-zero exit.

        Arrange: _parse_args with no --db-url and DATABASE_URL not set.
        Assert: SystemExit raised with non-zero code.
        """
        with patch.dict("os.environ", {}, clear=True):
            # Remove DATABASE_URL if present
            import os as _os

            _os.environ.pop("DATABASE_URL", None)

            with pytest.raises(SystemExit) as exc_info:
                mod._parse_args([])
        assert exc_info.value.code != 0, "Missing DSN must cause non-zero exit"

    def test_dsn_not_echoed_in_error_output(
        self, mod: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """DSN must never be echoed to stdout or stderr.

        Security: DSNs contain credentials.  Even in error output, the DSN
        must not be reflected back to the caller.

        Arrange: _parse_args with an obviously invalid epsilon but a real DSN.
        Assert: captured stdout+stderr does not contain the DSN string.
        """
        dsn = "postgresql://secret_user:secret_pass@db.internal/prod"  # pragma: allowlist secret
        with pytest.raises(SystemExit):
            mod._parse_args(["--db-url", dsn, "--epsilon", "99.9"])

        captured = capsys.readouterr()
        full_output = captured.out + captured.err
        assert "secret_pass" not in full_output, (
            "DSN credentials must not appear in error output. "
            f"Found 'secret_pass' in: {full_output!r}"
        )


# ===========================================================================
# FEATURE RED — Positive feature tests
# ===========================================================================


class TestLoadTestArgParsing:
    """_parse_args() must accept valid configurations."""

    def test_defaults_applied_when_not_specified(self, mod: Any) -> None:
        """_parse_args() must apply default values for optional arguments.

        Arrange: _parse_args with only required --db-url.
        Assert: row_count=5000, epochs=50, epsilon=10.0 (spec defaults).
        """
        args = mod._parse_args(["--db-url", _FICTIONAL_DSN])
        assert args.row_count == 5000, f"Expected default row_count=5000, got {args.row_count}"
        assert args.epochs == 50, f"Expected default epochs=50, got {args.epochs}"
        assert args.epsilon == 10.0, f"Expected default epsilon=10.0, got {args.epsilon}"

    def test_custom_row_count_accepted(self, mod: Any) -> None:
        """_parse_args() must accept custom row count values.

        Arrange: _parse_args with --row-count 1000.
        Assert: args.row_count == 1000.
        """
        args = mod._parse_args(["--db-url", _FICTIONAL_DSN, "--row-count", "1000"])
        assert args.row_count == 1000, f"Expected row_count=1000, got {args.row_count}"

    def test_custom_epsilon_accepted(self, mod: Any) -> None:
        """_parse_args() must accept valid epsilon values.

        Arrange: _parse_args with --epsilon 3.5.
        Assert: args.epsilon == 3.5.
        """
        args = mod._parse_args(["--db-url", _FICTIONAL_DSN, "--epsilon", "3.5"])
        assert args.epsilon == 3.5, f"Expected epsilon=3.5, got {args.epsilon}"

    def test_epsilon_boundary_10_accepted(self, mod: Any) -> None:
        """epsilon = 10.0 (boundary) must be accepted.

        Arrange: _parse_args with --epsilon 10.0.
        Assert: args.epsilon == 10.0 (no SystemExit).
        """
        args = mod._parse_args(["--db-url", _FICTIONAL_DSN, "--epsilon", "10.0"])
        assert args.epsilon == 10.0, f"Expected epsilon=10.0 accepted, got {args.epsilon}"

    def test_epsilon_boundary_just_above_zero_accepted(self, mod: Any) -> None:
        """epsilon = 0.001 (just above zero) must be accepted.

        Arrange: _parse_args with --epsilon 0.001.
        Assert: args.epsilon == 0.001 (no SystemExit).
        """
        args = mod._parse_args(["--db-url", _FICTIONAL_DSN, "--epsilon", "0.001"])
        assert abs(args.epsilon - 0.001) < 1e-9, (
            f"Expected epsilon=0.001 accepted, got {args.epsilon}"
        )

    def test_database_url_env_var_takes_precedence(self, mod: Any) -> None:
        """DATABASE_URL environment variable must override --db-url.

        Security: DSNs should come from the environment to avoid shell history.

        Arrange: _parse_args with --db-url set, AND DATABASE_URL env var set.
        Assert: args.db_url == value from DATABASE_URL env var.
        """
        env_dsn = "postgresql://env_user:env_pass@localhost:5432/env_db"  # pragma: allowlist secret
        with patch.dict("os.environ", {"DATABASE_URL": env_dsn}):
            args = mod._parse_args(["--db-url", _FICTIONAL_DSN])
        assert args.db_url == env_dsn, (
            f"DATABASE_URL env var must override --db-url. Got: {args.db_url!r}"
        )


class TestLoadTestNanInfDetection:
    """_detect_nan_inf() must correctly identify problematic values."""

    def test_clean_dataframe_returns_false(self, mod: Any) -> None:
        """_detect_nan_inf() returns False for a DataFrame with no NaN/Inf.

        Arrange: DataFrame with clean numeric values.
        Assert: returns False.
        """
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": ["x", "y", "z"]})
        result = mod._detect_nan_inf(df, "clean_table")
        assert result is False, f"Expected False for clean DataFrame, got {result}"

    def test_nan_returns_true(self, mod: Any) -> None:
        """_detect_nan_inf() returns True when a DataFrame contains NaN.

        Arrange: DataFrame with one NaN value.
        Assert: returns True.
        """
        import numpy as np

        df = pd.DataFrame({"a": [1.0, np.nan, 3.0]})
        result = mod._detect_nan_inf(df, "nan_table")
        assert result is True, f"Expected True for DataFrame with NaN, got {result}"

    def test_inf_returns_true(self, mod: Any) -> None:
        """_detect_nan_inf() returns True when a DataFrame contains Inf.

        Arrange: DataFrame with one Inf value.
        Assert: returns True.
        """
        import numpy as np

        df = pd.DataFrame({"a": [1.0, np.inf, 3.0]})
        result = mod._detect_nan_inf(df, "inf_table")
        assert result is True, f"Expected True for DataFrame with Inf, got {result}"

    def test_empty_dataframe_returns_false(self, mod: Any) -> None:
        """_detect_nan_inf() returns False for an empty DataFrame.

        An empty DataFrame has no rows and therefore no NaN/Inf values.

        Arrange: Empty DataFrame.
        Assert: returns False.
        """
        df = pd.DataFrame()
        result = mod._detect_nan_inf(df, "empty_table")
        assert result is False, f"Expected False for empty DataFrame, got {result}"


class TestLoadTestPerTableResult:
    """PerTableResult must correctly encode convergence status."""

    def test_per_table_result_converged_when_no_nan(self, mod: Any) -> None:
        """PerTableResult.converged must be True when synth DataFrame has no NaN/Inf.

        Arrange: PerTableResult with a clean synthetic DataFrame.
        Assert: result.converged == True.
        """

        df = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
        result = mod.PerTableResult(
            table_name="test_table",
            rows_synthesized=2,
            wall_clock_seconds=1.5,
            converged=not mod._detect_nan_inf(df, "test_table"),
            epsilon_spent=0.1,
        )
        assert result.converged is True, f"Expected converged=True, got {result.converged}"

    def test_per_table_result_not_converged_when_nan(self, mod: Any) -> None:
        """PerTableResult.converged must be False when synth DataFrame has NaN.

        Arrange: PerTableResult with a diverged (NaN-containing) DataFrame.
        Assert: result.converged == False.
        """
        import numpy as np

        df = pd.DataFrame({"a": [1.0, np.nan]})
        result = mod.PerTableResult(
            table_name="diverged_table",
            rows_synthesized=2,
            wall_clock_seconds=5.0,
            converged=not mod._detect_nan_inf(df, "diverged_table"),
            epsilon_spent=0.5,
        )
        assert result.converged is False, f"Expected converged=False, got {result.converged}"

    def test_per_table_result_rows_per_second_positive(self, mod: Any) -> None:
        """PerTableResult.rows_per_second must be > 0 given positive rows and duration.

        Arrange: PerTableResult with 1000 rows and 2.5 seconds.
        Assert: rows_per_second == 400.0.
        """
        result = mod.PerTableResult(
            table_name="perf_table",
            rows_synthesized=1000,
            wall_clock_seconds=2.5,
            converged=True,
            epsilon_spent=0.3,
        )
        expected_rps = 400.0
        assert abs(result.rows_per_second - expected_rps) < 0.01, (
            f"Expected rows_per_second={expected_rps}, got {result.rows_per_second}"
        )

    def test_per_table_result_rows_per_second_zero_duration(self, mod: Any) -> None:
        """PerTableResult.rows_per_second must handle zero duration gracefully.

        If synthesis completes in 0 seconds (extremely fast or mocked), the
        rows_per_second property must return 0.0 or infinity — but not raise.

        Arrange: PerTableResult with 1000 rows and 0.0 seconds.
        Assert: rows_per_second returns a float (not exception).
        """
        result = mod.PerTableResult(
            table_name="instant_table",
            rows_synthesized=1000,
            wall_clock_seconds=0.0,
            converged=True,
            epsilon_spent=0.1,
        )
        # Must not raise; the value may be 0.0 or infinity depending on implementation
        rps = result.rows_per_second
        assert isinstance(rps, float), f"rows_per_second must be a float, got {type(rps)}"


class TestLoadTestExitCodes:
    """Exit code constants must match the spec."""

    def test_exit_success_is_zero(self, mod: Any) -> None:
        """EXIT_SUCCESS must be 0.

        Arrange: import load_test module.
        Assert: EXIT_SUCCESS == 0.
        """
        assert mod.EXIT_SUCCESS == 0, f"EXIT_SUCCESS must be 0, got {mod.EXIT_SUCCESS}"

    def test_exit_pipeline_failure_is_one(self, mod: Any) -> None:
        """EXIT_PIPELINE_FAILURE must be 1.

        Arrange: import load_test module.
        Assert: EXIT_PIPELINE_FAILURE == 1.
        """
        assert mod.EXIT_PIPELINE_FAILURE == 1, (
            f"EXIT_PIPELINE_FAILURE must be 1, got {mod.EXIT_PIPELINE_FAILURE}"
        )

    def test_exit_infrastructure_error_is_two(self, mod: Any) -> None:
        """EXIT_INFRASTRUCTURE_ERROR must be 2.

        Arrange: import load_test module.
        Assert: EXIT_INFRASTRUCTURE_ERROR == 2.
        """
        assert mod.EXIT_INFRASTRUCTURE_ERROR == 2, (
            f"EXIT_INFRASTRUCTURE_ERROR must be 2, got {mod.EXIT_INFRASTRUCTURE_ERROR}"
        )


class TestLoadTestScriptStructure:
    """Structural verification that load_test.py has the required interface."""

    def test_script_is_importable(self) -> None:
        """scripts/load_test.py must be importable without errors."""
        mod = _import_load_test_module()
        assert mod.__name__ == "load_test", (
            f"load_test module must be importable with name 'load_test', got {mod.__name__!r}"
        )

    def test_sanitize_function_imported_from_validate(self, mod: Any) -> None:
        """_sanitize_dataframe_for_sdv must be available in load_test.

        The spec requires reusing the function from validate_full_pipeline.py.

        Arrange: import load_test module.
        Assert: _sanitize_dataframe_for_sdv is callable.
        """
        assert callable(mod._sanitize_dataframe_for_sdv), (
            "_sanitize_dataframe_for_sdv must be callable in load_test module"
        )

    def test_peak_rss_monitor_class_exists(self, mod: Any) -> None:
        """PeakRSSMonitor class must exist in load_test module."""
        assert hasattr(mod, "PeakRSSMonitor"), "load_test module must define PeakRSSMonitor class"

    def test_per_table_result_class_exists(self, mod: Any) -> None:
        """PerTableResult dataclass must exist in load_test module."""
        assert hasattr(mod, "PerTableResult"), "load_test module must define PerTableResult class"

    def test_parse_args_function_exists(self, mod: Any) -> None:
        """_parse_args function must exist and be callable."""
        assert callable(mod._parse_args), "_parse_args must be callable"

    def test_detect_nan_inf_function_exists(self, mod: Any) -> None:
        """_detect_nan_inf function must exist and be callable."""
        assert callable(mod._detect_nan_inf), "_detect_nan_inf must be callable"


class TestPeakRSSMonitor:
    """PeakRSSMonitor must capture peak RSS via resource.getrusage."""

    def test_peak_rss_returns_non_negative_bytes(self, mod: Any) -> None:
        """PeakRSSMonitor.peak_rss_bytes() must return a non-negative value.

        Arrange: instantiate PeakRSSMonitor, call peak_rss_bytes().
        Assert: result >= 0.
        """
        monitor = mod.PeakRSSMonitor()
        rss = monitor.peak_rss_bytes()
        assert isinstance(rss, int), f"peak_rss_bytes() must return int, got {type(rss)}"
        assert rss >= 0, f"peak_rss_bytes() must be non-negative, got {rss}"

    def test_peak_rss_returns_positive_value_after_allocation(self, mod: Any) -> None:
        """PeakRSSMonitor.peak_rss_bytes() must return > 0 for a running process.

        The current process has allocated memory (test suite itself), so
        getrusage RUSAGE_SELF will report a positive RSS.

        Arrange: instantiate PeakRSSMonitor.
        Assert: peak_rss_bytes() > 0.
        """
        monitor = mod.PeakRSSMonitor()
        rss = monitor.peak_rss_bytes()
        assert rss > 0, (
            "PeakRSSMonitor.peak_rss_bytes() returned 0 — resource.getrusage may not be working"
        )
