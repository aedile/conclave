"""Unit tests for boundary value conditions across engine modules.

Covers edge inputs that are valid-but-degenerate, invalid, or precision-critical:

1. Empty DataFrame passed to SynthesisEngine.train() — must raise, not silently return.
2. Single-row DataFrame — minimum viable training set (synthesizer engine must not crash).
3. Zero epsilon passed to spend_budget() — must raise ValueError.
4. Negative epsilon passed to spend_budget() — must raise ValueError.
5. Very large epsilon (1e9) — valid large-but-legal value; must be accepted.
6. Unicode/emoji in masking input columns — deterministic masking must handle UTF-8.
7. Maximum-length strings in FPE masking (max_length boundary enforcement).
8. Sub-scale Decimal passes spend_budget() positivity guard without raising.
9. Empty string as masking input — must return deterministic result.
10. Negative max_length in mask_name — must not raise; returns empty string.
11. check_budget() with zero allocated_epsilon — must raise ValueError.
12. check_budget() with negative allocated_epsilon — must raise ValueError.
13. Empty DataFrame guard in SynthesisEngine.train() must be in production code,
    not delegated to CTGANSynthesizer — guards against ordering-dependent flakiness.

These tests use only stdlib and production module imports — no external
infrastructure required.  Any test that cannot run without the synthesizer
dependency group is skipped automatically via a guard.

CONSTITUTION Priority 3: TDD
CONSTITUTION Priority 4: 95%+ test coverage
Task: T40.3 — Add Missing Test Categories: Boundary Values
Task: T55.6 — Flaky Test Resolution (empty-parquet guard moved into production code)
"""

from __future__ import annotations

import pathlib
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Boundary: empty DataFrame → SynthesisEngine.train()
# ---------------------------------------------------------------------------


def test_synthesis_engine_train_raises_on_empty_parquet(tmp_path: pathlib.Path) -> None:
    """SynthesisEngine.train() must raise ValueError for an empty Parquet file.

    An empty source DataFrame has zero rows.  Training a generative model on
    zero rows is meaningless.  This test asserts that the engine raises
    ValueError BEFORE reaching CTGANSynthesizer.fit(), ensuring the guard is
    in our production code and not delegated to SDV internals.

    Previously this test relied on CTGANSynthesizer.fit() raising internally,
    which made it ordering-dependent: if CTGANSynthesizer was patched by another
    test and the patch leaked, MagicMock.fit() would not raise, causing a
    false pass or a hang.  The fix (T55.6) moves the empty-DataFrame guard into
    SynthesisEngine.train() before the model is constructed.

    The test is skipped if the synthesizer group is not installed.
    """
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed")

    try:
        import pyarrow  # noqa: F401
    except ImportError:
        pytest.skip("pyarrow not installed (synthesizer group absent)")

    from synth_engine.modules.synthesizer.training.engine import CTGANSynthesizer

    if CTGANSynthesizer is None:
        pytest.skip("synthesizer group not installed")

    parquet_path = tmp_path / "empty.parquet"
    empty_df = pd.DataFrame({"col_a": pd.Series([], dtype="float64")})
    empty_df.to_parquet(str(parquet_path), engine="pyarrow")

    from synth_engine.modules.synthesizer.training.engine import SynthesisEngine

    engine = SynthesisEngine(epochs=1)

    with pytest.raises(ValueError, match="fit dataframe is empty"):
        engine.train("empty_table", str(parquet_path))


