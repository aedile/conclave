"""Integration test: concurrent privacy budget contention.

Two simultaneous synthesis jobs contending for the same table's epsilon
budget must either both complete within budget or exactly one fail with
``BudgetExhaustionError``.  This test uses real PostgreSQL to verify that
``SELECT ... FOR UPDATE`` serialises concurrent budget claims correctly.

``SELECT FOR UPDATE`` is silently ignored by SQLite — SQLite-based unit
tests would always pass but provide no correctness guarantee for the
race-condition scenario.

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH. If absent, all tests in this module are
  skipped automatically via the ``_require_postgresql`` autouse fixture.
- ``asyncpg`` installed: ``poetry install`` (main dependency group).

Marks: ``integration``

CONSTITUTION Priority 0: Security — pessimistic locking prevents overrun.
CONSTITUTION Priority 3: TDD — concurrency integration gate for P19-T19.3.
Task: P19-T19.3 — Integration Test CI Gate & Property-Based Testing
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from synth_engine.modules.privacy.accountant import spend_budget
from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError
from synth_engine.modules.privacy.ledger import PrivacyLedger
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
            "pg_ctl not found on PATH — install PostgreSQL to run concurrent budget "
            "contention integration tests",
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

    Creates the test database via ``DatabaseJanitor``, creates all SQLModel
    tables, yields the engine, drops all tables, and disposes the engine.

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
async def test_two_concurrent_jobs_contend_for_budget_exactly_one_wins(
    pg_async_engine: AsyncEngine,
) -> None:
    """Two simultaneous jobs contending for a tight budget — exactly one wins.

    This is the canonical two-job contention scenario from the task spec:
    "Two simultaneous synthesis jobs contending for the same table's epsilon
    budget should either both complete within budget or exactly one fail
    with BudgetExhaustionError."

    Arrange: Create a PrivacyLedger with total_allocated_epsilon=0.5,
    total_spent=0.0.  Each job requests 0.4 epsilon.
    Act: Fire 2 concurrent spend_budget(0.4) calls via asyncio.gather.
    Assert:
    - Exactly 1 call succeeds (0.4 < 0.5 budget).
    - Exactly 1 call raises BudgetExhaustionError (0.4 + 0.4 = 0.8 > 0.5).
    - total_spent_epsilon in the DB is exactly 0.4 (no overrun).
    """
    # --- Arrange: create ledger with tight budget ---
    async with get_async_session(pg_async_engine) as session:
        ledger = PrivacyLedger(total_allocated_epsilon=0.5, total_spent_epsilon=0.0)
        session.add(ledger)
        await session.commit()
        await session.refresh(ledger)
        ledger_id = ledger.id
        assert ledger_id is not None, "ledger.id must be set after commit and refresh"

    # --- Act: fire 2 concurrent calls, each requesting 0.4 epsilon ---
    async def _attempt(job_id: int) -> str:
        """Try to spend 0.4 epsilon; return 'success' or 'exhausted'.

        Args:
            job_id: Unique integer identifier for this attempt.

        Returns:
            ``'success'`` if the budget was allocated, ``'exhausted'`` if
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

    results = await asyncio.gather(_attempt(1), _attempt(2))

    # --- Assert: outcome counts ---
    success_count = results.count("success")
    exhausted_count = results.count("exhausted")

    assert success_count == 1, (
        f"Expected exactly 1 successful call, got {success_count}. "
        f"Exhausted: {exhausted_count}. Results: {results}"
    )
    assert exhausted_count == 1, (
        f"Expected exactly 1 BudgetExhaustionError, got {exhausted_count}. Success: {success_count}"
    )

    # --- Assert: DB state reflects exactly the one successful spend ---
    async with get_async_session(pg_async_engine) as s:
        ledger_result = await s.execute(
            select(PrivacyLedger).where(PrivacyLedger.id == ledger_id)  # type: ignore[arg-type]
        )
        final_ledger = ledger_result.scalar_one()
        assert abs(final_ledger.total_spent_epsilon - Decimal("0.4")) < Decimal("1e-9"), (
            f"Expected total_spent_epsilon == 0.4, got {final_ledger.total_spent_epsilon}. "
            "Budget overrun or underrun — FOR UPDATE locking may not be working."
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_jobs_both_succeed_when_budget_is_sufficient(
    pg_async_engine: AsyncEngine,
) -> None:
    """Two simultaneous jobs both succeed when the budget accommodates both.

    Arrange: Create a PrivacyLedger with total_allocated_epsilon=2.0,
    total_spent=0.0.  Each job requests 0.5 epsilon.
    Act: Fire 2 concurrent spend_budget(0.5) calls via asyncio.gather.
    Assert:
    - Both calls succeed (0.5 + 0.5 = 1.0 <= 2.0 budget).
    - total_spent_epsilon in the DB is exactly 1.0.
    """
    # --- Arrange ---
    async with get_async_session(pg_async_engine) as session:
        ledger = PrivacyLedger(total_allocated_epsilon=2.0, total_spent_epsilon=0.0)
        session.add(ledger)
        await session.commit()
        await session.refresh(ledger)
        ledger_id = ledger.id
        assert ledger_id is not None, "ledger.id must be set after commit and refresh"

    # --- Act ---
    async def _attempt(job_id: int) -> str:
        """Try to spend 0.5 epsilon.

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

    results = await asyncio.gather(_attempt(10), _attempt(11))

    # --- Assert ---
    assert results.count("success") == 2, f"Expected both calls to succeed, got: {results}"
    assert results.count("exhausted") == 0, f"Expected no BudgetExhaustionError, got: {results}"

    async with get_async_session(pg_async_engine) as s:
        ledger_result = await s.execute(
            select(PrivacyLedger).where(PrivacyLedger.id == ledger_id)  # type: ignore[arg-type]
        )
        final_ledger = ledger_result.scalar_one()
        assert abs(final_ledger.total_spent_epsilon - Decimal("1.0")) < Decimal("1e-9"), (
            f"Expected total_spent_epsilon == 1.0, got {final_ledger.total_spent_epsilon}"
        )
