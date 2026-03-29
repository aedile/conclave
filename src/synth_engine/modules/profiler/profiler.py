"""Statistical Profiler for the Air-Gapped Synthetic Data Generation Engine.

Computes baseline distributions of source data before synthesis so that
the synthetic output can be quantitatively compared against the original.

The profiler operates entirely on plain Python objects (pandas DataFrames)
and has NO dependency on any other module within synth_engine.  It is the
bootstrapper's or subsetting engine's responsibility to convert database
rows to DataFrames before passing them here.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from synth_engine.modules.profiler.models import (
    ColumnDelta,
    ColumnProfile,
    ProfileDelta,
    TableProfile,
)

# Quantiles computed for each numeric column.
_QUANTILES = (0.25, 0.50, 0.75)

# pandas dtype kinds that are treated as numeric for statistics purposes.
# 'i' = signed integer, 'u' = unsigned integer, 'f' = floating point.
_NUMERIC_KINDS = frozenset({"i", "u", "f"})

#: Cardinality threshold for safe-default PII suppression (T69.2).
#: Categorical columns with this many or more distinct values have their
#: ``value_counts`` suppressed when ``pii_columns=None`` (opt-out model).
_PII_CARDINALITY_THRESHOLD: int = 50


def _is_numeric(series: pd.Series[Any]) -> bool:
    """Return True when the series holds numeric (integer or float) data.

    Args:
        series: A pandas Series.

    Returns:
        ``True`` if the dtype kind is ``'i'``, ``'u'``, or ``'f'``; otherwise
        ``False``.
    """
    return series.dtype.kind in _NUMERIC_KINDS


def _safe_float(value: Any) -> float | None:
    """Convert a scalar to float, returning None for NaN or non-finite values.

    Args:
        value: A value to convert.

    Returns:
        ``float(value)`` when finite; ``None`` when NaN or infinite.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _profile_numeric_column(name: str, series: pd.Series[Any]) -> ColumnProfile:
    """Build a :class:`ColumnProfile` for a numeric pandas Series.

    Statistics are computed on non-null values only.  When all values are
    null, the statistical fields default to ``None`` rather than raising
    errors.

    Args:
        name: Column name.
        series: Pandas Series with a numeric dtype.

    Returns:
        A :class:`ColumnProfile` with all numeric fields populated and
        ``is_numeric=True``.
    """
    total = len(series)
    null_count = int(series.isna().sum())
    null_rate = null_count / total if total > 0 else 0.0

    non_null = series.dropna()

    if len(non_null) == 0:
        return ColumnProfile(
            name=name,
            dtype=str(series.dtype),
            null_count=null_count,
            null_rate=null_rate,
            is_numeric=True,
        )

    q25, q50, q75 = non_null.quantile(list(_QUANTILES)).tolist()

    # ddof=1 -> sample standard deviation (pandas default).
    std_val = non_null.std(ddof=1)

    return ColumnProfile(
        name=name,
        dtype=str(series.dtype),
        null_count=null_count,
        null_rate=null_rate,
        is_numeric=True,
        mean=_safe_float(non_null.mean()),
        stddev=_safe_float(std_val),
        min=_safe_float(non_null.min()),
        max=_safe_float(non_null.max()),
        q25=_safe_float(q25),
        q50=_safe_float(q50),
        q75=_safe_float(q75),
    )


def _profile_categorical_column(name: str, series: pd.Series[Any]) -> ColumnProfile:
    """Build a :class:`ColumnProfile` for a categorical (non-numeric) Series.

    Args:
        name: Column name.
        series: Pandas Series with a non-numeric dtype.

    Returns:
        A :class:`ColumnProfile` with value_counts and cardinality populated.
    """
    total = len(series)
    null_count = int(series.isna().sum())
    null_rate = null_count / total if total > 0 else 0.0

    non_null = series.dropna()
    vc: dict[str, int] = {str(k): int(v) for k, v in non_null.value_counts().items()}
    cardinality = len(vc)

    return ColumnProfile(
        name=name,
        dtype=str(series.dtype),
        null_count=null_count,
        null_rate=null_rate,
        value_counts=vc,
        cardinality=cardinality,
    )


