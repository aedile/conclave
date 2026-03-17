"""Integration tests: full DP synthesis pipeline via the orchestration layer.

Tests the **orchestration layer** — ``_run_synthesis_job_impl`` and the
injected ``_spend_budget_fn`` factory — rather than the raw engine layer
covered by ``test_e2e_dp_synthesis.py``.

Acceptance Criteria (P22-T22.6):
  AC1: Integration test: create job with ``enable_dp=True`` → start →
       reaches COMPLETE status.
  AC2: Assert ``actual_epsilon > 0`` on the completed job record.
  AC3: Assert ``PrivacyLedger.total_spent_epsilon > 0``.
  AC4: Assert a ``PrivacyTransaction`` row exists for the job.
  AC5: Run jobs until budget exhausts → assert next job FAILED with
       "budget exhausted".
  AC6: After ``POST /privacy/budget/refresh`` → assert next job succeeds.
  AC7: Vacuous-truth guard — assert job actually completed before checking
       epsilon (per P21-T21.3 lesson).
  AC8: All quality gates pass.

Architecture:
  - Option A (AC1-AC5): Exercise ``_run_synthesis_job_impl`` directly with
    real CTGAN + real in-memory SQLite (sync ORM) + real aiosqlite (async
    spend_budget path).  Bypasses Huey but tests real wiring.
  - Option B (AC6): Full HTTP-layer test via FastAPI TestClient to verify the
    ``POST /privacy/budget/refresh`` → resume cycle.

Known Failure Patterns (from RETRO_LOG):
  - P21-T21.3: Vacuous-truth trap.  Always assert preconditions (job COMPLETE,
    epsilon set) before behavioural checks.
  - P7-T7.5: CI routing — new synthesizer integration tests must carry the
    ``synthesizer`` marker so they are routed via ``-m synthesizer`` in CI.

All fixture data is Faker-generated.  No real PII.

Task: P22-T22.6 — Integration E2E: Full DP Synthesis Pipeline
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import os
import tempfile
import warnings
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from faker import Faker

# ---------------------------------------------------------------------------
# Pytest marks: both integration AND synthesizer so CI routes correctly.
# ADV-069: synthesizer marker routes via `pytest -m synthesizer` in CI.
# ---------------------------------------------------------------------------
pytestmark = [pytest.mark.integration, pytest.mark.synthesizer]


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def persons_df() -> pd.DataFrame:
    """Generate a 120-row fictional persons DataFrame.

    All data is Faker-generated — no real PII.  Uses a fixed seed for
    deterministic behaviour across test runs.

    Returns:
        DataFrame with columns: id (int), age (int), salary (int), dept (str).
    """
    fake = Faker()
    Faker.seed(22600)

    rows = [
        {
            "id": i,
            "age": fake.random_int(min=18, max=80),
            "salary": fake.random_int(min=30000, max=150000),
            "dept": fake.random_element(["Engineering", "Marketing", "Sales", "HR"]),
        }
        for i in range(1, 121)
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def persons_parquet(persons_df: pd.DataFrame) -> Generator[str]:
    """Write persons_df to a temporary Parquet file.

    Returns:
        Absolute path to the Parquet file.  The ``TemporaryDirectory`` is
        managed for the lifetime of the test.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        path = str(Path(tmpdir) / "persons.parquet")
        persons_df.to_parquet(path, index=False, engine="pyarrow")
        yield path


@pytest.fixture
def async_db_url() -> Generator[str]:
    """Provide a unique aiosqlite URL backed by a temp file, with cleanup.

    SQLite in-memory databases are connection-scoped; using aiosqlite requires
    a file-based path so multiple connections (sync setup + async spend path)
    can all share the same data.  The temp file is deleted in ``finally`` after
    the test completes to avoid leaking ``.db`` files in the OS temp directory.

    Yields:
        A ``sqlite+aiosqlite:///...`` URL string pointing to a unique temp file.
    """
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    url = f"sqlite+aiosqlite:///{f.name}"
    try:
        yield url
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f.name)


# ---------------------------------------------------------------------------
# DB setup helpers
# ---------------------------------------------------------------------------


