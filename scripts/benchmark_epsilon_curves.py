"""Parameterized benchmark harness for epsilon/quality trade-off curves (T52.1).

Trains CTGAN at configurable (noise_multiplier x epochs x sample_size) parameter
grids and records per-run quality metrics, privacy accounting, and hardware metadata
to structured JSON and CSV artifacts.

The delta used for epsilon accounting is ``_BENCHMARK_DP_DELTA`` which is explicitly
required to match the production constant ``DP_EPSILON_DELTA`` in
``synth_engine.modules.synthesizer.dp_accounting``.

Security requirements:
  - YAML config loading uses ``yaml.safe_load()`` ONLY (Bandit B506).
  - Output filenames are derived from the parameter grid config, NOT from
    dataset column names, to prevent path-traversal injection from real data.
  - Each result row includes ``schema_version`` for forward-compatibility.
  - Artifact filenames are sanitized via ``_sanitize_filename()``.

Idempotence:
  - A completed run's parameter combination is detected via a ``results.csv``
    already present in the output directory.  Matching rows are skipped so
    that interrupted benchmarks can be resumed without duplicating work.

Per-run timeout:
  - Each grid cell has a configurable timeout (default: 1800 s / 30 min).
  - A timed-out cell writes a ``status=TIMEOUT`` result row and continues.

Usage::

    poetry run python3 scripts/benchmark_epsilon_curves.py \\
        --conn "postgresql://user:pass@localhost/db" \\  # pragma: allowlist secret
        --table my_table \\
        --grid-config demos/results/grid_config.json \\
        --output-dir demos/results/

    # For offline/CI usage, pass --source-csv to skip DB entirely:
    poetry run python3 scripts/benchmark_epsilon_curves.py \\
        --source-csv tests/fixtures/benchmark_fixture.csv \\
        --grid-config demos/results/grid_config.json \\
        --output-dir demos/results/

Task: P52-T52.1 — Benchmark Infrastructure
"""

from __future__ import annotations

import json
import os
import platform
import random
import re
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path adjustment — allows `poetry run python3 scripts/...` from repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# ---------------------------------------------------------------------------
# Graceful dependency check — exit with a clear message if core deps are missing
# ---------------------------------------------------------------------------
try:
    import numpy as np
    import pandas as pd
    import yaml