def _profile_pii_column(name: str, series: pd.Series[Any]) -> ColumnProfile:
    """Build a :class:`ColumnProfile` for a PII-tagged categorical Series.

    Suppresses raw ``value_counts`` entirely to prevent PII from appearing
    in the statistical profile.  Reports only safe aggregate statistics:
    ``cardinality``, ``null_rate``, ``min_length``, and ``max_length``.

    Args:
        name: Column name.
        series: Pandas Series with a non-numeric dtype tagged as PII.

    Returns:
        A :class:`ColumnProfile` with ``value_counts=None`` and safe
        aggregate fields populated.  Suitable for export without PII leakage.
    """
    total = len(series)
    null_count = int(series.isna().sum())
    null_rate = null_count / total if total > 0 else 0.0

    non_null = series.dropna()
    cardinality = non_null.nunique()

    if len(non_null) == 0:
        return ColumnProfile(
            name=name,
            dtype=str(series.dtype),
            null_count=null_count,
            null_rate=null_rate,
            cardinality=0,
        )

    # Compute min/max string lengths for distribution shape (no raw values)
    lengths = non_null.astype(str).str.len()
    min_len = int(lengths.min())
    max_len = int(lengths.max())

    return ColumnProfile(
        name=name,
        dtype=str(series.dtype),
        null_count=null_count,
        null_rate=null_rate,
        cardinality=cardinality,
        min_length=min_len,
        max_length=max_len,
    )


def _covariance_entry(raw: Any) -> float:
    """Convert a raw covariance matrix cell value to a safe float.

    Args:
        raw: The raw value from a pandas covariance DataFrame cell.

    Returns:
        The float value, or ``0.0`` if the value is NaN.
    """
    f = float(raw)
    return 0.0 if math.isnan(f) else f


def _build_covariance_matrix(
    df: pd.DataFrame,
    numeric_cols: list[str],
) -> dict[str, dict[str, float]]:
    """Compute pairwise sample covariances for all numeric columns.

    When fewer than two numeric columns exist, the result is an empty dict.

    Args:
        df: The source DataFrame.
        numeric_cols: List of numeric column names.

    Returns:
        Nested dict ``{col_a: {col_b: cov, ...}, ...}`` using ddof=1.
        The dict is symmetric: ``result[a][b] == result[b][a]``.
    """
    if len(numeric_cols) < 2:
        return {}

    cov_df = df[numeric_cols].cov()  # pandas default: ddof=1
    result: dict[str, dict[str, float]] = {}
    for col_a in numeric_cols:
        result[col_a] = {}
        for col_b in numeric_cols:
            result[col_a][col_b] = _covariance_entry(cov_df.loc[col_a, col_b])
    return result


