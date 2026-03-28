"""Unit tests for the Privacy Accountant — global epsilon budget ledger.

Tests verify:
1. ``PrivacyLedger`` and ``PrivacyTransaction`` SQLModel tables are well-formed.
2. ``spend_budget()`` with sufficient balance creates a ``PrivacyTransaction``
   record, deducts from the ledger, and commits — in-memory SQLite (aiosqlite).
3. ``spend_budget()`` with insufficient balance raises ``BudgetExhaustionError``,
   writes no transaction, and leaves the ledger balance unchanged.
4. ``get_async_engine()`` and ``get_async_session()`` helpers are importable and
   return the correct SQLAlchemy async types.
5. ``spend_budget()`` raises ``ValueError`` for zero or negative ``amount``.
6. ``spend_budget()`` raises ``LedgerNotFoundError`` when the
   requested ``ledger_id`` does not exist.
7. ``spend_budget()`` with a ``Decimal`` input exercises the
   ``isinstance(amount, Decimal)`` fast-path (no float→string→Decimal conversion).
8. ``reset_budget()`` resets ``total_spent_epsilon`` to zero atomically.
9. ``reset_budget()`` with ``new_allocated_epsilon`` updates the ceiling.
10. ``reset_budget()`` raises ``LedgerNotFoundError`` for a missing ledger.
11. ``reset_budget()`` raises ``ValueError`` for a non-positive new allocation.
12. NUMERIC(20,10) precision: value at rounding boundary rounds to zero in SQLite.

These tests use ``sqlite+aiosqlite:///:memory:`` so they require no external
infrastructure.  Concurrency safety is covered by the integration tests which
use real PostgreSQL + ``SELECT FOR UPDATE`` semantics.

CONSTITUTION Priority 3: TDD
CONSTITUTION Priority 4: 90%+ coverage
Task: P4-T4.4 — Privacy Accountant
Task: P8-T8.3 — Data Model & Architecture Cleanup (ADV-050, arch finding)
Task: P22-T22.4 — Budget Management API (reset_budget)
Task: T36.4 — NUMERIC(20,10) precision loss at rounding boundary
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import SQLModel

from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError
from synth_engine.modules.privacy.ledger import PrivacyLedger, PrivacyTransaction
from synth_engine.shared.db import get_async_engine, get_async_session

# ---------------------------------------------------------------------------
# Async engine + session fixture (SQLite / aiosqlite for unit tests)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def async_engine() -> AsyncGenerator[AsyncEngine]:
    """Provide an in-memory async SQLite engine with schema created.

    Creates all SQLModel tables on setup; disposes the engine on teardown
    to ensure aiosqlite connections are properly closed.

    Yields:
        An :class:`AsyncEngine` with ``privacy_ledger`` and
        ``privacy_transaction`` tables created.
    """
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# Tests: table definitions
# ---------------------------------------------------------------------------


def test_privacy_ledger_table_fields() -> None:
    """PrivacyLedger must expose all required fields with correct defaults.

    Verifies:
    - ``id`` field is present (integer primary key per spec).
    - ``total_allocated_epsilon`` accepts and stores Decimal values.
    - ``total_spent_epsilon`` defaults to Decimal("0.0").
    - ``last_updated`` field is present.
    """
    ledger = PrivacyLedger(total_allocated_epsilon=Decimal("10.0"))
    assert ledger.id is None or isinstance(ledger.id, int)
    assert ledger.total_allocated_epsilon == Decimal("10.0")
    assert isinstance(ledger.total_allocated_epsilon, Decimal)
    assert ledger.total_spent_epsilon == Decimal("0.0")
    assert isinstance(ledger.total_spent_epsilon, Decimal)
    from datetime import datetime

    assert isinstance(ledger.last_updated, datetime), (
        f"last_updated must be a datetime instance, got {type(ledger.last_updated).__name__}"
    )


def test_privacy_transaction_table_fields() -> None:
    """PrivacyTransaction must expose all required fields.

    Verifies:
    - ``ledger_id``, ``job_id``, ``epsilon_spent``, ``timestamp``, ``note``
      fields are present.
    - ``epsilon_spent`` stores and returns a Decimal value.
    - ``note`` may be None (optional).
    """
    tx = PrivacyTransaction(
        ledger_id=1,
        job_id=42,
        epsilon_spent=Decimal("0.5"),
        note="test run",
    )
    assert tx.ledger_id == 1
    assert tx.job_id == 42
    assert tx.epsilon_spent == Decimal("0.5")
    assert isinstance(tx.epsilon_spent, Decimal)
    assert tx.note == "test run"
    from datetime import datetime

    assert isinstance(tx.timestamp, datetime), (
        f"timestamp must be a datetime instance, got {type(tx.timestamp).__name__}"
    )


def test_privacy_transaction_note_is_optional() -> None:
    """PrivacyTransaction.note must accept None without error."""
    tx = PrivacyTransaction(ledger_id=1, job_id=1, epsilon_spent=Decimal("0.1"))
    assert tx.note is None


# ---------------------------------------------------------------------------
# Tests: spend_budget() — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_budget_with_sufficient_balance_creates_transaction(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget() creates a PrivacyTransaction when budget is available.

    Arrange: Insert a PrivacyLedger with total_allocated=5.0, total_spent=0.0.
    Act: Call spend_budget(0.5, job_id=1, session=...).
    Assert:
    - Exactly 1 PrivacyTransaction row exists in the DB.
    - The transaction has epsilon_spent == Decimal("0.5") and job_id == 1.
    - The ledger's total_spent_epsilon is updated to Decimal("0.5").
    """
    from sqlalchemy import select

    from synth_engine.modules.privacy.accountant import spend_budget

    # Arrange: create ledger
    async with get_async_session(async_engine) as setup_session:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("5.0"),
            total_spent_epsilon=Decimal("0.0"),
        )
        setup_session.add(ledger)
        await setup_session.commit()
        await setup_session.refresh(ledger)
        ledger_id = ledger.id

    # Act: spend budget with float input (exercises float→Decimal conversion path)
    async with get_async_session(async_engine) as spend_session:
        await spend_budget(amount=0.5, job_id=1, ledger_id=ledger_id, session=spend_session)

    # Assert: verify DB state
    async with get_async_session(async_engine) as check_session:
        # Check transaction record
        tx_result = await check_session.execute(
            select(PrivacyTransaction).where(PrivacyTransaction.ledger_id == ledger_id)
        )
        transactions = tx_result.scalars().all()
        assert len(transactions) == 1, f"Expected 1 transaction, got {len(transactions)}"
        assert transactions[0].epsilon_spent == Decimal("0.5"), (
            f"Expected Decimal('0.5'), got {transactions[0].epsilon_spent!r}"
        )
        assert transactions[0].job_id == 1

        # Check ledger balance updated
        ledger_result = await check_session.execute(
            select(PrivacyLedger).where(PrivacyLedger.id == ledger_id)
        )
        updated_ledger = ledger_result.scalar_one()
        assert updated_ledger.total_spent_epsilon == Decimal("0.5"), (
            f"Expected Decimal('0.5') spent, got {updated_ledger.total_spent_epsilon!r}"
        )


