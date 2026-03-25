"""Full pipeline validation script for the Air-Gapped Synthetic Data Engine.

Exercises the COMPLETE production pipeline end-to-end using real code paths
(no mocks, no test doubles).  Requires ``poetry install --with synthesizer``
and a running PostgreSQL database with the pagila sample schema.

Exit codes:
    0: All validations pass.
    1: Validation failure (FK orphans > 0, epsilon exceeded, masking violation,
       BudgetExhaustionError raised).
    2: Infrastructure error (DB connection failed, training diverged with NaN/inf).

Security notes:
    - The database URL is NEVER printed to stdout/stderr or included in the
      report file.  It may contain credentials.
    - The DATABASE_URL environment variable takes precedence over --db-url so
      that DSNs do not appear in shell history.
    - The output dir is validated as writable before the pipeline starts.

Usage::

    # Preferred (DSN stays out of shell history):
    DATABASE_URL=postgresql://... poetry run python scripts/validate_full_pipeline.py

    # Also accepted (DSN appears in process table — less secure):
    poetry run python scripts/validate_full_pipeline.py --db-url postgresql://...

Task: T54.2 — Full Pipeline Validation Script (Phase 54)
CONSTITUTION Priority 0: Security — no DSN in output, no credential echo.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import subprocess  # nosec B404 — used only for git branch detection with fixed args
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

from synth_engine.modules.ingestion.postgres_adapter import SchemaInspector
from synth_engine.modules.masking.registry import ColumnType, MaskingRegistry
from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
from synth_engine.modules.profiler.profiler import StatisticalProfiler
from synth_engine.modules.synthesizer.training.engine import (
    SynthesisEngine,
    apply_fk_post_processing,
)
from synth_engine.shared.exceptions import BudgetExhaustionError
from synth_engine.shared.schema_topology import ColumnInfo, ForeignKeyInfo, SchemaTopology

# ---------------------------------------------------------------------------
# Exit code constants — named to prevent int-literal confusion
# ---------------------------------------------------------------------------
EXIT_SUCCESS: int = 0
EXIT_VALIDATION_FAILURE: int = 1
EXIT_INFRASTRUCTURE_ERROR: int = 2

# ---------------------------------------------------------------------------
# DP privacy configuration
# ---------------------------------------------------------------------------
_DEFAULT_MAX_GRAD_NORM: float = 1.0
_DEFAULT_NOISE_MULTIPLIER: float = 1.1

# ---------------------------------------------------------------------------
# Epsilon privacy warning threshold — per task spec
# ---------------------------------------------------------------------------
_EPSILON_HIGH_PRIVACY_THRESHOLD: float = 3.0

# ---------------------------------------------------------------------------
# Bounds constants — validated at parse time
# ---------------------------------------------------------------------------
_MAX_SUBSET_SIZE: int = 50000
_MAX_EPSILON: float = 100.0
_MAX_EPOCHS: int = 500

# ---------------------------------------------------------------------------
# Tables to validate (5-table pagila subset)
# ---------------------------------------------------------------------------
_TARGET_TABLES: list[str] = ["customer", "address", "rental", "inventory", "film"]

# ---------------------------------------------------------------------------
# PII-like column mapping — table -> list of (column_name, ColumnType)
# ---------------------------------------------------------------------------
_PII_COLUMNS: dict[str, list[tuple[str, ColumnType]]] = {
    "customer": [
        ("first_name", ColumnType.FIRST_NAME),
        ("last_name", ColumnType.LAST_NAME),
        ("email", ColumnType.EMAIL),
    ],
    "address": [
        ("address", ColumnType.ADDRESS),
    ],
}

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
_logger = logging.getLogger("validate_full_pipeline")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate CLI arguments.

    The DATABASE_URL environment variable takes precedence over --db-url so
    that database credentials do not appear in shell history.  All numeric
    bounds are validated at parse time, before any database connection is
    attempted.

    Args:
        argv: Argument list override (used in testing).  Defaults to sys.argv.

    Returns:
        Parsed :class:`argparse.Namespace` with all validated arguments.
    """
    parser = argparse.ArgumentParser(
        prog="validate_full_pipeline",
        description=(
            "End-to-end pipeline validation for the Synthetic Data Engine. "
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
        "--subset-size",
        type=int,
        default=500,
        metavar="N",
        help=f"Number of seed rows to extract (default: 500, max: {_MAX_SUBSET_SIZE}).",
    )
    parser.add_argument(
        "--epsilon",
        type=float,
        default=10.0,
        metavar="E",
        help=(
            f"Differential privacy epsilon budget (default: 10.0, max: {_MAX_EPSILON}). "
            f"Values > {_EPSILON_HIGH_PRIVACY_THRESHOLD} emit a WARNING."
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
        "--epochs",
        type=int,
        default=50,
        metavar="N",
        help=f"CTGAN training epochs (default: 50, max: {_MAX_EPOCHS}).",
    )
    parser.add_argument(
        "--output-dir",
        default="output/",
        metavar="DIR",
        help="Output directory for Parquet files and validation report (default: output/).",
    )
    parser.add_argument(
        "--force-cpu",
        action="store_true",
        default=True,
        help=(
            "Force CPU-only mode by setting CUDA_VISIBLE_DEVICES='' "
            "(default: True — ensures reproducibility on non-GPU machines)."
        ),
    )

    args = parser.parse_args(argv)

    # DATABASE_URL env var takes precedence over --db-url (security: keeps DSN
    # out of shell history when operators use the env var approach).
    env_db_url = os.environ.get("DATABASE_URL")
    if env_db_url:
        args.db_url = env_db_url

    if not args.db_url:
        parser.error("A database URL is required. Provide --db-url or set DATABASE_URL.")

    # --- Bounds validation (before any DB connection) ---
    if args.subset_size < 1 or args.subset_size > _MAX_SUBSET_SIZE:
        parser.error(
            f"--subset-size must be between 1 and {_MAX_SUBSET_SIZE}, got {args.subset_size}."
        )

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

    Creates the directory (and parents) if it does not exist.  Raises
    SystemExit(2) if the directory cannot be created or is not writable.

    Args:
        output_dir: Path string for the output directory.

    Returns:
        :class:`pathlib.Path` for the validated output directory.
    """
    path = Path(output_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _logger.error("Cannot create output directory '%s': %s", output_dir, type(exc).__name__)
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    if not os.access(path, os.W_OK):
        _logger.error(
            "Output directory '%s' is not writable. Check permissions and try again.",
            output_dir,
        )
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    return path


# ---------------------------------------------------------------------------
# NaN / inf detection (training divergence guard)
# ---------------------------------------------------------------------------


def _detect_nan_inf(df: pd.DataFrame, table_name: str) -> bool:
    """Return True if the DataFrame contains any NaN or infinite values.

    Args:
        df: DataFrame to inspect.
        table_name: Table name used in log messages.

    Returns:
        True if any NaN or inf value is detected; False otherwise.
    """
    # Check for NaN
    if df.isna().any().any():
        _logger.error(
            "training_divergence: NaN values detected in synthetic output "
            "for table '%s'. Training may have diverged.",
            table_name,
        )
        return True

    # Check for inf in numeric columns
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    for col in numeric_cols:
        if np.isinf(df[col]).any():
            _logger.error(
                "training_divergence: inf values detected in column '%s' "
                "of synthetic output for table '%s'. Training may have diverged.",
                col,
                table_name,
            )
            return True

    return False


# ---------------------------------------------------------------------------
# SDV compatibility pre-processing
# ---------------------------------------------------------------------------


def _sanitize_dataframe_for_sdv(df: pd.DataFrame, table_name: str) -> pd.DataFrame:
    """Strip column types that SDV's CTGAN metadata inference cannot handle.

    SDV rejects timezone-aware timestamps and raw date columns.  This function
    normalises those types in-place on a copy so that the Parquet files written
    to the source directory are compatible with :class:`SynthesisEngine`.

    Columns with types that cannot be coerced (e.g. PostgreSQL arrays, bytea)
    are dropped rather than passed through, because SDV would raise an
    ``InvalidMetadataError`` on them anyway.

    Args:
        df: Raw DataFrame as read from PostgreSQL via SQLAlchemy.
        table_name: Table name used only for log messages.

    Returns:
        A new DataFrame with SDV-compatible column types.
    """
    df = df.copy()
    dropped: list[str] = []

    for col in df.columns:
        series = df[col]
        dtype = series.dtype

        # Timezone-aware datetimes → strip tz info so SDV treats as naive datetime
        if hasattr(dtype, "tz") and dtype.tz is not None:
            df[col] = series.dt.tz_localize(None)
            _logger.debug("sanitize[%s.%s]: stripped timezone (was %s)", table_name, col, dtype)
            continue

        # Python date objects (object dtype containing datetime.date) → datetime64
        if pd.api.types.is_object_dtype(dtype) and len(series.dropna()) > 0:
            sample = series.dropna().iloc[0]
            if isinstance(sample, datetime.date) and not isinstance(sample, datetime.datetime):
                try:
                    df[col] = pd.to_datetime(series)
                    _logger.debug("sanitize[%s.%s]: converted date -> datetime64", table_name, col)
                    continue
                except Exception:  # coercion failure is non-fatal; column will fall through to drop
                    _logger.debug(
                        "sanitize[%s.%s]: date coercion failed, will drop if unsupported type",
                        table_name,
                        col,
                    )

        # Drop columns whose dtype is still object-with-non-scalar content
        # (arrays, bytea, composite types).  SDV cannot infer metadata for these.
        if pd.api.types.is_object_dtype(dtype) and len(series.dropna()) > 0:
            sample = series.dropna().iloc[0]
            if isinstance(sample, list | dict | bytes | memoryview):
                dropped.append(col)

    if dropped:
        df = df.drop(columns=dropped)
        _logger.warning(
            "sanitize[%s]: dropped %d unsupported column(s): %s",
            table_name,
            len(dropped),
            dropped,
        )

    return df


# ---------------------------------------------------------------------------
# Current git branch (for report metadata)
# ---------------------------------------------------------------------------


def _get_git_branch() -> str:
    """Return the current git branch name for the validation report.

    Returns:
        Branch name string, or ``"unknown"`` if git is unavailable.
    """
    try:
        result = subprocess.run(  # nosec B603, B607 — fixed args, no user input
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Stage 1: Schema Reflection
# ---------------------------------------------------------------------------


def _stage_schema_reflection(
    engine: Any,
) -> tuple[dict[str, Any], SchemaTopology]:
    """Reflect the database schema and build a SchemaTopology for downstream stages.

    Uses :class:`SchemaInspector` to enumerate tables, columns, and FK
    relationships for the 5-table pagila subset.

    Args:
        engine: A connected SQLAlchemy Engine.

    Returns:
        Tuple of (stage_result dict, SchemaTopology for downstream use).
    """
    t_start = time.monotonic()

    inspector = SchemaInspector(engine)
    all_tables = inspector.get_tables()

    # Filter to the target subset of tables.
    available_tables = [t for t in _TARGET_TABLES if t in all_tables]

    # Build column and FK info for SchemaTopology.
    columns: dict[str, tuple[ColumnInfo, ...]] = {}
    foreign_keys: dict[str, tuple[ForeignKeyInfo, ...]] = {}
    fk_edges: list[dict[str, Any]] = []

    for table in available_tables:
        raw_cols = inspector.get_columns(table)
        columns[table] = tuple(
            ColumnInfo(
                name=col["name"],
                type=str(col["type"]),
                primary_key=int(col.get("primary_key", 0)),
                nullable=bool(col.get("nullable", True)),
            )
            for col in raw_cols
        )

        raw_fks = inspector.get_foreign_keys(table)
        fk_info_list: list[ForeignKeyInfo] = []
        for fk in raw_fks:
            fk_info = ForeignKeyInfo(
                constrained_columns=tuple(fk["constrained_columns"]),
                referred_table=fk["referred_table"],
                referred_columns=tuple(fk["referred_columns"]),
            )
            fk_info_list.append(fk_info)
            fk_edges.append(
                {
                    "from_table": table,
                    "from_columns": fk["constrained_columns"],
                    "to_table": fk["referred_table"],
                    "to_columns": fk["referred_columns"],
                }
            )
        foreign_keys[table] = tuple(fk_info_list)

    # Topological order: parents before children.
    # Build a simple topological sort from the FK graph.
    table_order = _topological_sort(available_tables, foreign_keys)

    topology = SchemaTopology(
        table_order=tuple(table_order),
        columns=columns,
        foreign_keys=foreign_keys,
    )

    duration = time.monotonic() - t_start
    _logger.info(
        "Schema reflection complete: %d tables, %d FK edges (%.2fs)",
        len(available_tables),
        len(fk_edges),
        duration,
    )

    stage_result: dict[str, Any] = {
        "duration_seconds": round(duration, 4),
        "tables": available_tables,
        "fk_edges": fk_edges,
    }
    return stage_result, topology


def _topological_sort(
    tables: list[str],
    foreign_keys: dict[str, tuple[ForeignKeyInfo, ...]],
) -> list[str]:
    """Return tables in topological order (parents before children).

    Uses Kahn's algorithm on the FK dependency graph.  Tables with no
    incoming edges (no FK dependencies) appear first.

    Args:
        tables: All table names to include in the sort.
        foreign_keys: Mapping of child table -> FK descriptors.

    Returns:
        Tables in parent-first topological order.
    """
    # Build in-degree and adjacency structures.
    in_degree: dict[str, int] = dict.fromkeys(tables, 0)
    dependents: dict[str, list[str]] = {t: [] for t in tables}

    for child_table, fk_infos in foreign_keys.items():
        if child_table not in in_degree:
            continue
        for fk in fk_infos:
            parent = fk.referred_table
            if parent in in_degree and parent != child_table:
                in_degree[child_table] += 1
                dependents[parent].append(child_table)

    # Kahn's algorithm.
    queue: list[str] = [t for t in tables if in_degree[t] == 0]
    result: list[str] = []

    while queue:
        node = queue.pop(0)
        result.append(node)
        for dependent in dependents[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Any remaining tables (cycles) are appended at the end.
    remaining = [t for t in tables if t not in result]
    result.extend(remaining)

    return result


# ---------------------------------------------------------------------------
# Stage 2: Subsetting
# ---------------------------------------------------------------------------


def _stage_subsetting(
    engine: Any,
    topology: SchemaTopology,
    subset_size: int,
    source_dir: Path,
) -> dict[str, Any]:
    """Extract a subset of rows from the source DB and write to Parquet.

    Reads directly via SQLAlchemy LIMIT queries (one per table) and writes
    each table's rows to ``source_dir/<table>.parquet``.

    Args:
        engine: SQLAlchemy Engine connected to the source database.
        topology: SchemaTopology for the 5-table subset.
        subset_size: Maximum number of seed rows to extract.
        source_dir: Directory where Parquet files will be written.

    Returns:
        Stage result dict with duration and per-table row counts.
    """
    t_start = time.monotonic()
    source_dir.mkdir(parents=True, exist_ok=True)

    # The SubsettingEngine writes to a target DB via EgressWriter.
    # For validation purposes, we stream directly from the source without
    # a second DB — we read each table directly via SQLAlchemy and write Parquet.
    #
    # This approach exercises the SchemaInspector path and writes the same
    # data format expected by SynthesisEngine.train(), while avoiding the
    # requirement for a separate target database in validation mode.

    row_counts: dict[str, int] = {}

    from sqlalchemy import text as sql_text

    for table in topology.table_order:
        if table not in topology.columns:
            continue

        col_names = [col.name for col in topology.columns[table]]
        # Allowlist guard: table must be in the known-safe set before interpolation.
        assert table in _TARGET_TABLES, f"Table '{table}' not in allowlist"
        # Use a parameterised LIMIT to extract the subset.
        with engine.connect() as conn:
            result = conn.execute(
                sql_text(f"SELECT * FROM {table} LIMIT :lim"),  # nosec B608 -- table name is from SchemaInspector allowlist  # noqa: S608
                {"lim": subset_size},
            )
            rows = [dict(r._mapping) for r in result]

        fallback_cols = [c.name for c in topology.columns[table]]
        df = pd.DataFrame(rows, columns=col_names if rows else fallback_cols)
        df = _sanitize_dataframe_for_sdv(df, table)
        parquet_path = source_dir / f"{table}.parquet"
        df.to_parquet(parquet_path, engine="pyarrow", index=False)
        row_counts[table] = len(df)
        _logger.info("Subsetted table '%s': %d rows -> %s", table, len(df), parquet_path)

    # Guard: warn on empty tables; exit with infrastructure error if ALL are empty.
    empty_tables = [t for t, n in row_counts.items() if n == 0]
    for t in empty_tables:
        _logger.warning("Subset table '%s' is empty — no rows extracted.", t)
    if empty_tables and len(empty_tables) == len(row_counts):
        _logger.error(
            "All subset tables are empty (%s). "
            "The source database may be unpopulated or the connection is wrong.",
            empty_tables,
        )
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    duration = time.monotonic() - t_start
    _logger.info("Subsetting complete: %s (%.2fs)", row_counts, duration)

    return {
        "duration_seconds": round(duration, 4),
        "row_counts": row_counts,
    }


# ---------------------------------------------------------------------------
# Stage 3: Masking
# ---------------------------------------------------------------------------


def _stage_masking(
    topology: SchemaTopology,
    source_dir: Path,
    masked_dir: Path,
) -> dict[str, Any]:
    """Apply MaskingRegistry to PII-like columns and write masked Parquet.

    Processes each table in topology order.  PII columns listed in
    ``_PII_COLUMNS`` are masked deterministically.  Non-PII tables are
    copied unchanged.

    Args:
        topology: SchemaTopology describing the tables.
        source_dir: Directory containing source Parquet files.
        masked_dir: Directory where masked Parquet files will be written.

    Returns:
        Stage result dict with duration and list of masked column specs.
    """
    t_start = time.monotonic()
    masked_dir.mkdir(parents=True, exist_ok=True)

    registry = MaskingRegistry()
    columns_masked: list[str] = []

    for table in topology.table_order:
        source_path = source_dir / f"{table}.parquet"
        if not source_path.exists():
            _logger.warning("Source Parquet not found for '%s', skipping masking.", table)
            continue

        df = pd.read_parquet(source_path, engine="pyarrow")
        pii_cols = _PII_COLUMNS.get(table, [])
        registry.reset()

        for col_name, col_type in pii_cols:
            if col_name not in df.columns:
                continue
            salt = f"{table}.{col_name}"
            df[col_name] = df[col_name].apply(
                lambda val, ct=col_type, s=salt: (
                    registry.mask(str(val), ct, s) if pd.notna(val) and str(val).strip() else val
                )
            )
            columns_masked.append(f"{table}.{col_name}")
            _logger.info("Masked column '%s.%s'", table, col_name)

        masked_path = masked_dir / f"{table}.parquet"
        df.to_parquet(masked_path, engine="pyarrow", index=False)
        _logger.info("Wrote masked Parquet for '%s' -> %s", table, masked_path)

    duration = time.monotonic() - t_start
    _logger.info("Masking complete: %d columns masked (%.2fs)", len(columns_masked), duration)

    return {
        "duration_seconds": round(duration, 4),
        "columns_masked": columns_masked,
    }


# ---------------------------------------------------------------------------
# Stage 4: Statistical Profiling
# ---------------------------------------------------------------------------


def _stage_profiling(
    topology: SchemaTopology,
    masked_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Profile each masked table using StatisticalProfiler.

    Args:
        topology: SchemaTopology describing the tables.
        masked_dir: Directory containing masked Parquet files.

    Returns:
        Tuple of (stage_result dict, baseline_profiles mapping table->TableProfile).
    """
    t_start = time.monotonic()

    profiler = StatisticalProfiler()
    baseline_profiles: dict[str, Any] = {}

    for table in topology.table_order:
        masked_path = masked_dir / f"{table}.parquet"
        if not masked_path.exists():
            continue

        df = pd.read_parquet(masked_path, engine="pyarrow")
        profile = profiler.profile(table, df)
        baseline_profiles[table] = profile
        _logger.info(
            "Profiled table '%s': %d rows, %d cols",
            table,
            profile.row_count,
            len(profile.columns),
        )

    duration = time.monotonic() - t_start
    _logger.info("Profiling complete (%.2fs)", duration)

    return {
        "duration_seconds": round(duration, 4),
    }, baseline_profiles


# ---------------------------------------------------------------------------
# Stage 5: CTGAN Training with DP-SGD
# ---------------------------------------------------------------------------


def _stage_training(
    topology: SchemaTopology,
    masked_dir: Path,
    epochs: int,
    epsilon: float,
    delta: float,
    dp_wrapper: DPTrainingWrapper,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Train SynthesisEngine on each masked table's Parquet file.

    Args:
        topology: SchemaTopology describing the tables.
        masked_dir: Directory containing masked Parquet files.
        epochs: Number of CTGAN training epochs.
        epsilon: Allocated epsilon budget.
        delta: Delta value for DP accounting.
        dp_wrapper: Pre-configured DPTrainingWrapper instance.

    Returns:
        Tuple of (stage_result dict, artifacts mapping table->ModelArtifact).
    """
    t_start = time.monotonic()

    synth_engine = SynthesisEngine(epochs=epochs)
    artifacts: dict[str, Any] = {}

    for table in topology.table_order:
        masked_path = masked_dir / f"{table}.parquet"
        if not masked_path.exists():
            _logger.warning("Masked Parquet not found for '%s', skipping training.", table)
            continue

        _logger.info("Training on table '%s' ...", table)
        artifact = synth_engine.train(
            table_name=table,
            parquet_path=str(masked_path),
            dp_wrapper=dp_wrapper,
        )
        artifacts[table] = artifact
        _logger.info("Training complete for table '%s'.", table)

    epsilon_spent = dp_wrapper.epsilon_spent(delta=delta)
    duration = time.monotonic() - t_start
    _logger.info(
        "Training complete: epsilon_spent=%.4f / %.4f, delta=%s (%.2fs)",
        epsilon_spent,
        epsilon,
        delta,
        duration,
    )

    return {
        "duration_seconds": round(duration, 4),
        "epsilon_spent": epsilon_spent,
        "delta": delta,
    }, artifacts


# ---------------------------------------------------------------------------
# Stage 6: Synthetic Generation
# ---------------------------------------------------------------------------


def _stage_generation(
    topology: SchemaTopology,
    artifacts: dict[str, Any],
    source_row_counts: dict[str, int],
) -> tuple[dict[str, Any], dict[str, pd.DataFrame], bool]:
    """Generate synthetic rows for each trained table.

    Args:
        topology: SchemaTopology describing the tables.
        artifacts: Mapping of table name to ModelArtifact from training.
        source_row_counts: Row counts from the subsetting stage.

    Returns:
        Tuple of (stage_result dict, synthetic DataFrames, training_divergence flag).
    """
    t_start = time.monotonic()

    synth_engine = SynthesisEngine()
    synthetic_dfs: dict[str, pd.DataFrame] = {}
    row_counts: dict[str, int] = {}
    training_divergence = False

    for table in topology.table_order:
        artifact = artifacts.get(table)
        if artifact is None:
            continue

        n_rows = source_row_counts.get(table, 100)
        if n_rows < 1:
            n_rows = 1

        _logger.info("Generating %d synthetic rows for '%s' ...", n_rows, table)
        synth_df = synth_engine.generate(artifact, n_rows=n_rows)

        # Training divergence detection.
        if _detect_nan_inf(synth_df, table):
            training_divergence = True

        synthetic_dfs[table] = synth_df
        row_counts[table] = len(synth_df)
        _logger.info("Generated %d rows for '%s'.", len(synth_df), table)

    duration = time.monotonic() - t_start
    _logger.info("Generation complete: %s (%.2fs)", row_counts, duration)

    return (
        {
            "duration_seconds": round(duration, 4),
            "row_counts": row_counts,
        },
        synthetic_dfs,
        training_divergence,
    )


# ---------------------------------------------------------------------------
# Stage 7: FK Post-Processing
# ---------------------------------------------------------------------------


def _stage_fk_post_processing(
    topology: SchemaTopology,
    synthetic_dfs: dict[str, pd.DataFrame],
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    """Apply FK post-processing to eliminate orphan FK references.

    Args:
        topology: SchemaTopology with FK relationship information.
        synthetic_dfs: Synthetic DataFrames keyed by table name.

    Returns:
        Tuple of (stage_result dict, updated synthetic_dfs with orphans removed).
    """
    t_start = time.monotonic()

    orphans_removed: dict[str, int] = {}
    updated_dfs = dict(synthetic_dfs)

    for child_table, fk_infos in topology.foreign_keys.items():
        if child_table not in updated_dfs:
            continue

        for fk in fk_infos:
            parent_table = fk.referred_table
            if parent_table not in updated_dfs:
                continue

            fk_col = fk.constrained_columns[0] if fk.constrained_columns else None
            parent_pk_col = fk.referred_columns[0] if fk.referred_columns else None

            if fk_col is None or parent_pk_col is None:
                continue

            parent_df = updated_dfs[parent_table]
            if parent_pk_col not in parent_df.columns:
                continue

            valid_parent_pks = set(parent_df[parent_pk_col].dropna().tolist())
            if not valid_parent_pks:
                continue

            child_df = updated_dfs[child_table]
            if fk_col not in child_df.columns:
                continue

            before_count = int((~child_df[fk_col].isin(valid_parent_pks)).sum())
            fixed_df = apply_fk_post_processing(
                child_df=child_df,
                fk_column=fk_col,
                valid_parent_pks=valid_parent_pks,
            )
            after_count = int((~fixed_df[fk_col].isin(valid_parent_pks)).sum())

            removed = before_count - after_count
            orphans_removed[f"{child_table}.{fk_col}"] = removed
            updated_dfs[child_table] = fixed_df

            _logger.info(
                "FK post-processing '%s.%s' -> '%s.%s': removed %d orphan(s).",
                child_table,
                fk_col,
                parent_table,
                parent_pk_col,
                removed,
            )

    duration = time.monotonic() - t_start
    _logger.info("FK post-processing complete: %s (%.2fs)", orphans_removed, duration)

    return {
        "duration_seconds": round(duration, 4),
        "orphans_removed": orphans_removed,
    }, updated_dfs


# ---------------------------------------------------------------------------
# Stage 8: Validation
# ---------------------------------------------------------------------------


def _stage_validation(
    topology: SchemaTopology,
    synthetic_dfs: dict[str, pd.DataFrame],
    source_dir: Path,
    source_row_counts: dict[str, int],
    baseline_profiles: dict[str, Any],
    epsilon_spent: float,
    epsilon_allocated: float,
    delta: float,
) -> dict[str, Any]:
    """Run all validation checks and return the validation result dict.

    Checks:
    1. FK integrity: count orphan FKs after post-processing (must be 0).
    2. Epsilon budget: epsilon_spent must be < epsilon_allocated.
    3. Masking verification: no source PII values in synthetic output.
    4. Statistical comparison: KS statistics per numeric column.
    5. Row count comparison: source vs synthetic per table.

    Args:
        topology: SchemaTopology with FK relationships.
        synthetic_dfs: Synthetic DataFrames after FK post-processing.
        source_dir: Directory with original (unmasked) Parquet files.
        source_row_counts: Row counts from subsetting stage.
        baseline_profiles: TableProfile objects from profiling stage.
        epsilon_spent: Actual epsilon used during training.
        epsilon_allocated: Maximum allowed epsilon.
        delta: Delta value for DP accounting.

    Returns:
        Validation result dict matching the report schema.
    """
    # --- 1. FK integrity ---
    fk_orphan_counts: dict[str, int] = {}
    fk_pass = True

    for child_table, fk_infos in topology.foreign_keys.items():
        if child_table not in synthetic_dfs:
            continue
        for fk in fk_infos:
            parent_table = fk.referred_table
            fk_col = fk.constrained_columns[0] if fk.constrained_columns else None
            parent_pk_col = fk.referred_columns[0] if fk.referred_columns else None

            if fk_col is None or parent_pk_col is None:
                continue
            if parent_table not in synthetic_dfs:
                continue

            parent_df = synthetic_dfs[parent_table]
            child_df = synthetic_dfs[child_table]

            if fk_col not in child_df.columns or parent_pk_col not in parent_df.columns:
                continue

            valid_pks = set(parent_df[parent_pk_col].dropna().tolist())
            orphan_count = int((~child_df[fk_col].isin(valid_pks)).sum())
            key = f"{child_table}.{fk_col}"
            fk_orphan_counts[key] = orphan_count
            if orphan_count > 0:
                fk_pass = False
                _logger.error(
                    "FK integrity violation: %d orphan(s) in '%s.%s'.",
                    orphan_count,
                    child_table,
                    fk_col,
                )

    # --- 2. Epsilon budget ---
    epsilon_pass = epsilon_spent <= epsilon_allocated
    if not epsilon_pass:
        _logger.error(
            "Epsilon budget exceeded: spent=%.4f, allocated=%.4f",
            epsilon_spent,
            epsilon_allocated,
        )

    # --- 3. Masking verification ---
    masking_violations: list[str] = []
    masking_pass = True

    for table, pii_cols in _PII_COLUMNS.items():
        source_path = source_dir / f"{table}.parquet"
        if not source_path.exists() or table not in synthetic_dfs:
            continue

        source_df = pd.read_parquet(source_path, engine="pyarrow")
        synth_df = synthetic_dfs[table]

        for col_name, _ in pii_cols:
            if col_name not in source_df.columns or col_name not in synth_df.columns:
                continue

            source_values = set(source_df[col_name].dropna().astype(str).tolist())
            synth_values = set(synth_df[col_name].dropna().astype(str).tolist())

            leaked = source_values & synth_values
            if leaked:
                violation = (
                    f"{table}.{col_name}: {len(leaked)} source value(s) found in synthetic output"
                )
                masking_violations.append(violation)
                masking_pass = False
                _logger.error("Masking violation: %s", violation)

    # --- 4. Statistical comparison (KS statistics — informational) ---
    statistical_per_table: dict[str, dict[str, dict[str, float]]] = {}

    for table in topology.table_order:
        source_path = source_dir / f"{table}.parquet"
        if not source_path.exists() or table not in synthetic_dfs:
            continue

        source_df = pd.read_parquet(source_path, engine="pyarrow")
        synth_df = synthetic_dfs[table]
        table_ks: dict[str, dict[str, float]] = {}

        numeric_cols = source_df.select_dtypes(include=[np.number]).columns
        for col in numeric_cols:
            if col not in synth_df.columns:
                continue
            src_vals = source_df[col].dropna().to_numpy()
            syn_vals = synth_df[col].dropna().to_numpy()
            if len(src_vals) < 2 or len(syn_vals) < 2:
                continue
            ks_stat, p_val = ks_2samp(src_vals, syn_vals)
            table_ks[col] = {
                "ks_statistic": float(ks_stat),
                "p_value": float(p_val),
            }

        if table_ks:
            statistical_per_table[table] = table_ks

    # --- 5. Row count comparison ---
    row_count_comparison: dict[str, dict[str, int]] = {}
    for table in topology.table_order:
        synth_rows = len(synthetic_dfs[table]) if table in synthetic_dfs else 0
        source_rows = source_row_counts.get(table, 0)
        row_count_comparison[table] = {"source": source_rows, "synthetic": synth_rows}

    return {
        "fk_integrity": {
            "pass": fk_pass,
            "orphan_counts": fk_orphan_counts,
        },
        "epsilon_budget": {
            "pass": epsilon_pass,
            "spent": epsilon_spent,
            "allocated": epsilon_allocated,
        },
        "masking_verification": {
            "pass": masking_pass,
            "violations": masking_violations,
        },
        "statistical_comparison": {
            "per_table": statistical_per_table,
        },
        "row_counts": row_count_comparison,
    }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the full pipeline validation and write a JSON report.

    Args:
        argv: Argument list override for testing.  Defaults to sys.argv.

    Returns:
        Exit code: 0 (pass), 1 (validation failure), 2 (infrastructure error).
    """
    args = _parse_args(argv)

    # --- Epsilon high-value warning (per spec) ---
    if args.epsilon > _EPSILON_HIGH_PRIVACY_THRESHOLD:
        _logger.warning(
            "WARNING: --epsilon=%.1f exceeds the privacy-quality threshold of %.1f. "
            "High epsilon values provide weaker differential privacy guarantees. "
            "Consider using epsilon <= %.1f for production data.",
            args.epsilon,
            _EPSILON_HIGH_PRIVACY_THRESHOLD,
            _EPSILON_HIGH_PRIVACY_THRESHOLD,
        )

    # --- Force CPU mode ---
    if args.force_cpu:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        _logger.info("Force-CPU mode active: CUDA_VISIBLE_DEVICES='' set.")

    # --- Validate output directory before pipeline starts ---
    output_dir = _validate_output_dir(args.output_dir)
    source_dir = output_dir / "source"
    masked_dir = output_dir / "masked"
    source_dir.mkdir(parents=True, exist_ok=True)
    masked_dir.mkdir(parents=True, exist_ok=True)

    wall_start = time.monotonic()
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()

    report: dict[str, Any] = {
        "timestamp": timestamp,
        "branch": _get_git_branch(),
        "python_version": sys.version,
        "config": {
            "subset_size": args.subset_size,
            "epsilon": args.epsilon,
            "delta": args.delta,
            "epochs": args.epochs,
            "force_cpu": args.force_cpu,
        },
        "stages": {},
        "overall_pass": False,  # nosec B105 — "pass" in key name is not a password
        "wall_clock_seconds": 0.0,
    }

    # --- Connect to DB ---
    try:
        from sqlalchemy import create_engine

        _logger.info("Connecting to source database ...")
        engine = create_engine(args.db_url, pool_pre_ping=True)
        # Verify connectivity before committing to the full pipeline.
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        _logger.info("Database connection OK.")
    except Exception as exc:
        _logger.error("Database connection failed: %s", type(exc).__name__)
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    # --- Stage 1: Schema Reflection ---
    _logger.info("=== Stage 1: Schema Reflection ===")
    try:
        schema_result, topology = _stage_schema_reflection(engine)
        report["stages"]["schema_reflection"] = schema_result
    except Exception as exc:
        _logger.error("Schema reflection failed: %s", type(exc).__name__)
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    # --- Stage 2: Subsetting ---
    _logger.info("=== Stage 2: Subsetting ===")
    try:
        subsetting_result = _stage_subsetting(engine, topology, args.subset_size, source_dir)
        report["stages"]["subsetting"] = subsetting_result
        source_row_counts = subsetting_result["row_counts"]
    except Exception as exc:
        _logger.error("Subsetting failed: %s", type(exc).__name__)
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    # --- Stage 3: Masking ---
    _logger.info("=== Stage 3: Masking ===")
    try:
        masking_result = _stage_masking(topology, source_dir, masked_dir)
        report["stages"]["masking"] = masking_result
    except Exception as exc:
        _logger.error("Masking failed: %s", type(exc).__name__)
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    # --- Stage 4: Profiling ---
    _logger.info("=== Stage 4: Statistical Profiling ===")
    try:
        profiling_result, baseline_profiles = _stage_profiling(topology, masked_dir)
        report["stages"]["profiling"] = profiling_result
    except Exception as exc:
        _logger.error("Profiling failed: %s", type(exc).__name__)
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    # --- Stage 5: Training with DP-SGD ---
    _logger.info("=== Stage 5: CTGAN Training with DP-SGD ===")
    dp_wrapper = DPTrainingWrapper(
        max_grad_norm=_DEFAULT_MAX_GRAD_NORM,
        noise_multiplier=_DEFAULT_NOISE_MULTIPLIER,
    )
    try:
        training_result, artifacts = _stage_training(
            topology,
            masked_dir,
            args.epochs,
            args.epsilon,
            args.delta,
            dp_wrapper,
        )
        report["stages"]["training"] = training_result
        epsilon_spent = training_result["epsilon_spent"]
    except BudgetExhaustionError as exc:
        _logger.error(
            "DP budget exhausted during training: %s. "
            "Increase --epsilon or reduce --epochs to stay within budget.",
            exc,
        )
        report["stages"]["training"] = {
            "duration_seconds": 0.0,
            "epsilon_spent": float(exc.total_spent),
            "delta": args.delta,
            "error": "BudgetExhaustionError",
        }
        _write_report(report, output_dir, wall_start)
        sys.exit(EXIT_VALIDATION_FAILURE)
    except Exception as exc:
        _logger.error("Training failed: %s", type(exc).__name__)
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    # --- Stage 6: Synthetic Generation ---
    _logger.info("=== Stage 6: Synthetic Generation ===")
    try:
        generation_result, synthetic_dfs, training_divergence = _stage_generation(
            topology, artifacts, source_row_counts
        )
        report["stages"]["generation"] = generation_result

        if training_divergence:
            generation_result["training_divergence"] = True
            _logger.error(
                "training_divergence detected in synthetic output. "
                "The model may have failed to converge."
            )
            _write_report(report, output_dir, wall_start)
            sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    except Exception as exc:
        _logger.error("Generation failed: %s", type(exc).__name__)
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    # --- Stage 7: FK Post-Processing ---
    _logger.info("=== Stage 7: FK Post-Processing ===")
    try:
        fk_pp_result, synthetic_dfs = _stage_fk_post_processing(topology, synthetic_dfs)
        report["stages"]["fk_post_processing"] = fk_pp_result
    except Exception as exc:
        _logger.error("FK post-processing failed: %s", type(exc).__name__)
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    # --- Stage 8: Validation ---
    _logger.info("=== Stage 8: Validation ===")
    try:
        validation_result = _stage_validation(
            topology=topology,
            synthetic_dfs=synthetic_dfs,
            source_dir=source_dir,
            source_row_counts=source_row_counts,
            baseline_profiles=baseline_profiles,
            epsilon_spent=epsilon_spent,
            epsilon_allocated=args.epsilon,
            delta=args.delta,
        )
    except Exception as exc:
        _logger.error("Validation stage failed: %s", type(exc).__name__)
        sys.exit(EXIT_INFRASTRUCTURE_ERROR)

    report["stages"]["validation"] = validation_result

    # --- Overall pass/fail ---
    fk_pass = validation_result["fk_integrity"]["pass"]
    epsilon_pass = validation_result["epsilon_budget"]["pass"]
    masking_pass = validation_result["masking_verification"]["pass"]
    overall_pass = fk_pass and epsilon_pass and masking_pass

    report["overall_pass"] = overall_pass

    _write_report(report, output_dir, wall_start)

    if not overall_pass:
        _logger.error(
            "Validation FAILED. FK integrity: %s | Epsilon budget: %s | Masking: %s",
            "PASS" if fk_pass else "FAIL",
            "PASS" if epsilon_pass else "FAIL",
            "PASS" if masking_pass else "FAIL",
        )
        sys.exit(EXIT_VALIDATION_FAILURE)

    _logger.info("Validation PASSED. FK integrity: PASS | Epsilon budget: PASS | Masking: PASS")
    return EXIT_SUCCESS


def _write_report(
    report: dict[str, Any],
    output_dir: Path,
    wall_start: float,
) -> None:
    """Write the validation report JSON to the output directory.

    The DSN is never included in the report.  The file is named with an
    ISO timestamp to prevent overwrites between runs.

    Args:
        report: Report dictionary to serialise.
        output_dir: Directory where the report file will be written.
        wall_start: Wall-clock start time (from time.monotonic()) for total duration.
    """
    wall_duration = time.monotonic() - wall_start
    report["wall_clock_seconds"] = round(wall_duration, 2)

    ts_safe = report["timestamp"].replace(":", "-").replace("+", "Z")[:23]
    report_path = output_dir / f"validation-report-{ts_safe}.json"

    report_path.write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    _logger.info("Validation report written to %s", report_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