class StatisticalProfiler:
    """Computes statistical profiles of source data and compares them.

    The profiler is stateless; each call to :meth:`profile` or
    :meth:`compare` is independent.  No database connections or external
    services are used -- only pandas DataFrames.

    Example::

        profiler = StatisticalProfiler()
        baseline = profiler.profile("users", source_df)
        synthetic = profiler.profile("users", synth_df)
        delta = profiler.compare(baseline, synthetic)
    """

    def profile(
        self,
        table_name: str,
        df: pd.DataFrame,
        *,
        pii_columns: set[str] | None = None,
    ) -> TableProfile:
        """Compute statistical properties of every column in *df*.

        For each column:
        - Numeric columns: dtype, null_count, null_rate, mean, stddev,
          min, max, q25, q50, q75.
        - Categorical columns: dtype, null_count, null_rate, value_counts,
          cardinality.
        - PII columns (T69.2): value_counts suppressed; only cardinality,
          null_rate, min_length, max_length reported.

        A covariance matrix is computed for all numeric column pairs.

        Safe-default PII suppression (T69.2): when ``pii_columns`` is
        ``None`` (not explicitly provided), categorical columns with
        cardinality >= :data:`_PII_CARDINALITY_THRESHOLD` (50) have their
        ``value_counts`` suppressed to prevent inadvertent PII leakage.
        Pass ``pii_columns=set()`` to opt out of safe-default suppression
        and report all columns normally.

        Args:
            table_name: Logical name of the source table (used for
                identification in :class:`TableProfile` and
                :class:`ProfileDelta`).
            df: Source DataFrame.  May contain NaN/None values.
            pii_columns: Optional set of column names to treat as PII.
                For tagged columns, ``value_counts`` is suppressed and only
                safe aggregates are reported.  Column names not present in
                ``df`` are silently ignored.  When ``None`` (default), safe
                heuristic suppression applies: any categorical column with
                cardinality >= :data:`_PII_CARDINALITY_THRESHOLD` is treated
                as PII.  Pass ``set()`` to disable heuristic suppression.

        Returns:
            A frozen :class:`TableProfile` snapshot.
        """
        numeric_cols: list[str] = []
        columns: dict[str, ColumnProfile] = {}
        # Resolve the PII column set against actual DataFrame columns.
        # Unknown names in pii_columns are silently ignored.
        resolved_pii: set[str] = (pii_columns or set()) & set(df.columns)

        for col_name in df.columns:
            series = df[col_name]
            if _is_numeric(series):
                numeric_cols.append(col_name)
                columns[col_name] = _profile_numeric_column(col_name, series)
            elif col_name in resolved_pii:
                # Explicit PII tagging — suppress value_counts
                columns[col_name] = _profile_pii_column(col_name, series)
            else:
                profile = _profile_categorical_column(col_name, series)
                # Safe-default threshold: when pii_columns was None (not set()),
                # suppress high-cardinality columns automatically.
                if pii_columns is None and (profile.cardinality or 0) >= _PII_CARDINALITY_THRESHOLD:
                    columns[col_name] = _profile_pii_column(col_name, series)
                else:
                    columns[col_name] = profile

        covariance_matrix = _build_covariance_matrix(df, numeric_cols)

        return TableProfile(
            table_name=table_name,
            row_count=len(df),
            columns=columns,
            covariance_matrix=covariance_matrix,
        )

    def compare(
        self,
        baseline: TableProfile,
        synthetic: TableProfile,
    ) -> ProfileDelta:
        """Compare a synthetic profile against a baseline and report drift.

        For numeric columns, drift is ``synthetic_value - baseline_value``
        for mean and stddev.  For categorical columns, drift is the change in
        cardinality.  Columns present only in baseline or only in synthetic
        are included with ``None`` drift values.

        The numeric/categorical classification uses the ``is_numeric`` field
        on :class:`ColumnProfile` rather than checking whether ``mean`` is
        non-``None``.  This ensures that all-null numeric columns (where
        ``mean=None``) are still treated as numeric.

        Args:
            baseline: Statistical profile of the original source data.
            synthetic: Statistical profile of the synthesised data.

        Returns:
            A frozen :class:`ProfileDelta` with per-column drift metrics.
        """
        all_columns = set(baseline.columns) | set(synthetic.columns)
        deltas: dict[str, ColumnDelta] = {}

        for col_name in sorted(all_columns):
            base_col = baseline.columns.get(col_name)
            synth_col = synthetic.columns.get(col_name)

            if base_col is None or synth_col is None:
                # Column is present in only one profile.
                deltas[col_name] = ColumnDelta(column_name=col_name)
                continue

            # Use the is_numeric flag — not mean — so that all-null numeric
            # columns (mean=None) are still routed to the numeric branch.
            if base_col.is_numeric or synth_col.is_numeric:
                base_mean = base_col.mean if base_col.mean is not None else 0.0
                synth_mean = synth_col.mean if synth_col.mean is not None else 0.0
                base_std = base_col.stddev if base_col.stddev is not None else 0.0
                synth_std = synth_col.stddev if synth_col.stddev is not None else 0.0

                # When both means are None the column is all-null on both sides;
                # report None drift rather than a spurious 0.0.
                mean_drift: float | None
                stddev_drift: float | None
                if base_col.mean is None and synth_col.mean is None:
                    mean_drift = None
                    stddev_drift = None
                else:
                    mean_drift = synth_mean - base_mean
                    stddev_drift = synth_std - base_std

                deltas[col_name] = ColumnDelta(
                    column_name=col_name,
                    mean_drift=mean_drift,
                    stddev_drift=stddev_drift,
                )
            else:
                # Categorical column -- compute cardinality drift.
                base_card = base_col.cardinality if base_col.cardinality is not None else 0
                synth_card = synth_col.cardinality if synth_col.cardinality is not None else 0
                deltas[col_name] = ColumnDelta(
                    column_name=col_name,
                    cardinality_drift=synth_card - base_card,
                )

        return ProfileDelta(
            baseline_table=baseline.table_name,
            synthetic_table=synthetic.table_name,
            column_deltas=deltas,
        )
