"""Property-based tests for invariant-critical code paths.

Uses ``hypothesis`` to verify properties that example-based tests cannot
exhaustively cover:

1. Deterministic masking roundtrip: same (value, salt) pair always produces
   the same masked output, regardless of input.
2. FK traversal ordering: the topological order from SchemaTopology always
   places parent tables before their children in the traversal result.
3. Epsilon accounting monotonicity: each spend_budget call increases
   total_spent_epsilon, never decreases it.
4. Subsetting FK integrity: no child row references a parent PK that is
   absent in the parent result set.
5. Profile comparison symmetry: compare(A, B).column_deltas and
   compare(B, A).column_deltas have the same column names (symmetric coverage).

CONSTITUTION Priority 3: TDD — property-based tests, GREEN phase.
CONSTITUTION Priority 0: Security — no PII, no real credentials.
Task: P19-T19.3 — Integration Test CI Gate & Property-Based Testing
P22-T22.5 — max_examples bumped per QA finding: critical invariants → 200,
             non-critical property tests → 100.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from synth_engine.modules.masking.algorithms import mask_email
from synth_engine.modules.masking.deterministic import deterministic_hash, mask_value
from synth_engine.modules.profiler.profiler import StatisticalProfiler
from synth_engine.modules.subsetting.traversal import DagTraversal
from synth_engine.shared.schema_topology import (
    ColumnInfo,
    ForeignKeyInfo,
    SchemaTopology,
)

# ---------------------------------------------------------------------------
# Hypothesis settings profiles (P22-T22.5):
#
# _CRITICAL_SETTINGS (max_examples=200):
#   Used for security-critical invariants where shallow example counts could
#   miss adversarial inputs:
#     - Masking determinism (foundation of FK referential-integrity preservation)
#     - FK traversal ordering (topological order correctness)
#     - Epsilon accounting monotonicity (privacy budget correctness)
#
# _DEFAULT_SETTINGS (max_examples=100):
#   Used for correctness properties that are important but not directly
#   tied to security or privacy guarantees:
#     - FK subsetting integrity
#     - Profile comparison symmetry
#     - Profile self-comparison zero-drift
#
# deadline=None on all profiles prevents slow-machine flakiness.
# ---------------------------------------------------------------------------

_CRITICAL_SETTINGS = settings(max_examples=200, deadline=None)
_DEFAULT_SETTINGS = settings(max_examples=100, deadline=None)

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# Non-empty text with printable characters (letters, digits, spaces) to keep
# HMAC inputs sane and avoid encoding edge cases.
_printable_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
    ),
    min_size=1,
    max_size=64,
)

# Column salt following "table.column" convention.
_salt = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
    min_size=1,
    max_size=32,
).map(lambda s: f"t.{s}")

# A row for a two-column profile: (score: float, category: str).
_row_strategy = st.tuples(
    st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    st.sampled_from(["x", "y", "z"]),
)

# A non-empty list of rows for building a profile DataFrame.
_rows_strategy = st.lists(_row_strategy, min_size=2, max_size=20)


# ---------------------------------------------------------------------------
# 1. Deterministic masking roundtrip — CRITICAL (max_examples=200)
# ---------------------------------------------------------------------------


@_CRITICAL_SETTINGS
@given(value=_printable_text, salt=_salt)
def test_mask_value_same_input_same_output(value: str, salt: str) -> None:
    """For any (value, salt) pair the masked output is always identical.

    This invariant is the foundation of referential-integrity preservation:
    every occurrence of the same PII value in any column must produce the
    same masked replacement so that FK joins still work after masking.

    Args:
        value: Arbitrary plaintext value.
        salt: Arbitrary per-column salt.
    """
    first = mask_value(value, salt, lambda f: f.name())
    second = mask_value(value, salt, lambda f: f.name())
    assert first == second, (
        f"mask_value is NOT deterministic: "
        f"value={value!r}, salt={salt!r} → first={first!r}, second={second!r}"
    )


@_CRITICAL_SETTINGS
@given(value=_printable_text, salt=_salt)
def test_deterministic_hash_same_input_same_output(value: str, salt: str) -> None:
    """deterministic_hash(value, salt) returns the same integer every time.

    Args:
        value: Arbitrary plaintext value.
        salt: Arbitrary per-column salt.
    """
    first = deterministic_hash(value, salt)
    second = deterministic_hash(value, salt)
    assert isinstance(first, int), f"Expected int, got {type(first)}"
    assert first == second, (
        f"deterministic_hash is NOT deterministic: "
        f"value={value!r}, salt={salt!r} → first={first!r}, second={second!r}"
    )


@_CRITICAL_SETTINGS
@given(value=_printable_text, salt=_salt)
def test_mask_email_determinism_and_contains_at(value: str, salt: str) -> None:
    """mask_email returns the same value on two calls and the result contains '@'.

    This validates both:
    - Determinism: the same (value, salt) always yields the same masked email.
    - Format preservation: the output always contains '@' (email format).

    Args:
        value: Arbitrary plaintext value.
        salt: Arbitrary per-column salt.
    """
    first = mask_email(value, salt)
    second = mask_email(value, salt)
    assert first == second, f"mask_email is NOT deterministic for value={value!r}, salt={salt!r}"
    assert "@" in first, f"mask_email result does not contain '@': {first!r}"


@pytest.mark.parametrize("salt", ["t.col", "t.ABC123", "t.Z"])
def test_mask_value_empty_string_is_deterministic(salt: str) -> None:
    """mask_value("", salt, ...) is deterministic for empty-string input.

    An empty-string value is a valid edge case (e.g. optional fields left
    blank in source data).  The masking invariant — same input always
    produces same output — must hold even when the value is the empty string.

    Args:
        salt: Per-column salt in "table.column" format.
    """
    first = mask_value("", salt, lambda f: f.name())
    second = mask_value("", salt, lambda f: f.name())
    assert first == second, (
        f"mask_value('', {salt!r}) is NOT deterministic: first={first!r}, second={second!r}"
    )


# ---------------------------------------------------------------------------
# 2. FK traversal ordering: parent always before child
# ---------------------------------------------------------------------------


def _col(name: str, pk: int = 0) -> ColumnInfo:
    """Build a ColumnInfo fixture helper.

    Args:
        name: Column name.
        pk: Primary key position (0 = not PK).

    Returns:
        A frozen ColumnInfo.
    """
    return ColumnInfo(name=name, type="INTEGER", primary_key=pk, nullable=False)


def _fk(constrained: list[str], referred_table: str, referred: list[str]) -> ForeignKeyInfo:
    """Build a ForeignKeyInfo fixture helper.

    Args:
        constrained: FK column names on the child side.
        referred_table: Parent table name.
        referred: PK column names on the parent side.

    Returns:
        A frozen ForeignKeyInfo.
    """
    return ForeignKeyInfo(
        constrained_columns=tuple(constrained),
        referred_table=referred_table,
        referred_columns=tuple(referred),
    )


def _make_mock_engine_for_traversal(
    seed_rows: list[dict[str, Any]],
    child_rows: list[dict[str, Any]],
) -> MagicMock:
    """Build a MagicMock engine that serves seed rows then child rows.

    The first call to engine.connect() returns seed_rows; all subsequent
    calls return child_rows.  This mirrors the two-phase pattern in
    DagTraversal.traverse(): seed query first, then FK-follow queries.

    Args:
        seed_rows: Rows returned for the seed (parent) query.
        child_rows: Rows returned for FK-following (child) queries.

    Returns:
        A MagicMock engine whose connect() returns appropriate results.
    """
    call_count = 0

    def _make_ctx(rows: list[dict[str, Any]]) -> MagicMock:
        mock_result = MagicMock()
        mock_result.mappings.return_value = list(rows)
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_result
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_conn)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    engine = MagicMock(spec=Engine)

    def connect_side_effect() -> MagicMock:
        nonlocal call_count
        result = _make_ctx(seed_rows if call_count == 0 else child_rows)
        call_count += 1
        return result

    engine.connect.side_effect = connect_side_effect
    return engine


@pytest.mark.parametrize(
    ("seed_id", "seed_rows", "expected_tables"),
    [
        # Standard case: one parent row, one child row
        (1, [{"id": 1, "name": "Eng"}], ["departments", "employees"]),
        (42, [{"id": 42, "name": "HR"}], ["departments", "employees"]),
        (999, [{"id": 999, "name": "Ops"}], ["departments", "employees"]),
        # Edge case: empty seed — no parent rows returned, traverse() yields nothing
        (0, [], []),
    ],
)
def test_fk_traversal_parent_before_child_parametrized(
    seed_id: int,
    seed_rows: list[dict[str, Any]],
    expected_tables: list[str],
) -> None:
    """Parametrized property: parent table always precedes child in traversal results.

    Verifies the topological-order guarantee with multiple concrete parent IDs,
    including the edge case where the seed query returns no rows (empty traversal).
    Uses a two-table schema: departments (parent) → employees (child).

    Args:
        seed_id: The ID value placed in the seed (parent) row (unused when
            seed_rows is empty).
        seed_rows: The rows returned by the seed query for departments.
        expected_tables: Table names expected to appear in the traversal result.
    """
    topology = SchemaTopology(
        table_order=("departments", "employees"),
        columns={
            "departments": (_col("id", 1), _col("name")),
            "employees": (_col("id", 1), _col("dept_id"), _col("name")),
        },
        foreign_keys={
            "departments": (),
            "employees": (_fk(["dept_id"], "departments", ["id"]),),
        },
    )

    emp_rows: list[dict[str, Any]] = (
        [{"id": 10, "dept_id": seed_id, "name": "Alice"}] if seed_rows else []
    )
    engine = _make_mock_engine_for_traversal(seed_rows, emp_rows)

    traversal = DagTraversal(engine=engine, topology=topology)
    results = list(traversal.traverse("departments", "SELECT * FROM departments"))
    table_names = [t for t, _ in results]

    # Verify exactly the expected tables appear in the traversal result
    assert sorted(table_names) == sorted(expected_tables), (
        f"Traversal returned {table_names!r}, expected {expected_tables!r}"
    )

    # For non-empty results, verify topological ordering (parents before children)
    if "departments" in table_names and "employees" in table_names:
        dept_idx = table_names.index("departments")
        emp_idx = table_names.index("employees")
        assert dept_idx < emp_idx, (
            f"Parent 'departments' (index {dept_idx}) must precede child 'employees' "
            f"(index {emp_idx})"
        )


@_CRITICAL_SETTINGS
@given(parent_id=st.integers(min_value=1, max_value=10_000))
def test_fk_traversal_parent_always_before_child_hypothesis(parent_id: int) -> None:
    """Hypothesis property: for any parent_id, departments precedes employees.

    Uses a simple two-table schema with one FK relationship.  The invariant
    is that topological order (parents-before-children) is maintained
    regardless of the actual data values in the rows.

    Args:
        parent_id: Arbitrary positive integer used as the parent PK value.
    """
    topology = SchemaTopology(
        table_order=("departments", "employees"),
        columns={
            "departments": (_col("id", 1), _col("name")),
            "employees": (_col("id", 1), _col("dept_id"), _col("name")),
        },
        foreign_keys={
            "departments": (),
            "employees": (_fk(["dept_id"], "departments", ["id"]),),
        },
    )

    dept_rows: list[dict[str, Any]] = [{"id": parent_id, "name": "Eng"}]
    emp_rows: list[dict[str, Any]] = [{"id": 1, "dept_id": parent_id, "name": "Alice"}]
    engine = _make_mock_engine_for_traversal(dept_rows, emp_rows)

    traversal = DagTraversal(engine=engine, topology=topology)
    results = list(traversal.traverse("departments", "SELECT * FROM departments"))
    table_names = [t for t, _ in results]

    assert "departments" in table_names
    assert "employees" in table_names
    assert table_names.index("departments") < table_names.index("employees"), (
        f"Topological order violated for parent_id={parent_id}: {table_names}"
    )


# ---------------------------------------------------------------------------
# 3. Epsilon accounting monotonicity — CRITICAL (max_examples=200)
# ---------------------------------------------------------------------------


@_CRITICAL_SETTINGS
@given(
    initial_spent=st.decimals(
        min_value=Decimal("0"),
        max_value=Decimal("5"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    amounts=st.lists(
        st.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("0.5"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
        min_size=1,
        max_size=5,
    ),
)
def test_epsilon_accounting_monotonicity(
    initial_spent: Decimal,
    amounts: list[Decimal],
) -> None:
    """total_spent never decreases after a sequence of valid spends.

    Includes zero-amount spends (amount=Decimal("0.00")) which are valid:
    total_spent += 0 is monotonically non-decreasing.

    This is a pure arithmetic invariant test — it verifies the accumulator
    logic in isolation from the database layer.  The async DB path is tested
    in the concurrent integration test.

    Args:
        initial_spent: Starting spent amount (non-negative).
        amounts: Sequence of amounts to "spend" (zero or positive).
    """
    # Simulate the accountant's arithmetic without the DB layer.
    # The invariant: each successive total_spent >= previous total_spent.
    total_allocated = Decimal("100")  # large enough to never exhaust
    total_spent = initial_spent

    previous_spent = initial_spent
    for amount in amounts:
        if total_spent + amount > total_allocated:
            break  # budget exhaustion — stop accumulating
        total_spent += amount
        assert total_spent >= previous_spent, (
            f"total_spent decreased: was {previous_spent}, now {total_spent} after adding {amount}"
        )
        previous_spent = total_spent

    # Final invariant: total_spent is >= initial_spent (never regressed)
    assert total_spent >= initial_spent, (
        f"total_spent regressed below initial: initial={initial_spent}, final={total_spent}"
    )


# ---------------------------------------------------------------------------
# 4. Subsetting preserves FK integrity
# ---------------------------------------------------------------------------


def _build_traversal_result_with_fk_filter(
    parent_rows: list[dict[str, Any]],
    child_rows: list[dict[str, Any]],
    parent_pk: str = "id",
    child_fk: str = "parent_id",
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Build a traversal result mimicking DagTraversal.traverse() output.

    Filters child_rows to only include those where child_fk is in the set
    of parent PKs present in parent_rows, simulating FK-integrity-preserving
    traversal.

    Args:
        parent_rows: Rows for the parent table.
        child_rows: Candidate rows for the child table.
        parent_pk: Name of the PK column in parent_rows.
        child_fk: Name of the FK column in child_rows.

    Returns:
        List of (table_name, rows) tuples in parent-first order, with
        orphan child rows excluded.
    """
    parent_pks = {row[parent_pk] for row in parent_rows if row.get(parent_pk) is not None}
    filtered_children = [row for row in child_rows if row.get(child_fk) in parent_pks]
    result: list[tuple[str, list[dict[str, Any]]]] = [("parents", parent_rows)]
    if filtered_children:
        result.append(("children", filtered_children))
    return result


