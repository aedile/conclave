"""Synthesis job orchestration: OOM pre-flight, training loop, DP accounting.

Contains the core synthesis job lifecycle driver ``_run_synthesis_job_impl``
and its helpers.  This is the implementation backing the Huey task entry
point in ``tasks.py``.

Split from ``tasks.py`` in P26-T26.1 to improve module focus.

Status lifecycle::

    QUEUED → TRAINING → GENERATING → COMPLETE   (success)
                                   ↘ FAILED     (OOM guardrail rejection or RuntimeError)
                                   ↘ FAILED     (BudgetExhaustionError from spend_budget)

DP wiring (P22-T22.2)
---------------------
When a job has ``enable_dp=True``, a ``DPWrapperProtocol`` instance is
constructed via the injected ``_dp_wrapper_factory``.  After training,
``dp_wrapper.epsilon_spent(delta=1e-5)`` is read and stored on the job record
as ``actual_epsilon``.

Privacy budget wiring (P22-T22.3)
----------------------------------
After successful DP training and epsilon recording, ``_spend_budget_fn`` is
called to deduct the spent epsilon from the global ``PrivacyLedger``.

The ``_dp_wrapper_factory`` and ``_spend_budget_fn`` are registered at startup
by the bootstrapper via ``set_dp_wrapper_factory()`` and
``set_spend_budget_fn()`` (ADR-0029).

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.
    - May import from ``shared/`` — this is the approved path for
      ``BudgetExhaustionError``, which replaces the ADR-0033 duck-typing
      pattern now that the exception lives in ``shared/exceptions.py``.

Task: P26-T26.1 — Split Oversized Files (Refactor Only)
Task: P26-T26.2 — Replace ADR-0033 duck-typing with typed BudgetExhaustionError catch
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from synth_engine.modules.synthesizer.guardrails import (
    OOMGuardrailError,
    check_memory_feasibility,
)
from synth_engine.modules.synthesizer.job_finalization import (
    _GENERATION_FAILED_MSG,
    _write_parquet_with_signing,
)
from synth_engine.modules.synthesizer.job_models import SynthesisJob
from synth_engine.shared.exceptions import BudgetExhaustionError
from synth_engine.shared.protocols import DPWrapperProtocol, SpendBudgetProtocol
from synth_engine.shared.security.audit import get_audit_logger

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


def _handle_dp_accounting(
    job: SynthesisJob,
    dp_wrapper: DPWrapperProtocol,
    job_id: int,
) -> None:
    """Record actual epsilon after DP training and optionally spend privacy budget.

    Covers steps 5 and 5b of the synthesis lifecycle:
    - Step 5: read ``epsilon_spent()`` from the DP wrapper and write to
      ``job.actual_epsilon``.
    - Step 5b: if ``_spend_budget_fn`` is registered and epsilon was read
      successfully, call it to deduct from the global ``PrivacyLedger``.
      On :exc:`~synth_engine.shared.exceptions.BudgetExhaustionError`,
      set ``job.status = FAILED`` and return.  On success, emit a
      WORM audit event.

    The audit log call is outside the ``BudgetExhaustionError`` try/except so
    that an audit logger failure cannot corrupt the exhaustion-detection path.

    Args:
        job: The ``SynthesisJob`` record being updated (mutated in place).
        dp_wrapper: The DP training wrapper whose ``epsilon_spent()`` is
            called to read the actual epsilon after training.
        job_id: Job primary key (used for log messages and audit details).

    Returns:
        Nothing.  Raises if a non-budget exception escapes ``_spend_budget_fn``.

    Raises:
        Exception: Any non-``BudgetExhaustionError`` exception raised by
            ``_spend_budget_fn`` propagates to the caller.
    """
    # ------------------------------------------------------------------
    # Step 5: Record actual epsilon
    # ------------------------------------------------------------------
    try:
        actual_eps = dp_wrapper.epsilon_spent(delta=_DP_EPSILON_DELTA)
        job.actual_epsilon = actual_eps
        _logger.info(
            "Job %d: DP training complete, actual_epsilon=%.4f.",
            job_id,
            actual_eps,
        )
    except Exception:  # Broad catch intentional: dp_wrapper.epsilon_spent() is a protocol method
        # whose concrete implementation (Opacus PrivacyEngine.get_epsilon) may raise
        # opacus-specific exceptions not known at this call site.  Training succeeded;
        # we log the failure and continue to COMPLETE with actual_epsilon=None.
        _logger.exception(
            "Job %d: Failed to read epsilon_spent from DP wrapper.",
            job_id,
        )
        # Training succeeded but epsilon accounting failed.
        # Continue to COMPLETE — the artifact is valid, actual_epsilon stays None.

    # ------------------------------------------------------------------
    # Step 5b: Spend privacy budget
    # ------------------------------------------------------------------
    # Only deduct budget if DP was used AND epsilon was successfully read.
    # If epsilon_spent() failed above, actual_epsilon is None and we skip
    # the deduction — no measurable budget was spent from the system's view.
    if _spend_budget_fn is None or job.actual_epsilon is None:
        return

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
    except BudgetExhaustionError:
        # BudgetExhaustionError is now imported directly from shared/exceptions.py,
        # replacing the ADR-0033 duck-typing pattern.  Both modules/synthesizer and
        # modules/privacy import from shared/; there is no import boundary violation.
        _logger.error("Job %d: Privacy budget exhausted — marking FAILED.", job_id)
        job.status = "FAILED"
        job.error_msg = "Privacy budget exhausted"
        return

    # WORM audit event — emitted OUTSIDE the BudgetExhaustionError try/except
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
        except Exception:  # Broad catch intentional: audit logger failure must NOT prevent
            # the status transition — the budget was deducted successfully and the
            # PrivacyTransaction record is already committed.  Log and continue.
            _logger.exception("Job %d: Audit log failed after budget deduction.", job_id)
            # Budget was deducted successfully — continue to COMPLETE
            # even if audit logging fails.  The deduction is recorded in
            # the PrivacyTransaction table by spend_budget() itself.


def _generate_and_finalize(
    job: SynthesisJob,
    engine: SynthesisEngine,
    last_artifact: Any,
    effective_checkpoint_dir: str,
    session: Session,
    job_id: int,
) -> bool:
    """Generate synthetic data and persist the Parquet output (steps 8-10).

    Calls ``engine.generate()``, writes the result as a Parquet file, updates
    ``job.output_path``, and transitions the job to ``COMPLETE``.

    Args:
        job: The ``SynthesisJob`` record being updated (mutated in place).
        engine: The ``SynthesisEngine`` used to produce synthetic rows.
        last_artifact: The trained ``ModelArtifact`` to generate from.
        effective_checkpoint_dir: Filesystem directory for Parquet output.
        session: Open SQLModel ``Session`` for committing status updates.
        job_id: Job primary key (used for log messages).

    Returns:
        ``True`` if generation and persistence succeeded; ``False`` if the job
        was transitioned to ``FAILED`` and the caller should return early.
    """
    # ------------------------------------------------------------------
    # Step 8: Generate synthetic data
    # ------------------------------------------------------------------
    try:
        synthetic_df = engine.generate(last_artifact, n_rows=job.num_rows)
    except RuntimeError as exc:
        _logger.error(
            "Job %d: RuntimeError during generation: %s",
            job_id,
            exc,
        )
        job.status = "FAILED"
        # F4 fix: sanitize error_msg — do not expose raw exception details in
        # the API response.  Full exception is logged above for diagnostics.
        job.error_msg = _GENERATION_FAILED_MSG
        session.add(job)
        session.commit()
        return False

    # ------------------------------------------------------------------
    # Step 9: Persist Parquet output (with optional HMAC signing)
    # ------------------------------------------------------------------
    parquet_out = str(Path(effective_checkpoint_dir) / f"job_{job_id}_synthetic.parquet")
    # F1 fix: wrap _write_parquet_with_signing in a try/except so that
    # OSError or ValueError from Parquet write or HMAC signing transitions
    # the job to FAILED instead of leaving it permanently in GENERATING.
    try:
        _write_parquet_with_signing(synthetic_df, parquet_out)
    except (OSError, ValueError) as exc:
        _logger.error(
            "Job %d: Failed to write Parquet artifact: %s",
            job_id,
            exc,
        )
        job.status = "FAILED"
        # F4 fix: sanitize error_msg for the same reason as RuntimeError above.
        job.error_msg = _GENERATION_FAILED_MSG
        session.add(job)
        session.commit()
        return False

    job.output_path = parquet_out
    _logger.info(
        "Job %d: synthetic Parquet written → %s",
        job_id,
        Path(parquet_out).name,
    )

    # ------------------------------------------------------------------
    # Step 10: GENERATING → COMPLETE
    # ------------------------------------------------------------------
    job.status = "COMPLETE"
    session.add(job)
    session.commit()

    _logger.info(
        "Job %d: COMPLETE (table=%s, output=%s).",
        job_id,
        job.table_name,
        Path(parquet_out).name,
    )
    return True


def _run_synthesis_job_impl(
    job_id: int,
    session: Session,
    engine: SynthesisEngine,
    checkpoint_dir: str | None = None,
    dp_wrapper: DPWrapperProtocol | None = None,
) -> None:
    """Core synthesis job logic with injected dependencies.

    This function drives the full training and generation lifecycle and is
    called by both the public ``run_synthesis_job`` Huey task (with real
    infrastructure) and unit tests (with mocks).  Separating the logic from
    the Huey decorator enables synchronous, dependency-injected testing
    without a Huey worker.

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
       (from ``shared/exceptions.py``): set ``FAILED``,
       ``error_msg="Privacy budget exhausted"``, commit, and return WITHOUT
       persisting the artifact.  Emit a WORM audit event on success.
       The audit call is placed OUTSIDE the ``BudgetExhaustionError`` try/except
       so audit logger failures do not affect budget exhaustion detection.
    7. Save final artifact; set ``artifact_path``, ``current_epoch``.
    8. Set status → ``GENERATING``; commit.
    9. Call ``engine.generate(artifact, n_rows=job.num_rows)`` to produce
       synthetic DataFrame.  On ``RuntimeError``: set ``FAILED`` +
       sanitized ``error_msg``, commit, return.
    10. Write Parquet to ``checkpoint_dir`` as ``job_{id}_synthetic.parquet``.
        If ``ARTIFACT_SIGNING_KEY`` env var is set, write HMAC-SHA256 sidecar.
        On ``OSError`` or ``ValueError``: set ``FAILED`` + sanitized
        ``error_msg``, commit, return.
        Set ``job.output_path``; set status → ``COMPLETE``; commit.

    Args:
        job_id: Primary key of the ``SynthesisJob`` record to process.
        session: Open SQLModel ``Session`` for reading and updating the job.
        engine: ``SynthesisEngine`` instance used to run CTGAN training and
            generation.
        checkpoint_dir: Optional filesystem directory for writing checkpoint
            pickle files and the generated Parquet.  If ``None``, a temporary
            directory is created and cleaned up automatically.
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
        "Starting synthesis job %d "
        "(table=%s, total_epochs=%d, checkpoint_every_n=%d, num_rows=%d).",
        job_id,
        job.table_name,
        job.total_epochs,
        job.checkpoint_every_n,
        job.num_rows,
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
    last_artifact: Any = None

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
            last_artifact = artifact
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

        # ------------------------------------------------------------------
        # 5 + 5b. DP accounting and privacy budget spend
        # ------------------------------------------------------------------
        if dp_wrapper is not None:
            _handle_dp_accounting(job=job, dp_wrapper=dp_wrapper, job_id=job_id)
            # If budget was exhausted, status is now FAILED — commit and return.
            if job.status == "FAILED":
                session.add(job)
                session.commit()
                return

        # ------------------------------------------------------------------
        # 6. Guard: no artifact produced (total_epochs=0 edge case)
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
        session.add(job)
        session.commit()

        # ------------------------------------------------------------------
        # 7. TRAINING → GENERATING
        # ------------------------------------------------------------------
        job.status = "GENERATING"
        session.add(job)
        session.commit()
        _logger.info("Job %d: status set to GENERATING.", job_id)

        # ------------------------------------------------------------------
        # Steps 8-10: Generate synthetic data, persist Parquet, → COMPLETE
        # ------------------------------------------------------------------
        _generate_and_finalize(
            job=job,
            engine=engine,
            last_artifact=last_artifact,
            effective_checkpoint_dir=effective_checkpoint_dir,
            session=session,
            job_id=job_id,
        )

    finally:
        if _tmp_dir_ctx is not None:
            _tmp_dir_ctx.cleanup()