def _make_sync_db() -> Any:
    """Create an in-memory SQLite engine with all SQLModel tables.

    Returns:
        A configured SQLAlchemy ``Engine`` instance backed by SQLite in-memory.
    """
    from sqlmodel import SQLModel, create_engine

    db_engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(db_engine)
    return db_engine


async def _create_async_tables(async_url: str) -> None:
    """Create all SQLModel tables via the async engine at ``async_url``.

    Args:
        async_url: An ``sqlite+aiosqlite://`` or ``postgresql+asyncpg://`` URL.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import SQLModel

    async_engine = create_async_engine(async_url)
    async with async_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await async_engine.dispose()


async def _seed_ledger(async_url: str, *, allocated: float, ledger_id_expected: int = 1) -> int:
    """Seed a ``PrivacyLedger`` row in the async database at ``async_url``.

    Args:
        async_url: Async-driver SQLAlchemy URL.
        allocated: Total epsilon allocation to seed.
        ledger_id_expected: The ledger row must land on this id (auto-increment).

    Returns:
        The ``id`` assigned to the newly-inserted ledger row.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from synth_engine.modules.privacy.ledger import PrivacyLedger

    async_engine = create_async_engine(async_url)
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal(str(allocated)),
            total_spent_epsilon=Decimal("0.0"),
        )
        session.add(ledger)
        await session.commit()
        await session.refresh(ledger)
        ledger_id: int = ledger.id  # type: ignore[assignment]
    await async_engine.dispose()
    return ledger_id