def test_synthesis_engine_train_empty_parquet_raises_even_with_mocked_ctgan(
    tmp_path: pathlib.Path,
) -> None:
    """Empty-parquet guard must fire before CTGANSynthesizer is constructed.

    This test exposes the ordering-dependent flakiness fixed in T55.6.  It
    patches CTGANSynthesizer with a MagicMock (simulating the state left by
    another test that patches but fails to clean up), then asserts that
    SynthesisEngine.train() still raises ValueError for an empty Parquet file.

    If the guard were delegated to CTGANSynthesizer.fit() (the pre-T55.6 design),
    a mocked CTGANSynthesizer would silently succeed and this test would fail.
    The production code must raise before the model is constructed.

    The test is skipped if pyarrow is not installed.
    """
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed")

    try:
        import pyarrow  # noqa: F401
    except ImportError:
        pytest.skip("pyarrow not installed (synthesizer group absent)")

    parquet_path = tmp_path / "empty.parquet"
    empty_df = pd.DataFrame({"col_a": pd.Series([], dtype="float64")})
    empty_df.to_parquet(str(parquet_path), engine="pyarrow")

    mock_ctgan_cls = MagicMock()
    mock_ctgan_instance = MagicMock()
    mock_ctgan_cls.return_value = mock_ctgan_instance

    from synth_engine.modules.synthesizer.training.engine import SynthesisEngine

    engine = SynthesisEngine(epochs=1)

    with patch(
        "synth_engine.modules.synthesizer.training.engine.CTGANSynthesizer",
        mock_ctgan_cls,
    ):
        with pytest.raises(ValueError, match="fit dataframe is empty"):
            engine.train("empty_table", str(parquet_path))

    # CTGANSynthesizer must NOT have been constructed — the guard fires first.
    mock_ctgan_cls.assert_not_called()
    mock_ctgan_instance.fit.assert_not_called()


# ---------------------------------------------------------------------------
# Boundary: single-row DataFrame → SynthesisEngine (mock CTGANSynthesizer)
# ---------------------------------------------------------------------------


def test_synthesis_engine_train_single_row_does_not_crash_structurally(
    tmp_path: pathlib.Path,
) -> None:
    """SynthesisEngine.train() with a single-row DataFrame must not crash internally.

    A single-row DataFrame is the minimum viable input.  This test patches
    CTGANSynthesizer so training completes without GPU/SDV dependency while
    still exercising the engine's data-loading path for a 1-row DataFrame.
    """
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed")

    try:
        import pyarrow  # noqa: F401
    except ImportError:
        pytest.skip("pyarrow not installed")

    from synth_engine.modules.synthesizer.training.engine import CTGANSynthesizer

    if CTGANSynthesizer is None:
        pytest.skip("synthesizer group not installed")

    single_row_df = pd.DataFrame({"age": [25], "income": [50000.0]})
    parquet_path = tmp_path / "single_row.parquet"
    single_row_df.to_parquet(str(parquet_path), engine="pyarrow")

    mock_model = MagicMock()
    mock_metadata = MagicMock()

    with (
        patch(
            "synth_engine.modules.synthesizer.training.engine.CTGANSynthesizer",
            return_value=mock_model,
        ),
        patch(
            "synth_engine.modules.synthesizer.training.engine._build_metadata",
            return_value=mock_metadata,
        ),
    ):
        from synth_engine.modules.synthesizer.training.engine import SynthesisEngine

        engine = SynthesisEngine(epochs=1)
        artifact = engine.train("single_row_table", str(parquet_path))

    assert artifact.table_name == "single_row_table"
    assert artifact.column_names == ["age", "income"]
    mock_model.fit.assert_called_once()


