"""Epsilon Curve Figure Generation Script (T52.3).

Reads committed benchmark results from demos/results/ and generates
pre-rendered SVG figures into demos/figures/ (or a custom output directory).

This script is the single source of truth for figure generation.
It must be runnable standalone::

    poetry run python demos/generate_figures.py
    poetry run python demos/generate_figures.py --output-dir /tmp/figs

No live training is performed -- all data comes from committed JSON artifacts.

Security requirements:
  - Reads only from demos/results/ (path-traversal guard applied)
  - Writes only to the designated output directory
  - No external network calls
  - No PII: sample_data/ fixtures use fictional data; results contain
    only numeric metrics and hardware metadata

Task: P52-T52.3 -- Epsilon Curve Notebook
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup -- resolve repo root relative to this file so the script works
# regardless of the working directory it is launched from.
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).parent.resolve()
_REPO_ROOT = _SCRIPT_DIR.parent
_RESULTS_DIR = _SCRIPT_DIR / "results"
_DEFAULT_OUTPUT_DIR = _SCRIPT_DIR / "figures"

_CUSTOMERS_FILE = _RESULTS_DIR / "benchmark_customers_v1.json"
_ORDERS_FILE = _RESULTS_DIR / "benchmark_orders_v1.json"


# ---------------------------------------------------------------------------
# Result loading
# ---------------------------------------------------------------------------


def _load_results(artifact_path: Path) -> list[dict[str, Any]]:
    """Load benchmark result rows from a committed JSON artifact.

    Args:
        artifact_path: Absolute path to a benchmark JSON artifact.  Must be
            under ``_RESULTS_DIR`` to prevent path-traversal reads.

    Returns:
        List of result row dicts.  Failed rows (status != 'COMPLETED') are
        included in the raw list; callers filter as needed.

    Raises:
        ValueError: If the resolved path is outside ``_RESULTS_DIR``.
        FileNotFoundError: If the artifact does not exist.
        json.JSONDecodeError: If the artifact contains invalid JSON.
        KeyError: If the JSON object does not contain a "rows" key.
    """
    resolved = artifact_path.resolve()
    results_resolved = _RESULTS_DIR.resolve()
    if not resolved.is_relative_to(results_resolved):
        raise ValueError(
            f"Path traversal blocked: {artifact_path!r} resolves outside "
            f"results directory {results_resolved!r}"
        )
    raw = artifact_path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(raw)
    rows: list[dict[str, Any]] = data["rows"]
    return rows


def _completed_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to rows with status == 'COMPLETED'.

    Args:
        rows: All result rows from an artifact.

    Returns:
        Only the rows whose ``status`` field equals ``'COMPLETED'``.
    """
    return [r for r in rows if r.get("status") == "COMPLETED"]


# ---------------------------------------------------------------------------
# Figure 1: Epsilon vs Noise Multiplier
# ---------------------------------------------------------------------------


