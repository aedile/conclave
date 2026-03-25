"""Privacy budget integration tests (T35.4 AC3–AC5).

Exercises the concurrent privacy budget spend pathway end-to-end with a real
PostgreSQL instance — verifying that SELECT ... FOR UPDATE pessimistic locking
prevents budget overruns under concurrent synthesis job contention.

Tests in this file:
    - test_privacy_budget_decremented_after_synthesis  (AC3)
    - test_concurrent_budget_exhaustion_exactly_one_wins  (AC4)
    - test_concurrent_both_succeed_when_budget_sufficient  (AC4 — positive path)
    - test_budget_exhausted_no_partial_commit  (AC5 — boundary edge case)

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH. If absent, all tests in this module are
  skipped automatically via the ``_require_postgresql`` autouse fixture.

Marks: ``integration``, ``slow``

CONSTITUTION Priority 0: Security — no PII committed.
CONSTITUTION Priority 3: TDD — RED/GREEN/REFACTOR.
Task: T35.4 — Add Full E2E Pipeline Integration Test
Split: T56.3 — budget + concurrency tests split from test_full_pipeline_e2e.py
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
import pytest_asyncio
from pytest_postgresql import factories
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from synth_engine.modules.privacy.accountant import spend_budget
from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError
from synth_engine.modules.privacy.ledger import PrivacyLedger, PrivacyTransaction
from synth_engine.shared.db import get_async_engine, get_async_session
from tests.conftest_types import PostgreSQLProc
from tests.integration.full_pipeline_helpers import _E2E_CONCURRENT_DB

# ---------------------------------------------------------------------------
# pytest-postgresql process fixture (module-scoped — one PG process per module)
# ---------------------------------------------------------------------------

postgresql_proc = factories.postgresql_proc()

# ---------------------------------------------------------------------------
# Skip guard: runs before every test when pg_ctl is absent
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _require_postgresql() -> None:
    """Skip the entire module when ``pg_ctl`` is not installed.

    In CI the PostgreSQL service is always present, so the guard has no effect.
    If a developer's laptop lacks a local PostgreSQL installation, all tests
    are skipped with a clear diagnostic message.
    """
    if shutil.which("pg_ctl") is None:
        pytest.skip(
            "pg_ctl not found on PATH — install PostgreSQL to run budget E2E tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Async engine fixture for privacy budget tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def pg_async_engine(
    postgresql_proc: PostgreSQLProc,
) -> AsyncGenerator[AsyncEngine]:
    """Provide an async SQLAlchemy engine connected to the ephemeral PG instance.

    Creates the concurrent-budget test database, creates all SQLModel tables,
    yields the engine, then drops the DB and disposes the engine on teardown.

    Args:
        postgresql_proc: The running pytest-postgresql process executor.

    Yields:
        An :class:`AsyncEngine` pointed at the ephemeral PostgreSQL database
        with the privacy tables created.
    """
    from pytest_postgresql.janitor import DatabaseJanitor

    proc = postgresql_proc
    password = proc.password or ""
    db_url = (
        f"postgresql+asyncpg://{proc.user}:{password}@{proc.host}:{proc.port}/{_E2E_CONCURRENT_DB}"
    )

    with DatabaseJanitor(
        user=proc.user,
        host=proc.host,
        port=proc.port,
        dbname=_E2E_CONCURRENT_DB,
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
# AC3: Privacy budget is decremented correctly after synthesis
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
async def test_privacy_budget_decremented_after_synthesis(
    pg_async_engine: AsyncEngine,
) -> None:
    """Privacy budget ledger is correctly decremented after a synthesis spend.

    This test exercises the privacy budget pathway that would be invoked
    after a synthesis job completes (budget spend step).

    Arrange: Create a PrivacyLedger with total_allocated_epsilon=5.0.
    Act: Spend 1.5 epsilon (simulating one synthesis job completing).
    Assert:
        - total_spent_epsilon == 1.5 in the database.
        - One PrivacyTransaction row exists with epsilon_spent == 1.5.
        - Remaining budget is 5.0 - 1.5 == 3.5.
    """
    from sqlalchemy import select

    async with get_async_session(pg_async_engine) as session:
        ledger = PrivacyLedger(
            total_allocated_epsilon=5.0,
            total_spent_epsilon=0.0,
        )
        session.add(ledger)
        await session.commit()
        await session.refresh(ledger)
        ledger_id = ledger.id
        assert ledger_id is not None, "ledger.id must be set after commit and refresh"

    # Simulate a synthesis job spending epsilon
    async with get_async_session(pg_async_engine) as session:
        await spend_budget(
            amount=1.5,
            job_id=1001,
            ledger_id=ledger_id,
            session=session,
        )

    # Verify ledger state
    async with get_async_session(pg_async_engine) as session:
        ledger_result = await session.execute(
            select(PrivacyLedger).where(PrivacyLedger.id == ledger_id)  # type: ignore[arg-type]
        )
        updated_ledger = ledger_result.scalar_one()

        assert abs(float(updated_ledger.total_spent_epsilon) - 1.5) < 1e-6, (
            f"Expected total_spent_epsilon=1.5, got {updated_ledger.total_spent_epsilon}"
        )
        remaining = float(updated_ledger.total_allocated_epsilon) - float(
            updated_ledger.total_spent_epsilon
        )
        assert abs(remaining - 3.5) < 1e-6, (
            f"Expected remaining budget=3.5 after spending 1.5 from 5.0, got {remaining}"
        )

        tx_result = await session.execute(
            select(PrivacyTransaction).where(
                PrivacyTransaction.ledger_id == ledger_id  # type: ignore[arg-type]
            )
        )
        transactions = list(tx_result.scalars().all())
        assert len(transactions) == 1, (
            f"Expected exactly 1 PrivacyTransaction, got {len(transactions)}"
        )
        assert abs(float(transactions[0].epsilon_spent) - 1.5) < 1e-6, (
            f"Expected epsilon_spent=1.5, got {transactions[0].epsilon_spent}"
        )
        assert transactions[0].job_id == 1001, f"Expected job_id=1001, got {transactions[0].job_id}"


# ---------------------------------------------------------------------------
# AC4: Concurrent budget exhaustion — exactly one of two simultaneous spends wins
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_budget_exhaustion_exactly_one_wins(
    pg_async_engine: AsyncEngine,
) -> None:
    """Two simultaneous budget spend attempts — exactly one wins, one fails.

    This test verifies the SELECT ... FOR UPDATE pessimistic locking that
    prevents budget overruns under concurrent synthesis job contention.

    Arrange: PrivacyLedger with total_allocated_epsilon=0.5.
    Act: Two concurrent spend_budget(0.4) calls via asyncio.gather.
    Assert:
        - Exactly 1 call succeeds.
        - Exactly 1 call raises BudgetExhaustionError.
        - total_spent_epsilon == 0.4 in the DB (no overrun).

    This is the canonical two-job race condition test for T35.4 AC4.
    """
    from sqlalchemy import select

    async with get_async_session(pg_async_engine) as session:
        ledger = PrivacyLedger(
            total_allocated_epsilon=0.5,
            total_spent_epsilon=0.0,
        )
        session.add(ledger)
        await session.commit()
        await session.refresh(ledger)
        ledger_id = ledger.id
        assert ledger_id is not None, "ledger.id must be set after commit and refresh"

    async def _attempt(job_id: int) -> str:
        """Try to spend 0.4 epsilon; return the outcome string.

        Args:
            job_id: Unique integer identifier for this budget spend attempt.

        Returns:
            ``'success'`` if the budget was allocated; ``'exhausted'`` if
            :exc:`BudgetExhaustionError` was raised.
        """
        try:
            async with get_async_session(pg_async_engine) as s:
                await spend_budget(
                    amount=Decimal("0.4"),
                    job_id=job_id,
                    ledger_id=ledger_id,
                    session=s,
                )
            return "success"
        except BudgetExhaustionError:
            return "exhausted"

    results = await asyncio.gather(_attempt(2001), _attempt(2002))

    success_count = results.count("success")
    exhausted_count = results.count("exhausted")

    assert success_count == 1, (
        f"Expected exactly 1 successful spend, got {success_count}. Results: {results}"
    )
    assert exhausted_count == 1, (
        f"Expected exactly 1 BudgetExhaustionError, got {exhausted_count}. Results: {results}"
    )

    # Verify no overrun in the database
    async with get_async_session(pg_async_engine) as s:
        ledger_result = await s.execute(
            select(PrivacyLedger).where(PrivacyLedger.id == ledger_id)  # type: ignore[arg-type]
        )
        final_ledger = ledger_result.scalar_one()
        assert abs(float(final_ledger.total_spent_epsilon) - 0.4) < 1e-6, (
            f"Expected total_spent_epsilon=0.4 (no overrun), "
            f"got {final_ledger.total_spent_epsilon}. "
            "FOR UPDATE locking may not be functioning correctly."
        )


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_both_succeed_when_budget_sufficient(
    pg_async_engine: AsyncEngine,
) -> None:
    """Two simultaneous budget spend attempts both succeed when budget allows.

    Arrange: PrivacyLedger with total_allocated_epsilon=2.0.
    Act: Two concurrent spend_budget(0.5) calls via asyncio.gather.
    Assert:
        - Both calls succeed.
        - total_spent_epsilon == 1.0 in the DB.
    """
    from sqlalchemy import select

    async with get_async_session(pg_async_engine) as session:
        ledger = PrivacyLedger(
            total_allocated_epsilon=2.0,
            total_spent_epsilon=0.0,
        )
        session.add(ledger)
        await session.commit()
        await session.refresh(ledger)
        ledger_id = ledger.id
        assert ledger_id is not None, "ledger.id must be set after commit and refresh"

    async def _attempt(job_id: int) -> str:
        """Try to spend 0.5 epsilon; return the outcome string.

        Args:
            job_id: Unique integer identifier for this attempt.

        Returns:
            ``'success'`` or ``'exhausted'``.
        """
        try:
            async with get_async_session(pg_async_engine) as s:
                await spend_budget(
                    amount=Decimal("0.5"),
                    job_id=job_id,
                    ledger_id=ledger_id,
                    session=s,
                )
            return "success"
        except BudgetExhaustionError:
            return "exhausted"

    results = await asyncio.gather(_attempt(3001), _attempt(3002))

    assert results.count("success") == 2, (
        f"Expected both jobs to succeed when budget is sufficient. Got: {results}"
    )
    assert results.count("exhausted") == 0, (
        f"Expected no BudgetExhaustionError when budget is sufficient. Got: {results}"
    )

    async with get_async_session(pg_async_engine) as s:
        ledger_result = await s.execute(
            select(PrivacyLedger).where(PrivacyLedger.id == ledger_id)  # type: ignore[arg-type]
        )
        final_ledger = ledger_result.scalar_one()
        assert abs(float(final_ledger.total_spent_epsilon) - 1.0) < 1e-6, (
            f"Expected total_spent_epsilon=1.0, got {final_ledger.total_spent_epsilon}"
        )


# ---------------------------------------------------------------------------
# AC5 (edge case): Budget exhaustion on exact boundary
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
async def test_budget_exhausted_no_partial_commit(
    pg_async_engine: AsyncEngine,
) -> None:
    """BudgetExhaustionError leaves the ledger unchanged (no partial commit).

    Arrange: PrivacyLedger with allocated=1.0, spent=0.9.
    Act: Attempt to spend 0.2 (would bring total to 1.1 > 1.0).
    Assert:
        - BudgetExhaustionError is raised.
        - total_spent_epsilon remains 0.9 (atomic — no partial write).
        - No PrivacyTransaction row was written.
    """
    from sqlalchemy import select

    async with get_async_session(pg_async_engine) as session:
        ledger = PrivacyLedger(
            total_allocated_epsilon=1.0,
            total_spent_epsilon=0.9,
        )
        session.add(ledger)
        await session.commit()
        await session.refresh(ledger)
        ledger_id = ledger.id
        assert ledger_id is not None, "ledger.id must be set after commit and refresh"

    with pytest.raises(BudgetExhaustionError):
        async with get_async_session(pg_async_engine) as s:
            await spend_budget(
                amount=0.2,
                job_id=4001,
                ledger_id=ledger_id,
                session=s,
            )

    async with get_async_session(pg_async_engine) as s:
        ledger_result = await s.execute(
            select(PrivacyLedger).where(PrivacyLedger.id == ledger_id)  # type: ignore[arg-type]
        )
        unchanged_ledger = ledger_result.scalar_one()
        assert abs(float(unchanged_ledger.total_spent_epsilon) - 0.9) < 1e-6, (
            f"Ledger must not be modified on exhaustion. "
            f"Expected 0.9, got {unchanged_ledger.total_spent_epsilon}"
        )

        tx_result = await s.execute(
            select(PrivacyTransaction).where(
                PrivacyTransaction.ledger_id == ledger_id  # type: ignore[arg-type]
            )
        )
        tx_count = len(list(tx_result.scalars().all()))
        assert tx_count == 0, (
            f"No PrivacyTransaction must be written on exhaustion. Got {tx_count}."
        )
