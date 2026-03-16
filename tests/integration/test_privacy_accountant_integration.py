"""Integration tests for the Privacy Accountant — concurrency and FOR UPDATE locking.

These tests require a real PostgreSQL database via pytest-postgresql.
``SELECT FOR UPDATE`` is silently ignored by SQLite — SQLite-based tests would
always pass but provide no correctness guarantee for the race-condition scenario.

The critical test fires 50 concurrent ``spend_budget()`` calls simultaneously
using ``asyncio.gather``.  Only 25 calls should succeed (25 × 0.2 = 5.0 = budget);
the remaining 25 must raise ``BudgetExhaustionError``.  The ledger's
``total_spent_epsilon`` must be exactly 5.0 — no overrun, no underrun.

Each call uses its own ``AsyncSession`` (its own transaction) to test real
concurrency. ``SELECT ... FOR UPDATE`` ensures only one transaction can hold
the lock at a time, serialising the budget deductions.

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH. If absent, all tests in this module are
  skipped automatically via the ``_require_postgresql`` autouse fixture.
- ``asyncpg`` installed: ``poetry install`` (main dependency group).

Marks: ``integration``

CONSTITUTION Priority 3: TDD — concurrency integration gate for P4-T4.4
CONSTITUTION Priority 0: Security — pessimistic locking prevents budget overrun
Task: P4-T4.4 — Privacy Accountant
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
import pytest_asyncio
from pytest_postgresql import factories
from pytest_postgresql.janitor import DatabaseJanitor
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from synth_engine.modules.privacy.accountant import spend_budget
from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError
from synth_engine.modules.privacy.ledger import PrivacyLedger, PrivacyTransaction
from synth_engine.shared.db import get_async_engine, get_async_session
from tests.conftest_types import PostgreSQLProc

# ---------------------------------------------------------------------------
# pytest-postgresql process fixture
# ---------------------------------------------------------------------------

postgresql_proc = factories.postgresql_proc()

# ---------------------------------------------------------------------------
# Skip guard — runs before all tests in this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _require_postgresql() -> None:
    """Skip entire module when pg_ctl is not installed.

    ``postgresql_proc`` from pytest-postgresql spawns a real PostgreSQL process
    using ``pg_ctl``.  If the binary is absent (e.g. developer laptops without
    a local PG installation), all tests would error rather than skip.

    In CI the PostgreSQL service is always present so the guard has no effect.
    """
    if shutil.which("pg_ctl") is None:
        pytest.skip(
            "pg_ctl not found on PATH — install PostgreSQL to run privacy accountant "
            "integration tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Async engine fixture backed by real PostgreSQL
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def pg_async_engine(
    postgresql_proc: PostgreSQLProc,
) -> AsyncGenerator[AsyncEngine]:
    """Provide an async SQLAlchemy engine connected to the ephemeral PostgreSQL instance.

    Creates the test database via ``DatabaseJanitor``, then creates all SQLModel
    tables, yields the engine, drops all tables, and disposes the engine.

    The ``postgresql_proc`` fixture only starts the server process — it does NOT
    create the test database.  ``DatabaseJanitor`` is responsible for
    ``CREATE DATABASE`` / ``DROP DATABASE`` lifecycle management.

    Args:
        postgresql_proc: The running pytest-postgresql process executor providing
            host, port, user, and password for the ephemeral PostgreSQL instance.

    Yields:
        An :class:`AsyncEngine` pointed at the ephemeral PostgreSQL database
        with the privacy tables created.
    """
    proc = postgresql_proc
    password = proc.password or ""
    db_url = f"postgresql+asyncpg://{proc.user}:{password}@{proc.host}:{proc.port}/{proc.dbname}"

    # DatabaseJanitor creates the test database (CREATE DATABASE) and drops it
    # on __exit__.  Without this, the database named in proc.dbname ("tests" by
    # default) does not exist and asyncpg raises InvalidCatalogNameError.
    with DatabaseJanitor(
        user=proc.user,
        host=proc.host,
        port=proc.port,
        dbname=proc.dbname,
        version=proc.version,
        password=password,
    ):
        engine = get_async_engine(db_url)

        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        yield engine

        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)

        await engine.dispose()


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_spend_budget_for_update_prevents_overrun(
    pg_async_engine: AsyncEngine,
) -> None:
    """50 concurrent spend_budget(0.2) calls on a 5.0 budget — exactly 25 succeed.

    This is the critical correctness test for pessimistic locking.

    Arrange: Create a PrivacyLedger with total_allocated_epsilon=5.0, total_spent=0.0.
    Act: Fire 50 simultaneous spend_budget(0.2) calls via asyncio.gather.
    Assert:
    - Exactly 25 calls succeed (25 × 0.2 = 5.0 = total budget).
    - Exactly 25 calls raise BudgetExhaustionError.
    - total_spent_epsilon in the DB is exactly 5.0 (no overrun, no underrun).
    - Exactly 25 PrivacyTransaction records exist in the DB.
    """
    # --- Arrange: create ledger ---
    async with get_async_session(pg_async_engine) as s:
        ledger = PrivacyLedger(total_allocated_epsilon=5.0, total_spent_epsilon=0.0)
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    # --- Act: fire 50 concurrent calls, each with its own session ---
    async def _attempt(job_id: int) -> str:
        """Try to spend 0.2 epsilon; return 'success' or 'exhausted'.

        Args:
            job_id: Unique integer identifier for this attempt.

        Returns:
            ``'success'`` if the budget was allocated, ``'exhausted'`` if
            :exc:`BudgetExhaustionError` was raised.
        """
        try:
            async with get_async_session(pg_async_engine) as s:
                await spend_budget(amount=0.2, job_id=job_id, ledger_id=ledger_id, session=s)
            return "success"
        except BudgetExhaustionError:
            return "exhausted"

    results = await asyncio.gather(*[_attempt(i) for i in range(50)])

    # --- Assert: counts ---
    success_count = results.count("success")
    exhausted_count = results.count("exhausted")

    assert success_count == 25, (
        f"Expected exactly 25 successful calls, got {success_count}. "
        f"Exhausted: {exhausted_count}. Results: {results}"
    )
    assert exhausted_count == 25, (
        f"Expected exactly 25 BudgetExhaustionError raises, got {exhausted_count}. "
        f"Success: {success_count}"
    )

    # --- Assert: DB state ---
    async with get_async_session(pg_async_engine) as s:
        ledger_result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        final_ledger = ledger_result.scalar_one()
        # PrivacyLedger stores epsilon as Decimal(20, 10) — use Decimal arithmetic
        # to avoid "TypeError: unsupported operand for -: 'decimal.Decimal' and 'float'".
        assert abs(final_ledger.total_spent_epsilon - Decimal("5.0")) < Decimal("1e-9"), (
            f"Expected total_spent_epsilon == 5.0, got {final_ledger.total_spent_epsilon}. "
            "Budget overrun or underrun detected — FOR UPDATE locking may not be working."
        )

        tx_count_result = await s.execute(
            select(func.count())
            .select_from(PrivacyTransaction)
            .where(PrivacyTransaction.ledger_id == ledger_id)
        )
        tx_count = tx_count_result.scalar_one()
        assert tx_count == 25, (
            f"Expected exactly 25 PrivacyTransaction records, got {tx_count}. "
            "Each successful spend must produce exactly one transaction."
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_spend_budget_postgresql_creates_transaction_record(
    pg_async_engine: AsyncEngine,
) -> None:
    """spend_budget() against real PostgreSQL creates a transaction record.

    Verifies the basic happy path works with the asyncpg driver.

    Arrange: Create a PrivacyLedger with total_allocated_epsilon=10.0.
    Act: Call spend_budget(1.0, job_id=99, ...).
    Assert: One PrivacyTransaction exists; ledger shows 1.0 spent.
    """
    async with get_async_session(pg_async_engine) as s:
        ledger = PrivacyLedger(total_allocated_epsilon=10.0, total_spent_epsilon=0.0)
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    async with get_async_session(pg_async_engine) as s:
        await spend_budget(amount=1.0, job_id=99, ledger_id=ledger_id, session=s)

    async with get_async_session(pg_async_engine) as s:
        tx_result = await s.execute(
            select(PrivacyTransaction).where(PrivacyTransaction.ledger_id == ledger_id)
        )
        transactions = tx_result.scalars().all()
        assert len(transactions) == 1, f"Expected 1 transaction, got {len(transactions)}"
        # PrivacyTransaction stores epsilon as Decimal(20, 10); compare with Decimal.
        assert transactions[0].epsilon_spent == Decimal("1.0"), (
            f"Expected epsilon_spent == 1.0, got {transactions[0].epsilon_spent}"
        )
        assert transactions[0].job_id == 99

        ledger_result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        updated_ledger = ledger_result.scalar_one()
        assert updated_ledger.total_spent_epsilon == Decimal("1.0"), (
            f"Expected 1.0 spent, got {updated_ledger.total_spent_epsilon}"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_spend_budget_postgresql_raises_on_exhaustion(
    pg_async_engine: AsyncEngine,
) -> None:
    """spend_budget() raises BudgetExhaustionError on real PostgreSQL when exhausted.

    Arrange: Create a PrivacyLedger with total_allocated=1.0, total_spent=0.95.
    Act: Call spend_budget(0.1, ...) — would require 1.05 total.
    Assert: BudgetExhaustionError raised; ledger balance unchanged at 0.95.
    """
    async with get_async_session(pg_async_engine) as s:
        ledger = PrivacyLedger(total_allocated_epsilon=1.0, total_spent_epsilon=0.95)
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    with pytest.raises(BudgetExhaustionError):
        async with get_async_session(pg_async_engine) as s:
            await spend_budget(amount=0.1, job_id=1, ledger_id=ledger_id, session=s)

    async with get_async_session(pg_async_engine) as s:
        ledger_result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        unchanged_ledger = ledger_result.scalar_one()
        # PrivacyLedger stores epsilon as Decimal(20, 10); compare with Decimal.
        assert unchanged_ledger.total_spent_epsilon == Decimal("0.95"), (
            f"Ledger balance should be 0.95, got {unchanged_ledger.total_spent_epsilon}"
        )
