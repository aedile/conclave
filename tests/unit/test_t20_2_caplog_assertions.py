"""T20.2 — caplog assertion augmentation for silent failure paths.

AC3: At least 5 existing tests augmented with caplog assertions verifying
warning/error log messages on failure paths.

The paths verified in this module:

1. ``_run_synthesis_job_impl`` OOM guardrail rejection → ``logger.error``
   (``synth_engine.modules.synthesizer.tasks``)
2. ``_run_synthesis_job_impl`` RuntimeError during training → ``logger.error``
   (``synth_engine.modules.synthesizer.tasks``)
3. ``_get_parquet_dimensions`` fallback on unreadable Parquet → ``logger.warning``
   (``synth_engine.modules.synthesizer.tasks``)
4. ``spend_budget`` budget exhaustion → ``logger.warning``
   (``synth_engine.modules.privacy.accountant``)
5. ``EgressWriter.rollback`` Saga rollback → ``logger.warning``
   (``synth_engine.modules.subsetting.egress``)

Each test verifies:
- The code-under-test still raises / sets the expected status (existing assertion).
- The expected log record was emitted with the correct level and message fragment.

QA roast finding (T20.1): tests must use caplog.at_level() to capture log
records at the correct level for the correct logger.  Asserting only on absence
of exceptions is insufficient.

Task: P20-T20.2 — Integration Test Expansion (Real Infrastructure)
CONSTITUTION Priority 3: TDD
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from synth_engine.modules.privacy.ledger import (  # noqa: F401 — imported to register SQLModel metadata before create_all
    PrivacyLedger,
    PrivacyTransaction,
)
from synth_engine.shared.db import get_async_engine, get_async_session

# ---------------------------------------------------------------------------
# Helpers shared with test_synthesizer_tasks.py
# ---------------------------------------------------------------------------


def _make_synthesis_job(**kwargs: Any) -> Any:
    """Create a SynthesisJob with default values overridden by kwargs.

    Args:
        **kwargs: Fields to override on the SynthesisJob defaults.

    Returns:
        A SynthesisJob instance.
    """
    from synth_engine.modules.synthesizer.job_models import SynthesisJob

    defaults: dict[str, Any] = {
        "id": 1,
        "status": "QUEUED",
        "current_epoch": 0,
        "total_epochs": 10,
        "artifact_path": None,
        "error_msg": None,
        "table_name": "persons",
        "parquet_path": "/data/persons.parquet",
        "checkpoint_every_n": 5,
    }
    defaults.update(kwargs)
    return SynthesisJob(**defaults)


# ---------------------------------------------------------------------------
# Async engine fixture (SQLite in-memory for accountant tests)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def caplog_async_engine() -> AsyncGenerator[AsyncEngine]:
    """Provide an in-memory async SQLite engine with all SQLModel tables created.

    Yields:
        An :class:`AsyncEngine` pointed at an in-memory SQLite database.
    """
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


# ===========================================================================
# caplog test 1: OOM guardrail rejection emits logger.error
# ===========================================================================


class TestCaplogOOMGuardrailError:
    """Verify OOM guardrail rejection emits an error log message."""

    def test_oom_rejection_emits_error_log(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """OOM guardrail rejection must emit a logger.error with the job ID and error.

        Arrange: Set up a mock session and job.  Patch check_memory_feasibility to
            raise OOMGuardrailError.
        Act: Call _run_synthesis_job_impl.
        Assert:
        - job.status == 'FAILED' (existing correctness assertion).
        - A log record at ERROR level was emitted from the tasks logger.
        - The record message contains 'OOM guardrail rejected job' and the job ID.
        """
        from synth_engine.modules.synthesizer.guardrails import OOMGuardrailError
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=7, status="QUEUED", total_epochs=100, checkpoint_every_n=5)
        mock_session.get.return_value = job
        mock_engine = MagicMock()

        with (
            caplog.at_level(
                logging.ERROR,
                logger="synth_engine.modules.synthesizer.tasks",
            ),
            patch(
                "synth_engine.modules.synthesizer.tasks.check_memory_feasibility",
                side_effect=OOMGuardrailError("6.8 GiB estimated, 4.0 GiB available"),
            ),
        ):
            _run_synthesis_job_impl(
                job_id=7,
                session=mock_session,
                engine=mock_engine,
            )

        # Existing correctness assertion
        assert job.status == "FAILED", f"Expected FAILED, got {job.status!r}"

        # caplog assertion: error log must have been emitted
        error_records = [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "synth_engine.modules.synthesizer.tasks" in r.name
        ]
        assert error_records, (
            f"Expected an ERROR log from synthesizer.tasks on OOM rejection; "
            f"got records: {[r.message for r in caplog.records]}"
        )
        assert any("OOM guardrail rejected job" in r.message for r in error_records), (
            f"Expected 'OOM guardrail rejected job' in ERROR log; "
            f"got: {[r.message for r in error_records]}"
        )
        assert any("7" in r.message for r in error_records), (
            f"Expected job ID '7' in ERROR log; got: {[r.message for r in error_records]}"
        )


# ===========================================================================
# caplog test 2: RuntimeError during training emits logger.error
# ===========================================================================


class TestCaplogRuntimeErrorDuringTraining:
    """Verify RuntimeError during training emits an error log message."""

    def test_runtime_error_training_emits_error_log(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """RuntimeError during training must emit a logger.error with the job ID and message.

        Arrange: Set up a mock session and job.  Make engine.train() raise RuntimeError.
        Act: Call _run_synthesis_job_impl.
        Assert:
        - job.status == 'FAILED' (existing correctness assertion).
        - A log record at ERROR level was emitted from the tasks logger.
        - The record message contains 'RuntimeError during training' and the job ID.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=8, status="QUEUED", total_epochs=5, checkpoint_every_n=3)
        mock_session.get.return_value = job
        mock_engine = MagicMock()
        mock_engine.train.side_effect = RuntimeError("CUDA out of memory")

        with (
            caplog.at_level(
                logging.ERROR,
                logger="synth_engine.modules.synthesizer.tasks",
            ),
            patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"),
        ):
            _run_synthesis_job_impl(
                job_id=8,
                session=mock_session,
                engine=mock_engine,
            )

        # Existing correctness assertion
        assert job.status == "FAILED", f"Expected FAILED, got {job.status!r}"

        # caplog assertion: error log must have been emitted
        error_records = [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "synth_engine.modules.synthesizer.tasks" in r.name
        ]
        assert error_records, (
            f"Expected an ERROR log from synthesizer.tasks on RuntimeError; "
            f"got records: {[r.message for r in caplog.records]}"
        )
        assert any("RuntimeError" in r.message for r in error_records), (
            f"Expected 'RuntimeError' in ERROR log; got: {[r.message for r in error_records]}"
        )
        assert any("8" in r.message for r in error_records), (
            f"Expected job ID '8' in ERROR log; got: {[r.message for r in error_records]}"
        )