# ---------------------------------------------------------------------------
# Boundary: zero epsilon → spend_budget() must raise ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_budget_zero_epsilon_raises_value_error() -> None:
    """spend_budget() with amount=0 must raise ValueError.

    Zero epsilon is not a valid privacy allocation — it would mean spending
    nothing, which indicates a caller logic error.
    """
    from sqlmodel import SQLModel

    from synth_engine.modules.privacy.accountant import spend_budget
    from synth_engine.shared.db import get_async_engine, get_async_session

    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    try:
        async with get_async_session(engine) as session:
            with pytest.raises(ValueError, match="amount must be positive"):
                await spend_budget(
                    amount=Decimal("0"),
                    job_id=1,
                    ledger_id=1,
                    session=session,
                )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Boundary: negative epsilon → spend_budget() must raise ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_budget_negative_epsilon_raises_value_error() -> None:
    """spend_budget() with a negative amount must raise ValueError.

    Negative epsilon is physically meaningless and indicates a programming
    error in the caller.
    """
    from sqlmodel import SQLModel

    from synth_engine.modules.privacy.accountant import spend_budget
    from synth_engine.shared.db import get_async_engine, get_async_session

    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    try:
        async with get_async_session(engine) as session:
            with pytest.raises(ValueError, match="amount must be positive"):
                await spend_budget(
                    amount=Decimal("-0.5"),
                    job_id=1,
                    ledger_id=1,
                    session=session,
                )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Boundary: very large epsilon → spend_budget() must accept it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_budget_very_large_epsilon_accepted() -> None:
    """spend_budget() with a very large epsilon (1e9) is valid if within budget.

    Large-but-legal epsilon values should be accepted without error.  There
    is no upper bound enforcement in spend_budget() — the caller controls
    budget allocation.
    """
    from sqlalchemy import select as sa_select
    from sqlmodel import SQLModel

    from synth_engine.modules.privacy.accountant import spend_budget
    from synth_engine.modules.privacy.ledger import PrivacyLedger
    from synth_engine.shared.db import get_async_engine, get_async_session

    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    try:
        # Insert a ledger with a very large allocation
        async with get_async_session(engine) as setup_session:
            async with setup_session.begin():
                large_epsilon = Decimal("2000000000")  # 2e9 — larger than the spend
                ledger = PrivacyLedger(
                    total_allocated_epsilon=large_epsilon,
                    total_spent_epsilon=Decimal("0"),
                )
                setup_session.add(ledger)

        # Retrieve ledger id
        async with get_async_session(engine) as read_session:
            result = await read_session.execute(sa_select(PrivacyLedger))
            created_ledger = result.scalar_one()
            ledger_id = created_ledger.id

        # This must NOT raise — very large epsilon is valid
        async with get_async_session(engine) as spend_session:
            await spend_budget(
                amount=Decimal("1000000000"),  # 1e9
                job_id=42,
                ledger_id=ledger_id,
                session=spend_session,
            )
        # Verify: read back and check spent amount
        async with get_async_session(engine) as verify_session:
            verify_result = await verify_session.execute(sa_select(PrivacyLedger))
            updated_ledger = verify_result.scalar_one()
            assert updated_ledger.total_spent_epsilon >= Decimal("1000000000")
            assert updated_ledger.total_allocated_epsilon == Decimal("2000000000")
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Boundary: unicode / emoji in masking input
# ---------------------------------------------------------------------------


def test_mask_name_handles_unicode_emoji_input() -> None:
    """mask_name() must produce a deterministic result for unicode/emoji input.

    Emoji and multi-byte UTF-8 characters are valid string inputs.  The
    deterministic masking layer must encode them correctly in HMAC-SHA256
    and produce a stable output.
    """
    from synth_engine.modules.masking.algorithms import mask_name

    emoji_name = "Ren\u00e9 \U0001f600"  # "René 😀"
    salt = "users.full_name"

    result1 = mask_name(emoji_name, salt)
    result2 = mask_name(emoji_name, salt)

    assert isinstance(result1, str)
    assert len(result1) > 0
    assert result1 == result2, "Same unicode input must produce identical masked output"


def test_mask_email_handles_unicode_local_part() -> None:
    """mask_email() must handle unicode characters in the email local part.

    Unicode email addresses are RFC 6530-compliant.  The masking layer must
    not crash or produce an inconsistent result for such inputs.
    """
    from synth_engine.modules.masking.algorithms import mask_email

    unicode_email = "\u4e2d\u6587@example.com"  # "中文@example.com"
    salt = "users.email"

    result1 = mask_email(unicode_email, salt)
    result2 = mask_email(unicode_email, salt)

    assert isinstance(result1, str)
    assert result1 == result2, "Unicode email must produce identical masked output"


# ---------------------------------------------------------------------------
# Boundary: maximum-length strings in FPE masking
# ---------------------------------------------------------------------------


def test_mask_name_max_length_exactly_at_boundary() -> None:
    """mask_name() with max_length set to exactly the output length returns full output.

    When the masked output happens to be exactly max_length characters, no
    truncation should occur — the result must equal the un-truncated output.
    """
    from synth_engine.modules.masking.algorithms import mask_name

    value = "John Doe"
    salt = "people.name"

    full_output = mask_name(value, salt)
    at_boundary = mask_name(value, salt, max_length=len(full_output))

    assert at_boundary == full_output


def test_mask_name_max_length_one_shorter_truncates() -> None:
    """mask_name() with max_length one less than output length truncates by one char."""
    from synth_engine.modules.masking.algorithms import mask_name

    value = "Jane Smith"
    salt = "people.name"

    full_output = mask_name(value, salt)

    assert len(full_output) > 1, (
        "Precondition: mask_name output must exceed 1 character for truncation test"
    )
    truncated = mask_name(value, salt, max_length=len(full_output) - 1)
    assert truncated == full_output[: len(full_output) - 1]


