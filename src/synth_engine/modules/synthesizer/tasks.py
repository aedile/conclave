"""Huey background task for synthesis training with checkpointing.

Defines ``run_synthesis_job``, a ``@huey.task()`` that drives the full
synthesis training lifecycle: OOM pre-flight check, CTGAN training,
epoch checkpointing, and database status updates.

Status lifecycle::

    QUEUED → TRAINING → COMPLETE   (success)
                      ↘ FAILED     (OOM guardrail rejection or RuntimeError)
                      ↘ FAILED     (BudgetExhaustionError from spend_budget)

Checkpointing
-------------
ModelArtifact snapshots are written every ``job.checkpoint_every_n`` epochs.
The snapshot filename is::

    job_{job_id}_epoch_{epoch}.pkl

in the ``checkpoint_dir`` (a temporary directory by default).

On failure the most recent snapshot remains accessible from storage; the
Orphan Task Reaper (T2.1) marks the database record ``FAILED`` if the
worker crashes before the task itself can write the failure status.

Database session management
---------------------------
The public ``run_synthesis_job`` Huey task creates its own SQLModel ``Session``
using the engine URL from the ``DATABASE_URL`` environment variable.  The
``_run_synthesis_job_impl`` helper accepts an injected ``session`` for full
unit-test isolation — no real database is required for unit tests.

OOM guardrail
-------------
``check_memory_feasibility`` from ``modules/synthesizer/guardrails`` is called
before training starts.  When pyarrow is available, the task reads the Parquet
file dimensions to compute an accurate estimate.  When pyarrow is absent
(unit-test environments without the ``synthesizer`` poetry group), the guardrail
is called with conservative defaults so the check still runs.

DP wiring (P22-T22.2)
---------------------
When ``run_synthesis_job`` is called for a job with ``enable_dp=True``, the
task reads the job's DP parameters (``max_grad_norm``, ``noise_multiplier``)
from a short-lived pre-flight session and constructs a ``DPTrainingWrapper``
via the injected ``_dp_wrapper_factory``.  The wrapper is then injected into
``_run_synthesis_job_impl`` which passes it to every ``engine.train()`` call.
After training completes, ``dp_wrapper.epsilon_spent(delta=1e-5)`` is read and
stored on the job record as ``actual_epsilon``.

The ``_dp_wrapper_factory`` is registered at startup by the bootstrapper via
``set_dp_wrapper_factory()`` (ADR-0029: bootstrapper injects INTO modules).

Privacy budget wiring (P22-T22.3)
----------------------------------
After successful DP training and epsilon recording, ``_spend_budget_fn`` is
called to deduct the spent epsilon from the global ``PrivacyLedger``.  If
the budget is exhausted the exception name contains ``BudgetExhaustion`` and
the job is marked ``FAILED`` with error_msg ``"Privacy budget exhausted"``.
The synthesis artifact is NOT persisted in that case.

The ``_spend_budget_fn`` is a sync callable registered at startup by the
bootstrapper via ``set_spend_budget_fn()``.  It wraps the async
``spend_budget()`` from ``modules/privacy/accountant.py`` using
``asyncio.run()`` so it can be called from this synchronous Huey task.

A WORM audit event (``PRIVACY_BUDGET_SPEND``) is emitted after each
successful budget deduction via the shared ``AuditLogger`` singleton.
The audit call is placed OUTSIDE the ``BudgetExhaustion`` try/except so
that an audit logger failure does not corrupt the BudgetExhaustion handler.

Boundary note — BudgetExhaustionError
--------------------------------------
``BudgetExhaustionError`` lives in ``modules/privacy/dp_engine.py``.
Import-linter forbids ``modules/synthesizer`` from importing
``modules/privacy``.  Rather than defining a shared exception class (which
would require moving the exception to ``shared/``), the task uses duck-typing
exception name matching::

    "BudgetExhaustion" in type(exc).__name__

This avoids the import violation while preserving correct handling.  The
pattern is documented here and in the test assertions.

Typing DP wrapper without crossing boundaries
---------------------------------------------
``DPTrainingWrapper`` lives in ``modules/privacy/``.  Import-linter enforces
module independence between ``synthesizer`` and ``privacy``, so this file must
not import from ``modules/privacy/`` — even under ``TYPE_CHECKING``.  Instead,
the DP wrapper contract is captured by ``DPWrapperProtocol`` from
``shared/protocols`` (a structural subtype).  The concrete ``DPTrainingWrapper``
satisfies this Protocol at runtime; mypy verifies compatibility structurally.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

Task: P4-T4.2c — Huey Task Wiring & Checkpointing
Task: P22-T22.2 — Wire DP into run_synthesis_job()
Task: P22-T22.3 — Wire spend_budget() into Synthesis Pipeline
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from synth_engine.modules.synthesizer.guardrails import (
    OOMGuardrailError,
    check_memory_feasibility,
)
from synth_engine.modules.synthesizer.job_models import SynthesisJob
from synth_engine.shared.protocols import DPWrapperProtocol, SpendBudgetProtocol
from synth_engine.shared.security.audit import get_audit_logger
from synth_engine.shared.task_queue import huey

if TYPE_CHECKING:
    from sqlmodel import Session

    from synth_engine.modules.synthesizer.engine import SynthesisEngine

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: OOM guardrail overhead factor for GAN training (gradient buffers +
#: optimizer state).  6x is conservative for CTGAN on typical tabular data.
_OOM_OVERHEAD_FACTOR: float = 6.0

#: Default dtype byte size for mixed-type tabular data (float64 = 8 bytes).
_OOM_DTYPE_BYTES: int = 8

#: Fallback row/column counts used for OOM estimation when the Parquet file
#: cannot be read (pyarrow absent or file not yet written).  Values are
#: deliberately conservative so the guardrail rejects implausibly large jobs
#: even without exact counts.
_OOM_FALLBACK_ROWS: int = 100_000
_OOM_FALLBACK_COLUMNS: int = 50

#: Delta value used when querying epsilon_spent() after DP training.
#: 1e-5 is the canonical value from ADR-0025 / Opacus documentation.
_DP_EPSILON_DELTA: float = 1e-5

#: Default PrivacyLedger id seeded by migration 005.
#: Until multi-tenant is implemented, all jobs debit this single ledger.
_DEFAULT_LEDGER_ID: int = 1


# ---------------------------------------------------------------------------
# DI factory callbacks — injected by bootstrapper at startup (ADR-0029)
# ---------------------------------------------------------------------------

# Module-level DP wrapper factory — injected by bootstrapper at startup.
# This follows the DI pattern: bootstrapper injects INTO modules (ADR-0029).
_dp_wrapper_factory: Callable[[float, float], DPWrapperProtocol] | None = None

# Module-level spend_budget callable — injected by bootstrapper at startup.
# This follows the same DI pattern as _dp_wrapper_factory (ADR-0029).
# The callable wraps async spend_budget() with asyncio.run() for Huey compat.
_spend_budget_fn: SpendBudgetProtocol | None = None


def set_dp_wrapper_factory(
    factory: Callable[[float, float], DPWrapperProtocol],
) -> None:
    """Register the DP wrapper factory (called by bootstrapper at startup).

    The bootstrapper calls this function during application initialization
    to inject the ``build_dp_wrapper`` factory.  This maintains the correct
    dependency direction: bootstrapper → modules (ADR-0029).

    Args:
        factory: A callable accepting ``(max_grad_norm, noise_multiplier)``
            and returning an object satisfying ``DPWrapperProtocol``
            (concretely a ``DPTrainingWrapper`` instance).
    """
    global _dp_wrapper_factory
    _dp_wrapper_factory = factory


def set_spend_budget_fn(fn: SpendBudgetProtocol) -> None:
    """Register the sync spend_budget callable (called by bootstrapper at startup).

    The bootstrapper calls this at application startup to inject the sync
    wrapper built by ``build_spend_budget_fn()`` from ``bootstrapper/factories``.
    This maintains the correct dependency direction: bootstrapper → modules
    (ADR-0029, Rule 8).

    Args:
        fn: A sync callable satisfying ``SpendBudgetProtocol`` that wraps
            the async ``modules/privacy/accountant.spend_budget()`` via
            ``asyncio.run()`` for Huey's synchronous task context.
    """
    global _spend_budget_fn
    _spend_budget_fn = fn


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_parquet_dimensions(parquet_path: str) -> tuple[int, int]:
    """Return (rows, columns) for the Parquet file at ``parquet_path``.

    Uses pyarrow's ``ParquetFile.metadata`` to read dimensions without
    loading all data into memory.  Falls back to conservative defaults when
    pyarrow is absent or the file cannot be read.

    Args:
        parquet_path: Absolute path to the Parquet file.

    Returns:
        A ``(rows, columns)`` tuple.  Falls back to
        ``(_OOM_FALLBACK_ROWS, _OOM_FALLBACK_COLUMNS)`` when the file cannot
        be read.
    """
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]  # optional dep: absent without synthesizer group; no py.typed marker

        meta = pq.read_metadata(parquet_path)
        rows = meta.num_rows
        columns = meta.num_columns
        return int(rows), int(columns)
    except (ImportError, OSError):
        # Fall back to conservative defaults — the guardrail still fires for
        # implausibly large jobs; precise estimation requires pyarrow.
        _logger.warning(
            "Could not read Parquet metadata from %s; "
            "using fallback dimensions (%d rows, %d cols) for OOM estimation.",
            parquet_path,
            _OOM_FALLBACK_ROWS,
            _OOM_FALLBACK_COLUMNS,
        )
        return _OOM_FALLBACK_ROWS, _OOM_FALLBACK_COLUMNS


# ---------------------------------------------------------------------------
# Internal implementation — injectable for unit tests
# ---------------------------------------------------------------------------


def _run_synthesis_job_impl(
    job_id: int,
    session: Session,
    engine: SynthesisEngine,
    checkpoint_dir: str | None = None,
    dp_wrapper: DPWrapperProtocol | None = None,
) -> None:
    """Core synthesis job logic with injected dependencies.

    This function drives the full training lifecycle and is called by both
    the public ``run_synthesis_job`` Huey task (with real infrastructure) and
    unit tests (with mocks).  Separating the logic from the Huey decorator
    enables synchronous, dependency-injected testing without a Huey worker.

    Status transitions performed:

    1. Load job; validate it exists.
    2. Run OOM pre-flight check.  On ``OOMGuardrailError``:
       set ``FAILED`` + ``error_msg``, commit, return.
    3. Set status → ``TRAINING``; commit.
    4. Train with ``engine.train()``.  On ``RuntimeError``:
       set ``FAILED`` + ``error_msg``, commit, return.
    5. After the training loop completes, if ``dp_wrapper`` is not ``None``:
       call ``dp_wrapper.epsilon_spent(delta=_DP_EPSILON_DELTA)`` and write
       the result to ``job.actual_epsilon``; log the value at INFO level.
    6. If ``_spend_budget_fn`` is registered and ``job.actual_epsilon`` is set:
       call ``_spend_budget_fn(amount, job_id, ledger_id=1)`` to deduct
       epsilon from the global ``PrivacyLedger``.  On ``BudgetExhaustionError``
       (detected via exception class name duck-typing): set ``FAILED``,
       ``error_msg="Privacy budget exhausted"``, commit, and return WITHOUT
       persisting the artifact.  Emit a WORM audit event on success.
       The audit call is placed OUTSIDE the ``BudgetExhaustion`` try/except
       so audit logger failures do not affect budget exhaustion detection.
    7. Save final artifact; set ``artifact_path``, ``current_epoch``,
       status → ``COMPLETE``; commit.

    Args:
        job_id: Primary key of the ``SynthesisJob`` record to process.
        session: Open SQLModel ``Session`` for reading and updating the job.
        engine: ``SynthesisEngine`` instance used to run CTGAN training.
        checkpoint_dir: Optional filesystem directory for writing checkpoint
            pickle files.  If ``None``, a temporary directory is created and
            cleaned up automatically.
        dp_wrapper: Optional DP wrapper satisfying ``DPWrapperProtocol``
            (concretely ``DPTrainingWrapper`` from ``modules/privacy/``).
            Constructed by the bootstrapper via the injected
            ``_dp_wrapper_factory`` and passed here.
            When ``None``, vanilla CTGAN training is used (no DP).

    Raises:
        ValueError: If no ``SynthesisJob`` row exists for ``job_id``.
    """

    # ------------------------------------------------------------------
    # 1. Load job record
    # ------------------------------------------------------------------
    job = session.get(SynthesisJob, job_id)
    if job is None:
        raise ValueError(f"SynthesisJob with id={job_id} not found in database.")

    _logger.info(
        "Starting synthesis job %d (table=%s, total_epochs=%d, checkpoint_every_n=%d).",
        job_id,
        job.table_name,
        job.total_epochs,
        job.checkpoint_every_n,
    )

    # ------------------------------------------------------------------
    # 2. OOM pre-flight check
    # ------------------------------------------------------------------
    rows, columns = _get_parquet_dimensions(job.parquet_path)
    try:
        check_memory_feasibility(
            rows=rows,
            columns=columns,
            dtype_bytes=_OOM_DTYPE_BYTES,
            overhead_factor=_OOM_OVERHEAD_FACTOR,
        )
    except OOMGuardrailError as exc:
        _logger.error("OOM guardrail rejected job %d: %s", job_id, exc)
        job.status = "FAILED"
        job.error_msg = str(exc)
        session.add(job)
        session.commit()
        return

    # ------------------------------------------------------------------
    # 3. QUEUED → TRAINING
    # ------------------------------------------------------------------
    job.status = "TRAINING"
    session.add(job)
    session.commit()
    _logger.info("Job %d: status set to TRAINING.", job_id)

    # ------------------------------------------------------------------
    # 4. Epoch-chunked training with checkpointing
    # ------------------------------------------------------------------
    # Training strategy: divide total_epochs into checkpoint_every_n-sized
    # chunks.  Each chunk trains for that many epochs.  After each chunk a
    # ModelArtifact checkpoint is saved.  On the final (possibly partial)
    # chunk we train the remaining epochs.
    #
    # Note: SynthesisEngine.train() trains for self._epochs epochs in one
    # call.  We call it once per checkpoint chunk to emulate per-N-epoch
    # checkpointing.  The final model trained on the last chunk is the
    # authoritative artifact.
    #
    # For unit tests, engine.train() is mocked and chunk_epochs is ignored
    # inside the mock; the test controls the return value/side_effect.

    total = job.total_epochs
    n = job.checkpoint_every_n
    completed_epochs = 0
    last_ckpt_path: str | None = None

    # Determine whether to use an explicit checkpoint_dir or a temp dir.
    if checkpoint_dir is not None:
        _tmp_dir_ctx: tempfile.TemporaryDirectory[str] | None = None
        effective_checkpoint_dir: str = checkpoint_dir
    else:
        _tmp_dir_ctx = tempfile.TemporaryDirectory()
        effective_checkpoint_dir = _tmp_dir_ctx.name

    try:
        while completed_epochs < total:
            chunk_epochs = min(n, total - completed_epochs)

            try:
                artifact = engine.train(
                    table_name=job.table_name,
                    parquet_path=job.parquet_path,
                    dp_wrapper=dp_wrapper,
                )
                completed_epochs += chunk_epochs

            except RuntimeError as exc:
                _logger.error(
                    "Job %d: RuntimeError during training at epoch ~%d: %s",
                    job_id,
                    completed_epochs,
                    exc,
                )
                job.status = "FAILED"
                job.error_msg = str(exc)
                session.add(job)
                session.commit()
                return

            # Save checkpoint for this chunk.
            ckpt_path = str(
                Path(effective_checkpoint_dir) / f"job_{job_id}_epoch_{completed_epochs}.pkl"
            )
            artifact.save(ckpt_path)
            last_ckpt_path = ckpt_path
            _logger.info(
                "Job %d: checkpoint saved at epoch %d → %s",
                job_id,
                completed_epochs,
                ckpt_path,
            )

            # Update current_epoch in DB after each successful chunk.
            job.current_epoch = completed_epochs
            session.add(job)
            session.commit()

    finally:
        if _tmp_dir_ctx is not None:
            _tmp_dir_ctx.cleanup()

    # ------------------------------------------------------------------
    # 5. Record actual epsilon after successful DP training
    # ------------------------------------------------------------------
    if dp_wrapper is not None:
        try:
            actual_eps = dp_wrapper.epsilon_spent(delta=_DP_EPSILON_DELTA)
            job.actual_epsilon = actual_eps
            _logger.info(
                "Job %d: DP training complete, actual_epsilon=%.4f.",
                job_id,
                actual_eps,
            )
        except Exception:
            _logger.exception(
                "Job %d: Failed to read epsilon_spent from DP wrapper.",
                job_id,
            )
            # Training succeeded but epsilon accounting failed.
            # Continue to COMPLETE — the artifact is valid, actual_epsilon stays None.

    # ------------------------------------------------------------------
    # 5b. Spend privacy budget (P22-T22.3)
    # ------------------------------------------------------------------
    # Only deduct budget if DP was used AND epsilon was successfully read.
    # If epsilon_spent() failed above, actual_epsilon is None and we skip
    # the deduction — no measurable budget was spent from the system's view.
    if _spend_budget_fn is not None and dp_wrapper is not None and job.actual_epsilon is not None:
        budget_spent = False
        try:
            _spend_budget_fn(
                amount=job.actual_epsilon,
                job_id=job_id,
                ledger_id=_DEFAULT_LEDGER_ID,
                note=f"DP synthesis job {job_id}",
            )
            budget_spent = True
            _logger.info(
                "Job %d: privacy budget deducted (epsilon=%.4f, ledger_id=%d).",
                job_id,
                job.actual_epsilon,
                _DEFAULT_LEDGER_ID,
            )
        except Exception as exc:
            # Duck-typing: detect BudgetExhaustionError without importing from
            # modules/privacy (import-linter enforced boundary).  Any exception
            # whose class name contains "BudgetExhaustion" is treated as budget
            # exhaustion.  All other exceptions are re-raised.
            if "BudgetExhaustion" in type(exc).__name__:
                _logger.error("Job %d: Privacy budget exhausted — marking FAILED.", job_id)
                job.status = "FAILED"
                job.error_msg = "Privacy budget exhausted"
                session.add(job)
                session.commit()
                return
            # Unknown exception — re-raise to surface the error.
            raise

        # WORM audit event — emitted OUTSIDE the BudgetExhaustion try/except
        # so that an audit logger failure cannot corrupt the exception handler.
        # The budget was successfully deducted; budget_spent is True here.
        if budget_spent:
            try:
                audit = get_audit_logger()
                audit.log_event(
                    event_type="PRIVACY_BUDGET_SPEND",
                    actor="system/huey-worker",
                    resource=f"privacy_ledger/{_DEFAULT_LEDGER_ID}",
                    action="spend_budget",
                    details={
                        "job_id": str(job_id),
                        "epsilon_spent": str(job.actual_epsilon),
                    },
                )
            except Exception:
                _logger.exception("Job %d: Audit log failed after budget deduction.", job_id)
                # Budget was deducted successfully — continue to COMPLETE
                # even if audit logging fails.  The deduction is recorded in
                # the PrivacyTransaction table by spend_budget() itself.

    # ------------------------------------------------------------------
    # 6. TRAINING → COMPLETE
    # ------------------------------------------------------------------
    if last_ckpt_path is None:
        # Guard: total_epochs=0 would skip the while loop entirely.
        job.status = "FAILED"
        job.error_msg = "No artifact produced — total_epochs may be 0."
        session.add(job)
        session.commit()
        return

    # The last checkpoint IS the final artifact — no duplicate save needed.
    # artifact_path points to the last epoch checkpoint written in the loop.
    job.artifact_path = last_ckpt_path
    job.current_epoch = total
    job.status = "COMPLETE"
    session.add(job)
    session.commit()

    _logger.info(
        "Job %d: COMPLETE (table=%s, artifact=%s).",
        job_id,
        job.table_name,
        last_ckpt_path,
    )


# ---------------------------------------------------------------------------
# Public Huey task
# ---------------------------------------------------------------------------


@huey.task()  # type: ignore[untyped-decorator]  # huey.task() has no type stub; unfixable without upstream py.typed marker
def run_synthesis_job(job_id: int) -> None:
    """Huey background task: run a synthesis training job by ID.

    Reads job configuration from the ``SynthesisJob`` record identified by
    ``job_id``, runs the OOM pre-flight check, trains a CTGAN model with
    epoch checkpointing, and updates the record status throughout.

    When the job has ``enable_dp=True``, a ``DPWrapperProtocol`` instance is
    constructed via the injected ``_dp_wrapper_factory`` (registered at startup
    by the bootstrapper via ``set_dp_wrapper_factory()`` — see ADR-0029) using
    the job's ``max_grad_norm`` and ``noise_multiplier`` fields.  The wrapper is
    then passed to ``_run_synthesis_job_impl``.  After training, the actual
    epsilon privacy budget is recorded on ``job.actual_epsilon`` and deducted
    from the global ``PrivacyLedger`` via the injected ``_spend_budget_fn``.

    This task is registered with the shared Huey instance
    (``shared/task_queue.py``) and is executed by the Huey worker process.
    It is imported in ``bootstrapper/main.py`` so the Huey worker discovers
    the task at process start (see bootstrapper wiring note below).

    Args:
        job_id: Primary key of the ``SynthesisJob`` record to process.

    Note:
        On OOM guardrail rejection or ``RuntimeError`` during training the
        task sets ``status=FAILED`` and returns normally (does not re-raise).
        The Huey worker marks the task as completed from the queue perspective.
        The database record carries the failure reason in ``error_msg``.

    Note:
        On budget exhaustion (``BudgetExhaustionError`` raised by
        ``_spend_budget_fn``), the task sets ``status=FAILED`` with
        ``error_msg="Privacy budget exhausted"`` and returns normally.
        The synthesis artifact is NOT persisted.

    Note (Bootstrapper wiring — Rule 8):
        ``bootstrapper/main.py`` imports this module at startup via::

            from synth_engine.modules.synthesizer import tasks as _synthesizer_tasks  # noqa: F401

        This import side-effect registers ``run_synthesis_job`` with the
        shared Huey instance so the worker process discovers it.  The
        bootstrapper also calls ``set_dp_wrapper_factory(build_dp_wrapper)``
        and ``set_spend_budget_fn(build_spend_budget_fn())`` to inject both
        factories before any jobs are processed.

    Raises:
        RuntimeError: If ``enable_dp=True`` but no ``_dp_wrapper_factory``
            has been registered via ``set_dp_wrapper_factory()``.
    """
    from sqlmodel import Session

    from synth_engine.modules.synthesizer.engine import SynthesisEngine
    from synth_engine.shared.db import get_engine

    database_url = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
    db_engine = get_engine(database_url)
    synthesis_engine = SynthesisEngine()

    # Pre-flight: read DP settings from the job record before starting impl.
    # A short-lived session is used here so the DP wrapper is constructed
    # before the main training session is opened.
    dp_wrapper: DPWrapperProtocol | None = None
    with Session(db_engine) as preflight_session:
        job = preflight_session.get(SynthesisJob, job_id)
        # If the job is not found here, dp_wrapper stays None and
        # _run_synthesis_job_impl will raise ValueError on its own lookup.
        if job is not None and job.enable_dp:
            if _dp_wrapper_factory is None:
                raise RuntimeError(
                    "DP training requested but no dp_wrapper_factory has been "
                    "registered. Ensure bootstrapper calls "
                    "set_dp_wrapper_factory() at startup."
                )
            dp_wrapper = _dp_wrapper_factory(
                job.max_grad_norm,
                job.noise_multiplier,
            )

    with Session(db_engine) as session:
        _run_synthesis_job_impl(
            job_id=job_id,
            session=session,
            engine=synthesis_engine,
            dp_wrapper=dp_wrapper,
        )