# ===========================================================================
# caplog test 3: Parquet metadata read failure emits logger.warning
# ===========================================================================


class TestCaplogParquetMetadataFallback:
    """Verify unreadable Parquet metadata emits a warning log."""

    def test_parquet_metadata_failure_emits_warning_log(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """_get_parquet_dimensions fallback emits a logger.warning when pyarrow fails.

        Arrange: Pass a path that does not exist on disk — pyarrow.parquet.read_metadata
            raises OSError for missing files, triggering the fallback path.
        Act: Call _get_parquet_dimensions with a guaranteed non-existent path.
        Assert:
        - The function returns fallback dimensions (not (0, 0)).
        - A log record at WARNING level was emitted from the tasks logger.
        - The record message contains 'fallback'.

        Note: Since ``import pyarrow.parquet as pq`` is a local import inside
        ``_get_parquet_dimensions``, we trigger the OSError branch naturally by
        passing a path that does not exist — no patching of pyarrow is required.
        This is simpler and more reliable than sys.modules manipulation.
        """
        from synth_engine.modules.synthesizer.tasks import (
            _OOM_FALLBACK_COLUMNS,
            _OOM_FALLBACK_ROWS,
            _get_parquet_dimensions,
        )

        # Use a path that is guaranteed to not exist — triggers OSError in pyarrow.
        # The trailing .parquet extension ensures pyarrow does not mistake it for a dir.
        nonexistent_path = "/tmp/t202_caplog_test_does_not_exist_abc123.parquet"  # nosec B108 — test-only temp path, no sensitive data

        with caplog.at_level(
            logging.WARNING,
            logger="synth_engine.modules.synthesizer.tasks",
        ):
            rows, cols = _get_parquet_dimensions(nonexistent_path)

        # Correctness: fallback dimensions returned
        assert rows == _OOM_FALLBACK_ROWS, (
            f"Expected fallback rows {_OOM_FALLBACK_ROWS}, got {rows}"
        )
        assert cols == _OOM_FALLBACK_COLUMNS, (
            f"Expected fallback cols {_OOM_FALLBACK_COLUMNS}, got {cols}"
        )

        # caplog assertion: warning must have been emitted
        warning_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "synth_engine.modules.synthesizer.tasks" in r.name
        ]
        assert warning_records, (
            f"Expected a WARNING log from synthesizer.tasks on Parquet metadata failure; "
            f"got records: {[(r.levelname, r.message) for r in caplog.records]}"
        )
        assert any("fallback" in r.message.lower() for r in warning_records), (
            f"Expected 'fallback' in WARNING log; got: {[r.message for r in warning_records]}"
        )