def test_mask_name_max_length_zero_returns_empty_string() -> None:
    """mask_name() with max_length=0 returns an empty string."""
    from synth_engine.modules.masking.algorithms import mask_name

    result = mask_name("Some Name", "table.col", max_length=0)
    assert result == ""


# ---------------------------------------------------------------------------
# Boundary: sub-scale Decimal — passes spend_budget() positivity guard without raising
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_budget_sub_scale_decimal_does_not_raise() -> None:
    """spend_budget() accepts a sub-scale Decimal amount without raising ValueError.

    Decimal("1e-11") is positive and therefore passes the positivity guard in
    spend_budget().  This test verifies that a value smaller than the
    NUMERIC(20,10) scale boundary is accepted without error — the positivity
    check does not incorrectly reject sub-scale Decimal values.
    """
    from sqlalchemy import select as sa_select
    from sqlmodel import SQLModel

    from synth_engine.modules.privacy.accountant import spend_budget
    from synth_engine.modules.privacy.ledger import PrivacyLedger
    from synth_engine.shared.db import get_async_engine, get_async_session

    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    try:
        # Create a ledger with a moderate allocation
        async with get_async_session(engine) as setup_session:
            async with setup_session.begin():
                ledger = PrivacyLedger(
                    total_allocated_epsilon=Decimal("1.0"),
                    total_spent_epsilon=Decimal("0"),
                )
                setup_session.add(ledger)

        async with get_async_session(engine) as read_session:
            result = await read_session.execute(sa_select(PrivacyLedger))
            created = result.scalar_one()
            ledger_id = created.id

        # Decimal("1e-11") is positive so it passes the positivity gate.
        # The test asserts this call does not raise ValueError (precision boundary
        # does not violate the positive-amount guard).
        tiny_amount = Decimal("1e-11")
        assert tiny_amount > 0, "Precondition: the tiny amount is positive"

        async with get_async_session(engine) as spend_session:
            # Must not raise ValueError — the amount is positive
            await spend_budget(
                amount=tiny_amount,
                job_id=99,
                ledger_id=ledger_id,
                session=spend_session,
            )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Boundary: empty string as masking input
# ---------------------------------------------------------------------------


def test_mask_name_empty_string_is_deterministic() -> None:
    """mask_name() on an empty string must produce consistent output.

    An empty first/last name may appear in dirty production data.  The masking
    layer must handle it without crashing and produce a stable deterministic
    output.
    """
    from synth_engine.modules.masking.algorithms import mask_name

    result1 = mask_name("", "users.full_name")
    result2 = mask_name("", "users.full_name")

    assert isinstance(result1, str)
    assert result1 == result2


# ---------------------------------------------------------------------------
# Boundary: check_budget() with zero / negative allocated_epsilon
# ---------------------------------------------------------------------------


def test_dp_check_budget_zero_allocated_epsilon_raises_value_error() -> None:
    """DPTrainingWrapper.check_budget() raises ValueError for zero allocated_epsilon.

    A zero epsilon allocation is not a valid privacy budget.
    """
    from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

    wrapper = DPTrainingWrapper()
    wrapper._wrapped = True  # Simulate post-wrap state
    wrapper._privacy_engine = MagicMock()
    wrapper._privacy_engine.get_epsilon.return_value = 0.5

    with pytest.raises(ValueError, match="allocated_epsilon must be positive"):
        wrapper.check_budget(allocated_epsilon=0.0, delta=1e-5)


def test_dp_check_budget_negative_allocated_epsilon_raises_value_error() -> None:
    """DPTrainingWrapper.check_budget() raises ValueError for negative allocated_epsilon."""
    from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

    wrapper = DPTrainingWrapper()
    wrapper._wrapped = True
    wrapper._privacy_engine = MagicMock()
    wrapper._privacy_engine.get_epsilon.return_value = 0.1

    with pytest.raises(ValueError, match="allocated_epsilon must be positive"):
        wrapper.check_budget(allocated_epsilon=-1.0, delta=1e-5)