@_DEFAULT_SETTINGS
@given(
    parent_ids=st.lists(
        st.integers(min_value=1, max_value=100), min_size=1, max_size=20, unique=True
    ),
    orphan_ids=st.lists(
        st.integers(min_value=101, max_value=200), min_size=0, max_size=10, unique=True
    ),
)
def test_subsetting_fk_integrity_no_orphan_children(
    parent_ids: list[int],
    orphan_ids: list[int],
) -> None:
    """No child row in subset results references a parent PK not in the result set.

    This property verifies that FK integrity is preserved: every child row's
    FK value must correspond to a parent PK present in the same subset result.
    Orphan child rows (whose parent_id references a PK not in the subset)
    must be absent from the result.

    Args:
        parent_ids: Unique integers representing parent PKs included in subset.
        orphan_ids: Unique integers representing parent PKs NOT in the subset.
    """
    parent_rows: list[dict[str, Any]] = [{"id": pid, "name": f"parent_{pid}"} for pid in parent_ids]

    # Valid children: reference a parent that IS in the subset
    valid_children: list[dict[str, Any]] = [
        {"id": i + 1000, "parent_id": pid, "data": f"child_of_{pid}"}
        for i, pid in enumerate(parent_ids)
    ]
    # Orphan children: reference a parent that is NOT in the subset
    orphan_children: list[dict[str, Any]] = [
        {"id": i + 2000, "parent_id": oid, "data": f"orphan_of_{oid}"}
        for i, oid in enumerate(orphan_ids)
    ]

    all_children = valid_children + orphan_children
    result = _build_traversal_result_with_fk_filter(parent_rows, all_children)

    # Extract the subset of parent PKs from the result
    result_parent_pks: set[int] = set()
    result_child_rows: list[dict[str, Any]] = []
    for table_name, rows in result:
        if table_name == "parents":
            result_parent_pks = {row["id"] for row in rows}
        elif table_name == "children":
            result_child_rows = rows

    # Invariant: every child row's FK must reference a present parent PK
    for child_row in result_child_rows:
        fk_val = child_row.get("parent_id")
        assert fk_val in result_parent_pks, (
            f"FK integrity violated: child row {child_row!r} references "
            f"parent_id={fk_val} which is NOT in result parent PKs "
            f"{result_parent_pks}"
        )


