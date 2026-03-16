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
6. ``spend_budget()`` raises ``sqlalchemy.exc.NoResultFound`` when the
   requested ``ledger_id`` does not exist.

These tests use ``sqlite+aiosqlite:///:memory:`` so they require no external
infrastructure.  Concurrency safety is covered by the integration tests which
use real PostgreSQL + ``SELECT FOR UPDATE`` semantics.

CONSTITUTION Priority 3: TDD
CONSTITUTION Priority 4: 90%+ coverage
Task: P4-T4.4 — Privacy Accountant
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.exc import NoResultFound
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
    assert ledger.last_updated is not None


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
    assert tx.timestamp is not None


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
async def test_spend_budget_raises_no_result_found_for_missing_ledger(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget() raises NoResultFound when the ledger_id does not exist.

    Arrange: Empty database — no PrivacyLedger rows inserted.
    Act: Call spend_budget with ledger_id=9999 (non-existent).
    Assert: sqlalchemy.exc.NoResultFound is raised.
    """
    from synth_engine.modules.privacy.accountant import spend_budget

    async with get_async_session(async_engine) as s:
        with pytest.raises(NoResultFound):
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