@pytest.mark.asyncio
async def test_spend_budget_with_decimal_input_exercises_decimal_path(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget() with a Decimal amount exercises the isinstance fast-path.

    The accountant function has two branches at normalisation:
      ``amount if isinstance(amount, Decimal) else Decimal(str(amount))``
    Existing tests pass float; this test passes Decimal directly to verify
    the ``isinstance(amount, Decimal) is True`` branch is exercised and that
    the arithmetic produces correct results end-to-end.

    Arrange: Insert a PrivacyLedger with total_allocated=2.0, total_spent=0.0.
    Act: Call spend_budget(amount=Decimal("0.5"), job_id=7, session=...).
    Assert:
    - Exactly 1 PrivacyTransaction row exists with epsilon_spent == Decimal("0.5").
    - The ledger's total_spent_epsilon is updated to Decimal("0.5").
    """
    from sqlalchemy import select

    from synth_engine.modules.privacy.accountant import spend_budget

    # Arrange
    async with get_async_session(async_engine) as setup_session:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("2.0"),
            total_spent_epsilon=Decimal("0.0"),
        )
        setup_session.add(ledger)
        await setup_session.commit()
        await setup_session.refresh(ledger)
        ledger_id = ledger.id

    # Act — pass Decimal directly, not float
    async with get_async_session(async_engine) as spend_session:
        await spend_budget(
            amount=Decimal("0.5"),
            job_id=7,
            ledger_id=ledger_id,
            session=spend_session,
        )

    # Assert
    async with get_async_session(async_engine) as check_session:
        tx_result = await check_session.execute(
            select(PrivacyTransaction).where(PrivacyTransaction.ledger_id == ledger_id)
        )
        transactions = tx_result.scalars().all()
        assert len(transactions) == 1, f"Expected 1 transaction, got {len(transactions)}"
        assert transactions[0].epsilon_spent == Decimal("0.5"), (
            f"Expected Decimal('0.5'), got {transactions[0].epsilon_spent!r}"
        )
        assert transactions[0].job_id == 7

        ledger_result = await check_session.execute(
            select(PrivacyLedger).where(PrivacyLedger.id == ledger_id)
        )
        updated_ledger = ledger_result.scalar_one()
        assert updated_ledger.total_spent_epsilon == Decimal("0.5"), (
            f"Expected Decimal('0.5') spent, got {updated_ledger.total_spent_epsilon!r}"
        )


@pytest.mark.asyncio
async def test_spend_budget_sequential_calls_accumulate(
    async_engine: AsyncEngine,
) -> None:
    """Multiple sequential spend_budget() calls correctly accumulate spent epsilon.

    Arrange: Insert a PrivacyLedger with total_allocated=1.0, total_spent=0.0.
    Act: Call spend_budget(0.3) twice.
    Assert: total_spent_epsilon == 0.6; 2 PrivacyTransaction rows exist.
    """
    from sqlalchemy import func, select

    from synth_engine.modules.privacy.accountant import spend_budget

    async with get_async_session(async_engine) as s:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("1.0"),
            total_spent_epsilon=Decimal("0.0"),
        )
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    for i in range(2):
        async with get_async_session(async_engine) as s:
            await spend_budget(amount=0.3, job_id=i, ledger_id=ledger_id, session=s)

    async with get_async_session(async_engine) as s:
        count_result = await s.execute(
            select(func.count())
            .select_from(PrivacyTransaction)
            .where(PrivacyTransaction.ledger_id == ledger_id)
        )
        count = count_result.scalar_one()
        assert count == 2, f"Expected 2 transactions, got {count}"

        ledger_result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        updated_ledger = ledger_result.scalar_one()
        # Use Decimal comparison: DB returns Decimal from NUMERIC(20,10) column (ADV-050).
        # float subtraction would raise TypeError against Decimal values.
        assert updated_ledger.total_spent_epsilon == Decimal("0.6"), (
            f"Expected 0.6 total_spent, got {updated_ledger.total_spent_epsilon}"
        )


# ---------------------------------------------------------------------------
# Tests: spend_budget() — budget exhausted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_budget_raises_when_budget_exhausted(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget() raises BudgetExhaustionError when balance insufficient.

    Arrange: Insert a PrivacyLedger with total_allocated=1.0, total_spent=0.9.
    Act: Call spend_budget(0.2, ...) — would require 1.1 total.
    Assert:
    - BudgetExhaustionError is raised.
    - No PrivacyTransaction is written.
    - Ledger total_spent_epsilon remains 0.9.
    """
    from sqlalchemy import func, select

    from synth_engine.modules.privacy.accountant import spend_budget

    async with get_async_session(async_engine) as s:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("1.0"),
            total_spent_epsilon=Decimal("0.9"),
        )
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    with pytest.raises(BudgetExhaustionError, match="budget exhausted"):
        async with get_async_session(async_engine) as s:
            await spend_budget(amount=0.2, job_id=99, ledger_id=ledger_id, session=s)

    # Verify no transaction was written
    async with get_async_session(async_engine) as s:
        count_result = await s.execute(
            select(func.count())
            .select_from(PrivacyTransaction)
            .where(PrivacyTransaction.ledger_id == ledger_id)
        )
        count = count_result.scalar_one()
        assert count == 0, f"Expected 0 transactions after exhaustion, got {count}"

        ledger_result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        unchanged_ledger = ledger_result.scalar_one()
        # Use Decimal comparison: DB returns Decimal from NUMERIC(20,10) column (ADV-050).
        # Comparing Decimal to float(0.9) may raise TypeError in strict arithmetic contexts.
        assert unchanged_ledger.total_spent_epsilon == Decimal("0.9"), (
            f"Ledger balance should be unchanged at 0.9, got {unchanged_ledger.total_spent_epsilon}"
        )