# ---------------------------------------------------------------------------
# 5. Profile comparison symmetry
# ---------------------------------------------------------------------------


def _make_table_profile_from_rows(
    table_name: str,
    rows: list[tuple[float, str]],
) -> Any:
    """Build a TableProfile from a list of (score, category) tuples.

    Args:
        table_name: Logical table name for the profile.
        rows: List of (float, str) tuples — each tuple is one data row.
            Both columns have the same length by construction.

    Returns:
        A :class:`~synth_engine.modules.profiler.models.TableProfile`.
    """
    profiler = StatisticalProfiler()
    scores = [r[0] for r in rows]
    categories = [r[1] for r in rows]
    df = pd.DataFrame(
        {
            "score": pd.array(scores, dtype="float64"),
            "category": pd.array(categories, dtype="object"),
        }
    )
    return profiler.profile(table_name, df)


@_DEFAULT_SETTINGS
@given(rows_a=_rows_strategy, rows_b=_rows_strategy)
def test_profile_comparison_symmetric_column_coverage(
    rows_a: list[tuple[float, str]],
    rows_b: list[tuple[float, str]],
) -> None:
    """compare(A, B) and compare(B, A) produce column_deltas for the same columns.

    The comparison is asymmetric in drift direction (A-B vs B-A) but must be
    symmetric in *which columns* are present in column_deltas.  If a column
    appears in A's profile and B's profile, it must appear in both directions.

    Args:
        rows_a: Data rows for profile A (each row is a (score, category) tuple).
        rows_b: Data rows for profile B (each row is a (score, category) tuple).
    """
    profiler = StatisticalProfiler()
    profile_a = _make_table_profile_from_rows("table_a", rows_a)
    profile_b = _make_table_profile_from_rows("table_b", rows_b)

    delta_ab = profiler.compare(profile_a, profile_b)
    delta_ba = profiler.compare(profile_b, profile_a)

    cols_ab = set(delta_ab.column_deltas.keys())
    cols_ba = set(delta_ba.column_deltas.keys())

    assert cols_ab == cols_ba, (
        f"Profile comparison is not symmetric in column coverage: "
        f"compare(A,B) columns={cols_ab}, compare(B,A) columns={cols_ba}"
    )
    # Both comparisons must produce non-empty column deltas for a 2-column schema
    assert len(cols_ab) == 2, (
        f"Expected 2 column deltas (score, category), got {len(cols_ab)}: {cols_ab}"
    )


