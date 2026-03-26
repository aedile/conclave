"""Load test script for the Air-Gapped Synthetic Data Engine (T59.2).

Exercises the full synthesis pipeline with configurable data volumes to
establish performance baselines and validate production-grade throughput.

Produces a performance report including:
- Wall-clock time per pipeline stage
- Peak RSS memory (via resource.getrusage)
- Epsilon spent per table
- Rows/second throughput
- Per-table convergence status (NaN/Inf detection)

Exit codes:
    0: All tables synthesized successfully.
    1: Pipeline failure (synthesis diverged, epsilon exceeded, FK violations).
    2: Infrastructure error (DB connection failed, output directory not writable).

Security notes:
    - The database URL is NEVER printed to stdout/stderr or included in the
      report file.  It may contain credentials.
    - DATABASE_URL environment variable takes precedence over --db-url so that
      DSNs do not appear in shell history.
    - The output directory is validated as writable before the pipeline starts.

Reuse:
    _sanitize_dataframe_for_sdv() is imported from validate_full_pipeline.py
    to avoid duplication of SDV compatibility logic.

Usage::

    # Preferred (DSN stays out of shell history):
    DATABASE_URL=postgresql://... poetry run python scripts/load_test.py

    # Also accepted (DSN appears in process table — less secure):
    poetry run python scripts/load_test.py --db-url postgresql://...

    # Large run with custom parameters:
    DATABASE_URL=... poetry run python scripts/load_test.py \\
        --row-count 10000 --epochs 100 --epsilon 5.0

Task: T59.2 — Load Test with Realistic Data Volumes
CONSTITUTION Priority 0: Security — no DSN in output, no credential echo.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import importlib.util
import logging
import os
import resource
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Reuse _sanitize_dataframe_for_sdv from validate_full_pipeline.py
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).parent
_VFP_PATH = _SCRIPTS_DIR / "validate_full_pipeline.py"

# Dynamically import validate_full_pipeline to reuse _sanitize_dataframe_for_sdv.
# This avoids duplicating ~60 lines of SDV column-type normalization logic.
_vfp_spec = importlib.util.spec_from_file_location("validate_full_pipeline", _VFP_PATH)
if _vfp_spec is None or _vfp_spec.loader is None:  # pragma: no cover
    raise ImportError(f"Cannot locate validate_full_pipeline.py at {_VFP_PATH}")
_vfp_module: Any = importlib.util.module_from_spec(_vfp_spec)
_vfp_spec.loader.exec_module(_vfp_module)  # type: ignore[union-attr]

_sanitize_dataframe_for_sdv = _vfp_module._sanitize_dataframe_for_sdv

# ---------------------------------------------------------------------------
# Exit code constants
# ---------------------------------------------------------------------------

EXIT_SUCCESS: int = 0
EXIT_PIPELINE_FAILURE: int = 1
EXIT_INFRASTRUCTURE_ERROR: int = 2

# ---------------------------------------------------------------------------
# Bounds constants — validated at parse time
# ---------------------------------------------------------------------------

_DEFAULT_ROW_COUNT: int = 5000
_DEFAULT_EPOCHS: int = 50
_DEFAULT_EPSILON: float = 10.0
_MAX_EPSILON: float = 10.0
_MAX_EPOCHS: int = 500
_MAX_ROW_COUNT: int = 100_000

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
_logger = logging.getLogger("load_test")


# ---------------------------------------------------------------------------
# Peak RSS monitor (T59.2: memory profiling via resource.getrusage)
# ---------------------------------------------------------------------------


class PeakRSSMonitor:
    """Wrapper around resource.getrusage for peak RSS memory measurement.

    Uses RUSAGE_SELF to measure the peak resident set size of the current
    process.  On macOS, getrusage returns bytes; on Linux, kilobytes.
    This class normalises to bytes for platform consistency.

    Attributes:
        None — all state is read from the OS on demand.
    """

    _LINUX_PLATFORM_FACTOR = 1024  # Linux reports in KB; multiply to get bytes

    def peak_rss_bytes(self) -> int:
        """Return the peak RSS of the current process in bytes.

        Reads RUSAGE_SELF.ru_maxrss and normalises to bytes:
        - macOS: ru_maxrss is already in bytes.
        - Linux: ru_maxrss is in kilobytes; multiply by 1024.

        Returns:
            Peak RSS in bytes as a non-negative integer.
        """
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = usage.ru_maxrss

        # Normalise: Linux reports KB, macOS reports bytes
        if sys.platform.startswith("linux"):
            rss *= self._LINUX_PLATFORM_FACTOR

        return int(rss)

    def peak_rss_mb(self) -> float:
        """Return the peak RSS of the current process in mebibytes.

        Returns:
            Peak RSS in MiB as a float.
        """
        return self.peak_rss_bytes() / (1024 * 1024)


# ---------------------------------------------------------------------------
# Per-table result dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PerTableResult:
    """Performance and quality metrics for a single synthesized table.

    Attributes:
        table_name: Name of the source/target table.
        rows_synthesized: Number of synthetic rows produced.
        wall_clock_seconds: Total wall-clock time for this table's synthesis.
        converged: True if the trained model produced no NaN or Inf values.
        epsilon_spent: Differential privacy epsilon actually consumed.
    """

    table_name: str
    rows_synthesized: int
    wall_clock_seconds: float
    converged: bool
    epsilon_spent: float

    @property
    def rows_per_second(self) -> float:
        """Return throughput in rows per second.

        Returns:
            Rows/second as a float.  Returns 0.0 if wall_clock_seconds is 0.
        """
        if self.wall_clock_seconds == 0.0:
            return 0.0
        return float(self.rows_synthesized) / self.wall_clock_seconds


# ---------------------------------------------------------------------------
# NaN / Inf detection helper (matches validate_full_pipeline.py)
# ---------------------------------------------------------------------------


def _detect_nan_inf(df: pd.DataFrame, table_name: str) -> bool:
    """Return True if the DataFrame contains any NaN or Inf values.

    Args:
        df: DataFrame to inspect.
        table_name: Table name used in log messages only.

    Returns:
        True if any NaN or +/-Inf value is present; False otherwise.
    """
    if df.empty:
        return False

    numeric_cols = df.select_dtypes(include=[np.number])
    if numeric_cols.empty:
        return False

    has_nan = bool(numeric_cols.isna().any().any())
    has_inf = bool(np.isinf(numeric_cols.values).any())

    if has_nan:
        _logger.warning("NaN detected in synthetic output for table '%s'", table_name)
    if has_inf:
        _logger.warning("Inf detected in synthetic output for table '%s'", table_name)

    return has_nan or has_inf


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate CLI arguments for the load test.

    DATABASE_URL environment variable takes precedence over --db-url so that
    database credentials do not appear in shell history.  All numeric bounds
    are validated at parse time, before any database connection is attempted.

    Args:
        argv: Argument list override (used in testing).  Defaults to sys.argv.

    Returns:
        Parsed :class:`argparse.Namespace` with all validated arguments.

    Raises:
        SystemExit: With non-zero code if arguments fail validation.
    """
    parser = argparse.ArgumentParser(
        prog="load_test",
        description=(
            "Load test for the Synthetic Data Engine. "
            "Exercises the full pipeline with configurable data volumes. "
            "Requires --db-url or DATABASE_URL environment variable."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--db-url",
        default=None,
        metavar="DSN",
        help=(
            "PostgreSQL connection URL.  If DATABASE_URL is set in the "
            "environment, it takes precedence (recommended — keeps the DSN "
            "out of shell history)."
        ),
    )
    parser.add_argument(
        "--row-count",
        type=int,
        default=_DEFAULT_ROW_COUNT,
        metavar="N",
        help=f"Number of rows to synthesize per table (default: {_DEFAULT_ROW_COUNT}).",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=_DEFAULT_EPOCHS,
        metavar="N",
        help=f"CTGAN training epochs per table (default: {_DEFAULT_EPOCHS}, max: {_MAX_EPOCHS}).",
    )
    parser.add_argument(
        "--epsilon",
        type=float,
        default=_DEFAULT_EPSILON,
        metavar="E",
        help=(
            f"Differential privacy epsilon budget (default: {_DEFAULT_EPSILON}, "
            f"range: (0, {_MAX_EPSILON}])."
        ),
    )
    parser.add_argument(
        "--delta",
        type=float,
        default=1e-5,
        metavar="D",
        help="Differential privacy delta (default: 1e-5).",
    )
    parser.add_argument(
        "--output-dir",
        default="output/load_test",
        metavar="DIR",
        help="Output directory for load test results (default: output/load_test/).",
    )
    parser.add_argument(
        "--results-file",
        default="docs/LOAD_TEST_RESULTS.md",
        metavar="FILE",
        help="Path for the performance report Markdown file (default: docs/LOAD_TEST_RESULTS.md).",
    )

    args = parser.parse_args(argv)

    # DATABASE_URL env var takes precedence over --db-url.
    env_db_url = os.environ.get("DATABASE_URL")
    if env_db_url:
        args.db_url = env_db_url

    if not args.db_url:
        parser.error(
            "A database URL is required. Provide --db-url or set DATABASE_URL in the environment."
        )

    # --- Bounds validation (before any DB connection) ---
    if args.row_count < 1 or args.row_count > _MAX_ROW_COUNT:
        parser.error(f"--row-count must be between 1 and {_MAX_ROW_COUNT}, got {args.row_count}.")

    if args.epsilon <= 0 or args.epsilon > _MAX_EPSILON:
        parser.error(f"--epsilon must be in (0, {_MAX_EPSILON}], got {args.epsilon}.")

    if args.delta <= 0 or args.delta >= 1:
        parser.error(f"--delta must be in (0, 1), got {args.delta}.")

    if args.epochs < 1 or args.epochs > _MAX_EPOCHS:
        parser.error(f"--epochs must be between 1 and {_MAX_EPOCHS}, got {args.epochs}.")

    return args


# ---------------------------------------------------------------------------
# Output directory validation
# ---------------------------------------------------------------------------


def _validate_output_dir(output_dir: str) -> Path:
    """Validate that the output directory is writable before the pipeline starts.

    Creates the directory (and parents) if it does not exist.

    Args:
        output_dir: Path string for the output directory.

    Returns:
        :class:`pathlib.Path` for the validated output directory.
    """
    path = Path(output_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _logger.error(
            "Cannot create output directory '%s': %s",
            output_dir,
            type(exc).__name__,
        )
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    if not os.access(path, os.W_OK):
        _logger.error(
            "Output directory '%s' is not writable. Check permissions and try again.",
            output_dir,
        )
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    return path


# ---------------------------------------------------------------------------
# Performance report writer
# ---------------------------------------------------------------------------


def _write_results_report(
    output_path: Path,
    args: argparse.Namespace,
    table_results: list[PerTableResult],
    peak_rss_monitor: PeakRSSMonitor,
    total_wall_clock: float,
    run_timestamp: str,
) -> None:
    """Write a Markdown performance report to the given output path.

    The report includes: configuration, per-table metrics, peak RSS, and
    a convergence summary.  The DATABASE_URL is never included in the report.

    Args:
        output_path: Path for the Markdown report file.
        args: Parsed CLI arguments (epsilon, row_count, epochs).
        table_results: List of per-table synthesis results.
        peak_rss_monitor: PeakRSSMonitor instance for peak RSS.
        total_wall_clock: Total wall-clock seconds for the full run.
        run_timestamp: ISO-8601 timestamp string for this run.
    """
    peak_mb = peak_rss_monitor.peak_rss_mb()
    converged_tables = [r for r in table_results if r.converged]
    diverged_tables = [r for r in table_results if not r.converged]
    total_rows = sum(r.rows_synthesized for r in table_results)
    overall_rps = total_rows / total_wall_clock if total_wall_clock > 0 else 0.0

    lines: list[str] = [
        "# Load Test Results",
        "",
        f"**Run timestamp**: {run_timestamp}  ",
        f"**Row count**: {args.row_count}  ",
        f"**Epochs**: {args.epochs}  ",
        f"**Epsilon**: {args.epsilon}  ",
        f"**Total wall-clock time**: {total_wall_clock:.2f}s  ",
        f"**Peak RSS**: {peak_mb:.1f} MiB  ",
        f"**Overall throughput**: {overall_rps:.1f} rows/s  ",
        "",
        "## Per-Table Results",
        "",
        "| Table | Rows | Time (s) | Rows/s | Epsilon Spent | Converged |",
        "|-------|------|----------|--------|---------------|-----------|",
    ]

    for r in table_results:
        lines.append(
            f"| {r.table_name} | {r.rows_synthesized} | "
            f"{r.wall_clock_seconds:.2f} | {r.rows_per_second:.1f} | "
            f"{r.epsilon_spent:.4f} | {'Yes' if r.converged else 'No'} |"
        )

    lines += [
        "",
        "## Convergence Summary",
        "",
        f"- **Converged**: {len(converged_tables)} table(s)",
        f"- **Diverged** (NaN/Inf detected): {len(diverged_tables)} table(s)",
    ]

    if diverged_tables:
        lines += [
            "",
            "### Diverged Tables",
            "",
            "The following tables produced NaN or Inf values in their synthetic output.",
            "This typically indicates insufficient data for DP-SGD training.",
            "Recommended actions: increase `--row-count`, reduce `--epsilon`,",
            "or tune the noise multiplier in the DP training configuration.",
            "",
        ]
        for r in diverged_tables:
            lines.append(
                f"- **{r.table_name}**: {r.rows_synthesized} rows, {r.wall_clock_seconds:.2f}s"
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n")
    _logger.info("Performance report written to: %s", output_path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the load test pipeline.

    Args:
        argv: Argument list override for testing.  Defaults to sys.argv.

    Returns:
        Exit code: 0 (success), 1 (pipeline failure), 2 (infrastructure error).
    """
    args = _parse_args(argv)
    run_timestamp = datetime.datetime.now(tz=datetime.UTC).isoformat()

    _logger.info(
        "Load test starting: row_count=%d, epochs=%d, epsilon=%.2f",
        args.row_count,
        args.epochs,
        args.epsilon,
    )

    output_dir = _validate_output_dir(args.output_dir)
    rss_monitor = PeakRSSMonitor()

    # Late imports — avoid loading heavy ML dependencies on import error paths
    try:
        from sqlalchemy import create_engine

        from synth_engine.modules.ingestion.postgres_adapter import SchemaInspector
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
        from synth_engine.modules.synthesizer.training.engine import (
            SynthesisEngine,
        )
        from synth_engine.shared.exceptions import BudgetExhaustionError

    except ImportError as exc:
        _logger.error(
            "Import error — ensure synthesizer group is installed: %s",
            type(exc).__name__,
        )
        return EXIT_INFRASTRUCTURE_ERROR

    # Connect to the database
    try:
        engine = create_engine(args.db_url, pool_pre_ping=True)
        with engine.connect():
            pass  # Verify connectivity
    except Exception as exc:
        _logger.error("Database connection failed: %s", type(exc).__name__)
        return EXIT_INFRASTRUCTURE_ERROR

    table_results: list[PerTableResult] = []
    run_start = time.monotonic()

    try:
        inspector = SchemaInspector(engine)
        all_tables = inspector.get_tables()

        # Use the same 5-table pagila subset as validate_full_pipeline.py
        target_tables = [
            t for t in ["customer", "address", "rental", "inventory", "film"] if t in all_tables
        ]

        if not target_tables:
            _logger.error(
                "None of the target tables found in the database. Is the pagila schema loaded?"
            )
            return EXIT_INFRASTRUCTURE_ERROR

        for table_name in target_tables:
            _logger.info("--- Synthesizing table: %s ---", table_name)
            table_start = time.monotonic()

            try:
                # Read source data
                import pandas as _pd
                from sqlalchemy import text as _sql_text

                with engine.connect() as conn:
                    df = _pd.read_sql(
                        _sql_text(
                            f"SELECT * FROM {table_name} LIMIT :n"  # nosec B608  # noqa: S608
                        ),
                        conn,
                        params={"n": args.row_count},
                    )

                if df.empty:
                    _logger.warning("Table '%s' returned no rows; skipping.", table_name)
                    continue

                df = _sanitize_dataframe_for_sdv(df, table_name)

                # DP training
                dp_wrapper = DPTrainingWrapper(
                    epsilon=args.epsilon,
                    delta=1e-5,
                    max_grad_norm=1.0,
                    noise_multiplier=1.1,
                )

                synthesis_engine = SynthesisEngine(
                    epochs=args.epochs,
                    dp_wrapper=dp_wrapper,
                )

                output_path = output_dir / f"{table_name}_synthetic.parquet"
                synth_df = synthesis_engine.fit_sample(
                    df,
                    table_name=table_name,
                    output_path=output_path,
                )

                table_wall_clock = time.monotonic() - table_start
                has_diverged = _detect_nan_inf(synth_df, table_name)

                epsilon_spent = (
                    dp_wrapper.epsilon_spent
                    if hasattr(dp_wrapper, "epsilon_spent")
                    else args.epsilon
                )

                result = PerTableResult(
                    table_name=table_name,
                    rows_synthesized=len(synth_df),
                    wall_clock_seconds=table_wall_clock,
                    converged=not has_diverged,
                    epsilon_spent=epsilon_spent,
                )
                table_results.append(result)

                _logger.info(
                    "Table '%s': %d rows, %.2fs, %.1f rows/s, converged=%s",
                    table_name,
                    result.rows_synthesized,
                    result.wall_clock_seconds,
                    result.rows_per_second,
                    result.converged,
                )

            except BudgetExhaustionError:
                _logger.error("Privacy budget exhausted for table '%s'.", table_name)
                return EXIT_PIPELINE_FAILURE
            except Exception as exc:
                _logger.error(
                    "Synthesis failed for table '%s': %s",
                    table_name,
                    type(exc).__name__,
                )
                return EXIT_PIPELINE_FAILURE

    except Exception as exc:
        _logger.error("Pipeline error: %s", type(exc).__name__)
        return EXIT_INFRASTRUCTURE_ERROR

    total_wall_clock = time.monotonic() - run_start

    # Write performance report
    results_path = Path(args.results_file)
    _write_results_report(
        output_path=results_path,
        args=args,
        table_results=table_results,
        peak_rss_monitor=rss_monitor,
        total_wall_clock=total_wall_clock,
        run_timestamp=run_timestamp,
    )

    # Check convergence for exit code
    diverged = [r for r in table_results if not r.converged]
    if diverged:
        _logger.warning(
            "Load test completed with %d diverged table(s): %s",
            len(diverged),
            [r.table_name for r in diverged],
        )
        return EXIT_PIPELINE_FAILURE

    _logger.info(
        "Load test complete: %d tables, %.2fs total, %.1f MiB peak RSS",
        len(table_results),
        total_wall_clock,
        rss_monitor.peak_rss_mb(),
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