except ImportError as _missing:
    print(
        f"ERROR: Required dependency missing: {_missing}.\n"
        "Run: poetry install --with dev,synthesizer",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Artifact schema version — bump when the output format changes.
SCHEMA_VERSION: str = "1.0"

#: Delta for epsilon accounting — MUST match production DP_EPSILON_DELTA.
#: Verified by test_benchmark_epsilon_delta_matches_production_constant.
_BENCHMARK_DP_DELTA: float = 1e-5

#: Default per-run timeout in seconds (30 minutes).
_DEFAULT_TIMEOUT_SECONDS: int = 1800

#: Maximum filename segment length after sanitisation.
_MAX_FILENAME_SEGMENT_LEN: int = 64

#: Filename-safe character whitelist pattern.
_SAFE_FILENAME_RE: re.Pattern[str] = re.compile(r"[^a-zA-Z0-9_\-]")

#: Production delta constant path for documentation purposes.
_PRODUCTION_DELTA_IMPORT: str = "synth_engine.modules.synthesizer.dp_accounting.DP_EPSILON_DELTA"


# ---------------------------------------------------------------------------
# Security: filename sanitisation
# ---------------------------------------------------------------------------


def _sanitize_filename(raw: str, max_len: int = _MAX_FILENAME_SEGMENT_LEN) -> str:
    """Sanitize a string for safe use as a filesystem path component.

    Replaces any character that is not alphanumeric, underscore, or hyphen
    with an underscore.  Truncates to ``max_len`` characters.  This prevents
    path-traversal attacks when parameter grid keys are used in output filenames.

    Args:
        raw: The raw string to sanitize (from config keys, never from data).
        max_len: Maximum length of the returned string.

    Returns:
        A filesystem-safe string with at most ``max_len`` characters.
    """
    sanitized = _SAFE_FILENAME_RE.sub("_", raw)
    return sanitized[:max_len]


# ---------------------------------------------------------------------------
# Security: error message sanitisation
# ---------------------------------------------------------------------------

# Pattern: PostgreSQL DSN creds (scheme://user:pass@host).  # pragma: allowlist secret
_DSN_CREDENTIALS_RE: re.Pattern[str] = re.compile(
    r"postgresql(\+\w+)?://[^@\s]*@",
    re.IGNORECASE,
)


def _sanitize_error_message(raw: str) -> str:
    """Remove PostgreSQL DSN credentials from an exception message.

    Strips any ``postgresql[+driver]://...@`` credential prefix  # pragma: allowlist secret
    from the error string so that database connection strings
    are never persisted to result artifacts.

    Args:
        raw: The raw exception message that may contain a DSN.

    Returns:
        The message with any DSN credential component replaced by
        ``postgresql://<redacted>@``.
    """
    return _DSN_CREDENTIALS_RE.sub("postgresql://<redacted>@", raw)


# ---------------------------------------------------------------------------
# Hardware metadata
# ---------------------------------------------------------------------------


def _collect_hardware_metadata() -> dict[str, Any]:
    """Collect hardware metadata for the current host.

    Returns:
        Dictionary with keys: cpu_model, ram_gb, cpu_count, os, gpu_available,
        gpu_name.  GPU info is included if torch is installed and CUDA is
        available; otherwise gpu_available=False and gpu_name=None.
    """
    import psutil

    metadata: dict[str, Any] = {
        "cpu_model": platform.processor() or platform.machine(),
        "ram_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        "cpu_count": os.cpu_count(),
        "os": f"{platform.system()} {platform.release()}",
        "gpu_available": False,
        "gpu_name": None,
    }

    try:
        import torch

        if torch.cuda.is_available():
            metadata["gpu_available"] = True
            metadata["gpu_name"] = torch.cuda.get_device_name(0)
    except ImportError:
        pass

    return metadata


# ---------------------------------------------------------------------------
# Seed management
# ---------------------------------------------------------------------------


def _set_seeds(seed: int) -> None:
    """Set random seeds for Python, NumPy, and (if available) PyTorch.

    Args:
        seed: The integer seed value to apply across all RNG backends.
    """
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Grid config loading
# ---------------------------------------------------------------------------


def load_grid_config(config_path: str) -> dict[str, Any]:
    """Load a parameter grid config from a JSON or YAML file.

    YAML files are parsed with ``yaml.safe_load()`` ONLY.  Any YAML document
    containing non-standard tags (e.g. ``!!python/object/apply:os.system``)
    will raise ``yaml.constructor.ConstructorError`` before any code executes.

    Args:
        config_path: Absolute or relative path to a ``.json`` or ``.yaml``/
            ``.yml`` config file.

    Returns:
        Dictionary mapping parameter names to lists of values.

    Raises:
        ValueError: If the file extension is not .json, .yaml, or .yml.
        yaml.constructor.ConstructorError: If the YAML contains unsafe tags.
    """
    path = Path(config_path)
    suffix = path.suffix.lower()

    if suffix == ".json":
        with open(path, encoding="utf-8") as f:
            return json.load(f)  # type: ignore[no-any-return]
    elif suffix in {".yaml", ".yml"}:
        with open(path, encoding="utf-8") as f:
            result = yaml.safe_load(f)
        return result  # type: ignore[no-any-return]
    else:
        raise ValueError(
            f"Unsupported config file extension: {suffix!r}. Expected .json, .yaml, or .yml."
        )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_from_db(connection_string: str, table_name: str) -> pd.DataFrame:
    """Load a table from PostgreSQL into a DataFrame.

    Args:
        connection_string: PostgreSQL DSN.  # pragma: allowlist secret
        table_name: Name of the table to SELECT from.

    Returns:
        DataFrame containing all rows from the specified table.

    Raises:
        ValueError: If table_name contains characters outside [a-zA-Z0-9_].
    """
    from sqlalchemy import create_engine, text

    # Sanitize table name before embedding in SQL — must be identifier-safe only
    if not re.match(r"^[a-zA-Z0-9_]+$", table_name):
        raise ValueError(
            f"table_name must contain only alphanumeric characters and underscores, "
            f"got: {table_name!r}"
        )

    engine = create_engine(connection_string)
    with engine.connect() as conn:
        # Use text() with a validated identifier — not user-controlled string interpolation
        return pd.read_sql(text(f"SELECT * FROM {table_name}"), conn)  # noqa: S608  # nosec B608 -- table_name validated by regex above


# ---------------------------------------------------------------------------
# Quality metrics
# ---------------------------------------------------------------------------


def _compute_ks_statistic(
    source_col: pd.Series,  # type: ignore[type-arg]
    synth_col: pd.Series,  # type: ignore[type-arg]
) -> float:
    """Compute the Kolmogorov-Smirnov statistic between source and synthetic columns.

    Args:
        source_col: Source column as a numeric pandas Series.
        synth_col: Synthetic column as a numeric pandas Series.

    Returns:
        KS statistic (0.0 to 1.0, lower is better).
    """
    from scipy.stats import ks_2samp

    stat, _ = ks_2samp(source_col.dropna().values, synth_col.dropna().values)
    return float(stat)


def _compute_chi2_pvalue(
    source_col: pd.Series,  # type: ignore[type-arg]
    synth_col: pd.Series,  # type: ignore[type-arg]
) -> float:
    """Compute chi-squared p-value between source and synthetic categorical columns.

    Args:
        source_col: Source categorical column.
        synth_col: Synthetic categorical column.

    Returns:
        p-value from chi-squared test (0.0 to 1.0, higher is better).
    """
    from scipy.stats import chi2_contingency

    all_cats = pd.concat([source_col, synth_col]).unique()
    source_counts = source_col.value_counts().reindex(all_cats, fill_value=0)
    synth_counts = synth_col.value_counts().reindex(all_cats, fill_value=0)

    contingency = np.array([source_counts.values, synth_counts.values])
    _, p_value, _, _ = chi2_contingency(contingency)
    return float(p_value)


def _compute_mae(
    source_col: pd.Series,  # type: ignore[type-arg]
    synth_col: pd.Series,  # type: ignore[type-arg]
) -> float:
    """Compute mean absolute error between source and synthetic numeric columns.

    Args:
        source_col: Source numeric column.
        synth_col: Synthetic numeric column.

    Returns:
        MAE (mean absolute error) between column means.
    """
    return float(abs(source_col.mean() - synth_col.mean()))


def _compute_correlation_delta(source_df: pd.DataFrame, synth_df: pd.DataFrame) -> float:
    """Compute the Frobenius norm of the correlation matrix difference.

    Args:
        source_df: Source DataFrame (numeric columns only).
        synth_df: Synthetic DataFrame (numeric columns only).

    Returns:
        Frobenius norm of (source_corr - synth_corr).  Lower is better.
    """
    num_source = source_df.select_dtypes(include="number")
    num_synth = synth_df.select_dtypes(include="number")

    if num_source.shape[1] < 2 or num_synth.shape[1] < 2:
        return 0.0

    common_cols = [c for c in num_source.columns if c in num_synth.columns]
    if len(common_cols) < 2:
        return 0.0

    source_corr = num_source[common_cols].corr().fillna(0).values
    synth_corr = num_synth[common_cols].corr().fillna(0).values
    return float(np.linalg.norm(source_corr - synth_corr))


def _compute_column_metrics(
    source_df: pd.DataFrame, synth_df: pd.DataFrame
) -> dict[str, dict[str, float]]:
    """Compute per-column quality metrics.

    For numeric columns: KS statistic and MAE.
    For categorical columns: chi-squared p-value.

    Args:
        source_df: Source training DataFrame.
        synth_df: Synthetic output DataFrame.

    Returns:
        Nested dict mapping column name to a dict of metric_name -> value.
    """
    metrics: dict[str, dict[str, float]] = {}

    for col in source_df.columns:
        if col not in synth_df.columns:
            continue

        col_metrics: dict[str, float] = {}

        if pd.api.types.is_numeric_dtype(source_df[col]):
            try:
                col_metrics["ks_statistic"] = _compute_ks_statistic(source_df[col], synth_df[col])
                col_metrics["mae"] = _compute_mae(source_df[col], synth_df[col])
            except Exception:
                col_metrics["ks_statistic"] = float("nan")
                col_metrics["mae"] = float("nan")
        else:
            try:
                col_metrics["chi2_pvalue"] = _compute_chi2_pvalue(source_df[col], synth_df[col])
            except Exception:
                col_metrics["chi2_pvalue"] = float("nan")

        metrics[col] = col_metrics

    return metrics


# ---------------------------------------------------------------------------
# FK orphan rate (stub — DB-backed only)
# ---------------------------------------------------------------------------


def _compute_fk_orphan_rate(synth_df: pd.DataFrame) -> float | None:
    """Compute FK orphan rate for the synthetic output.

    Args:
        synth_df: Synthetic DataFrame.

    Returns:
        FK orphan rate as a float (0.0-1.0), or None if not applicable.

    Note:
        This is a stub for DB-backed runs.  Without FK metadata from the
        database schema, orphan rate cannot be computed.  Returns None.
    """
    return None


# ---------------------------------------------------------------------------
# Single-run execution
# ---------------------------------------------------------------------------


def _train_and_sample(
    source_df: pd.DataFrame,
    noise_multiplier: float,
    epochs: int,
    sample_size: int,
) -> tuple[pd.DataFrame, float]:
    """Train DP-CTGAN on source_df and return (synthetic_df, actual_epsilon).

    Args:
        source_df: Training DataFrame.
        noise_multiplier: Opacus noise multiplier.
        epochs: Number of training epochs.
        sample_size: Number of synthetic rows to generate.

    Returns:
        Tuple of (synthetic_df, actual_epsilon).

    Raises:
        ImportError: If the synthesizer group is not installed.
        ValueError: If source_df is empty.
    """
    import warnings

    if source_df.empty:
        raise ValueError("source_df is empty — cannot train CTGAN on an empty DataFrame.")

    try:
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
        from synth_engine.modules.synthesizer.dp_training import (
            DPCompatibleCTGAN,
        )
    except ImportError as exc:
        raise ImportError(
            f"Synthesizer group not installed: {exc}. Run: poetry install --with synthesizer"
        ) from exc

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        try:
            from sdv.metadata import SingleTableMetadata

            meta = SingleTableMetadata()
            meta.detect_from_dataframe(source_df)
        except ImportError as exc:
            raise ImportError(
                f"SDV not installed: {exc}. Run: poetry install --with synthesizer"
            ) from exc

        wrapper = DPTrainingWrapper(
            max_grad_norm=1.0,
            noise_multiplier=noise_multiplier,
        )
        model = DPCompatibleCTGAN(
            metadata=meta,
            epochs=epochs,
            dp_wrapper=wrapper,
        )
        model.fit(source_df)
        synth_df = model.sample(sample_size)
        actual_epsilon = wrapper.epsilon_spent(delta=_BENCHMARK_DP_DELTA)

    return synth_df, actual_epsilon


# ---------------------------------------------------------------------------
# Grid execution
# ---------------------------------------------------------------------------


def _build_param_combinations(
    grid_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expand a parameter grid config into a list of parameter dicts.

    Args:
        grid_config: Dict mapping parameter names to lists of values.
            Recognised keys: noise_multiplier, epochs, sample_size, seed.
            Extra keys are passed through as-is.

    Returns:
        List of dicts, one per grid cell (Cartesian product).
    """
    import itertools

    noise_multipliers: list[float] = grid_config.get("noise_multiplier", [1.0])
    epochs_list: list[int] = grid_config.get("epochs", [10])
    sample_sizes: list[int] = grid_config.get("sample_size", [500])
    seeds: list[int] = grid_config.get("seed", [42])

    combinations: list[dict[str, Any]] = []
    for nm, ep, ss, seed in itertools.product(noise_multipliers, epochs_list, sample_sizes, seeds):
        combinations.append(
            {
                "noise_multiplier": nm,
                "epochs": ep,
                "sample_size": ss,
                "seed": seed,
            }
        )
    return combinations


def _load_completed_keys(output_dir: str) -> set[tuple[float, int, int, int]]:
    """Load the set of already-completed parameter keys from a prior run.

    Reads ``results.csv`` from output_dir (if present) and returns a set of
    (noise_multiplier, epochs, sample_size, seed) tuples that completed with
    status != FAILED and status != TIMEOUT.

    Args:
        output_dir: Directory where results.csv may exist.

    Returns:
        Set of (noise_multiplier, epochs, sample_size, seed) tuples.
    """
    csv_path = Path(output_dir) / "results.csv"
    if not csv_path.exists():
        return set()

    df = pd.read_csv(csv_path)
    completed: set[tuple[float, int, int, int]] = set()
    for _, row in df.iterrows():
        if row.get("status") not in {"FAILED", "TIMEOUT"}:
            completed.add(
                (
                    float(row.get("noise_multiplier", 0.0)),
                    int(row.get("epochs", 0)),
                    int(row.get("sample_size", 0)),
                    int(row.get("seed", 42)),
                )
            )
    return completed


def run_grid(
    connection_string: str | None,
    table_name: str | None,
    grid_config: dict[str, Any],
    output_dir: str,
    source_df: pd.DataFrame | None = None,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Execute the full parameter grid and collect result rows.

    Accepts a pre-loaded ``source_df`` (for testing/offline use) or loads
    from ``connection_string``/``table_name`` (for production runs).

    Args:
        connection_string: PostgreSQL DSN, or None when source_df is provided.
        table_name: Table name to load, or None when source_df is provided.
        grid_config: Parameter grid (from load_grid_config or inline dict).
        output_dir: Directory to write partial results.
        source_df: Pre-loaded DataFrame.  If None, loads from DB.
        timeout_seconds: Per-run timeout in seconds.

    Returns:
        List of result row dicts, one per grid cell.

    Raises:
        ValueError: If neither source_df nor connection_string/table_name provided.
    """
    if source_df is None:
        if connection_string is None or table_name is None:
            raise ValueError(
                "Either source_df or both connection_string and table_name must be provided."
            )
        source_df = _load_from_db(connection_string, table_name)

    hardware_meta = _collect_hardware_metadata()
    combinations = _build_param_combinations(grid_config)
    completed_keys = _load_completed_keys(output_dir)

    rows: list[dict[str, Any]] = []

    for params in combinations:
        key = (
            float(params["noise_multiplier"]),
            int(params["epochs"]),
            int(params["sample_size"]),
            int(params["seed"]),
        )

        if key in completed_keys:
            continue

        _set_seeds(params["seed"])

        row: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "run_timestamp": datetime.now(UTC).isoformat(),
            "noise_multiplier": params["noise_multiplier"],
            "epochs": params["epochs"],
            "sample_size": params["sample_size"],
            "seed": params["seed"],
            "status": "PENDING",
            "actual_epsilon": None,
            "wall_time_seconds": None,
            "column_metrics": None,
            "correlation_matrix_delta": None,
            "fk_orphan_rate": None,
            "hardware": hardware_meta,
            "error_type": None,
            "error_message": None,
        }

        # Per-run timeout via SIGALRM (Unix only; skipped on Windows)
        timed_out = False
        if hasattr(signal, "SIGALRM"):

            def _timeout_handler(signum: int, frame: object) -> None:
                raise TimeoutError("Benchmark run timed out")

            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(timeout_seconds)

        t0 = time.monotonic()
        try:
            synth_df, actual_epsilon = _train_and_sample(
                source_df=source_df,
                noise_multiplier=params["noise_multiplier"],
                epochs=params["epochs"],
                sample_size=params["sample_size"],
            )

            elapsed = time.monotonic() - t0

            column_metrics = _compute_column_metrics(source_df, synth_df)
            corr_delta = _compute_correlation_delta(source_df, synth_df)
            orphan_rate = _compute_fk_orphan_rate(synth_df)

            row.update(
                {
                    "status": "COMPLETED",
                    "actual_epsilon": actual_epsilon,
                    "wall_time_seconds": round(elapsed, 3),
                    "column_metrics": column_metrics,
                    "correlation_matrix_delta": corr_delta,
                    "fk_orphan_rate": orphan_rate,
                }
            )

        except TimeoutError:
            elapsed = time.monotonic() - t0
            timed_out = True
            row.update(
                {
                    "status": "TIMEOUT",
                    "wall_time_seconds": round(elapsed, 3),
                    "error_type": "TimeoutError",
                    "error_message": (
                        f"Run timed out after {timeout_seconds}s "
                        f"(params: nm={params['noise_multiplier']}, "
                        f"epochs={params['epochs']}, "
                        f"sample_size={params['sample_size']})"
                    ),
                }
            )

        except Exception as exc:
            elapsed = time.monotonic() - t0
            row.update(
                {
                    "status": "FAILED",
                    "wall_time_seconds": round(elapsed, 3),
                    "error_type": type(exc).__name__,
                    "error_message": _sanitize_error_message(str(exc)),
                }
            )

        finally:
            if hasattr(signal, "SIGALRM") and not timed_out:
                signal.alarm(0)

        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------


def write_results(
    rows: list[dict[str, Any]],
    output_dir: str,
    grid_config: dict[str, Any],
) -> str:
    """Write result rows to JSON and CSV artifacts in output_dir.

    The JSON artifact is the primary structured output.  The CSV is a
    flattened convenience format for quick inspection.

    The grid_config is saved alongside results as ``grid_config.json``.

    Args:
        rows: List of result row dicts from run_grid().
        output_dir: Target directory (created if absent).
        grid_config: The parameter grid config used for this run.

    Returns:
        Absolute path to the written JSON artifact.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Sanitize run timestamp for filename (from config, not data)
    run_ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    safe_ts = _sanitize_filename(run_ts)

    json_path = out_path / f"results_{safe_ts}.json"
    csv_path = out_path / "results.csv"
    grid_path = out_path / "grid_config.json"

    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "run_count": len(rows),
        "rows": rows,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, default=str)

    # Save grid config as a companion artifact
    with open(grid_path, "w", encoding="utf-8") as f:
        json.dump(grid_config, f, indent=2)

    # Flatten rows to CSV (append to existing for resume support)
    flat_rows: list[dict[str, Any]] = []
    for row in rows:
        flat = {k: v for k, v in row.items() if k not in {"column_metrics", "hardware"}}
        hw = row.get("hardware") or {}
        flat["hardware_cpu"] = hw.get("cpu_model")
        flat["hardware_os"] = hw.get("os")
        flat_rows.append(flat)

    new_df = pd.DataFrame(flat_rows)
    if csv_path.exists():
        existing = pd.read_csv(csv_path)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined.to_csv(csv_path, index=False)

    return str(json_path)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments and run the benchmark harness.

    Supports both database-backed and CSV-backed (offline) modes.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Benchmark DP-CTGAN epsilon/quality trade-off curves (T52.1)."
    )
    parser.add_argument(
        "--conn",
        metavar="DSN",
        help="PostgreSQL connection string.",  # pragma: allowlist secret
    )
    parser.add_argument(
        "--table",
        metavar="TABLE",
        help="Table name to load from PostgreSQL.",
    )
    parser.add_argument(
        "--source-csv",
        metavar="PATH",
        help="Load source data from a CSV file instead of PostgreSQL.",
    )
    parser.add_argument(
        "--grid-config",
        metavar="PATH",
        required=True,
        help="Path to JSON or YAML parameter grid config file.",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        required=True,
        help="Directory to write result artifacts (JSON, CSV, grid config).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-run timeout in seconds (default: {_DEFAULT_TIMEOUT_SECONDS}).",
    )

    args = parser.parse_args()

    # Load grid config
    grid_config = load_grid_config(args.grid_config)

    # Load source data
    source_df: pd.DataFrame | None = None
    if args.source_csv:
        source_df = pd.read_csv(args.source_csv)
        print(f"Loaded {len(source_df)} rows from {args.source_csv}")
    elif args.conn and args.table:
        print(f"Loading data from {args.table}...")
        source_df = _load_from_db(args.conn, args.table)
        print(f"Loaded {len(source_df)} rows from {args.table}")
    else:
        parser.error("Provide either --source-csv or both --conn and --table.")

    # Execute grid
    print(f"Running benchmark grid ({len(_build_param_combinations(grid_config))} cells)...")
    rows = run_grid(
        connection_string=args.conn,
        table_name=args.table,
        grid_config=grid_config,
        output_dir=args.output_dir,
        source_df=source_df,
        timeout_seconds=args.timeout,
    )

    # Write results
    json_path = write_results(rows=rows, output_dir=args.output_dir, grid_config=grid_config)

    completed = sum(1 for r in rows if r.get("status") == "COMPLETED")
    failed = sum(1 for r in rows if r.get("status") == "FAILED")
    timed_out = sum(1 for r in rows if r.get("status") == "TIMEOUT")

    print(f"\nBenchmark complete: {completed} completed, {failed} failed, {timed_out} timed out")
    print(f"Results written to: {json_path}")


if __name__ == "__main__":
    main()
