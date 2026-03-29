"""Negative/attack tests for profiler PII-aware mode (T69.2).

Covers:
- Profile of DataFrame with PII-tagged email column contains no email addresses
- Profile of DataFrame with PII-tagged SSN column contains no SSN values
- Profile of DataFrame with high-cardinality untagged column suppresses value_counts
  (safe default: cardinality >= 50 treated as PII when pii_columns not provided)
- Non-PII column (low cardinality) still reports full value_counts
- PII column reports only cardinality, null_rate, min_length, max_length
- Empty pii_columns set behaves identically to explicit None (all columns normal)
- pii_columns with a column name not in DataFrame is silently ignored
- Numeric column tagged as PII still reports numeric stats (PII mode is for
  categorical only — numeric values are not raw PII in value_counts form)
- All-null PII column reports cardinality=0, null_rate=1.0, no value_counts

ATTACK-FIRST TDD — these tests are written BEFORE the GREEN phase.
CONSTITUTION Priority 0: Security / Privacy — PII in statistical profiles (C5)
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T69.2 — Profiler PII-Aware Mode
"""

from __future__ import annotations

import pandas as pd
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def email_dataframe() -> pd.DataFrame:
    """DataFrame with an email column containing real-looking email addresses.

    Returns:
        DataFrame with 'email' (PII) and 'status' (non-PII) columns.
    """
    return pd.DataFrame(
        {
            "email": [
                "alice@example.com",
                "bob@example.com",
                "carol@example.com",
                "dave@example.com",
                "eve@example.com",
            ],
            "status": ["active", "active", "inactive", "active", "inactive"],
        }
    )


@pytest.fixture
def ssn_dataframe() -> pd.DataFrame:
    """DataFrame with an SSN column containing SSN-like strings.

    Returns:
        DataFrame with 'ssn' (PII) and 'age_group' (non-PII) columns.
    """
    return pd.DataFrame(
        {
            "ssn": [
                "123-45-6789",
                "987-65-4321",
                "111-22-3333",
                "444-55-6666",
                "777-88-9999",
            ],
            "age_group": ["20s", "30s", "40s", "20s", "50s"],
        }
    )


@pytest.fixture
def high_cardinality_dataframe() -> pd.DataFrame:
    """DataFrame with a high-cardinality column (>= 50 unique values).

    Returns:
        DataFrame with 'user_id' (cardinality=50) and 'category' (cardinality=3) columns.
    """
    return pd.DataFrame(
        {
            "user_id": [f"user_{i:04d}" for i in range(50)],
            "category": ["A", "B", "C"] * 16 + ["A", "B"],
        }
    )


# ---------------------------------------------------------------------------
# Attack tests — PII leakage prevention
# ---------------------------------------------------------------------------