def plot_epsilon_vs_noise_multiplier(
    customers_rows: list[dict[str, Any]],
    orders_rows: list[dict[str, Any]],
    output_dir: Path,
) -> Path:
    """Generate epsilon vs. noise multiplier scatter plot.

    Shows the inverse relationship between sigma (noise_multiplier) and
    measured epsilon across both the customers and orders schemas.  Each
    point is annotated with the epoch count.  The failed run
    (customers, nm=1.0, epochs=100) is shown as an 'x' marker with its
    error type, demonstrating real budget-exhaustion behaviour.

    Args:
        customers_rows: All result rows from benchmark_customers_v1.json.
        orders_rows: All result rows from benchmark_orders_v1.json.
        output_dir: Directory to write the SVG file into.

    Returns:
        Path to the written SVG file.
    """
    import matplotlib

    matplotlib.use("Agg")  # non-interactive backend -- safe for headless/CI
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 5))

    # Plot completed runs
    for rows, label, color, marker in [
        (customers_rows, "Customers (8 cols)", "#2563eb", "o"),
        (orders_rows, "Orders (5 cols)", "#16a34a", "s"),
    ]:
        completed = _completed_rows(rows)
        xs = [float(r["noise_multiplier"]) for r in completed]
        ys = [float(r["actual_epsilon"]) for r in completed]
        epochs_labels = [int(r["epochs"]) for r in completed]
        ax.scatter(xs, ys, label=label, color=color, marker=marker, s=80, zorder=3)
        for x, y, ep in zip(xs, ys, epochs_labels, strict=True):
            ax.annotate(
                f"ep={ep}",
                (x, y),
                textcoords="offset points",
                xytext=(6, 2),
                fontsize=7,
                color=color,
            )

    # Plot failed runs as X markers
    failed = [r for r in customers_rows if r.get("status") == "FAILED"]
    for r in failed:
        ax.scatter(
            float(r["noise_multiplier"]),
            0,
            marker="x",
            color="#dc2626",
            s=120,
            linewidths=2,
            zorder=4,
            label=f"FAILED: {r.get('error_type', 'unknown')} (ep={r['epochs']})",
        )

    ax.set_xlabel("Noise Multiplier (sigma)")
    ax.set_ylabel("Measured epsilon")
    ax.set_title(
        "Epsilon vs. Noise Multiplier\n(lower sigma -> higher epsilon; FAILED = budget exhausted)"
    )
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(visible=True, alpha=0.3)
    ax.set_xscale("log")
    ax.set_yscale("log")

    out_path = output_dir / "epsilon_vs_noise_multiplier.svg"
    fig.savefig(out_path, format="svg", bbox_inches="tight")
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Figure 2: Epsilon vs Statistical Fidelity
# ---------------------------------------------------------------------------


def plot_epsilon_vs_statistical_fidelity(
    customers_rows: list[dict[str, Any]],
    orders_rows: list[dict[str, Any]],
    output_dir: Path,
) -> Path:
    """Generate epsilon vs. statistical fidelity scatter plot.

    For categorical columns: uses chi2_pvalue as fidelity proxy
    (higher p-value = synthesised distribution matches real distribution).
    For numeric columns: uses (1 - ks_statistic) where available
    (higher = more similar cumulative distributions).

    Args:
        customers_rows: All result rows from benchmark_customers_v1.json.
        orders_rows: All result rows from benchmark_orders_v1.json.
        output_dir: Directory to write the SVG file into.

    Returns:
        Path to the written SVG file.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def _mean_chi2_pvalue(row: dict[str, Any]) -> float | None:
        """Extract mean chi2 p-value across categorical columns."""
        col_metrics = row.get("column_metrics")
        if not col_metrics:
            return None
        pvalues = [
            float(v["chi2_pvalue"])
            for v in col_metrics.values()
            if isinstance(v, dict) and "chi2_pvalue" in v
        ]
        return sum(pvalues) / len(pvalues) if pvalues else None

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    for ax, rows, title, color in [
        (axes[0], customers_rows, "Customers (8 cols)", "#2563eb"),
        (axes[1], orders_rows, "Orders (5 cols)", "#16a34a"),
    ]:
        completed = _completed_rows(rows)
        xs: list[float] = []
        ys: list[float] = []
        for r in completed:
            epsilon = r.get("actual_epsilon")
            mean_p = _mean_chi2_pvalue(r)
            if epsilon is not None and mean_p is not None:
                xs.append(float(epsilon))
                ys.append(mean_p)

        ax.scatter(xs, ys, color=color, s=80, zorder=3)
        ax.set_xlabel("Measured epsilon")
        ax.set_ylabel("Mean chi2 p-value (categorical columns)")
        ax.set_title(title)
        ax.set_ylim(0, 1.05)
        ax.axhline(0.05, linestyle="--", color="gray", alpha=0.5, label="p=0.05 threshold")
        ax.legend(fontsize=8)
        ax.grid(visible=True, alpha=0.3)

    fig.suptitle(
        "Epsilon vs. Statistical Fidelity\n"
        "(higher chi2 p-value = synthesised dist. matches real dist.)",
        fontsize=11,
    )
    fig.tight_layout()

    out_path = output_dir / "epsilon_vs_statistical_fidelity.svg"
    fig.savefig(out_path, format="svg", bbox_inches="tight")
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Figure 3: Epsilon vs Schema Complexity (proxy for "vs dataset size")
# ---------------------------------------------------------------------------


def plot_epsilon_vs_schema_complexity(
    customers_rows: list[dict[str, Any]],
    orders_rows: list[dict[str, Any]],
    output_dir: Path,
) -> Path:
    """Generate epsilon vs. schema complexity proxy chart.

    NOTE: Our grid has only sample_size=1000.  We cannot plot epsilon vs.
    dataset size because there is no size variation.  Instead we use schema
    complexity (number of columns: customers=8, orders=5) as a proxy, with
    an honest limitation note embedded in the figure title.

    Args:
        customers_rows: All result rows from benchmark_customers_v1.json.
        orders_rows: All result rows from benchmark_orders_v1.json.
        output_dir: Directory to write the SVG file into.

    Returns:
        Path to the written SVG file.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(figsize=(8, 5))

    schema_data: dict[str, dict[str, Any]] = {
        "Customers (8 cols, n=1000)": {
            "cols": 8,
            "rows": customers_rows,
            "color": "#2563eb",
        },
        "Orders (5 cols, n=1000)": {
            "cols": 5,
            "rows": orders_rows,
            "color": "#16a34a",
        },
    }

    width = 0.35
    x_positions = np.arange(len(schema_data))

    for i, (label, info) in enumerate(schema_data.items()):
        completed = _completed_rows(info["rows"])
        epsilons = [float(r["actual_epsilon"]) for r in completed]
        if epsilons:
            mean_eps = float(np.mean(epsilons))
            std_eps = float(np.std(epsilons))
            ax.bar(
                x_positions[i],
                mean_eps,
                width,
                yerr=std_eps,
                label=label,
                color=info["color"],
                alpha=0.8,
                capsize=6,
                error_kw={"elinewidth": 1.5},
            )

    ax.set_xticks(x_positions)
    ax.set_xticklabels(list(schema_data), fontsize=9)
    ax.set_xlabel("Schema (proxy -- sample_size=1000 for all runs)")
    ax.set_ylabel("Mean Measured epsilon +/- std dev")
    ax.set_title(
        "Epsilon vs. Schema Complexity\n"
        "(LIMITATION: sample_size not varied -- see Limitations section)"
    )
    ax.legend(fontsize=9)
    ax.grid(visible=True, alpha=0.3, axis="y")

    out_path = output_dir / "epsilon_vs_schema_complexity.svg"
    fig.savefig(out_path, format="svg", bbox_inches="tight")
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Figure 4: Correlation Preservation
# ---------------------------------------------------------------------------


