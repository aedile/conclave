"""DP Quality Benchmark — P7-T7.4 / T30.4.

Trains vanilla CTGAN, proxy-model DP-CTGAN, and discriminator-level DP-CTGAN
at five target epsilon levels on a Faker-generated persons table, then runs
ProfileDelta.compare() between the source data and each synthetic output.

The three configurations tested are:

1. **Vanilla CTGAN** — no DP; baseline for quality comparison.
2. **Proxy-model DP** — Opacus DP-SGD applied to a proxy linear model (T7.3
   fallback approach, renamed ``_activate_opacus_proxy`` in T30.3). Epsilon
   accounting reflects gradient steps on the proxy model, not the CTGAN
   Discriminator. Included for historical comparison with pre-Phase-30 results.
3. **Discriminator-level DP** — Opacus DP-SGD applied directly to the
   ``OpacusCompatibleDiscriminator`` (T30.3 primary path, ADR-0036). Epsilon
   accounting reflects real Discriminator gradient steps, making it the only
   configuration with a mathematically rigorous end-to-end DP guarantee.

Results are printed to stdout as a summary table and written to
``docs/DP_QUALITY_REPORT.md``.

Usage::

    poetry run python3 scripts/benchmark_dp_quality.py

Note:
    Epochs are intentionally kept LOW (10) to allow the benchmark to complete
    quickly.  Production synthesis uses 300+ epochs for higher fidelity.
    The quality metrics reported here should therefore be interpreted as
    lower-bound fidelity estimates under fast-training conditions.
"""

from __future__ import annotations

import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Adjust sys.path so the script can import from src/ when run from the repo
# root via: poetry run python3 scripts/benchmark_dp_quality.py
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# ---------------------------------------------------------------------------
# Graceful dependency check — exit with a clear message if core deps are missing
# ---------------------------------------------------------------------------
try:
    import pandas as pd
    from faker import Faker
except ImportError as _missing:
    print(
        f"ERROR: Required dependency missing: {_missing}.\nRun: poetry install --with synthesizer",
        file=sys.stderr,
    )
    sys.exit(1)

# Suppress SDV/ctgan/Opacus warnings so benchmark output is readable.
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

try:
    from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
    from synth_engine.modules.profiler.models import ProfileDelta, TableProfile
    from synth_engine.modules.profiler.profiler import StatisticalProfiler
    from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN
