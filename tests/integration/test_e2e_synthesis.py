"""E2E integration tests for the Generative Synthesis subsystem.

Validates the Privacy Ledger pathway end-to-end using a real async SQLite
database (aiosqlite) and the Dummy ML synthesizer fixture.

Context (P6-T6.1):
    Running real PyTorch models in CI takes too long.  The ``DummyMLSynthesizer``
    fixture exercises the exact same ``ModelArtifact`` interface as
    :class:`~synth_engine.modules.synthesizer.engine.SynthesisEngine` without
    performing any real ML training.  The privacy ledger tests confirm that
    ``spend_budget()`` correctly decrements epsilon and raises
    :exc:`~synth_engine.modules.privacy.dp_engine.BudgetExhaustionError` on
    exhaustion, using the same SQLAlchemy session contract that the production
    code uses.

Why aiosqlite here (not pytest-postgresql)?
    The existing ``test_privacy_accountant_integration.py`` already covers the
    ``SELECT ... FOR UPDATE`` concurrency correctness on a real PostgreSQL
    instance (the only scenario where ``FOR UPDATE`` semantics matter).  These
    tests focus on the functional correctness of the spend/exhaust path and the
    Dummy synthesizer interface — both of which work correctly with aiosqlite.
    Using aiosqlite keeps this file self-contained and runnable without
    PostgreSQL installed, complementing the PostgreSQL-backed concurrency tests.

Marks: ``integration``

CONSTITUTION Priority 3: TDD — Red/Green/Refactor
CONSTITUTION Priority 0: Security — no PII, no credential leaks
Task: P6-T6.1 — E2E Generative Synthesis Subsystem Tests
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from synth_engine.modules.privacy.accountant import spend_budget
from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError
from synth_engine.modules.privacy.ledger import PrivacyLedger, PrivacyTransaction
from synth_engine.shared.db import get_async_engine, get_async_session
from tests.fixtures.dummy_ml_synthesizer import DummyMLSynthesizer

# ---------------------------------------------------------------------------
# Async SQLite engine fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def sqlite_async_engine() -> AsyncGenerator[AsyncEngine]:
    """Provide a transient in-memory async SQLite engine with privacy tables.

    Creates all SQLModel tables on setup and drops them on teardown.
    Uses aiosqlite — no PostgreSQL installation required.

    Yields:
        An :class:`AsyncEngine` backed by an in-memory SQLite database
        with all privacy tables created.
    """
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)

    await engine.dispose()


# ---------------------------------------------------------------------------
# Privacy Ledger pathway tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_spend_budget_decrements_epsilon(
    sqlite_async_engine: AsyncEngine,
) -> None:
    """spend_budget() deducts epsilon and writes a PrivacyTransaction record.

    Arrange: Create a PrivacyLedger with total_allocated_epsilon=10.0.
    Act: Call spend_budget(2.5, job_id=1, ...).
    Assert:
      - total_spent_epsilon in the ledger is 2.5.
      - One PrivacyTransaction record exists with epsilon_spent=2.5.
    """
    async with get_async_session(sqlite_async_engine) as s:
        ledger = PrivacyLedger(total_allocated_epsilon=10.0, total_spent_epsilon=0.0)
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    async with get_async_session(sqlite_async_engine) as s:
        await spend_budget(amount=2.5, job_id=1, ledger_id=ledger_id, session=s)

    async with get_async_session(sqlite_async_engine) as s:
        ledger_result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        updated_ledger = ledger_result.scalar_one()
        assert updated_ledger.total_spent_epsilon == 2.5, (
            f"Expected total_spent_epsilon=2.5, got {updated_ledger.total_spent_epsilon}"
        )

        tx_result = await s.execute(
            select(PrivacyTransaction).where(PrivacyTransaction.ledger_id == ledger_id)
        )
        transactions = list(tx_result.scalars().all())
        assert len(transactions) == 1, f"Expected 1 PrivacyTransaction, got {len(transactions)}"
        assert transactions[0].epsilon_spent == 2.5
        assert transactions[0].job_id == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_spend_budget_multiple_calls_accumulate(
    sqlite_async_engine: AsyncEngine,
) -> None:
    """Sequential spend_budget() calls accumulate correctly in the ledger.

    Arrange: Create a PrivacyLedger with total_allocated_epsilon=5.0.
    Act: Call spend_budget(1.0) three times for three different jobs.
    Assert:
      - total_spent_epsilon is 3.0.
      - Three PrivacyTransaction records exist.
    """
    async with get_async_session(sqlite_async_engine) as s:
        ledger = PrivacyLedger(total_allocated_epsilon=5.0, total_spent_epsilon=0.0)
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    for job_id in range(1, 4):
        async with get_async_session(sqlite_async_engine) as s:
            await spend_budget(amount=1.0, job_id=job_id, ledger_id=ledger_id, session=s)

    async with get_async_session(sqlite_async_engine) as s:
        ledger_result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        updated_ledger = ledger_result.scalar_one()
        assert updated_ledger.total_spent_epsilon == 3.0, (
            f"Expected 3.0, got {updated_ledger.total_spent_epsilon}"
        )

        tx_result = await s.execute(
            select(PrivacyTransaction).where(PrivacyTransaction.ledger_id == ledger_id)
        )
        count = len(list(tx_result.scalars().all()))
        assert count == 3, f"Expected 3 transactions, got {count}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_spend_budget_raises_on_exhaustion(
    sqlite_async_engine: AsyncEngine,
) -> None:
    """spend_budget() raises BudgetExhaustionError when budget is exhausted.

    Arrange: Create a PrivacyLedger with allocated=1.0, spent=0.8.
    Act: Attempt to spend 0.3 (total would be 1.1 > 1.0).
    Assert:
      - BudgetExhaustionError is raised.
      - total_spent_epsilon remains 0.8 (no partial commit).
      - No PrivacyTransaction record is written.
    """
    async with get_async_session(sqlite_async_engine) as s:
        ledger = PrivacyLedger(total_allocated_epsilon=1.0, total_spent_epsilon=0.8)
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    with pytest.raises(BudgetExhaustionError):
        async with get_async_session(sqlite_async_engine) as s:
            await spend_budget(amount=0.3, job_id=99, ledger_id=ledger_id, session=s)

    async with get_async_session(sqlite_async_engine) as s:
        ledger_result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        unchanged_ledger = ledger_result.scalar_one()
        assert unchanged_ledger.total_spent_epsilon == 0.8, (
            f"Ledger must not be modified on exhaustion. "
            f"Expected 0.8, got {unchanged_ledger.total_spent_epsilon}"
        )

        tx_result = await s.execute(
            select(PrivacyTransaction).where(PrivacyTransaction.ledger_id == ledger_id)
        )
        tx_count = len(list(tx_result.scalars().all()))
        assert tx_count == 0, (
            f"No PrivacyTransaction must be written on exhaustion. Got {tx_count}."
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_spend_budget_exact_boundary_allowed(
    sqlite_async_engine: AsyncEngine,
) -> None:
    """spend_budget() allows spending exactly up to the allocated limit.

    Arrange: Create a PrivacyLedger with allocated=1.0, spent=0.5.
    Act: Spend exactly 0.5 (total = 1.0 = allocated).
    Assert: No exception; total_spent_epsilon == 1.0.
    """
    async with get_async_session(sqlite_async_engine) as s:
        ledger = PrivacyLedger(total_allocated_epsilon=1.0, total_spent_epsilon=0.5)
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    async with get_async_session(sqlite_async_engine) as s:
        await spend_budget(amount=0.5, job_id=10, ledger_id=ledger_id, session=s)

    async with get_async_session(sqlite_async_engine) as s:
        ledger_result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        final_ledger = ledger_result.scalar_one()
        assert abs(final_ledger.total_spent_epsilon - 1.0) < 1e-9, (
            f"Expected total_spent_epsilon == 1.0, got {final_ledger.total_spent_epsilon}"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_spend_budget_rejects_non_positive_amount(
    sqlite_async_engine: AsyncEngine,
) -> None:
    """spend_budget() raises ValueError for zero or negative amount.

    Validates the input guard in the accountant without hitting the DB.
    """
    async with get_async_session(sqlite_async_engine) as s:
        ledger = PrivacyLedger(total_allocated_epsilon=10.0, total_spent_epsilon=0.0)
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    with pytest.raises(ValueError, match="amount must be positive"):
        async with get_async_session(sqlite_async_engine) as s:
            await spend_budget(amount=0.0, job_id=1, ledger_id=ledger_id, session=s)

    with pytest.raises(ValueError, match="amount must be positive"):
        async with get_async_session(sqlite_async_engine) as s:
            await spend_budget(amount=-1.0, job_id=1, ledger_id=ledger_id, session=s)


# ---------------------------------------------------------------------------
# Dummy ML Synthesizer interface tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_dummy_synthesizer_train_returns_model_artifact() -> None:
    """DummyMLSynthesizer.train() returns a ModelArtifact with correct metadata.

    Validates that the dummy synthesizer implements the same interface as
    SynthesisEngine so it can be substituted in integration test pipelines.
    """
    from synth_engine.modules.synthesizer.models import ModelArtifact

    synthesizer = DummyMLSynthesizer()
    artifact = synthesizer.train("test_table", "/fake/path.parquet")

    assert isinstance(artifact, ModelArtifact), f"Expected ModelArtifact, got {type(artifact)}"
    assert artifact.table_name == "test_table"
    assert len(artifact.column_names) > 0, "ModelArtifact must have at least one column"


@pytest.mark.integration
def test_dummy_synthesizer_generate_returns_dataframe() -> None:
    """DummyMLSynthesizer.generate() returns a DataFrame with the requested row count.

    Validates the generate() half of the SynthesisEngine interface contract.
    """
    import pandas as pd

    synthesizer = DummyMLSynthesizer()
    artifact = synthesizer.train("orders", "/fake/orders.parquet")
    df = synthesizer.generate(artifact, n_rows=50)

    assert isinstance(df, pd.DataFrame), f"Expected DataFrame, got {type(df)}"
    assert len(df) == 50, f"Expected 50 rows, got {len(df)}"


@pytest.mark.integration
def test_dummy_synthesizer_generate_raises_on_non_positive_rows() -> None:
    """DummyMLSynthesizer.generate() raises ValueError for n_rows <= 0.

    Mirrors the guard in SynthesisEngine.generate() — the dummy must
    enforce the same contract to be a valid stand-in.
    """
    synthesizer = DummyMLSynthesizer()
    artifact = synthesizer.train("t", "/fake/t.parquet")

    with pytest.raises(ValueError, match="n_rows must be a positive integer"):
        synthesizer.generate(artifact, n_rows=0)

    with pytest.raises(ValueError, match="n_rows must be a positive integer"):
        synthesizer.generate(artifact, n_rows=-5)


@pytest.mark.integration
def test_dummy_synthesizer_train_generate_pipeline() -> None:
    """DummyMLSynthesizer can be used in a train-then-generate pipeline.

    Exercises the complete workflow: train → generate → verify output.
    This is the pattern the E2E pipeline uses to substitute real ML.
    """
    import pandas as pd

    synthesizer = DummyMLSynthesizer(seed=42)
    artifact = synthesizer.train("customers", "/fake/customers.parquet")
    df = synthesizer.generate(artifact, n_rows=100)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 100
    # The dummy synthesizer uses column_names from the artifact
    assert set(df.columns) == set(artifact.column_names)