def plot_correlation_preservation(
    customers_rows: list[dict[str, Any]],
    orders_rows: list[dict[str, Any]],
    output_dir: Path,
) -> Path:
    """Generate correlation matrix delta vs. epsilon scatter plot.

    correlation_matrix_delta measures the Frobenius-norm difference between
    the real and synthetic correlation matrices.  Lower delta = better
    preservation of inter-column relationships.

    Customers: all deltas are 0.0 (no numeric correlations to preserve).
    Orders: deltas are ~1.25-1.39 due to numeric columns.

    Args:
        customers_rows: All result rows from benchmark_customers_v1.json.
        orders_rows: All result rows from benchmark_orders_v1.json.
        output_dir: Directory to write the SVG file into.

    Returns:
        Path to the written SVG file.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 5))

    for rows, label, color, marker in [
        (customers_rows, "Customers (8 cols)", "#2563eb", "o"),
        (orders_rows, "Orders (5 cols)", "#16a34a", "s"),
    ]:
        completed = _completed_rows(rows)
        xs = []
        ys = []
        for r in completed:
            eps = r.get("actual_epsilon")
            delta = r.get("correlation_matrix_delta")
            if eps is not None and delta is not None:
                xs.append(float(eps))
                ys.append(float(delta))

        if xs:
            ax.scatter(xs, ys, label=label, color=color, marker=marker, s=80, zorder=3)

    ax.set_xlabel("Measured epsilon")
    ax.set_ylabel("Correlation Matrix Delta (Frobenius norm)")
    ax.set_title(
        "Correlation Preservation vs. Epsilon\n"
        "(customers delta=0 -- no numeric correlations; orders delta>0)"
    )
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(visible=True, alpha=0.3)

    out_path = output_dir / "correlation_preservation.svg"
    fig.savefig(out_path, format="svg", bbox_inches="tight")
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Figure 5: FK Integrity
# ---------------------------------------------------------------------------


def plot_fk_integrity(
    customers_rows: list[dict[str, Any]],
    orders_rows: list[dict[str, Any]],
    output_dir: Path,
) -> Path:
    """Generate FK orphan rate table as a matplotlib figure.

    For CSV-source runs, fk_orphan_rate is null (no FK enforcement in the
    synthesis pipeline at this stage).  The table shows all completed runs
    with their fk_orphan_rate values -- no results are filtered or hidden.

    Args:
        customers_rows: All result rows from benchmark_customers_v1.json.
        orders_rows: All result rows from benchmark_orders_v1.json.
        output_dir: Directory to write the SVG file into.

    Returns:
        Path to the written SVG file.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    all_rows: list[dict[str, Any]] = []
    for rows, schema_label in [
        (customers_rows, "customers"),
        (orders_rows, "orders"),
    ]:
        for r in rows:
            all_rows.append(
                {
                    "schema": schema_label,
                    "nm": r["noise_multiplier"],
                    "epochs": r["epochs"],
                    "status": r.get("status", "UNKNOWN"),
                    "fk_orphan_rate": r.get("fk_orphan_rate"),
                }
            )

    table_data = [
        [
            row["schema"],
            f"sigma={row['nm']:.1f}",
            str(row["epochs"]),
            row["status"],
            str(row["fk_orphan_rate"]) if row["fk_orphan_rate"] is not None else "null",
        ]
        for row in all_rows
    ]
    col_labels = ["Schema", "Noise Mult.", "Epochs", "Status", "FK Orphan Rate"]

    fig, ax = plt.subplots(figsize=(9, 4))
    ax.axis("off")
    tbl = ax.table(
        cellText=table_data,
        colLabels=col_labels,
        loc="center",
        cellLoc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    tbl.scale(1.2, 1.4)

    # Colour FAILED rows in light red
    for row_idx, row in enumerate(table_data):
        if row[3] == "FAILED":
            for col_idx in range(len(col_labels)):
                tbl[row_idx + 1, col_idx].set_facecolor("#fecaca")

    ax.set_title(
        "FK Integrity Verification\n"
        "(fk_orphan_rate=null for CSV-source runs -- no FK enforcement at synthesis time)",
        pad=10,
    )

    out_path = output_dir / "fk_integrity.svg"
    fig.savefig(out_path, format="svg", bbox_inches="tight")
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def generate_all_figures(output_dir: Path) -> list[Path]:
    """Generate all epsilon curve SVG figures from committed benchmark results.

    Reads ``benchmark_customers_v1.json`` and ``benchmark_orders_v1.json``
    from ``demos/results/``, then writes five SVG files to ``output_dir``.

    Args:
        output_dir: Directory to write SVG files into.  Created if absent.

    Returns:
        List of paths to the generated SVG files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    customers_rows = _load_results(_CUSTOMERS_FILE)
    orders_rows = _load_results(_ORDERS_FILE)

    generated: list[Path] = []
    generated.append(plot_epsilon_vs_noise_multiplier(customers_rows, orders_rows, output_dir))
    generated.append(plot_epsilon_vs_statistical_fidelity(customers_rows, orders_rows, output_dir))
    generated.append(plot_epsilon_vs_schema_complexity(customers_rows, orders_rows, output_dir))
    generated.append(plot_correlation_preservation(customers_rows, orders_rows, output_dir))
    generated.append(plot_fk_integrity(customers_rows, orders_rows, output_dir))
    return generated


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse args, generate figures, report results.

    Args:
        argv: Optional argument list.  Uses sys.argv if None.

    Returns:
        Exit code: 0 on success, 1 on failure.
    """
    parser = argparse.ArgumentParser(
        description="Generate epsilon curve SVG figures from committed benchmark results."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="Directory to write SVG files into (default: demos/figures/)",
    )
    args = parser.parse_args(argv)
    output_dir: Path = args.output_dir

    try:
        generated = generate_all_figures(output_dir)
    except Exception as exc:
        print(f"ERROR: Figure generation failed: {exc}", file=sys.stderr)
        return 1

    for path in generated:
        print(f"  Generated: {path}")
    print(f"\n{len(generated)} SVG figure(s) written to {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