# ===========================================================================
# caplog test 4: spend_budget exhaustion emits logger.warning
# ===========================================================================


@pytest.mark.asyncio
async def test_spend_budget_exhaustion_emits_warning_log(
    caplog_async_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """spend_budget() budget exhaustion must emit a logger.warning before raising.

    Arrange: Create a PrivacyLedger with total_allocated=1.0, total_spent=0.95.
    Act: Call spend_budget(0.1, ...) — would require 1.05 total (exceeds 1.0).
    Assert:
    - BudgetExhaustionError is raised (existing correctness assertion).
    - A log record at WARNING level was emitted from the accountant logger.
    - The record message contains 'Budget exhausted'.
    """
    from decimal import Decimal

    from synth_engine.modules.privacy.accountant import spend_budget
    from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError

    async with get_async_session(caplog_async_engine) as s:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("1.0"),
            total_spent_epsilon=Decimal("0.95"),
        )
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    with caplog.at_level(
        logging.WARNING,
        logger="synth_engine.modules.privacy.accountant",
    ):
        with pytest.raises(BudgetExhaustionError, match="budget exhausted"):
            async with get_async_session(caplog_async_engine) as s:
                await spend_budget(amount=0.1, job_id=99, ledger_id=ledger_id, session=s)

    # caplog assertion: warning must have been emitted
    warning_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "synth_engine.modules.privacy.accountant" in r.name
    ]
    assert warning_records, (
        f"Expected a WARNING log from privacy.accountant on budget exhaustion; "
        f"got records: {[(r.levelname, r.name, r.message) for r in caplog.records]}"
    )
    assert any("Budget exhausted" in r.message for r in warning_records), (
        f"Expected 'Budget exhausted' in WARNING log; got: {[r.message for r in warning_records]}"
    )


# ===========================================================================
# caplog test 5: EgressWriter.rollback() Saga rollback emits logger.warning
# ===========================================================================


class TestCaplogEgressWriterRollback:
    """Verify EgressWriter.rollback() Saga rollback emits a warning log."""

    def test_saga_rollback_emits_warning_log(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """EgressWriter.rollback() must emit a logger.warning listing tables to truncate.

        Arrange: Create an EgressWriter backed by a mock SQLAlchemy engine.
            Simulate a prior write by directly mutating ``_written_tables``.
        Act: Call ``egress.rollback()``.
        Assert:
        - A log record at WARNING level was emitted from the egress logger.
        - The record message contains 'Saga rollback' and the count of tables.

        The mock engine is sufficient because this test verifies the logging path,
        not the actual TRUNCATE SQL.  The integration test
        ``test_saga_rollback_leaves_target_clean`` in test_subsetting_integration.py
        verifies the database-level behaviour.
        """
        from synth_engine.modules.subsetting.egress import EgressWriter

        # Build a mock engine that returns a mock connection context
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn

        egress = EgressWriter(target_engine=mock_engine)
        # Simulate that two tables were written — normally set by write()
        egress._written_tables = {  # type: ignore[attr-defined]  # direct mutation for test setup only
            "customers": 5,
            "orders": 10,
        }

        with caplog.at_level(
            logging.WARNING,
            logger="synth_engine.modules.subsetting.egress",
        ):
            egress.rollback()

        # caplog assertion: warning must have been emitted
        warning_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "synth_engine.modules.subsetting.egress" in r.name
        ]
        assert warning_records, (
            f"Expected a WARNING log from subsetting.egress on Saga rollback; "
            f"got records: {[(r.levelname, r.name, r.message) for r in caplog.records]}"
        )
        assert any("Saga rollback" in r.message for r in warning_records), (
            f"Expected 'Saga rollback' in WARNING log; got: {[r.message for r in warning_records]}"
        )
        assert any("2" in r.message for r in warning_records), (
            f"Expected table count '2' in WARNING log; got: {[r.message for r in warning_records]}"
        )