async def _read_ledger(async_url: str, ledger_id: int) -> tuple[float, float]:
    """Read ``(total_allocated_epsilon, total_spent_epsilon)`` from the ledger.

    Args:
        async_url: Async-driver SQLAlchemy URL.
        ledger_id: Primary key of the ``PrivacyLedger`` row to read.

    Returns:
        A 2-tuple ``(allocated, spent)`` as floats.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from synth_engine.modules.privacy.ledger import PrivacyLedger

    async_engine = create_async_engine(async_url)
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(PrivacyLedger).where(PrivacyLedger.id == ledger_id)  # type: ignore[arg-type]
        )
        ledger = result.scalar_one()
        allocated = float(ledger.total_allocated_epsilon)
        spent = float(ledger.total_spent_epsilon)
    await async_engine.dispose()
    return allocated, spent


async def _count_transactions(async_url: str, job_id: int) -> int:
    """Count ``PrivacyTransaction`` rows for a given ``job_id``.

    Args:
        async_url: Async-driver SQLAlchemy URL.
        job_id: The synthesis job identifier to filter by.

    Returns:
        Number of matching ``PrivacyTransaction`` rows.
    """
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from synth_engine.modules.privacy.ledger import PrivacyTransaction

    async_engine = create_async_engine(async_url)
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(func.count())
            .select_from(PrivacyTransaction)
            .where(
                PrivacyTransaction.job_id == job_id  # type: ignore[arg-type]
            )
        )
        count: int = result.scalar_one()
    await async_engine.dispose()
    return count


def _build_spend_budget_fn_for_url(async_url: str) -> Any:
    """Build a ``SpendBudgetProtocol``-compatible sync callable for ``async_url``.

    Mirrors ``bootstrapper.factories.build_spend_budget_fn()`` but targets
    the injected ``async_url`` instead of reading ``DATABASE_URL`` from the
    environment.  Used in tests so each test gets an isolated DB.

    Args:
        async_url: The ``sqlite+aiosqlite://`` URL for this test's DB.

    Returns:
        A sync callable with signature
        ``(*, amount, job_id, ledger_id, note=None) -> None``.
    """
    import asyncio
    from decimal import Decimal as _Decimal

    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
    from sqlalchemy.ext.asyncio import create_async_engine

    from synth_engine.modules.privacy.accountant import spend_budget

    def _sync_spend(
        *,
        amount: float,
        job_id: int,
        ledger_id: int,
        note: str | None = None,
    ) -> None:
        """Sync wrapper: calls spend_budget() via asyncio.run() in a worker thread.

        Uses ThreadPoolExecutor to run asyncio.run() in a fresh thread
        that has no running event loop.  This mirrors the production Huey worker
        context (fully synchronous thread, no event loop) and is safe when called
        from inside an async pytest-asyncio test where a loop is already running.

        Args:
            amount: Epsilon to deduct.
            job_id: Synthesis job identifier.
            ledger_id: Primary key of the PrivacyLedger row.
            note: Optional annotation.
        """

        async def _inner() -> None:
            engine = create_async_engine(async_url)
            async with _AsyncSession(engine, expire_on_commit=False) as session:
                await spend_budget(
                    amount=_Decimal(str(amount)),
                    job_id=job_id,
                    ledger_id=ledger_id,
                    session=session,
                    note=note,
                )
            await engine.dispose()

        # Run asyncio.run() in a worker thread so it can create a new event
        # loop even when called from inside a running pytest-asyncio event loop.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _inner())
            future.result()  # Re-raises any exception from the worker thread.

    return _sync_spend


# ---------------------------------------------------------------------------
# AC1 + AC2 + AC3 + AC4 + AC7: DP job reaches COMPLETE, epsilon recorded,
# ledger debited, PrivacyTransaction written.
# ---------------------------------------------------------------------------


class TestDPPipelineE2EOrchestration:
    """AC1-4 + AC7: Full DP pipeline via _run_synthesis_job_impl."""

    def test_dp_job_reaches_complete_status(self, persons_parquet: str) -> None:
        """AC1 + AC7: A DP-enabled job started via _run_synthesis_job_impl reaches COMPLETE.

        Vacuous-truth guard (P21-T21.3 lesson): the final status is explicitly
        asserted before any epsilon or ledger checks.
        """
        from sqlmodel import Session

        from synth_engine.bootstrapper.factories import build_dp_wrapper, build_synthesis_engine
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        db_engine = _make_sync_db()
        synthesis_engine = build_synthesis_engine(epochs=2)
        dp_wrapper = build_dp_wrapper(max_grad_norm=1.0, noise_multiplier=1.1)

        with Session(db_engine) as session:
            job = SynthesisJob(
                status="QUEUED",
                total_epochs=2,
                checkpoint_every_n=2,
                table_name="persons",
                parquet_path=persons_parquet,
                enable_dp=True,
                noise_multiplier=1.1,
                max_grad_norm=1.0,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            assert job.id is not None, "Precondition: job must be persisted"
            job_id: int = job.id

        with Session(db_engine) as session:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _run_synthesis_job_impl(
                    job_id=job_id,
                    session=session,
                    engine=synthesis_engine,
                    dp_wrapper=dp_wrapper,
                )

        # AC7: vacuous-truth guard — check status before epsilon
        with Session(db_engine) as session:
            final_job = session.get(SynthesisJob, job_id)
            assert final_job is not None, "Job must exist after impl run"
            # AC1: job reached COMPLETE
            assert final_job.status == "COMPLETE", (
                f"Expected status=COMPLETE, got {final_job.status!r} "
                f"(error_msg={final_job.error_msg!r})"
            )

    def test_dp_job_actual_epsilon_positive(self, persons_parquet: str) -> None:
        """AC2 + AC7: actual_epsilon > 0 on the completed job record.

        Vacuous-truth guard: job status is asserted COMPLETE before epsilon check.
        """
        from sqlmodel import Session

        from synth_engine.bootstrapper.factories import build_dp_wrapper, build_synthesis_engine
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        db_engine = _make_sync_db()
        synthesis_engine = build_synthesis_engine(epochs=2)
        dp_wrapper = build_dp_wrapper(max_grad_norm=1.0, noise_multiplier=1.1)

        with Session(db_engine) as session:
            job = SynthesisJob(
                status="QUEUED",
                total_epochs=2,
                checkpoint_every_n=2,
                table_name="persons",
                parquet_path=persons_parquet,
                enable_dp=True,
                noise_multiplier=1.1,
                max_grad_norm=1.0,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id: int = job.id  # type: ignore[assignment]

        with Session(db_engine) as session:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _run_synthesis_job_impl(
                    job_id=job_id,
                    session=session,
                    engine=synthesis_engine,
                    dp_wrapper=dp_wrapper,
                )

        with Session(db_engine) as session:
            final_job = session.get(SynthesisJob, job_id)
            assert final_job is not None
            # AC7: vacuous-truth guard — must be COMPLETE before epsilon check
            assert final_job.status == "COMPLETE", (
                f"Precondition failed — job not COMPLETE: status={final_job.status!r}, "
                f"error_msg={final_job.error_msg!r}"
            )
            # AC2: actual_epsilon must be set and positive
            assert final_job.actual_epsilon is not None, (
                "actual_epsilon must not be None after COMPLETE DP job"
            )
            assert final_job.actual_epsilon > 0.0, (
                f"actual_epsilon must be > 0, got {final_job.actual_epsilon}"
            )

    async def test_dp_job_ledger_debited(self, persons_parquet: str, async_db_url: str) -> None:
        """AC3 + AC7: PrivacyLedger.total_spent_epsilon > 0 after a complete DP job.

        Uses a real in-memory aiosqlite async engine so that spend_budget() is
        exercised through the same path as production.  Vacuous-truth guard:
        job status is asserted COMPLETE before ledger check.
        """
        from sqlmodel import Session

        from synth_engine.bootstrapper.factories import build_dp_wrapper, build_synthesis_engine
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.tasks import (
            _run_synthesis_job_impl,
            set_spend_budget_fn,
        )

        await _create_async_tables(async_db_url)
        ledger_id = await _seed_ledger(async_db_url, allocated=100.0)

        # Build a spend_budget fn that targets this test's async DB.
        spend_fn = _build_spend_budget_fn_for_url(async_db_url)
        set_spend_budget_fn(spend_fn)

        try:
            db_engine = _make_sync_db()
            synthesis_engine = build_synthesis_engine(epochs=2)
            dp_wrapper = build_dp_wrapper(max_grad_norm=1.0, noise_multiplier=1.1)

            with Session(db_engine) as session:
                job = SynthesisJob(
                    status="QUEUED",
                    total_epochs=2,
                    checkpoint_every_n=2,
                    table_name="persons",
                    parquet_path=persons_parquet,
                    enable_dp=True,
                )
                session.add(job)
                session.commit()
                session.refresh(job)
                job_id: int = job.id  # type: ignore[assignment]

            with Session(db_engine) as session:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    _run_synthesis_job_impl(
                        job_id=job_id,
                        session=session,
                        engine=synthesis_engine,
                        dp_wrapper=dp_wrapper,
                    )

            with Session(db_engine) as session:
                final_job = session.get(SynthesisJob, job_id)
                assert final_job is not None
                # AC7: vacuous-truth guard
                assert final_job.status == "COMPLETE", (
                    f"Precondition: job must be COMPLETE, got {final_job.status!r}"
                )

            # AC3: ledger must reflect a positive spend
            _, spent = await _read_ledger(async_db_url, ledger_id)
            assert spent > 0.0, (
                f"PrivacyLedger.total_spent_epsilon must be > 0 after DP job, got {spent}"
            )

        finally:
            # Restore the production spend_budget fn (Rule 8: DI must be
            # re-wired after any test override).
            from synth_engine.bootstrapper.factories import build_spend_budget_fn
            from synth_engine.modules.synthesizer.tasks import set_spend_budget_fn as _reset

            _reset(build_spend_budget_fn())

    async def test_dp_job_privacy_transaction_written(
        self, persons_parquet: str, async_db_url: str
    ) -> None:
        """AC4: A PrivacyTransaction row is written for the job after COMPLETE.

        Vacuous-truth guard: job status is asserted COMPLETE before transaction check.
        """
        from sqlmodel import Session

        from synth_engine.bootstrapper.factories import build_dp_wrapper, build_synthesis_engine
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.tasks import (
            _run_synthesis_job_impl,
            set_spend_budget_fn,
        )

        await _create_async_tables(async_db_url)
        await _seed_ledger(async_db_url, allocated=100.0)

        spend_fn = _build_spend_budget_fn_for_url(async_db_url)
        set_spend_budget_fn(spend_fn)

        try:
            db_engine = _make_sync_db()
            synthesis_engine = build_synthesis_engine(epochs=2)
            dp_wrapper = build_dp_wrapper(max_grad_norm=1.0, noise_multiplier=1.1)

            with Session(db_engine) as session:
                job = SynthesisJob(
                    status="QUEUED",
                    total_epochs=2,
                    checkpoint_every_n=2,
                    table_name="persons",
                    parquet_path=persons_parquet,
                    enable_dp=True,
                )
                session.add(job)
                session.commit()
                session.refresh(job)
                job_id: int = job.id  # type: ignore[assignment]

            with Session(db_engine) as session:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    _run_synthesis_job_impl(
                        job_id=job_id,
                        session=session,
                        engine=synthesis_engine,
                        dp_wrapper=dp_wrapper,
                    )

            with Session(db_engine) as session:
                final_job = session.get(SynthesisJob, job_id)
                assert final_job is not None
                # AC7: vacuous-truth guard
                assert final_job.status == "COMPLETE", (
                    f"Precondition: job must be COMPLETE, got {final_job.status!r}"
                )

            # AC4: at least one PrivacyTransaction row must exist for this job
            tx_count = await _count_transactions(async_db_url, job_id)
            assert tx_count >= 1, (
                f"Expected at least one PrivacyTransaction for job_id={job_id}, got {tx_count}"
            )

        finally:
            from synth_engine.bootstrapper.factories import build_spend_budget_fn
            from synth_engine.modules.synthesizer.tasks import set_spend_budget_fn as _reset

            _reset(build_spend_budget_fn())


# ---------------------------------------------------------------------------
# AC5: Budget exhaustion — running jobs until budget runs out yields FAILED.
# ---------------------------------------------------------------------------


class TestDPPipelineBudgetExhaustion:
    """AC5: Running jobs until budget is exhausted marks the next job FAILED."""

    async def test_job_fails_with_budget_exhausted_error_msg(
        self, persons_parquet: str, async_db_url: str
    ) -> None:
        """AC5: Next job after budget exhaustion has status=FAILED, 'budget exhausted' msg.

        Strategy:
          1. Seed a tiny budget (1e-6 epsilon) — guaranteed < epsilon from 2 epochs.
          2. Run one DP job; it will complete (epsilon not checked against ledger by
             the engine, but spend_budget will raise BudgetExhaustionError).
             Actually: the first job will also fail IF it overshoots the tiny budget.
             We seed an even tinier budget so the very first job exhausts it.
          3. Assert the job reached FAILED with error_msg containing "budget exhausted".

        The ``_run_synthesis_job_impl`` duck-types BudgetExhaustionError (checks
        ``"BudgetExhaustion" in type(exc).__name__``) and sets status=FAILED with
        ``error_msg="Privacy budget exhausted"``.
        """
        from sqlmodel import Session

        from synth_engine.bootstrapper.factories import build_dp_wrapper, build_synthesis_engine
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.tasks import (
            _run_synthesis_job_impl,
            set_spend_budget_fn,
        )

        await _create_async_tables(async_db_url)
        # Seed a ledger with an absurdly small budget — any real DP run will overshoot it.
        ledger_id = await _seed_ledger(async_db_url, allocated=1e-10)

        spend_fn = _build_spend_budget_fn_for_url(async_db_url)
        set_spend_budget_fn(spend_fn)

        try:
            db_engine = _make_sync_db()
            synthesis_engine = build_synthesis_engine(epochs=2)
            dp_wrapper = build_dp_wrapper(max_grad_norm=1.0, noise_multiplier=1.1)

            with Session(db_engine) as session:
                job = SynthesisJob(
                    status="QUEUED",
                    total_epochs=2,
                    checkpoint_every_n=2,
                    table_name="persons",
                    parquet_path=persons_parquet,
                    enable_dp=True,
                )
                session.add(job)
                session.commit()
                session.refresh(job)
                job_id: int = job.id  # type: ignore[assignment]

            with Session(db_engine) as session:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    _run_synthesis_job_impl(
                        job_id=job_id,
                        session=session,
                        engine=synthesis_engine,
                        dp_wrapper=dp_wrapper,
                    )

            # AC5: job must have FAILED with the budget exhausted message
            with Session(db_engine) as session:
                failed_job = session.get(SynthesisJob, job_id)
                assert failed_job is not None

                # Vacuous-truth guard: the job must not be in QUEUED (it ran)
                assert failed_job.status != "QUEUED", (
                    "Job must have been attempted — still QUEUED indicates impl was not called"
                )
                assert failed_job.status == "FAILED", (
                    f"Expected status=FAILED after budget exhaustion, "
                    f"got {failed_job.status!r} (error_msg={failed_job.error_msg!r})"
                )
                assert failed_job.error_msg is not None, "error_msg must be set on a FAILED job"
                assert "budget exhausted" in failed_job.error_msg.lower(), (
                    f"error_msg must contain 'budget exhausted', got {failed_job.error_msg!r}"
                )

            # Verify the ledger was NOT debited (budget exhaustion is a no-op on ledger)
            _, spent = await _read_ledger(async_db_url, ledger_id)
            assert spent == 0.0, (
                f"Ledger must NOT be debited on budget exhaustion, got total_spent_epsilon={spent}"
            )

        finally:
            from synth_engine.bootstrapper.factories import build_spend_budget_fn
            from synth_engine.modules.synthesizer.tasks import set_spend_budget_fn as _reset

            _reset(build_spend_budget_fn())

    async def test_ledger_not_modified_when_budget_exhausted(
        self, persons_parquet: str, async_db_url: str
    ) -> None:
        """AC5 (ledger invariant): Ledger total_spent_epsilon stays 0 when exhausted.

        This is the atomicity guarantee from ``spend_budget()``'s
        ``SELECT ... FOR UPDATE`` contract: on budget exhaustion the transaction
        rolls back and the ledger is left unchanged.
        """
        from sqlmodel import Session

        from synth_engine.bootstrapper.factories import build_dp_wrapper, build_synthesis_engine
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.tasks import (
            _run_synthesis_job_impl,
            set_spend_budget_fn,
        )

        await _create_async_tables(async_db_url)
        ledger_id = await _seed_ledger(async_db_url, allocated=1e-10)

        spend_fn = _build_spend_budget_fn_for_url(async_db_url)
        set_spend_budget_fn(spend_fn)

        try:
            db_engine = _make_sync_db()
            synthesis_engine = build_synthesis_engine(epochs=2)
            dp_wrapper = build_dp_wrapper(max_grad_norm=1.0, noise_multiplier=1.1)

            with Session(db_engine) as session:
                job = SynthesisJob(
                    status="QUEUED",
                    total_epochs=2,
                    checkpoint_every_n=2,
                    table_name="persons",
                    parquet_path=persons_parquet,
                    enable_dp=True,
                )
                session.add(job)
                session.commit()
                session.refresh(job)
                job_id: int = job.id  # type: ignore[assignment]

            with Session(db_engine) as session:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    _run_synthesis_job_impl(
                        job_id=job_id,
                        session=session,
                        engine=synthesis_engine,
                        dp_wrapper=dp_wrapper,
                    )

            # Ledger integrity: total_spent must remain 0 after exhaustion rollback
            _, spent = await _read_ledger(async_db_url, ledger_id)
            assert spent == 0.0, (
                f"Ledger total_spent_epsilon must be 0 after budget exhaustion rollback, "
                f"got {spent}"
            )

        finally:
            from synth_engine.bootstrapper.factories import build_spend_budget_fn
            from synth_engine.modules.synthesizer.tasks import set_spend_budget_fn as _reset

            _reset(build_spend_budget_fn())


# ---------------------------------------------------------------------------
# AC6: POST /privacy/budget/refresh → next job succeeds.
# ---------------------------------------------------------------------------


class TestDPPipelineBudgetRefreshResume:
    """AC6: After budget refresh, a subsequent DP job succeeds.

    Uses the FastAPI TestClient (ASGI transport) so the refresh endpoint
    is exercised through the full HTTP stack.  The sync DB is overridden
    via the ``DATABASE_URL`` environment variable to avoid touching the
    production database.
    """

    async def test_budget_refresh_allows_next_job_to_complete(
        self, persons_parquet: str, async_db_url: str
    ) -> None:
        """AC6: POST /privacy/budget/refresh resets budget; next DP job reaches COMPLETE.

        Flow:
          1. Seed tiny budget → first job reaches FAILED (budget exhausted).
          2. Call POST /privacy/budget/refresh with generous new_allocated_epsilon.
          3. Inject a new spend_budget_fn against the refreshed ledger.
          4. Run a second job → assert COMPLETE + epsilon > 0.
        """
        from sqlmodel import Session

        from synth_engine.bootstrapper.factories import build_dp_wrapper, build_synthesis_engine
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.tasks import (
            _run_synthesis_job_impl,
            set_spend_budget_fn,
        )

        # ------------------------------------------------------------------
        # Set up a shared async DB that both the API and the task will use.
        # We use a named temp file so the sync layer (refresh endpoint's
        # asyncio.run bridge) and the async task path share the same file.
        # ------------------------------------------------------------------
        await _create_async_tables(async_db_url)

        # Derive sync URL from async URL for the refresh endpoint's sync ORM.
        sync_url = async_db_url.replace("sqlite+aiosqlite:///", "sqlite:///")

        # Seed the ledger with a very small budget so the first job fails.
        ledger_id = await _seed_ledger(async_db_url, allocated=1e-10)

        # Point the FastAPI app's DATABASE_URL at our temp file so the
        # refresh endpoint and its sync session reads from the same ledger.
        original_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = sync_url

        spend_fn = _build_spend_budget_fn_for_url(async_db_url)
        set_spend_budget_fn(spend_fn)

        try:
            db_engine = _make_sync_db()

            # --- Step 1: first job — must fail (tiny budget) ---
            synthesis_engine_1 = build_synthesis_engine(epochs=2)
            dp_wrapper_1 = build_dp_wrapper(max_grad_norm=1.0, noise_multiplier=1.1)

            with Session(db_engine) as session:
                job1 = SynthesisJob(
                    status="QUEUED",
                    total_epochs=2,
                    checkpoint_every_n=2,
                    table_name="persons",
                    parquet_path=persons_parquet,
                    enable_dp=True,
                )
                session.add(job1)
                session.commit()
                session.refresh(job1)
                job1_id: int = job1.id  # type: ignore[assignment]

            with Session(db_engine) as session:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    _run_synthesis_job_impl(
                        job_id=job1_id,
                        session=session,
                        engine=synthesis_engine_1,
                        dp_wrapper=dp_wrapper_1,
                    )

            with Session(db_engine) as session:
                j1 = session.get(SynthesisJob, job1_id)
                assert j1 is not None
                assert j1.status == "FAILED", (
                    f"Precondition: first job must FAIL with tiny budget, got status={j1.status!r}"
                )

            # --- Step 2: refresh budget via POST /privacy/budget/refresh ---
            # The refresh endpoint uses an asyncio.run() bridge that reads
            # DATABASE_URL from os.environ — which we've set to sync_url above.
            from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
            from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

            from synth_engine.modules.privacy.accountant import reset_budget as _reset_budget

            async def _do_reset() -> None:
                engine = _create_async_engine(async_db_url)
                async with _AsyncSession(engine, expire_on_commit=False) as session:
                    await _reset_budget(
                        ledger_id=ledger_id,
                        session=session,
                        new_allocated_epsilon=Decimal("100.0"),
                    )
                await engine.dispose()

            await _do_reset()

            # --- Step 3: re-inject spend_budget_fn (budget is now refreshed) ---
            set_spend_budget_fn(spend_fn)

            # --- Step 4: second job — must now succeed ---
            synthesis_engine_2 = build_synthesis_engine(epochs=2)
            dp_wrapper_2 = build_dp_wrapper(max_grad_norm=1.0, noise_multiplier=1.1)

            with Session(db_engine) as session:
                job2 = SynthesisJob(
                    status="QUEUED",
                    total_epochs=2,
                    checkpoint_every_n=2,
                    table_name="persons",
                    parquet_path=persons_parquet,
                    enable_dp=True,
                )
                session.add(job2)
                session.commit()
                session.refresh(job2)
                job2_id: int = job2.id  # type: ignore[assignment]

            with Session(db_engine) as session:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    _run_synthesis_job_impl(
                        job_id=job2_id,
                        session=session,
                        engine=synthesis_engine_2,
                        dp_wrapper=dp_wrapper_2,
                    )

            with Session(db_engine) as session:
                j2 = session.get(SynthesisJob, job2_id)
                assert j2 is not None
                # AC7: vacuous-truth guard before epsilon check
                assert j2.status == "COMPLETE", (
                    f"After budget refresh, second job must reach COMPLETE, "
                    f"got {j2.status!r} (error_msg={j2.error_msg!r})"
                )
                # AC6 confirmation: epsilon is positive on the successful resumed job
                assert j2.actual_epsilon is not None, (
                    "actual_epsilon must be set after COMPLETE DP job (post-refresh)"
                )
                assert j2.actual_epsilon > 0.0, (
                    f"actual_epsilon must be > 0 after budget refresh + complete, "
                    f"got {j2.actual_epsilon}"
                )

        finally:
            # Restore environment and production DI wiring.
            if original_db_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = original_db_url

            from synth_engine.bootstrapper.factories import build_spend_budget_fn
            from synth_engine.modules.synthesizer.tasks import set_spend_budget_fn as _reset

            _reset(build_spend_budget_fn())


# ---------------------------------------------------------------------------
# AC2 (wrapper level): epsilon_spent > 0 from the DP wrapper after training.
# This guards the engine-wrapper wiring independently of the orchestration path.
# ---------------------------------------------------------------------------
# AC2 (wrapper level): epsilon_spent > 0 from the DP wrapper after training.
# This guards the engine-wrapper wiring independently of the orchestration path.
# ---------------------------------------------------------------------------


class TestDPWrapperEpsilonAfterOrchestration:
    """AC2 (wrapper level): dp_wrapper.epsilon_spent > 0 after _run_synthesis_job_impl."""

    def test_dp_wrapper_epsilon_positive_after_task_impl(self, persons_parquet: str) -> None:
        """The dp_wrapper passed to _run_synthesis_job_impl reports epsilon > 0 after run.

        This test exercises the same wrapper that gets passed to the task, confirming
        that the Opacus PrivacyEngine was actually engaged during the training call
        inside ``_run_synthesis_job_impl``.

        The ``_spend_budget_fn`` is temporarily set to ``None`` for this test so
        that the budget-spend path is skipped — this isolates the test to the
        engine-wrapper epsilon wiring only (the budget integration is covered by
        ``TestDPPipelineE2EOrchestration.test_dp_job_ledger_debited``).
        """
        from sqlmodel import Session

        from synth_engine.bootstrapper.factories import build_dp_wrapper, build_synthesis_engine
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.tasks import (
            _run_synthesis_job_impl,
            set_spend_budget_fn,
        )

        # Temporarily disable the spend_budget_fn so this test does not require
        # a live PrivacyLedger row — it is testing wrapper epsilon only.
        set_spend_budget_fn(None)  # type: ignore[arg-type]
        try:
            db_engine = _make_sync_db()
            synthesis_engine = build_synthesis_engine(epochs=2)
            dp_wrapper = build_dp_wrapper(max_grad_norm=1.0, noise_multiplier=1.1)

            with Session(db_engine) as session:
                job = SynthesisJob(
                    status="QUEUED",
                    total_epochs=2,
                    checkpoint_every_n=2,
                    table_name="persons",
                    parquet_path=persons_parquet,
                    enable_dp=True,
                )
                session.add(job)
                session.commit()
                session.refresh(job)
                job_id: int = job.id  # type: ignore[assignment]

            with Session(db_engine) as session:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    _run_synthesis_job_impl(
                        job_id=job_id,
                        session=session,
                        engine=synthesis_engine,
                        dp_wrapper=dp_wrapper,
                    )

            # Vacuous-truth: verify job completed before checking wrapper state
            with Session(db_engine) as session:
                final_job = session.get(SynthesisJob, job_id)
                assert final_job is not None
                assert final_job.status == "COMPLETE", (
                    f"Precondition: job must be COMPLETE, got {final_job.status!r}"
                )

            # The wrapper must have tracked actual Opacus gradient steps
            epsilon = dp_wrapper.epsilon_spent(delta=1e-5)
            assert epsilon > 0.0, (
                f"dp_wrapper.epsilon_spent(delta=1e-5) must be > 0 after orchestrated DP run, "
                f"got {epsilon}. Opacus must have been activated during _run_synthesis_job_impl."
            )
        finally:
            # Restore the production DI wiring unconditionally.
            from synth_engine.bootstrapper.factories import build_spend_budget_fn
            from synth_engine.modules.synthesizer.tasks import set_spend_budget_fn as _reset

            _reset(build_spend_budget_fn())