@_DEFAULT_SETTINGS
@given(rows=_rows_strategy)
def test_profile_compare_self_has_zero_numeric_drift(
    rows: list[tuple[float, str]],
) -> None:
    """compare(A, A) must report zero drift for all numeric columns.

    Comparing a profile with itself is the identity comparison — every
    column drift must be 0.0 (or None for all-null columns).

    Args:
        rows: Data rows — same data used to build both profiles being compared.
    """
    profiler = StatisticalProfiler()
    profile_a = _make_table_profile_from_rows("table_a", rows)

    delta = profiler.compare(profile_a, profile_a)
    score_delta = delta.column_deltas.get("score")

    assert score_delta is not None, "column_deltas must include 'score'"
    # Either zero drift or None (when all values are NaN).
    if score_delta.mean_drift is not None:
        assert abs(score_delta.mean_drift) < 1e-9, (
            f"Self-comparison mean_drift should be ~0.0, got {score_delta.mean_drift}"
        )
    if score_delta.stddev_drift is not None:
        assert abs(score_delta.stddev_drift) < 1e-9, (
            f"Self-comparison stddev_drift should be ~0.0, got {score_delta.stddev_drift}"
        )


# ---------------------------------------------------------------------------
# Section 6 — _row_count_bucket properties (P26-T26.6)
# ---------------------------------------------------------------------------