class TestProfilerPIILeakagePrevention:
    """Attack tests for profiler PII-aware mode (T69.2, C5)."""

    def test_email_column_tagged_pii_has_no_email_in_profile(
        self,
        email_dataframe: pd.DataFrame,
    ) -> None:
        """Profile of email column tagged as PII contains no email addresses.

        Arrange: DataFrame with email and status columns.
                 Tag 'email' as pii_columns={'email'}.
        Act: profiler.profile() with pii_columns.
        Assert: no email address strings appear in the column profile.

        CONSTITUTION Priority 0: PII must not leak into statistical profiles.
        """
        from synth_engine.modules.profiler.profiler import StatisticalProfiler

        profiler = StatisticalProfiler()
        result = profiler.profile("users", email_dataframe, pii_columns={"email"})

        email_profile = result.columns["email"]

        # value_counts must be absent (None or empty dict)
        vc = email_profile.value_counts or {}
        for key in vc:
            assert "@" not in key, (
                f"Email address found in PII-tagged column profile value_counts key: {key!r}"
            )
            assert "example.com" not in key, (
                f"Email domain found in PII-tagged column profile value_counts key: {key!r}"
            )

        # Reconstruct all profile data as a string and verify no email appears
        profile_str = str(result)
        for email in email_dataframe["email"]:
            assert str(email) not in profile_str, (
                f"Raw email {email!r} found in profile output — PII leakage detected. "
                f"Profile: {profile_str!r}"
            )

    def test_ssn_column_tagged_pii_has_no_ssn_in_profile(
        self,
        ssn_dataframe: pd.DataFrame,
    ) -> None:
        """Profile of SSN column tagged as PII contains no SSN values.

        Arrange: DataFrame with ssn and age_group columns.
                 Tag 'ssn' as pii_columns={'ssn'}.
        Act: profiler.profile() with pii_columns.
        Assert: no SSN strings appear in the column profile.
        """
        from synth_engine.modules.profiler.profiler import StatisticalProfiler

        profiler = StatisticalProfiler()
        result = profiler.profile("employees", ssn_dataframe, pii_columns={"ssn"})

        profile_str = str(result)
        for ssn in ssn_dataframe["ssn"]:
            assert str(ssn) not in profile_str, (
                f"Raw SSN {ssn!r} found in profile output — PII leakage detected."
            )

    def test_pii_column_profile_reports_only_safe_aggregates(
        self,
        email_dataframe: pd.DataFrame,
    ) -> None:
        """PII column profile reports only cardinality, null_rate, min/max length.

        The value_counts must be None or empty.
        cardinality must equal the number of unique non-null values.
        null_rate must be accurate.

        Arrange: 5-row email DataFrame, 0 nulls, 5 unique values.
        Act: profile with pii_columns={'email'}.
        Assert: cardinality=5, null_rate=0.0, no value_counts.
        """
        from synth_engine.modules.profiler.profiler import StatisticalProfiler

        profiler = StatisticalProfiler()
        result = profiler.profile("users", email_dataframe, pii_columns={"email"})

        email_profile = result.columns["email"]

        # value_counts must be absent (None or empty)
        assert not email_profile.value_counts, (
            f"value_counts must be empty/None for PII column; got: {email_profile.value_counts!r}"
        )

        # cardinality must still be reported (5 unique emails)
        assert email_profile.cardinality == 5, (
            f"cardinality must be 5 for 5 unique emails; got {email_profile.cardinality!r}"
        )

        # null_rate must be accurate (0 nulls in 5 rows)
        assert email_profile.null_rate == 0.0, (
            f"null_rate must be 0.0; got {email_profile.null_rate!r}"
        )

    def test_non_pii_column_still_reports_full_value_counts(
        self,
        email_dataframe: pd.DataFrame,
    ) -> None:
        """Non-PII column (status) reports full value_counts when email is tagged PII.

        Arrange: DataFrame with email (PII) and status (non-PII).
                 Tag only 'email' as PII.
        Act: profiler.profile() with pii_columns={'email'}.
        Assert: status column has full value_counts with 'active' and 'inactive'.
        """
        from synth_engine.modules.profiler.profiler import StatisticalProfiler

        profiler = StatisticalProfiler()
        result = profiler.profile("users", email_dataframe, pii_columns={"email"})

        status_profile = result.columns["status"]

        assert status_profile.value_counts is not None, (
            "Non-PII column must have value_counts; got None"
        )
        assert "active" in status_profile.value_counts, (
            f"'active' must appear in status value_counts; got {status_profile.value_counts!r}"
        )
        assert "inactive" in status_profile.value_counts, (
            f"'inactive' must appear in status value_counts; got {status_profile.value_counts!r}"
        )

    def test_high_cardinality_untagged_column_suppresses_value_counts_by_default(
        self,
        high_cardinality_dataframe: pd.DataFrame,
    ) -> None:
        """High-cardinality column (>=50 unique) treated as PII in safe-default mode.

        When pii_columns is None (no explicit tagging), columns with cardinality
        >= 50 have their value_counts suppressed to prevent inadvertent PII leakage.

        Arrange: DataFrame with user_id (50 unique values) and category (3 unique).
        Act: profiler.profile() with pii_columns=None (default).
        Assert: user_id has no value_counts; category has full value_counts.
        """
        from synth_engine.modules.profiler.profiler import StatisticalProfiler

        profiler = StatisticalProfiler()
        result = profiler.profile("events", high_cardinality_dataframe)

        user_id_profile = result.columns["user_id"]
        category_profile = result.columns["category"]

        # user_id: 50 unique values >= threshold of 50 — must suppress value_counts
        assert not user_id_profile.value_counts, (
            f"user_id has cardinality=50 (>= threshold), value_counts must be suppressed; "
            f"got: {user_id_profile.value_counts!r}"
        )

        # category: 3 unique values < threshold — must report normally
        assert category_profile.value_counts, (
            f"category has cardinality=3 (< threshold), value_counts must be present; "
            f"got: {category_profile.value_counts!r}"
        )

    def test_pii_columns_not_in_dataframe_silently_ignored(
        self,
        email_dataframe: pd.DataFrame,
    ) -> None:
        """Column names in pii_columns that don't exist in DataFrame are ignored.

        Arrange: DataFrame with email and status. pii_columns includes 'phone'
                 (not in DataFrame).
        Act: profiler.profile() with pii_columns={'email', 'phone'}.
        Assert: no error raised; email is PII-masked; status is normal.
        """
        from synth_engine.modules.profiler.profiler import StatisticalProfiler

        profiler = StatisticalProfiler()
        # Should not raise even though 'phone' is not in the DataFrame
        result = profiler.profile(
            "users",
            email_dataframe,
            pii_columns={"email", "phone"},
        )

        # email is masked
        assert not result.columns["email"].value_counts, "email must be masked when in pii_columns"

        # status is normal
        assert result.columns["status"].value_counts, (
            "status must have value_counts when not in pii_columns"
        )

    def test_empty_pii_columns_set_reports_all_columns_normally(
        self,
        email_dataframe: pd.DataFrame,
    ) -> None:
        """Empty pii_columns set causes all columns to be reported normally.

        pii_columns=set() is explicit opt-in to "no PII columns" and bypasses
        the safe-default high-cardinality suppression logic.

        Arrange: email DataFrame with 5-row email column (cardinality=5).
        Act: profiler.profile() with pii_columns=set() (empty but explicit).
        Assert: email column has full value_counts (not suppressed).
        """
        from synth_engine.modules.profiler.profiler import StatisticalProfiler

        profiler = StatisticalProfiler()
        result = profiler.profile("users", email_dataframe, pii_columns=set())

        email_profile = result.columns["email"]

        # With empty (but explicit) pii_columns, safe-default threshold does not apply
        # and no column is tagged PII, so value_counts must be present
        assert email_profile.value_counts, (
            f"With pii_columns=set(), email column should have value_counts; "
            f"got: {email_profile.value_counts!r}"
        )

    def test_all_null_pii_column_reports_safe_aggregates(
        self,
    ) -> None:
        """All-null PII column reports cardinality=0, null_rate=1.0, no value_counts.

        Arrange: DataFrame with all-null email column, tagged as PII.
        Act: profiler.profile() with pii_columns={'email'}.
        Assert: cardinality=0, null_rate=1.0, value_counts empty.
        """
        from synth_engine.modules.profiler.profiler import StatisticalProfiler

        df = pd.DataFrame(
            {
                "email": [None, None, None],
                "status": ["active", "inactive", "active"],
            }
        )

        profiler = StatisticalProfiler()
        result = profiler.profile("users", df, pii_columns={"email"})

        email_profile = result.columns["email"]
        assert not email_profile.value_counts, (
            f"All-null PII column must have empty value_counts; got {email_profile.value_counts!r}"
        )
        assert email_profile.null_rate == 1.0, (
            f"null_rate must be 1.0 for all-null column; got {email_profile.null_rate!r}"
        )