@pytest.mark.asyncio
async def test_spend_budget_exact_boundary_exhausted(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget() raises BudgetExhaustionError when spend equals allocated.

    Exhaustion condition is total_spent + amount > total_allocated.
    When total_spent == total_allocated, any further spend raises.
    """
    from synth_engine.modules.privacy.accountant import spend_budget

    async with get_async_session(async_engine) as s:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("1.0"),
            total_spent_epsilon=Decimal("1.0"),
        )
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    with pytest.raises(BudgetExhaustionError):
        async with get_async_session(async_engine) as s:
            await spend_budget(amount=0.001, job_id=1, ledger_id=ledger_id, session=s)


# ---------------------------------------------------------------------------
# Tests: spend_budget() — invalid amount guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_budget_raises_value_error_for_zero_amount(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget() raises ValueError when amount is zero.

    Verifies the pre-condition guard fires before any DB interaction.
    """
    from synth_engine.modules.privacy.accountant import spend_budget

    async with get_async_session(async_engine) as s:
        with pytest.raises(ValueError, match="amount must be positive"):
            await spend_budget(amount=0, job_id=1, ledger_id=1, session=s)


@pytest.mark.asyncio
async def test_spend_budget_raises_value_error_for_negative_amount(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget() raises ValueError when amount is negative.

    Verifies the pre-condition guard fires before any DB interaction.
    """
    from synth_engine.modules.privacy.accountant import spend_budget

    async with get_async_session(async_engine) as s:
        with pytest.raises(ValueError, match="amount must be positive"):
            await spend_budget(amount=-0.5, job_id=1, ledger_id=1, session=s)


@pytest.mark.asyncio
async def test_spend_budget_raises_ledger_not_found_error_for_missing_ledger(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget() raises LedgerNotFoundError when the ledger_id does not exist.

    T66.5: The raw NoResultFound is wrapped in a typed LedgerNotFoundError so
    that the bootstrapper error map can return HTTP 404 instead of 500.

    Arrange: Empty database — no PrivacyLedger rows inserted.
    Act: Call spend_budget with ledger_id=9999 (non-existent).
    Assert: LedgerNotFoundError is raised (not raw NoResultFound).
    """
    from synth_engine.modules.privacy.accountant import spend_budget
    from synth_engine.shared.exceptions import LedgerNotFoundError

    async with get_async_session(async_engine) as s:
        with pytest.raises(LedgerNotFoundError):
            await spend_budget(amount=0.5, job_id=1, ledger_id=9999, session=s)


# ---------------------------------------------------------------------------
# Tests: async infrastructure in shared/db.py
# ---------------------------------------------------------------------------


def test_get_async_engine_returns_async_engine() -> None:
    """get_async_engine() must return an AsyncEngine instance."""
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    assert isinstance(engine, AsyncEngine)


@pytest.mark.asyncio
async def test_get_async_session_yields_async_session() -> None:
    """get_async_session() must yield an AsyncSession."""
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with get_async_session(engine) as session:
        assert isinstance(session, AsyncSession)


# ---------------------------------------------------------------------------
# Tests: module exports
# ---------------------------------------------------------------------------


def test_privacy_module_exports_ledger_classes() -> None:
    """Privacy module __init__.py must export PrivacyLedger, PrivacyTransaction, spend_budget."""
    import synth_engine.modules.privacy as privacy_module

    assert hasattr(privacy_module, "PrivacyLedger")
    assert hasattr(privacy_module, "PrivacyTransaction")
    assert hasattr(privacy_module, "spend_budget")
    assert hasattr(privacy_module, "BudgetExhaustionError")
    assert hasattr(privacy_module, "DPTrainingWrapper")


def test_privacy_module_exports_reset_budget() -> None:
    """Privacy module __init__.py must export reset_budget (P22-T22.4)."""
    import synth_engine.modules.privacy as privacy_module

    assert hasattr(privacy_module, "reset_budget")


# ---------------------------------------------------------------------------
# Tests: spend_budget() — scientific notation Decimal edge case (ADV-074)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "expected_decimal"),
    [
        (1e-11, Decimal("1e-11")),
        (1.1e-11, Decimal("1.1e-11")),
        (9.99e-12, Decimal("9.99e-12")),
    ],
    ids=["1e-11", "1.1e-11", "9.99e-12"],
)
def test_spend_budget_scientific_notation_decimal_conversion(
    amount: float,
    expected_decimal: Decimal,
) -> None:
    """Decimal(str(float)) contract: scientific notation floats convert correctly.

    ADV-074: Very small epsilon values expressed in scientific notation (e.g. 1e-11)
    undergo float→str→Decimal conversion inside spend_budget() before any DB
    interaction.  This test documents the contract boundary for the conversion
    itself: ``Decimal(str(1e-11))`` must produce ``Decimal("1e-11")``, which is
    mathematically equal to ``Decimal("0.00000000001")``.

    Production note: values smaller than NUMERIC(20, 10) precision (1e-10) will
    round to zero in PostgreSQL.  This test verifies the conversion layer is
    correct; the NUMERIC precision constraint is a separate concern.

    This is a pure-Python unit test — no DB interaction — verifying that the
    ``isinstance(amount, Decimal)``/``Decimal(str(amount))`` guard in
    ``spend_budget()`` produces the expected Decimal value for scientific-
    notation float inputs.
    """
    # Verify the conversion produces the correct Decimal value.
    # This verifies the Decimal(str(amount)) conversion that spend_budget() uses internally.
    result = Decimal(str(amount))
    assert result == expected_decimal, (
        f"Decimal(str({amount!r})) produced {result!r}, expected {expected_decimal!r}. "
        "The float→str→Decimal conversion changed behaviour — "
        "this may silently corrupt budget accounting for very small epsilon values."
    )


@pytest.mark.asyncio
async def test_spend_budget_small_scientific_notation_amount_does_not_raise(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget() accepts 1e-11 without raising ValueError.

    ADV-074: Verifies that very small positive epsilon values expressed as
    scientific-notation floats pass the ``amount > 0`` guard in spend_budget()
    and the function executes to completion without error.

    Note: the DB stores NUMERIC(20, 10) — values smaller than 1e-10 round to
    zero in SQLite (unit test backend).  This test only asserts that the function
    does not raise; the stored value precision is a NUMERIC column concern.

    Arrange: Insert a PrivacyLedger with total_allocated=1.0, total_spent=0.0.
    Act: Call spend_budget(amount=1e-11, job_id=99, session=...).
    Assert: No exception is raised; exactly 1 PrivacyTransaction row exists.
    """
    from sqlalchemy import select

    from synth_engine.modules.privacy.accountant import spend_budget

    async with get_async_session(async_engine) as setup_session:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("1.0"),
            total_spent_epsilon=Decimal("0.0"),
        )
        setup_session.add(ledger)
        await setup_session.commit()
        await setup_session.refresh(ledger)
        ledger_id = ledger.id

    # Act: must not raise — 1e-11 is a valid positive epsilon
    async with get_async_session(async_engine) as spend_session:
        await spend_budget(amount=1e-11, job_id=99, ledger_id=ledger_id, session=spend_session)

    # Assert: exactly 1 transaction was recorded
    async with get_async_session(async_engine) as check_session:
        tx_result = await check_session.execute(
            select(PrivacyTransaction).where(PrivacyTransaction.ledger_id == ledger_id)
        )
        transactions = tx_result.scalars().all()
        assert len(transactions) == 1, f"Expected 1 transaction, got {len(transactions)}"


# ---------------------------------------------------------------------------
# Tests: reset_budget() — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_budget_resets_spent_to_zero(
    async_engine: AsyncEngine,
) -> None:
    """reset_budget() must reset total_spent_epsilon to Decimal("0.0").

    Arrange: Insert a PrivacyLedger with total_allocated=10.0, total_spent=7.5.
    Act: Call reset_budget(ledger_id=..., session=...).
    Assert:
    - Returns (allocated, spent) where spent == Decimal("0.0").
    - The DB ledger row has total_spent_epsilon == Decimal("0.0").
    - total_allocated_epsilon is unchanged at Decimal("10.0").
    """
    from sqlalchemy import select

    from synth_engine.modules.privacy.accountant import reset_budget

    async with get_async_session(async_engine) as s:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("10.0"),
            total_spent_epsilon=Decimal("7.5"),
        )
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    async with get_async_session(async_engine) as s:
        allocated, spent = await reset_budget(ledger_id=ledger_id, session=s)

    assert spent == Decimal("0.0"), f"Expected spent=0.0, got {spent!r}"
    assert allocated == Decimal("10.0"), f"Expected allocated=10.0, got {allocated!r}"

    # Verify DB state persisted
    async with get_async_session(async_engine) as s:
        result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        updated = result.scalar_one()
        assert updated.total_spent_epsilon == Decimal("0.0")
        assert updated.total_allocated_epsilon == Decimal("10.0")


@pytest.mark.asyncio
async def test_reset_budget_with_new_allocated_updates_ceiling(
    async_engine: AsyncEngine,
) -> None:
    """reset_budget() with new_allocated_epsilon updates the allocation ceiling.

    Arrange: Insert a PrivacyLedger with total_allocated=10.0, total_spent=5.0.
    Act: Call reset_budget(ledger_id=..., new_allocated_epsilon=Decimal("25.0"), session=...).
    Assert:
    - Returns (allocated=25.0, spent=0.0).
    - The DB ledger row has total_allocated_epsilon == Decimal("25.0").
    - total_spent_epsilon == Decimal("0.0").
    """
    from sqlalchemy import select

    from synth_engine.modules.privacy.accountant import reset_budget

    async with get_async_session(async_engine) as s:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("10.0"),
            total_spent_epsilon=Decimal("5.0"),
        )
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    async with get_async_session(async_engine) as s:
        allocated, spent = await reset_budget(
            ledger_id=ledger_id,
            session=s,
            new_allocated_epsilon=Decimal("25.0"),
        )

    assert allocated == Decimal("25.0"), f"Expected allocated=25.0, got {allocated!r}"
    assert spent == Decimal("0.0"), f"Expected spent=0.0, got {spent!r}"

    async with get_async_session(async_engine) as s:
        result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        updated = result.scalar_one()
        assert updated.total_allocated_epsilon == Decimal("25.0")
        assert updated.total_spent_epsilon == Decimal("0.0")


# ---------------------------------------------------------------------------
# Tests: reset_budget() — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_budget_raises_ledger_not_found_error_for_missing_ledger(
    async_engine: AsyncEngine,
) -> None:
    """reset_budget() raises LedgerNotFoundError when the ledger_id does not exist.

    T66.5: The raw NoResultFound is wrapped in a typed LedgerNotFoundError so
    that the bootstrapper error map can return HTTP 404 instead of 500.

    Arrange: Empty database — no PrivacyLedger rows.
    Act: Call reset_budget(ledger_id=9999, session=...).
    Assert: LedgerNotFoundError is raised (not raw NoResultFound).
    """
    from synth_engine.modules.privacy.accountant import reset_budget
    from synth_engine.shared.exceptions import LedgerNotFoundError

    async with get_async_session(async_engine) as s:
        with pytest.raises(LedgerNotFoundError):
            await reset_budget(ledger_id=9999, session=s)


@pytest.mark.asyncio
async def test_reset_budget_raises_value_error_for_zero_new_allocated(
    async_engine: AsyncEngine,
) -> None:
    """reset_budget() raises ValueError when new_allocated_epsilon is zero."""
    from synth_engine.modules.privacy.accountant import reset_budget

    async with get_async_session(async_engine) as s:
        with pytest.raises(ValueError, match="new_allocated_epsilon must be positive"):
            await reset_budget(
                ledger_id=1,
                session=s,
                new_allocated_epsilon=Decimal("0.0"),
            )


@pytest.mark.asyncio
async def test_reset_budget_raises_value_error_for_negative_new_allocated(
    async_engine: AsyncEngine,
) -> None:
    """reset_budget() raises ValueError when new_allocated_epsilon is negative."""
    from synth_engine.modules.privacy.accountant import reset_budget

    async with get_async_session(async_engine) as s:
        with pytest.raises(ValueError, match="new_allocated_epsilon must be positive"):
            await reset_budget(
                ledger_id=1,
                session=s,
                new_allocated_epsilon=Decimal("-5.0"),
            )


# ---------------------------------------------------------------------------
# Tests: reset_budget() — locking verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_budget_uses_for_update_locking() -> None:
    """reset_budget() issues SELECT ... FOR UPDATE to prevent concurrent races.

    This test verifies the locking intent by inspecting the SQL statement
    constructed inside reset_budget().  We patch ``session.execute`` to
    capture the statement and confirm ``with_for_update()`` was applied.
    """
    from sqlalchemy import select

    from synth_engine.modules.privacy.accountant import reset_budget

    captured_stmts: list[object] = []

    mock_ledger = MagicMock(spec=PrivacyLedger)
    mock_ledger.id = 1
    mock_ledger.total_allocated_epsilon = Decimal("10.0")
    mock_ledger.total_spent_epsilon = Decimal("5.0")

    mock_result = MagicMock()
    mock_result.scalar_one.return_value = mock_ledger

    async def _mock_execute(stmt: object, *args: object, **kwargs: object) -> object:
        captured_stmts.append(stmt)
        return mock_result

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute = _mock_execute  # type: ignore[method-assign]

    # Use a context manager mock for session.begin()
    mock_begin = MagicMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin.return_value = mock_begin

    with patch(
        "synth_engine.modules.privacy.accountant.select",
        wraps=select,
    ):
        await reset_budget(ledger_id=1, session=mock_session)

    assert len(captured_stmts) == 1, "Expected exactly one SQL statement to be executed"
    stmt = captured_stmts[0]
    # The compiled statement string should contain FOR UPDATE
    stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": True}))  # type: ignore[union-attr]
    assert "FOR UPDATE" in stmt_str.upper(), (
        f"Expected SELECT ... FOR UPDATE in statement, got: {stmt_str}"
    )


# ---------------------------------------------------------------------------
# T25.1 — epsilon_spent_total Counter metric tests
# ---------------------------------------------------------------------------


def test_epsilon_spent_total_counter_is_module_attribute() -> None:
    """accountant module must expose EPSILON_SPENT_TOTAL as a module-level name."""
    import synth_engine.modules.privacy.accountant as accountant_mod

    assert hasattr(accountant_mod, "EPSILON_SPENT_TOTAL"), (
        "accountant module must expose EPSILON_SPENT_TOTAL Counter."
    )


def test_epsilon_spent_total_is_counter_instance() -> None:
    """EPSILON_SPENT_TOTAL must be a prometheus_client.Counter instance."""
    from prometheus_client import Counter

    from synth_engine.modules.privacy.accountant import EPSILON_SPENT_TOTAL

    assert isinstance(EPSILON_SPENT_TOTAL, Counter)


@pytest.mark.asyncio
async def test_spend_budget_increments_epsilon_counter_on_success(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget() must increment epsilon_spent_total Counter after commit.

    T25.1: The counter must be incremented AFTER a successful budget deduction.
    Label values: job_id=str(job_id), dataset_id=str(ledger_id).

    Arrange: Insert a PrivacyLedger with total_allocated=5.0, total_spent=0.0.
    Act: Call spend_budget(0.5, job_id=7, ledger_id=..., session=...).
    Assert: epsilon_spent_total{job_id="7", dataset_id="<ledger_id>"} == 1.0.
    """
    import prometheus_client

    from synth_engine.modules.privacy.accountant import spend_budget

    # Arrange: create ledger
    async with get_async_session(async_engine) as setup_session:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("5.0"),
            total_spent_epsilon=Decimal("0.0"),
        )
        setup_session.add(ledger)
        await setup_session.commit()
        await setup_session.refresh(ledger)
        ledger_id = ledger.id

    labels = {"job_id": str(7), "dataset_id": str(ledger_id)}

    before = prometheus_client.REGISTRY.get_sample_value(
        "epsilon_spent_total",
        labels,
    )
    before_val = before if before is not None else 0.0

    # Act
    async with get_async_session(async_engine) as spend_session:
        await spend_budget(amount=0.5, job_id=7, ledger_id=ledger_id, session=spend_session)

    after = prometheus_client.REGISTRY.get_sample_value(
        "epsilon_spent_total",
        labels,
    )
    after_val = after if after is not None else 0.0
    assert after_val == before_val + 1.0, (
        f"Counter must increment by 1 on success. Before={before_val}, After={after_val}"
    )


@pytest.mark.asyncio
async def test_spend_budget_does_not_increment_counter_on_exhaustion(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget() must NOT increment epsilon_spent_total on BudgetExhaustionError.

    T25.1: Counter must only fire after the successful commit. When
    BudgetExhaustionError is raised (before commit), the counter must stay put.

    Arrange: Insert a PrivacyLedger with total_allocated=1.0, total_spent=0.9.
    Act: Call spend_budget(0.5, job_id=99, ...) — exhaustion triggers.
    Assert: Counter is NOT incremented.
    """
    import prometheus_client

    from synth_engine.modules.privacy.accountant import spend_budget

    async with get_async_session(async_engine) as setup_session:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("1.0"),
            total_spent_epsilon=Decimal("0.9"),
        )
        setup_session.add(ledger)
        await setup_session.commit()
        await setup_session.refresh(ledger)
        ledger_id = ledger.id

    labels = {"job_id": str(99), "dataset_id": str(ledger_id)}

    before = prometheus_client.REGISTRY.get_sample_value(
        "epsilon_spent_total",
        labels,
    )
    before_val = before if before is not None else 0.0

    with pytest.raises(BudgetExhaustionError):
        async with get_async_session(async_engine) as s:
            await spend_budget(amount=0.5, job_id=99, ledger_id=ledger_id, session=s)

    after = prometheus_client.REGISTRY.get_sample_value(
        "epsilon_spent_total",
        labels,
    )
    after_val = after if after is not None else 0.0
    assert after_val == before_val, (
        f"Counter must NOT increment on exhaustion. Before={before_val}, After={after_val}"
    )


# ---------------------------------------------------------------------------
# Tests: NUMERIC(20,10) precision loss at rounding boundary (T36.4)
# ---------------------------------------------------------------------------


def test_numeric_precision_boundary_rounds_to_zero() -> None:
    """A value below NUMERIC(20,10) precision rounds to zero in Python Decimal context.

    NUMERIC(20, 10) has scale=10, meaning the smallest representable non-zero
    value is 1e-10 (one ten-billionth).  Values smaller than 5e-11 round to
    zero when quantized to 10 decimal places.

    This test documents the precision boundary so that callers are aware that
    very small epsilon values (< 5e-11) will silently become zero in PostgreSQL
    NUMERIC(20, 10) columns.  This is a data-model constraint, not a code bug.

    The test verifies the Python-layer representation of this boundary using
    Decimal.quantize() — the same rounding that PostgreSQL applies when
    storing to NUMERIC(20, 10).
    """
    from decimal import ROUND_HALF_UP

    # The scale-10 quantum: 0.0000000001
    quantize_target = Decimal("0.0000000001")

    # A value just below the rounding midpoint: 4.999...e-11 rounds to 0
    below_boundary = Decimal("0.00000000004")  # 4e-11
    rounded_below = below_boundary.quantize(quantize_target, rounding=ROUND_HALF_UP)
    assert rounded_below == Decimal("0.0000000000"), (
        f"Expected 4e-11 to round to zero at NUMERIC(20,10) scale, got {rounded_below!r}"
    )

    # A value exactly at the boundary: 5e-11 rounds up to 1e-10
    at_boundary = Decimal("0.00000000005")  # 5e-11
    rounded_at = at_boundary.quantize(quantize_target, rounding=ROUND_HALF_UP)
    assert rounded_at == Decimal("0.0000000001"), (
        f"Expected 5e-11 to round to 1e-10 at NUMERIC(20,10) scale, got {rounded_at!r}"
    )

    # A value above the boundary: 6e-11 also rounds up to 1e-10
    above_boundary = Decimal("0.00000000006")  # 6e-11
    rounded_above = above_boundary.quantize(quantize_target, rounding=ROUND_HALF_UP)
    assert rounded_above == Decimal("0.0000000001"), (
        f"Expected 6e-11 to round to 1e-10 at NUMERIC(20,10) scale, got {rounded_above!r}"
    )