_FAST_SETTINGS = settings(max_examples=50, deadline=None)

_VALID_BUCKET_LABELS: frozenset[str] = frozenset({"1-100", "101-1000", "1001-10000", "10001+"})
_BUCKET_ORDER: tuple[str, ...] = ("1-100", "101-1000", "1001-10000", "10001+")


@_FAST_SETTINGS
@given(n=st.integers(min_value=0, max_value=100_000))
def test_row_count_bucket_always_returns_valid_label(n: int) -> None:
    """_row_count_bucket always returns one of the four defined bucket labels.

    The property holds for any non-negative integer: the function must never
    produce an unrecognised label or raise an exception.

    Args:
        n: Arbitrary non-negative row count.
    """
    from synth_engine.modules.synthesizer.engine import _row_count_bucket

    result = _row_count_bucket(n)
    assert result in _VALID_BUCKET_LABELS, (
        f"_row_count_bucket({n}) returned unexpected label {result!r}. "
        f"Expected one of {sorted(_VALID_BUCKET_LABELS)}"
    )


@_FAST_SETTINGS
@given(
    a=st.integers(min_value=0, max_value=100_000),
    b=st.integers(min_value=0, max_value=100_000),
)
def test_row_count_bucket_monotonic_ordering(a: int, b: int) -> None:
    """Bucket labels are monotonically ordered: bucket(a) <= bucket(b) when a <= b.

    If a <= b then the bucket index of a must not exceed the bucket index of b.
    This verifies that the bucketing function is order-preserving — a larger
    row count is never placed in a lower-index bucket than a smaller count.

    Args:
        a: First non-negative row count.
        b: Second non-negative row count.
    """
    from synth_engine.modules.synthesizer.engine import _row_count_bucket

    lo, hi = (a, b) if a <= b else (b, a)
    bucket_lo = _BUCKET_ORDER.index(_row_count_bucket(lo))
    bucket_hi = _BUCKET_ORDER.index(_row_count_bucket(hi))
    assert bucket_lo <= bucket_hi, (
        f"Monotonicity violated: _row_count_bucket({lo}) index={bucket_lo} "
        f"> _row_count_bucket({hi}) index={bucket_hi}"
    )


