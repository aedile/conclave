"""Unit tests for the StatisticalProfiler — T4.2a.

TDD RED phase: all tests are written before implementation.

Test coverage targets:
- profile() on a known DataFrame (numeric + categorical columns)
- compare() returning zero drift on identical profiles
- compare() detecting drift on significantly different profiles
- profile() handling None/NaN values with correct null rates
- all-null column handled gracefully (no division-by-zero)
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from synth_engine.modules.profiler.models import ColumnProfile, ProfileDelta, TableProfile
from synth_engine.modules.profiler.profiler import StatisticalProfiler

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_known_df() -> pd.DataFrame:
    """Return a 10-row DataFrame with 3 numeric and 2 categorical columns.

    Ground truths (computed via pandas linear-interpolation quartiles):

    age:  [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
      mean=55.0, std=30.276..., min=10, max=100
      q25=32.5, q50=55.0, q75=77.5  (pandas default linear interpolation)
    score:  [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
      mean=5.5, std=3.027..., min=1.0, max=10.0
    weight: [100.0, 200.0, ..., 1000.0] (steps of 100)
      mean=550.0

    category: ["A","B","A","C","B","A","C","A","B","A"] → A:5, B:3, C:2, cardinality=3
    label: ["X","Y","X","X","Y","Z","X","Y","Z","X"] → X:5, Y:3, Z:2, cardinality=3
    """
    return pd.DataFrame(
        {
            "age": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
            "score": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
            "weight": [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0],
            "category": ["A", "B", "A", "C", "B", "A", "C", "A", "B", "A"],
            "label": ["X", "Y", "X", "X", "Y", "Z", "X", "Y", "Z", "X"],
        }
    )


# ---------------------------------------------------------------------------
# T4.2a-01: profile() on known DataFrame — numeric columns
# ---------------------------------------------------------------------------


class TestProfileKnownNumericColumns:
    """Verify per-column statistics for numeric columns match ground-truth values."""

    def setup_method(self) -> None:
        self.profiler = StatisticalProfiler()
        self.df = _make_known_df()
        self.result = self.profiler.profile("test_table", self.df)

    def test_returns_table_profile(self) -> None:
        assert isinstance(self.result, TableProfile)

    def test_table_name_stored(self) -> None:
        assert self.result.table_name == "test_table"

    def test_row_count_correct(self) -> None:
        assert self.result.row_count == 10

    def test_numeric_column_age_dtype(self) -> None:
        col = self.result.columns["age"]
        assert col.dtype == "int64"

    def test_numeric_column_age_null_count(self) -> None:
        col = self.result.columns["age"]
        assert col.null_count == 0

    def test_numeric_column_age_null_rate(self) -> None:
        col = self.result.columns["age"]
        assert col.null_rate == pytest.approx(0.0)

    def test_numeric_column_age_min(self) -> None:
        col = self.result.columns["age"]
        assert col.min == pytest.approx(10.0)

    def test_numeric_column_age_max(self) -> None:
        col = self.result.columns["age"]
        assert col.max == pytest.approx(100.0)

    def test_numeric_column_age_mean(self) -> None:
        col = self.result.columns["age"]
        assert col.mean == pytest.approx(55.0)

    def test_numeric_column_age_stddev(self) -> None:
        # pandas default: ddof=1 (sample std)
        col = self.result.columns["age"]
        expected_std = float(np.std([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], ddof=1))
        assert col.stddev == pytest.approx(expected_std, rel=1e-5)

    def test_numeric_column_age_quartiles(self) -> None:
        # pandas linear-interpolation quartiles for [10..100 step 10]:
        # q25=32.5, q50=55.0, q75=77.5
        col = self.result.columns["age"]
        series = pd.Series([10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
        expected_q25 = float(series.quantile(0.25))
        expected_q50 = float(series.quantile(0.50))
        expected_q75 = float(series.quantile(0.75))
        assert col.q25 == pytest.approx(expected_q25, rel=1e-5)
        assert col.q50 == pytest.approx(expected_q50, rel=1e-5)
        assert col.q75 == pytest.approx(expected_q75, rel=1e-5)

    def test_numeric_column_score_mean(self) -> None:
        col = self.result.columns["score"]
        assert col.mean == pytest.approx(5.5)

    def test_numeric_column_weight_mean(self) -> None:
        col = self.result.columns["weight"]
        assert col.mean == pytest.approx(550.0)


# ---------------------------------------------------------------------------
# T4.2a-02: profile() on known DataFrame — categorical columns
# ---------------------------------------------------------------------------


class TestProfileKnownCategoricalColumns:
    """Verify value_counts, cardinality, and null rates for categorical columns."""

    def setup_method(self) -> None:
        self.profiler = StatisticalProfiler()
        self.df = _make_known_df()
        self.result = self.profiler.profile("test_table", self.df)

    def test_categorical_column_category_dtype(self) -> None:
        col = self.result.columns["category"]
        assert col.dtype == "object"

    def test_categorical_column_category_null_count(self) -> None:
        col = self.result.columns["category"]
        assert col.null_count == 0

    def test_categorical_column_category_null_rate(self) -> None:
        col = self.result.columns["category"]
        assert col.null_rate == pytest.approx(0.0)

    def test_categorical_column_category_cardinality(self) -> None:
        col = self.result.columns["category"]
        assert col.cardinality == 3

    def test_categorical_column_category_value_counts_a(self) -> None:
        col = self.result.columns["category"]
        assert col.value_counts is not None
        assert col.value_counts["A"] == 5

    def test_categorical_column_category_value_counts_b(self) -> None:
        col = self.result.columns["category"]
        assert col.value_counts is not None
        assert col.value_counts["B"] == 3

    def test_categorical_column_category_value_counts_c(self) -> None:
        col = self.result.columns["category"]
        assert col.value_counts is not None
        assert col.value_counts["C"] == 2

    def test_categorical_column_label_cardinality(self) -> None:
        col = self.result.columns["label"]
        assert col.cardinality == 3

    def test_numeric_columns_have_no_value_counts(self) -> None:
        col = self.result.columns["age"]
        assert col.value_counts is None

    def test_numeric_columns_have_no_cardinality(self) -> None:
        col = self.result.columns["age"]
        assert col.cardinality is None


# ---------------------------------------------------------------------------
# T4.2a-03: profile() — covariance matrix
# ---------------------------------------------------------------------------


class TestProfileCovarianceMatrix:
    """Verify the covariance matrix is computed for all numeric pairs."""

    def setup_method(self) -> None:
        self.profiler = StatisticalProfiler()
        self.df = _make_known_df()
        self.result = self.profiler.profile("test_table", self.df)

    def test_covariance_matrix_present(self) -> None:
        assert self.result.covariance_matrix is not None

    def test_covariance_matrix_contains_numeric_columns(self) -> None:
        assert set(self.result.covariance_matrix.keys()) == {"age", "score", "weight"}

    def test_covariance_matrix_symmetric(self) -> None:
        cov = self.result.covariance_matrix
        assert cov["age"]["score"] == pytest.approx(cov["score"]["age"], rel=1e-10)
        assert cov["age"]["weight"] == pytest.approx(cov["weight"]["age"], rel=1e-10)

    def test_covariance_age_score_value(self) -> None:
        # age=[10..100 step 10], score=[1..10 step 1] → perfectly correlated
        # Cov(age, score) = Cov(10*score, score) = 10 * Var(score)
        # Var(score, ddof=1) = 9.166... → Cov = 91.666...
        cov = self.result.covariance_matrix
        age_vals = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        score_vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        expected = float(
            pd.DataFrame({"age": age_vals, "score": score_vals}).cov().loc["age", "score"]
        )
        assert cov["age"]["score"] == pytest.approx(expected, rel=1e-5)

    def test_single_numeric_column_empty_covariance(self) -> None:
        """Only one numeric column → covariance matrix is empty (no pairs)."""
        df = pd.DataFrame({"x": [1, 2, 3], "cat": ["a", "b", "c"]})
        result = self.profiler.profile("single_num", df)
        assert result.covariance_matrix == {}


# ---------------------------------------------------------------------------
# T4.2a-04: profile() with None/NaN values — null rates
# ---------------------------------------------------------------------------


class TestProfileWithNulls:
    """Verify null counts and null rates are correct when values are missing."""

    def setup_method(self) -> None:
        self.profiler = StatisticalProfiler()

    def test_numeric_column_with_two_nulls(self) -> None:
        df = pd.DataFrame({"x": [1.0, 2.0, None, 4.0, None]})
        result = self.profiler.profile("t", df)
        col = result.columns["x"]
        assert col.null_count == 2
        assert col.null_rate == pytest.approx(0.4)

    def test_categorical_column_with_one_null(self) -> None:
        df = pd.DataFrame({"cat": ["A", None, "B", "A"]})
        result = self.profiler.profile("t", df)
        col = result.columns["cat"]
        assert col.null_count == 1
        assert col.null_rate == pytest.approx(0.25)

    def test_numeric_nullable_mean_excludes_nulls(self) -> None:
        df = pd.DataFrame({"x": [10.0, 20.0, None, 40.0]})
        result = self.profiler.profile("t", df)
        col = result.columns["x"]
        # mean of [10, 20, 40] = 23.333...
        assert col.mean == pytest.approx(70.0 / 3.0, rel=1e-5)


# ---------------------------------------------------------------------------
# T4.2a-05: all-null column — no division-by-zero
# ---------------------------------------------------------------------------


class TestAllNullColumn:
    """An all-null column must not raise ZeroDivisionError; stats default to None."""

    def setup_method(self) -> None:
        self.profiler = StatisticalProfiler()

    def test_all_null_numeric_column_no_error(self) -> None:
        df = pd.DataFrame({"x": [None, None, None]})
        # Must not raise
        result = self.profiler.profile("t", df)
        col = result.columns["x"]
        assert col.null_count == 3
        assert col.null_rate == pytest.approx(1.0)

    def test_all_null_numeric_column_stats_are_none(self) -> None:
        df = pd.DataFrame({"x": [None, None, None]})
        result = self.profiler.profile("t", df)
        col = result.columns["x"]
        assert col.mean is None
        assert col.stddev is None
        assert col.min is None
        assert col.max is None

    def test_all_null_categorical_column_no_error(self) -> None:
        df = pd.DataFrame({"cat": pd.Series([None, None], dtype=object)})
        result = self.profiler.profile("t", df)
        col = result.columns["cat"]
        assert col.null_count == 2
        assert col.null_rate == pytest.approx(1.0)
        assert col.cardinality == 0


# ---------------------------------------------------------------------------
# T4.2a-06: compare() — zero drift on identical profiles
# ---------------------------------------------------------------------------


class TestCompareIdenticalProfiles:
    """compare() on identical profiles must return zero drift on all columns."""

    def setup_method(self) -> None:
        self.profiler = StatisticalProfiler()
        df = _make_known_df()
        self.baseline = self.profiler.profile("t", df)
        self.synthetic = self.profiler.profile("t", df)

    def test_compare_returns_profile_delta(self) -> None:
        delta = self.profiler.compare(self.baseline, self.synthetic)
        assert isinstance(delta, ProfileDelta)

    def test_compare_identical_zero_mean_drift_age(self) -> None:
        delta = self.profiler.compare(self.baseline, self.synthetic)
        assert delta.column_deltas["age"].mean_drift == pytest.approx(0.0)

    def test_compare_identical_zero_stddev_drift_age(self) -> None:
        delta = self.profiler.compare(self.baseline, self.synthetic)
        assert delta.column_deltas["age"].stddev_drift == pytest.approx(0.0)

    def test_compare_identical_all_columns_zero_drift(self) -> None:
        delta = self.profiler.compare(self.baseline, self.synthetic)
        for col_name, col_delta in delta.column_deltas.items():
            if col_delta.mean_drift is not None:
                assert col_delta.mean_drift == pytest.approx(0.0), f"Non-zero drift for {col_name}"

    def test_compare_identical_zero_categorical_drift(self) -> None:
        delta = self.profiler.compare(self.baseline, self.synthetic)
        cat_delta = delta.column_deltas["category"]
        # For categorical, cardinality_drift should be 0
        assert cat_delta.cardinality_drift == 0


# ---------------------------------------------------------------------------
# T4.2a-07: compare() — drift detected on significantly different profiles
# ---------------------------------------------------------------------------


class TestCompareDriftingProfiles:
    """compare() must identify the drifting columns when profiles differ significantly."""

    def setup_method(self) -> None:
        self.profiler = StatisticalProfiler()

    def test_compare_detects_mean_drift(self) -> None:
        baseline_df = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0]})
        synthetic_df = pd.DataFrame({"x": [100.0, 200.0, 300.0, 400.0, 500.0]})
        baseline = self.profiler.profile("t", baseline_df)
        synthetic = self.profiler.profile("t", synthetic_df)
        delta = self.profiler.compare(baseline, synthetic)
        # mean drift = 300.0 - 3.0 = 297.0
        assert delta.column_deltas["x"].mean_drift == pytest.approx(297.0, rel=1e-5)

    def test_compare_detects_stddev_drift(self) -> None:
        baseline_df = pd.DataFrame({"x": [1.0, 1.0, 1.0, 1.0, 1.0]})  # std ~ 0
        synthetic_df = pd.DataFrame({"x": [1.0, 10.0, 100.0, 1000.0, 10000.0]})
        baseline = self.profiler.profile("t", baseline_df)
        synthetic = self.profiler.profile("t", synthetic_df)
        delta = self.profiler.compare(baseline, synthetic)
        synth_std = float(pd.Series([1.0, 10.0, 100.0, 1000.0, 10000.0]).std())
        assert delta.column_deltas["x"].stddev_drift == pytest.approx(synth_std - 0.0, rel=1e-5)

    def test_compare_drifting_columns_listed(self) -> None:
        baseline_df = pd.DataFrame(
            {
                "stable": [1.0, 2.0, 3.0, 4.0, 5.0],
                "drifted": [1.0, 2.0, 3.0, 4.0, 5.0],
            }
        )
        synthetic_df = pd.DataFrame(
            {
                "stable": [1.0, 2.0, 3.0, 4.0, 5.0],
                "drifted": [100.0, 200.0, 300.0, 400.0, 500.0],
            }
        )
        baseline = self.profiler.profile("t", baseline_df)
        synthetic = self.profiler.profile("t", synthetic_df)
        delta = self.profiler.compare(baseline, synthetic)
        assert delta.column_deltas["stable"].mean_drift == pytest.approx(0.0)
        assert abs(delta.column_deltas["drifted"].mean_drift or 0.0) > 1.0

    def test_compare_categorical_cardinality_drift(self) -> None:
        baseline_df = pd.DataFrame({"cat": ["A", "B", "C"]})
        synthetic_df = pd.DataFrame({"cat": ["A", "B", "C", "D", "E"]})
        baseline = self.profiler.profile("t", baseline_df)
        synthetic = self.profiler.profile("t", synthetic_df)
        delta = self.profiler.compare(baseline, synthetic)
        # cardinality goes from 3 to 5 → drift = 2
        assert delta.column_deltas["cat"].cardinality_drift == 2


# ---------------------------------------------------------------------------
# T4.2a-08: serialization — to_dict / from_dict round-trip
# ---------------------------------------------------------------------------


class TestSerialisation:
    """TableProfile, ColumnProfile, and ProfileDelta must serialise to/from dict."""

    def setup_method(self) -> None:
        self.profiler = StatisticalProfiler()
        self.df = _make_known_df()
        self.profile = self.profiler.profile("t", self.df)

    def test_table_profile_to_dict_is_dict(self) -> None:
        d = self.profile.to_dict()
        assert isinstance(d, dict)

    def test_table_profile_to_dict_contains_table_name(self) -> None:
        d = self.profile.to_dict()
        assert d["table_name"] == "t"

    def test_table_profile_roundtrip(self) -> None:
        d = self.profile.to_dict()
        restored = TableProfile.from_dict(d)
        assert restored.table_name == self.profile.table_name
        assert restored.row_count == self.profile.row_count

    def test_table_profile_roundtrip_age_mean(self) -> None:
        d = self.profile.to_dict()
        restored = TableProfile.from_dict(d)
        assert restored.columns["age"].mean == pytest.approx(55.0)

    def test_profile_delta_to_dict(self) -> None:
        synthetic = self.profiler.profile("t", self.df)
        delta = self.profiler.compare(self.profile, synthetic)
        d = delta.to_dict()
        assert isinstance(d, dict)
        assert "column_deltas" in d

    def test_column_profile_to_dict_numeric(self) -> None:
        col = self.profile.columns["age"]
        d = col.to_dict()
        assert d["mean"] == pytest.approx(55.0)
        assert d["null_count"] == 0

    def test_column_profile_roundtrip_numeric(self) -> None:
        col = self.profile.columns["age"]
        d = col.to_dict()
        restored = ColumnProfile.from_dict(d)
        assert restored.mean == pytest.approx(55.0)
        assert restored.dtype == "int64"

    def test_column_profile_roundtrip_categorical(self) -> None:
        col = self.profile.columns["category"]
        d = col.to_dict()
        restored = ColumnProfile.from_dict(d)
        assert restored.cardinality == 3
        assert restored.value_counts is not None
        assert restored.value_counts["A"] == 5


# ---------------------------------------------------------------------------
# T4.2a-09: ProfileDelta is a frozen dataclass
# ---------------------------------------------------------------------------


class TestFrozenDataclasses:
    """Models must be frozen (immutable) dataclasses."""

    def test_table_profile_is_frozen(self) -> None:
        profiler = StatisticalProfiler()
        df = _make_known_df()
        profile = profiler.profile("t", df)
        with pytest.raises((AttributeError, TypeError)):
            profile.row_count = 999  # type: ignore[misc]

    def test_profile_delta_is_frozen(self) -> None:
        profiler = StatisticalProfiler()
        df = _make_known_df()
        p = profiler.profile("t", df)
        delta = profiler.compare(p, p)
        with pytest.raises((AttributeError, TypeError)):
            delta.column_deltas = {}  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T4.2a-10: empty DataFrame edge case
# ---------------------------------------------------------------------------


class TestEmptyDataFrame:
    """profile() on an empty DataFrame should not raise."""

    def test_empty_df_no_error(self) -> None:
        profiler = StatisticalProfiler()
        df = pd.DataFrame({"x": pd.Series([], dtype=float), "cat": pd.Series([], dtype=object)})
        result = profiler.profile("empty", df)
        assert result.row_count == 0
        assert result.columns["x"].null_count == 0
        null_rate = result.columns["x"].null_rate
        assert math.isnan(null_rate or 0.0) or null_rate == pytest.approx(0.0)