except ImportError as _missing_engine:
    print(
        f"ERROR: synth_engine import failed: {_missing_engine}.\n"
        "Ensure you are running from the repository root via: "
        "poetry run python3 scripts/benchmark_dp_quality.py",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Number of training rows for the source dataset.
_N_SOURCE_ROWS: int = 500

#: Number of synthetic rows to sample per model.
_N_SYNTHETIC_ROWS: int = 500

#: Number of GAN epochs.  LOW intentionally — benchmark speed over fidelity.
_BENCHMARK_EPOCHS: int = 10

#: Faker random seed for reproducibility.
_FAKER_SEED: int = 42

#: Opacus delta for epsilon accounting.
_DP_DELTA: float = 1e-5

#: Department categories for the source dataset.
_DEPARTMENTS: list[str] = ["Engineering", "Sales", "HR", "Finance", "Marketing"]

#: Columns that carry meaningful distributional signal.
#: ``id`` is a sequential index — excluded from acceptance checks.
_SIGNAL_COLUMNS: list[str] = ["age", "salary"]

#: Categorical columns — checked via cardinality_drift.
_CATEGORICAL_COLUMNS: list[str] = ["department"]

#: Epsilon levels to test for DP configurations.
#: (noise_multiplier, target_epsilon_label) pairs.
#: noise_multiplier values are calibrated against a 500-row dataset with
#: batch_size=500, 10 epochs (1 Opacus step per epoch at full-batch level).
#: These values are SPECIFIC to this benchmark configuration:
#:   noise_multiplier=4.00  -> epsilon ~0.1  (very strong — low utility expected)
#:   noise_multiplier=2.50  -> epsilon ~0.5  (strong)
#:   noise_multiplier=2.00  -> epsilon ~1.0
#:   noise_multiplier=0.75  -> epsilon ~5.9
#:   noise_multiplier=0.55  -> epsilon ~10.9
_EPSILON_CONFIGS: list[tuple[float, str]] = [
    (4.00, "~0.1"),
    (2.50, "~0.5"),
    (2.00, "~1"),
    (0.75, "~5"),
    (0.55, "~10"),
]

#: Acceptance threshold: mean drift must be within 2 standard deviations of
#: the source column's standard deviation to pass.
_MEAN_DRIFT_STDDEV_MULTIPLIER: float = 2.0

#: Acceptance threshold: categorical KL divergence (approximated as
#: cardinality_drift / source_cardinality) must be <= 0.10 (10%).
_KL_DIVERGENCE_THRESHOLD: float = 0.10

#: Absolute path to docs/DP_QUALITY_REPORT.md.
_REPORT_PATH: Path = _REPO_ROOT / "docs" / "DP_QUALITY_REPORT.md"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkRow:
    """One row in the summary table for a single training mode.

    Attributes:
        mode: Human-readable label (e.g. "Vanilla", "Disc-DP (eps~1)").
        approach: One of "vanilla", "proxy", "discriminator".
        noise_multiplier: Opacus noise_multiplier used (0.0 for vanilla).
        actual_epsilon: Epsilon reported by Opacus after training (0.0 for
            vanilla, which provides no DP guarantee).
        column_mean_drifts: Mapping of column -> mean_drift value.
        column_stddev_drifts: Mapping of column -> stddev_drift value.
        cardinality_drifts: Mapping of column -> cardinality_drift value.
        passes_acceptance: True when all signal columns meet the 2-stddev
            mean drift threshold and all categorical columns meet the 10%
            KL threshold.
    """

    mode: str
    approach: str
    noise_multiplier: float
    actual_epsilon: float
    column_mean_drifts: dict[str, float | None]
    column_stddev_drifts: dict[str, float | None]
    cardinality_drifts: dict[str, int | None]
    passes_acceptance: bool


# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------


def _generate_source_data() -> pd.DataFrame:
    """Generate a reproducible fictional persons table using Faker.

    Returns:
        DataFrame with columns: id (int), age (int), salary (int),
        department (str).  500 rows, seeded for reproducibility.
    """
    fake = Faker()
    fake.seed_instance(_FAKER_SEED)

    rows = [
        {
            "id": i,
            "age": fake.random_int(min=18, max=70),
            "salary": fake.random_int(min=30_000, max=150_000),
            "department": fake.random_element(elements=_DEPARTMENTS),
        }
        for i in range(_N_SOURCE_ROWS)
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Model training helpers
# ---------------------------------------------------------------------------


def _build_metadata(df: pd.DataFrame) -> Any:  # -> Any: sdv.metadata has no public type stubs
    """Detect SDV SingleTableMetadata from a DataFrame.

    Args:
        df: The training DataFrame.

    Returns:
        A ``sdv.metadata.SingleTableMetadata`` instance with detected sdtypes.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from sdv.metadata import SingleTableMetadata

        meta = SingleTableMetadata()
        meta.detect_from_dataframe(df)
    return meta


def _train_vanilla(df: pd.DataFrame, metadata: Any) -> pd.DataFrame:
    """Train a vanilla (non-DP) CTGAN and return a synthetic sample.

    Args:
        df: Source training DataFrame.
        metadata: SDV SingleTableMetadata.

    Returns:
        Synthetic DataFrame with ``_N_SYNTHETIC_ROWS`` rows.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = DPCompatibleCTGAN(metadata=metadata, epochs=_BENCHMARK_EPOCHS, dp_wrapper=None)
        model.fit(df)
        return model.sample(_N_SYNTHETIC_ROWS)


def _train_discriminator_dp(
    df: pd.DataFrame,
    metadata: Any,
    noise_multiplier: float,
) -> tuple[pd.DataFrame, float]:
    """Train discriminator-level DP-CTGAN and return a synthetic sample plus actual epsilon.

    Uses the T30.3 primary path: Opacus DP-SGD is applied directly to the
    ``OpacusCompatibleDiscriminator``, so epsilon accounting reflects real
    Discriminator gradient steps (ADR-0036).

    Args:
        df: Source training DataFrame.
        metadata: SDV SingleTableMetadata.
        noise_multiplier: Opacus noise_multiplier (higher = more noise = lower epsilon).

    Returns:
        Tuple of (synthetic_df, actual_epsilon).
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wrapper = DPTrainingWrapper(max_grad_norm=1.0, noise_multiplier=noise_multiplier)
        model = DPCompatibleCTGAN(
            metadata=metadata,
            epochs=_BENCHMARK_EPOCHS,
            dp_wrapper=wrapper,
        )
        model.fit(df)
        synthetic_df = model.sample(_N_SYNTHETIC_ROWS)
        actual_epsilon = wrapper.epsilon_spent(delta=_DP_DELTA)
    return synthetic_df, actual_epsilon


def _train_proxy_dp(
    df: pd.DataFrame,
    metadata: Any,
    noise_multiplier: float,
) -> tuple[pd.DataFrame, float]:
    """Train proxy-model DP-CTGAN and return a synthetic sample plus actual epsilon.

    Uses the T7.3 proxy fallback approach: forces the discriminator training to
    fail so the fallback path (_activate_opacus_proxy) is exercised.  The proxy
    model's epsilon accounting reflects gradient steps on a linear proxy model,
    NOT the CTGAN Discriminator — this is the pre-Phase-30 behavior included
    here for historical comparison only.

    Implementation note: We trigger the proxy fallback by passing a wrapper
    whose ``wrap()`` call raises a RuntimeError on the first call (for the
    discriminator), so ``fit()`` falls back to ``_activate_opacus_proxy()``.
    The second call succeeds (for the proxy model). The actual_epsilon returned
    is the proxy-model epsilon.

    Args:
        df: Source training DataFrame.
        metadata: SDV SingleTableMetadata.
        noise_multiplier: Opacus noise_multiplier.

    Returns:
        Tuple of (synthetic_df, actual_epsilon).
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        class _ProxyForcingWrapper:
            """Wrapper that fails on the first wrap() call to force proxy fallback.

            Attributes:
                max_grad_norm: Maximum gradient norm for DP clipping.
                noise_multiplier: Noise scale for Gaussian noise injection.
            """

            def __init__(self, max_grad_norm: float, noise_multiplier: float) -> None:
                """Initialise the proxy-forcing wrapper.

                Args:
                    max_grad_norm: Maximum gradient norm.
                    noise_multiplier: Noise multiplier for DP-SGD.
                """
                self.max_grad_norm = max_grad_norm
                self.noise_multiplier = noise_multiplier
                self._call_count = 0
                self._real_wrapper = DPTrainingWrapper(
                    max_grad_norm=max_grad_norm,
                    noise_multiplier=noise_multiplier,
                )

            def wrap(
                self,
                optimizer: Any,
                model: Any,
                dataloader: Any,
                *,
                max_grad_norm: float,
                noise_multiplier: float,
            ) -> Any:
                """Fail on first call (discriminator), succeed on second (proxy).

                Args:
                    optimizer: PyTorch optimizer to wrap.
                    model: PyTorch model to wrap.
                    dataloader: DataLoader for DP accounting.
                    max_grad_norm: Maximum gradient norm.
                    noise_multiplier: Noise multiplier.

                Returns:
                    DP-wrapped optimizer on the second call.

                Raises:
                    RuntimeError: On the first call to force proxy fallback.
                """
                self._call_count += 1
                if self._call_count == 1:
                    raise RuntimeError(
                        "Proxy-forcing wrapper: intentional failure on discriminator wrap "
                        "to exercise the _activate_opacus_proxy() fallback path."
                    )
                return self._real_wrapper.wrap(
                    optimizer=optimizer,
                    model=model,
                    dataloader=dataloader,
                    max_grad_norm=max_grad_norm,
                    noise_multiplier=noise_multiplier,
                )

            def epsilon_spent(self, *, delta: float) -> float:
                """Return epsilon spent by the real wrapper.

                Args:
                    delta: Privacy parameter delta.

                Returns:
                    Epsilon spent so far.
                """
                return self._real_wrapper.epsilon_spent(delta=delta)

            def check_budget(
                self,
                *,
                allocated_epsilon: float,
                delta: float,
            ) -> None:
                """Check budget against the real wrapper.

                Args:
                    allocated_epsilon: Allocated epsilon budget.
                    delta: Privacy parameter delta.
                """
                self._real_wrapper.check_budget(
                    allocated_epsilon=allocated_epsilon,
                    delta=delta,
                )

        proxy_wrapper = _ProxyForcingWrapper(max_grad_norm=1.0, noise_multiplier=noise_multiplier)
        # Use a high allocated_epsilon to avoid BudgetExhaustionError during benchmark
        model = DPCompatibleCTGAN(
            metadata=metadata,
            epochs=_BENCHMARK_EPOCHS,
            dp_wrapper=proxy_wrapper,
            allocated_epsilon=100.0,
        )
        model.fit(df)
        synthetic_df = model.sample(_N_SYNTHETIC_ROWS)
        actual_epsilon = proxy_wrapper.epsilon_spent(delta=_DP_DELTA)
    return synthetic_df, actual_epsilon


# ---------------------------------------------------------------------------
# Acceptance check
# ---------------------------------------------------------------------------


def _check_acceptance(
    delta: ProfileDelta,
    source_profile: TableProfile,
) -> bool:
    """Check whether a ProfileDelta passes acceptance criteria.

    Acceptance criteria:
    - All signal columns (age, salary): mean_drift within 2 standard deviations
      of the source column's stddev.
    - All categorical columns (department): |cardinality_drift| / source_cardinality
      <= 0.10 (proxy for 10% KL divergence threshold).

    A missing column entry in the ProfileDelta or source profile for either
    numeric or categorical columns causes this function to return False — the
    absence of data is treated as a failure, not a pass.

    Args:
        delta: ProfileDelta between source and synthetic profiles.
        source_profile: The source TableProfile (used to retrieve source stddev
            and cardinality for the threshold calculation).

    Returns:
        True if all acceptance criteria pass; False otherwise.
    """
    # Check numeric signal columns: mean drift <= 2 * source stddev
    for col_name in _SIGNAL_COLUMNS:
        col_delta = delta.column_deltas.get(col_name)
        if col_delta is None or col_delta.mean_drift is None:
            return False

        source_col = source_profile.columns.get(col_name)
        if source_col is None or source_col.stddev is None:
            return False

        threshold = _MEAN_DRIFT_STDDEV_MULTIPLIER * source_col.stddev
        if abs(col_delta.mean_drift) > threshold:
            return False

    # Check categorical columns: |cardinality_drift| / source_cardinality <= 10%
    for col_name in _CATEGORICAL_COLUMNS:
        col_delta = delta.column_deltas.get(col_name)
        if col_delta is None or col_delta.cardinality_drift is None:
            return False

        source_col = source_profile.columns.get(col_name)
        if source_col is None or not source_col.cardinality:
            return False

        ratio = abs(col_delta.cardinality_drift) / source_col.cardinality
        if ratio > _KL_DIVERGENCE_THRESHOLD:
            return False

    return True


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------


def _build_row(
    mode: str,
    approach: str,
    noise_multiplier: float,
    actual_epsilon: float,
    delta: ProfileDelta,
    source_profile: TableProfile,
) -> BenchmarkRow:
    """Assemble a BenchmarkRow from a ProfileDelta.

    Args:
        mode: Mode label string.
        approach: One of "vanilla", "proxy", "discriminator".
        noise_multiplier: Noise multiplier used (0.0 for vanilla).
        actual_epsilon: Epsilon from Opacus (0.0 for vanilla).
        delta: ProfileDelta from profiler.compare().
        source_profile: Source TableProfile for acceptance check.

    Returns:
        A populated BenchmarkRow.
    """
    mean_drifts: dict[str, float | None] = {}
    stddev_drifts: dict[str, float | None] = {}
    cardinality_drifts: dict[str, int | None] = {}

    for col_name in _SIGNAL_COLUMNS:
        cd = delta.column_deltas.get(col_name)
        mean_drifts[col_name] = cd.mean_drift if cd else None
        stddev_drifts[col_name] = cd.stddev_drift if cd else None

    for col_name in _CATEGORICAL_COLUMNS:
        cd = delta.column_deltas.get(col_name)
        cardinality_drifts[col_name] = cd.cardinality_drift if cd else None

    passes = _check_acceptance(delta, source_profile)

    return BenchmarkRow(
        mode=mode,
        approach=approach,
        noise_multiplier=noise_multiplier,
        actual_epsilon=actual_epsilon,
        column_mean_drifts=mean_drifts,
        column_stddev_drifts=stddev_drifts,
        cardinality_drifts=cardinality_drifts,
        passes_acceptance=passes,
    )


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _format_table(rows: list[BenchmarkRow]) -> str:
    """Format benchmark rows as a plain-text summary table.

    Args:
        rows: List of BenchmarkRow results.

    Returns:
        Multi-line string table for printing to stdout.
    """
    lines: list[str] = []
    lines.append(
        f"{'Mode':<30} {'Approach':<14} {'NoiseMul':>8} {'Epsilon':>9}"
        f"  {'age_mean_drift':>14} {'age_std_drift':>13}"
        f"  {'sal_mean_drift':>14} {'sal_std_drift':>13}"
        f"  {'dept_card_drift':>14}  {'PASS?':>5}"
    )
    lines.append("-" * 140)
    for row in rows:
        age_mean = row.column_mean_drifts.get("age")
        age_std = row.column_stddev_drifts.get("age")
        sal_mean = row.column_mean_drifts.get("salary")
        sal_std = row.column_stddev_drifts.get("salary")
        dept_card = row.cardinality_drifts.get("department")

        def _fmt_f(v: float | None) -> str:
            return f"{v:+.2f}" if v is not None else "N/A"

        def _fmt_i(v: int | None) -> str:
            return f"{v:+d}" if v is not None else "N/A"

        pass_str = "YES" if row.passes_acceptance else "NO"
        lines.append(
            f"{row.mode:<30} {row.approach:<14} {row.noise_multiplier:>8.2f}"
            f" {row.actual_epsilon:>9.4f}"
            f"  {_fmt_f(age_mean):>14} {_fmt_f(age_std):>13}"
            f"  {_fmt_f(sal_mean):>14} {_fmt_f(sal_std):>13}"
            f"  {_fmt_i(dept_card):>14}  {pass_str:>5}"
        )
    return "\n".join(lines)


def _format_markdown_report(
    vanilla_row: BenchmarkRow,
    proxy_rows: list[BenchmarkRow],
    discriminator_rows: list[BenchmarkRow],
    source_profile: TableProfile,
) -> str:
    """Format benchmark results as a Markdown report for docs/.

    Args:
        vanilla_row: BenchmarkRow for the vanilla baseline.
        proxy_rows: BenchmarkRow list for proxy-model DP configurations.
        discriminator_rows: BenchmarkRow list for discriminator-level DP configurations.
        source_profile: Source TableProfile (for metadata section).

    Returns:
        Full Markdown string for writing to docs/DP_QUALITY_REPORT.md.
    """
    source_age = source_profile.columns["age"]
    source_salary = source_profile.columns["salary"]
    source_dept = source_profile.columns["department"]

    lines: list[str] = []
    lines.append("# DP Quality Benchmark Report")
    lines.append("")
    lines.append("> Auto-generated by `scripts/benchmark_dp_quality.py` (P7-T7.4 / T30.4).")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(
        "This report documents the quality degradation curves for the Air-Gapped Synthetic "
        "Data Engine's Differential Privacy CTGAN implementation at varying epsilon levels. "
        "Vanilla (non-DP) CTGAN is used as the baseline. Phase 30 replaced the proxy-model "
        "approach with discriminator-level DP-SGD (ADR-0036). Both approaches are benchmarked "
        "here for comparison."
    )
    lines.append("")
    lines.append(
        "> **Phase 30 implementation note**: All `actual_epsilon` values in the "
        "**Discriminator-level DP** section reflect real Opacus gradient-step accounting on "
        "the `OpacusCompatibleDiscriminator` — not on a proxy model. This makes the "
        "discriminator-level epsilon values the authoritative DP measurement for Phase 30+ "
        "deployments. The proxy-model section is retained for historical comparison only. "
        "See ADR-0036 for the full rationale."
    )
    lines.append("")
    lines.append(
        "> **Proxy-model historical note**: The proxy-model measurements use "
        "``_activate_opacus_proxy()`` (the renamed T7.3 approach). Epsilon values reflect "
        "gradient steps on a linear proxy model trained on the same preprocessed data — not "
        "on the CTGAN Discriminator. These are **proxy-model measurements** included for "
        "comparison with pre-Phase-30 results. See ADR-0025 for proxy-model methodology."
    )
    lines.append("")
    lines.append("## Benchmark Configuration")
    lines.append("")
    lines.append(f"- **Source rows**: {_N_SOURCE_ROWS}")
    lines.append(f"- **Synthetic rows per model**: {_N_SYNTHETIC_ROWS}")
    lines.append(
        f"- **Training epochs**: {_BENCHMARK_EPOCHS} (intentionally low — production uses 300+)"
    )
    lines.append(f"- **Faker seed**: {_FAKER_SEED}")
    lines.append(f"- **Opacus delta**: {_DP_DELTA:.0e}")
    lines.append("- **max_grad_norm**: 1.0")
    lines.append("")
    lines.append("### Source Dataset Statistics")
    lines.append("")
    lines.append("| Column | Type | Mean | Stddev | Cardinality |")
    lines.append("|--------|------|------|--------|-------------|")
    lines.append(f"| age | numeric | {source_age.mean:.2f} | {source_age.stddev:.2f} | — |")
    lines.append(
        f"| salary | numeric | {source_salary.mean:.2f} | {source_salary.stddev:.2f} | — |"
    )
    lines.append(f"| department | categorical | — | — | {source_dept.cardinality} |")
    lines.append("")

    # --------------------------------------------------------------------------
    # Phase 30: Discriminator-level DP section (primary)
    # --------------------------------------------------------------------------
    lines.append("## Phase 30 — Discriminator-Level DP-SGD Results")
    lines.append("")
    lines.append(
        "These results use the T30.3 primary path: Opacus DP-SGD is applied directly to the "
        "`OpacusCompatibleDiscriminator`. Epsilon accounting reflects actual Discriminator "
        "gradient steps on real training data (ADR-0036)."
    )
    lines.append("")
    lines.append("### Vanilla Baseline")
    lines.append("")
    lines.append(
        "| Mode | noise_multiplier | actual_epsilon | age mean_drift | age stddev_drift "
        "| salary mean_drift | salary stddev_drift | dept cardinality_drift | Passes AC? |"
    )
    lines.append(
        "|------|-----------------|---------------|---------------|---------------"
        "|------------------|-------------------|----------------------|------------|"
    )
    _append_row_to_table(lines, vanilla_row)
    lines.append("")
    lines.append("### Discriminator-Level DP at Five Epsilon Levels")
    lines.append("")
    lines.append(
        "| Mode | noise_multiplier | actual_epsilon | age mean_drift | age stddev_drift "
        "| salary mean_drift | salary stddev_drift | dept cardinality_drift | Passes AC? |"
    )
    lines.append(
        "|------|-----------------|---------------|---------------|---------------"
        "|------------------|-------------------|----------------------|------------|"
    )
    for row in discriminator_rows:
        _append_row_to_table(lines, row)
    lines.append("")

    # --------------------------------------------------------------------------
    # Historical: Proxy-model section
    # --------------------------------------------------------------------------
    lines.append("## Historical — Proxy-Model DP Results (Pre-Phase-30)")
    lines.append("")
    lines.append(
        "> **Note**: These are proxy-model measurements. Epsilon accounting is applied to a "
        "proxy linear model, not the CTGAN Discriminator. Included for historical comparison "
        "only. Phase 30 deployments should use discriminator-level DP above."
    )
    lines.append("")
    lines.append(
        "| Mode | noise_multiplier | actual_epsilon (proxy-model measurement) "
        "| age mean_drift | age stddev_drift "
        "| salary mean_drift | salary stddev_drift | dept cardinality_drift | Passes AC? |"
    )
    lines.append(
        "|------|-----------------|----------------------------------------"
        "|---------------|---------------"
        "|------------------|-------------------|----------------------|------------|"
    )
    for row in proxy_rows:
        _append_row_to_table(lines, row)
    lines.append("")

    # --------------------------------------------------------------------------
    # Quality analysis sections
    # --------------------------------------------------------------------------
    lines.append("## Noise Multiplier Calibration")
    lines.append("")
    lines.append(
        "The following `noise_multiplier` values were calibrated empirically "
        f"against a {_N_SOURCE_ROWS}-row dataset with {_BENCHMARK_EPOCHS} training epochs. "
        "Because epsilon depends on dataset size, batch size, and number of DP gradient "
        "steps, these values are **specific to this benchmark configuration** and should "
        "be recalibrated for production datasets."
    )
    lines.append("")
    lines.append("| Target epsilon | noise_multiplier | Rationale |")
    lines.append("|---------------|-----------------|-----------|")
    lines.append(
        "| ~0.1 | 4.00 | Very high noise — strongest privacy, significant quality loss. "
        "Research/regulatory scenarios only. |"
    )
    lines.append("| ~0.5 | 2.50 | High noise — strong privacy, noticeable quality impact. |")
    lines.append(
        "| ~1 | 2.00 | High noise — strong privacy, significant quality loss. "
        "Suitable for highly sensitive PII release. |"
    )
    lines.append(
        "| ~5 | 0.75 | Moderate noise — balanced trade-off. "
        "Suitable for internal analytics on moderately sensitive data. |"
    )
    lines.append(
        "| ~10 | 0.55 | Low noise — weaker privacy, better quality. "
        "Suitable for non-sensitive synthetic data where utility is paramount. |"
    )
    lines.append("")
    lines.append("## Quality Degradation Analysis")
    lines.append("")
    lines.append(
        "The following observations describe the expected quality degradation curve "
        "as epsilon decreases (stronger privacy):"
    )
    lines.append("")
    lines.append(
        "1. **Vanilla CTGAN** establishes the best-quality baseline. At 10 epochs it "
        "already shows non-trivial drift; production epochs (300+) would reduce this."
    )
    lines.append(
        "2. **epsilon~10** introduces moderate noise. Mean drifts are typically within "
        "1-2 standard deviations of the source column. Categorical distributions are "
        "well-preserved."
    )
    lines.append(
        "3. **epsilon~5** increases drift noticeably on numeric columns. "
        "Salary columns are more affected due to wider absolute ranges amplifying "
        "the noise signal."
    )
    lines.append(
        "4. **epsilon~1** produces the strongest common privacy guarantee but at the cost of "
        "visible distributional distortion. Mean drifts can exceed 2 standard deviations "
        "on wide-range numeric columns under fast-training conditions."
    )
    lines.append(
        "5. **epsilon~0.5 and epsilon~0.1** represent extreme privacy regimes. Quality "
        "degradation is severe at these levels under fast-training conditions. Production "
        "use at epsilon < 1 requires substantially more training epochs and hyperparameter "
        "tuning."
    )
    lines.append("")
    lines.append(
        "**Discriminator-level vs proxy-model comparison**: Quality results between the two "
        "approaches will differ because discriminator-level DP directly constrains the GAN "
        "training gradient updates, while the proxy model runs separately from CTGAN. "
        "Discriminator-level DP is the only approach with end-to-end DP guarantees. "
        "Proxy-model results are included for historical comparison only."
    )
    lines.append("")
    lines.append("## Recommended Epsilon Ranges by Use Case")
    lines.append("")
    lines.append("| Use Case | Recommended Epsilon | Rationale |")
    lines.append("|----------|--------------------|-----------| ")
    lines.append(
        "| External publication / regulatory compliance | 1-2 | "
        "Strong privacy guarantee required; quality loss is acceptable. |"
    )
    lines.append(
        "| Internal analytics on sensitive PII | 5-8 | "
        "Balanced trade-off; distributions are broadly preserved. |"
    )
    lines.append(
        "| Non-sensitive internal testing / ML training | 10-20 | "
        "Utility is primary concern; privacy is best-effort. |"
    )
    lines.append(
        "| Production ML training (quality-first) | No DP (vanilla) | "
        "Use only when data sensitivity and regulatory requirements allow. |"
    )
    lines.append("")
    lines.append("## Acceptance Criteria Results")
    lines.append("")
    lines.append(
        "The benchmark acceptance criterion requires that discriminator-level DP at "
        "epsilon~10 passes basic distributional similarity:"
    )
    lines.append("")
    lines.append("- Column means within 2 standard deviations of source stddev (numeric columns)")
    lines.append(
        "- Categorical distributions within 10% KL divergence (approximated via cardinality ratio)"
    )
    lines.append("")
    eps10_disc = [r for r in discriminator_rows if "~10" in r.mode]
    if eps10_disc:
        eps10 = eps10_disc[0]
        result_word = "PASSED" if eps10.passes_acceptance else "FAILED"
        lines.append(
            f"**Result: discriminator-level DP epsilon~10 acceptance check — {result_word}** "
            f"(actual_epsilon={eps10.actual_epsilon:.4f})"
        )
    lines.append("")
    lines.append("## Benchmark Methodology and Limitations")
    lines.append("")
    lines.append(
        "This benchmark is intended as a quick operational health check, not as a "
        "production-grade privacy audit. Key limitations:"
    )
    lines.append("")
    lines.append(
        "- **Low epoch count**: 10 epochs is far below production (300+). "
        "Quality metrics here represent lower-bound fidelity estimates."
    )
    lines.append(
        "- **Small dataset**: 500 rows. Epsilon accounting is dataset-size-dependent. "
        "Production datasets will have different epsilon/noise_multiplier relationships."
    )
    lines.append(
        "- **Statistical metrics only**: The benchmark uses mean/stddev drift and "
        "cardinality as quality proxies. A full production evaluation should also "
        "include ML utility metrics and privacy attack evaluation."
    )
    lines.append(
        "- **Proxy fallback**: The proxy-model section forces the fallback path via an "
        "intentional first-call failure. This accurately tests the fallback behavior "
        "but the resulting quality may differ from a native proxy-model deployment."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*Note: All results are produced with intentionally low epoch counts "
        f"({_BENCHMARK_EPOCHS} epochs). Production fidelity with 300+ epochs "
        "will be substantially higher across all epsilon levels.*"
    )
    lines.append("")
    lines.append(
        "*Discriminator-level DP epsilon values reflect real Opacus accounting on the "
        "CTGAN Discriminator (Phase 30, ADR-0036). Proxy-model epsilon values reflect "
        "accounting on a linear proxy model (pre-Phase-30, ADR-0025).*"
    )
    return "\n".join(lines)


def _append_row_to_table(lines: list[str], row: BenchmarkRow) -> None:
    """Append one BenchmarkRow as a Markdown table row.

    Args:
        lines: The list of Markdown lines to append to.
        row: The BenchmarkRow to format.
    """
    age_mean = row.column_mean_drifts.get("age")
    age_std = row.column_stddev_drifts.get("age")
    sal_mean = row.column_mean_drifts.get("salary")
    sal_std = row.column_stddev_drifts.get("salary")
    dept_card = row.cardinality_drifts.get("department")

    def _fmf(v: float | None) -> str:
        return f"{v:+.2f}" if v is not None else "N/A"

    def _fmi(v: int | None) -> str:
        return f"{v:+d}" if v is not None else "N/A"

    pass_badge = "**YES**" if row.passes_acceptance else "NO"
    lines.append(
        f"| {row.mode} | {row.noise_multiplier:.2f} | {row.actual_epsilon:.4f} "
        f"| {_fmf(age_mean)} | {_fmf(age_std)} "
        f"| {_fmf(sal_mean)} | {_fmf(sal_std)} "
        f"| {_fmi(dept_card)} | {pass_badge} |"
    )


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the DP quality benchmark and write results.

    Steps:
    1. Generate 500-row fictional persons table via Faker.
    2. Profile the source data.
    3. Detect SDV metadata.
    4. Train vanilla CTGAN, sample, profile, compare.
    5. For each of 5 epsilon levels:
       a. Train discriminator-level DP-CTGAN, sample, profile, compare.
       b. Train proxy-model DP-CTGAN (historical), sample, profile, compare.
    6. Print summary table to stdout.
    7. Write Markdown report to docs/DP_QUALITY_REPORT.md.
    """
    # print() is intentional — benchmark output is developer-facing terminal tables,
    # not structured logs.
    print("=" * 80)
    print("Air-Gapped Synthetic Data Engine — DP Quality Benchmark (T30.4)")
    print("Vanilla vs Proxy-Model DP vs Discriminator-Level DP")
    print("=" * 80)
    print()

    # Step 1: Generate source data
    print(f"[1/5] Generating source dataset ({_N_SOURCE_ROWS} rows, Faker seed={_FAKER_SEED})...")
    source_df = _generate_source_data()
    print(f"      Source columns: {list(source_df.columns)}")
    print()

    # Step 2: Profile source data
    print("[2/5] Profiling source data...")
    profiler = StatisticalProfiler()
    source_profile = profiler.profile("persons", source_df)
    print(f"      Profiled {len(source_profile.columns)} columns.")
    print()

    # Step 3: Build SDV metadata
    print("[3/5] Detecting SDV metadata...")
    metadata = _build_metadata(source_df)
    print("      Metadata detected.")
    print()

    # Step 4: Train models and collect results
    print("[4/5] Training models...")
    vanilla_row: BenchmarkRow
    proxy_rows: list[BenchmarkRow] = []
    discriminator_rows: list[BenchmarkRow] = []

    # Vanilla baseline
    print("      [Vanilla] Training non-DP CTGAN...")
    vanilla_df = _train_vanilla(source_df, metadata)
    vanilla_profile = profiler.profile("persons_vanilla", vanilla_df)
    vanilla_delta = profiler.compare(source_profile, vanilla_profile)
    vanilla_row = _build_row("Vanilla", "vanilla", 0.0, 0.0, vanilla_delta, source_profile)
    print("      [Vanilla] Done.")

    # Discriminator-level DP configurations (primary — Phase 30)
    for noise_mult, target_eps in _EPSILON_CONFIGS:
        mode_label = f"Disc-DP (eps{target_eps})"
        print(
            f"      [Discriminator-DP eps{target_eps}] "
            f"Training with noise_multiplier={noise_mult:.2f}..."
        )
        try:
            disc_df, actual_eps = _train_discriminator_dp(source_df, metadata, noise_mult)
            disc_profile = profiler.profile(
                f"persons_disc_eps{target_eps.replace('.', '_')}",
                disc_df,
            )
            disc_delta = profiler.compare(source_profile, disc_profile)
            discriminator_rows.append(
                _build_row(
                    mode_label,
                    "discriminator",
                    noise_mult,
                    actual_eps,
                    disc_delta,
                    source_profile,
                )
            )
            print(f"      [Discriminator-DP eps{target_eps}] Done. actual_epsilon={actual_eps:.4f}")
        except Exception as exc:
            print(
                f"      [Discriminator-DP eps{target_eps}] FAILED: {exc}. "
                "Skipping this configuration."
            )

    # Proxy-model DP configurations (historical — pre-Phase-30)
    print()
    print("      [Proxy-model DP — historical comparison]")
    for noise_mult, target_eps in _EPSILON_CONFIGS:
        mode_label = f"Proxy-DP (eps{target_eps})"
        print(
            f"      [Proxy-DP eps{target_eps}] Training with noise_multiplier={noise_mult:.2f}..."
        )
        try:
            proxy_df, actual_eps = _train_proxy_dp(source_df, metadata, noise_mult)
            proxy_profile = profiler.profile(
                f"persons_proxy_eps{target_eps.replace('.', '_')}",
                proxy_df,
            )
            proxy_delta = profiler.compare(source_profile, proxy_profile)
            proxy_rows.append(
                _build_row(
                    mode_label,
                    "proxy",
                    noise_mult,
                    actual_eps,
                    proxy_delta,
                    source_profile,
                )
            )
            print(f"      [Proxy-DP eps{target_eps}] Done. actual_epsilon={actual_eps:.4f}")
        except Exception as exc:
            print(f"      [Proxy-DP eps{target_eps}] FAILED: {exc}. Skipping this configuration.")

    print()

    # Step 5: Print summary table
    print("[5/5] Results")
    print()
    print("--- DISCRIMINATOR-LEVEL DP (Phase 30 — primary) ---")
    disc_all_rows = [vanilla_row, *discriminator_rows]
    print(_format_table(disc_all_rows))
    print()
    print("--- PROXY-MODEL DP (historical comparison) ---")
    proxy_all_rows = [vanilla_row, *proxy_rows]
    print(_format_table(proxy_all_rows))
    print()

    # Write Markdown report
    report_content = _format_markdown_report(
        vanilla_row=vanilla_row,
        proxy_rows=proxy_rows,
        discriminator_rows=discriminator_rows,
        source_profile=source_profile,
    )
    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REPORT_PATH.write_text(report_content, encoding="utf-8")
    print(f"Report written to: {_REPORT_PATH}")
    print()

    # Final acceptance check summary
    eps10_disc = [r for r in discriminator_rows if "~10" in r.mode]
    if eps10_disc:
        eps10 = eps10_disc[0]
        status = "PASSED" if eps10.passes_acceptance else "FAILED"
        print(f"Acceptance criterion — discriminator-level DP epsilon~10: {status}")
    print()
    print("Benchmark complete.")


if __name__ == "__main__":
    main()