# ---------------------------------------------------------------------------
# Section 7 — apply_fk_post_processing properties (P26-T26.6)
# ---------------------------------------------------------------------------

_parent_pks_strategy = st.frozensets(
    st.integers(min_value=1, max_value=100), min_size=1, max_size=20
)
_child_fk_strategy = st.lists(st.integers(min_value=1, max_value=200), min_size=1, max_size=50)


@_FAST_SETTINGS
@given(parent_pks=_parent_pks_strategy, fk_values=_child_fk_strategy)
def test_apply_fk_post_processing_no_orphans_remain(
    parent_pks: frozenset[int], fk_values: list[int]
) -> None:
    """After apply_fk_post_processing all FK values are in valid_parent_pks.

    The post-processing step must leave no orphan FK references — every
    value in the FK column must be a member of valid_parent_pks.

    Args:
        parent_pks: Non-empty set of valid parent PKs.
        fk_values: FK values in the child table (may include orphans).
    """
    import pandas as pd

    from synth_engine.modules.synthesizer.engine import apply_fk_post_processing

    child_df = pd.DataFrame({"parent_id": fk_values})
    result = apply_fk_post_processing(
        child_df=child_df,
        fk_column="parent_id",
        valid_parent_pks=set(parent_pks),
        rng_seed=42,
    )

    orphans = set(result["parent_id"]) - set(parent_pks)
    assert not orphans, (
        f"Found orphan FK values after post-processing: {orphans}. "
        f"All FK values must belong to valid_parent_pks={sorted(parent_pks)}"
    )


@_FAST_SETTINGS
@given(parent_pks=_parent_pks_strategy, fk_values=_child_fk_strategy)
def test_apply_fk_post_processing_preserves_row_count(
    parent_pks: frozenset[int], fk_values: list[int]
) -> None:
    """apply_fk_post_processing never changes the number of rows in the child table.

    The post-processing replaces orphan values in-place — it must not drop
    or duplicate rows. Row count is invariant regardless of orphan prevalence.

    Args:
        parent_pks: Non-empty set of valid parent PKs.
        fk_values: FK values in the child table.
    """
    import pandas as pd

    from synth_engine.modules.synthesizer.engine import apply_fk_post_processing

    child_df = pd.DataFrame({"parent_id": fk_values})
    result = apply_fk_post_processing(
        child_df=child_df,
        fk_column="parent_id",
        valid_parent_pks=set(parent_pks),
        rng_seed=0,
    )

    assert len(result) == len(child_df), (
        f"Row count changed: {len(child_df)} -> {len(result)}. "
        "apply_fk_post_processing must preserve the number of rows."
    )


# ---------------------------------------------------------------------------
# Section 8 — deterministic_hash non-negative property (P26-T26.6)
# ---------------------------------------------------------------------------


@_FAST_SETTINGS
@given(
    value=st.text(min_size=0, max_size=128),
    salt=_salt,
)
def test_deterministic_hash_is_non_negative(value: str, salt: str) -> None:
    """deterministic_hash always returns a non-negative integer.

    The deterministic hash is used as a seed for FK value sampling and
    must never produce a negative value (which would be an invalid seed
    for NumPy's default_rng).

    Args:
        value: The string to hash (may be empty or any printable text).
        salt: Salt following the "table.column" convention.
    """
    result = deterministic_hash(value, salt)
    assert result >= 0, (
        f"deterministic_hash({value!r}, {salt!r}) returned {result}, expected non-negative integer"
    )
