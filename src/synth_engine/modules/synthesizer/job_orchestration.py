"""Synthesis job orchestration: step-based lifecycle driver (T35.1, ADR-0038).

The former 232-line god-function is replaced by a step-based orchestrator
that delegates OOM checking, Training, DP Accounting, and Generation to
discrete, independently-testable step classes.  The orchestrator is the
sole owner of ``job.status`` transitions (AC4).

Step classes (``OomCheckStep``, ``TrainingStep``, ``DpAccountingStep``,
``GenerationStep``) are accessible from this module and re-exported from
``job_steps`` so both old import paths (``job_orchestration.xxx``) and new
paths (``job_steps.xxx``) work transparently.

``DpAccountingStep`` and ``_handle_dp_accounting`` are defined in
``dp_accounting.py`` and re-imported here (T43.1).  ``_spend_budget_fn``
and ``get_audit_logger`` remain as module-level names in this module so
that existing test patch paths continue to work::

    patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn")
    patch("synth_engine.modules.synthesizer.job_orchestration.get_audit_logger")

Status lifecycle::

    QUEUED → TRAINING → GENERATING → COMPLETE   (success)
                                   ↘ FAILED     (OOM, RuntimeError, BudgetExhaustion,
                                                 EpsilonMeasurement, AuditWrite)

Step sequence (loop): OomCheckStep → TrainingStep → DpAccountingStep → GenerationStep.
OomCheckStep is the first step in the pipeline (AC4 — orchestrator is sole status owner).

DP wiring (P22-T22.2): ``_dp_wrapper_factory`` injected by bootstrapper.
Budget wiring (P22-T22.3): ``_spend_budget_fn`` injected by bootstrapper.
Both registered at startup via ``set_dp_wrapper_factory()`` / ``set_spend_budget_fn()``.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

Task: P26-T26.1, P26-T26.2, T35.1 — ADR-0038
Task: T38.1 — Fail job when WORM audit write fails after budget deduction
Task: T43.1 — Extract dp_accounting.py from job_orchestration.py
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from synth_engine.modules.synthesizer.dp_accounting import (
    _AUDIT_RECONCILIATION_MSG as _AUDIT_RECONCILIATION_MSG,
)
from synth_engine.modules.synthesizer.dp_accounting import (
    DpAccountingStep as DpAccountingStep,
)
from synth_engine.modules.synthesizer.dp_accounting import (
    _handle_dp_accounting as _handle_dp_accounting,
)
from synth_engine.modules.synthesizer.guardrails import OOMGuardrailError
from synth_engine.modules.synthesizer.guardrails import (
    check_memory_feasibility as check_memory_feasibility,
)
from synth_engine.modules.synthesizer.job_finalization import _GENERATION_FAILED_MSG
from synth_engine.modules.synthesizer.job_finalization import (
    _write_parquet_with_signing as _write_parquet_with_signing,
)
from synth_engine.modules.synthesizer.job_models import SynthesisJob
from synth_engine.shared.errors import safe_error_msg
from synth_engine.shared.protocols import DPWrapperProtocol, SpendBudgetProtocol
from synth_engine.shared.security.audit import get_audit_logger as get_audit_logger

if TYPE_CHECKING:
    from sqlmodel import Session

    from synth_engine.modules.synthesizer.engine import SynthesisEngine

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants (kept for backward-compat; re-exported by tasks.py)
# ---------------------------------------------------------------------------

_OOM_OVERHEAD_FACTOR: float = 6.0
_OOM_DTYPE_BYTES: int = 8
_OOM_FALLBACK_ROWS: int = 100_000
_OOM_FALLBACK_COLUMNS: int = 50


# ---------------------------------------------------------------------------
# DI factory callbacks — injected by bootstrapper at startup (ADR-0029)
# ---------------------------------------------------------------------------

_dp_wrapper_factory: Callable[[float, float], DPWrapperProtocol] | None = None
_spend_budget_fn: SpendBudgetProtocol | None = None


def set_dp_wrapper_factory(
    factory: Callable[[float, float], DPWrapperProtocol],
) -> None:
    """Register the DP wrapper factory (called by bootstrapper at startup).

    Args:
        factory: Callable ``(max_grad_norm, noise_multiplier) → DPWrapperProtocol``.
    """
    global _dp_wrapper_factory
    _dp_wrapper_factory = factory


def set_spend_budget_fn(fn: SpendBudgetProtocol) -> None:
    """Register the sync spend_budget callable (called by bootstrapper at startup).

    Also writes ``fn`` to ``job_steps._spend_budget_fn`` so the step module
    sees the live value (ADR-0029, Rule 8).

    Args:
        fn: Sync ``SpendBudgetProtocol`` callable wrapping async ``spend_budget()``.
    """
    global _spend_budget_fn
    _spend_budget_fn = fn
    # Late import avoids circular dependency (job_steps re-exports from here).
    import synth_engine.modules.synthesizer.job_steps as _steps_mod

    _steps_mod._spend_budget_fn = fn


# ---------------------------------------------------------------------------
# Internal helpers (defined here to preserve patch-path compatibility)
# ---------------------------------------------------------------------------


def _get_parquet_dimensions(parquet_path: str) -> tuple[int, int]:
    """Return (rows, columns) for the Parquet file at ``parquet_path``.

    Falls back to ``(_OOM_FALLBACK_ROWS, _OOM_FALLBACK_COLUMNS)`` when
    pyarrow is absent or the file cannot be read.

    Args:
        parquet_path: Absolute path to the Parquet file.

    Returns:
        A ``(rows, columns)`` tuple.
    """
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        meta = pq.read_metadata(parquet_path)
        return int(meta.num_rows), int(meta.num_columns)
    except (ImportError, OSError):
        _logger.warning(
            "Could not read Parquet metadata from %s; using fallback %d x %d.",
            parquet_path,
            _OOM_FALLBACK_ROWS,
            _OOM_FALLBACK_COLUMNS,
        )
        return _OOM_FALLBACK_ROWS, _OOM_FALLBACK_COLUMNS


def _commit_job(job: SynthesisJob, session: Session) -> None:
    """Add and commit ``job`` in one call — reduces repetition in the orchestrator.

    Args:
        job: The ``SynthesisJob`` record to persist.
        session: Open SQLModel ``Session``.
    """
    session.add(job)
    session.commit()


def _build_ctx(
    job: SynthesisJob,
    session: Session,
    engine: SynthesisEngine,
    dp_wrapper: DPWrapperProtocol | None,
    checkpoint_dir: str,
) -> JobContext:
    """Construct the ``JobContext`` for the orchestrator step pipeline.

    Extracted to keep ``_run_synthesis_job_impl`` under 50 lines (AC1).

    Args:
        job: The ``SynthesisJob`` record being executed.
        session: Open SQLModel ``Session``.
        engine: ``SynthesisEngine`` for training and generation.
        dp_wrapper: Optional DP wrapper (``None`` → vanilla CTGAN).
        checkpoint_dir: Filesystem directory for checkpoints and Parquet files.

    Returns:
        A fully initialised ``JobContext``.
    """
    return JobContext(
        job=job,
        session=session,
        engine=engine,
        dp_wrapper=dp_wrapper,
        checkpoint_dir=checkpoint_dir,
    )


# ---------------------------------------------------------------------------
# JobContext and StepResult value objects
# ---------------------------------------------------------------------------


@dataclass
class JobContext:
    """Mutable shared state carried through all synthesis steps.

    Attributes:
        job: The ``SynthesisJob`` record being executed.
        session: Open SQLModel ``Session`` for DB reads and writes.
        engine: ``SynthesisEngine`` for training and generation.
        dp_wrapper: Optional DP wrapper (``None`` → vanilla CTGAN).
        checkpoint_dir: Filesystem directory for checkpoint and Parquet files.
        last_artifact: Set by ``TrainingStep``; consumed by ``GenerationStep``.
        last_ckpt_path: Set by ``TrainingStep``; consumed by the orchestrator
            to write ``job.artifact_path`` only after DP accounting succeeds.
    """

    job: SynthesisJob
    session: Session
    engine: SynthesisEngine
    dp_wrapper: DPWrapperProtocol | None
    checkpoint_dir: str
    last_artifact: Any = field(default=None, init=False)
    last_ckpt_path: str | None = field(default=None, init=False)


@dataclass
class StepResult:
    """Outcome of a single synthesis step.

    Attributes:
        success: ``True`` if the step completed without error.
        error_msg: Human-readable failure reason; ``None`` on success.
    """

    success: bool
    error_msg: str | None = None


# ---------------------------------------------------------------------------
# Step protocol
# ---------------------------------------------------------------------------


class SynthesisJobStep(Protocol):
    """Stateless interface for all concrete synthesis steps.

    Steps receive a ``JobContext``, perform one concern, and return a
    ``StepResult``.  Steps must NOT set ``job.status`` — the orchestrator
    is the sole status owner (AC4).
    """

    def execute(self, ctx: JobContext) -> StepResult:
        """Execute this step.

        Args:
            ctx: Shared job execution context.

        Returns:
            A ``StepResult`` indicating success or failure.
        """
        ...


# ---------------------------------------------------------------------------
# Concrete step implementations
# ---------------------------------------------------------------------------


class OomCheckStep:
    """OOM pre-flight check step.  Wraps ``check_memory_feasibility``."""

    def execute(self, ctx: JobContext) -> StepResult:
        """Run the OOM pre-flight check.

        Args:
            ctx: Shared job execution context.

        Returns:
            Success or failure with sanitized error message.
        """
        rows, columns = _get_parquet_dimensions(ctx.job.parquet_path)
        try:
            check_memory_feasibility(
                rows=rows,
                columns=columns,
                dtype_bytes=_OOM_DTYPE_BYTES,
                overhead_factor=_OOM_OVERHEAD_FACTOR,
            )
        except OOMGuardrailError as exc:
            _logger.error("OOM guardrail rejected job %d: %s", ctx.job.id, exc)
            return StepResult(success=False, error_msg=safe_error_msg(str(exc)))
        return StepResult(success=True)


class TrainingStep:
    """Epoch-chunked CTGAN training with checkpointing."""

    def execute(self, ctx: JobContext) -> StepResult:
        """Run the training loop.

        Stores ``ctx.last_artifact`` and ``ctx.last_ckpt_path`` for downstream
        steps.  Does NOT write ``job.artifact_path`` — the orchestrator sets
        that only after DP accounting succeeds (AC4 + budget exhaustion guard).

        Args:
            ctx: Shared job execution context.

        Returns:
            Success after all epochs complete, or failure on RuntimeError /
            zero-epoch guard.
        """
        job = ctx.job
        job_id = job.id
        total, n = job.total_epochs, job.checkpoint_every_n
        completed_epochs = 0
        last_ckpt_path: str | None = None

        while completed_epochs < total:
            chunk_epochs = min(n, total - completed_epochs)
            try:
                artifact = ctx.engine.train(
                    table_name=job.table_name,
                    parquet_path=job.parquet_path,
                    dp_wrapper=ctx.dp_wrapper,
                )
                completed_epochs += chunk_epochs
            except RuntimeError as exc:
                _logger.error(
                    "Job %d: RuntimeError during training at epoch ~%d: %s",
                    job_id,
                    completed_epochs,
                    exc,
                )
                return StepResult(success=False, error_msg=safe_error_msg(str(exc)))

            ckpt_path = str(Path(ctx.checkpoint_dir) / f"job_{job_id}_epoch_{completed_epochs}.pkl")
            artifact.save(ckpt_path)
            last_ckpt_path = ckpt_path
            ctx.last_artifact = artifact
            _logger.info("Job %d: checkpoint saved at epoch %d.", job_id, completed_epochs)
            job.current_epoch = completed_epochs
            ctx.session.add(job)
            ctx.session.commit()

        if last_ckpt_path is None:
            return StepResult(
                success=False,
                error_msg="No artifact produced — total_epochs may be 0.",
            )

        ctx.last_ckpt_path = last_ckpt_path
        job.current_epoch = total
        ctx.session.add(job)
        ctx.session.commit()
        return StepResult(success=True)


class GenerationStep:
    """Synthetic data generation and Parquet persistence."""

    def execute(self, ctx: JobContext) -> StepResult:
        """Generate synthetic data and write the Parquet artifact.

        Args:
            ctx: Shared job execution context.  ``ctx.last_artifact`` must be set.

        Returns:
            Success, or failure on RuntimeError / OSError / ValueError.
        """
        job = ctx.job
        job_id = job.id

        try:
            synthetic_df = ctx.engine.generate(ctx.last_artifact, n_rows=job.num_rows)
        except RuntimeError as exc:
            _logger.error("Job %d: RuntimeError during generation: %s", job_id, exc)
            return StepResult(success=False, error_msg=_GENERATION_FAILED_MSG)

        parquet_out = str(Path(ctx.checkpoint_dir) / f"job_{job_id}_synthetic.parquet")
        try:
            _write_parquet_with_signing(synthetic_df, parquet_out)
        except (OSError, ValueError) as exc:
            _logger.error("Job %d: Failed to write Parquet artifact: %s", job_id, exc)
            return StepResult(success=False, error_msg=_GENERATION_FAILED_MSG)

        job.output_path = parquet_out
        _logger.info("Job %d: Parquet written → %s", job_id, Path(parquet_out).name)
        return StepResult(success=True)


# ---------------------------------------------------------------------------
# Step-based orchestrator — sole owner of job.status (AC4, ADR-0038)
# ---------------------------------------------------------------------------


def _run_synthesis_job_impl(
    job_id: int,
    session: Session,
    engine: SynthesisEngine,
    checkpoint_dir: str | None = None,
    dp_wrapper: DPWrapperProtocol | None = None,
) -> None:
    """Step pipeline: OomCheckStep → Training → DpAccounting → Generation (ADR-0038)."""
    job = session.get(SynthesisJob, job_id)
    if job is None:
        raise ValueError(f"SynthesisJob with id={job_id} not found in database.")
    _logger.info("Starting synthesis job %d (table=%s).", job_id, job.table_name)
    tmp_dir_ctx = None
    if checkpoint_dir is None:
        tmp_dir_ctx = tempfile.TemporaryDirectory()
        checkpoint_dir = tmp_dir_ctx.name
    try:
        ctx = _build_ctx(job, session, engine, dp_wrapper, checkpoint_dir)
        dp_accounting = DpAccountingStep()
        steps: list[tuple[str | None, SynthesisJobStep]] = [
            (None, OomCheckStep()),
            ("TRAINING", TrainingStep()),
            (None, dp_accounting),
            ("GENERATING", GenerationStep()),
        ]
        for pre_status, step in steps:
            if pre_status is not None:
                job.status = pre_status
                _commit_job(job, session)
                _logger.info("Job %d: status → %s.", job_id, pre_status)
            result: StepResult = step.execute(ctx)
            if not result.success:
                job.status = "FAILED"
                job.error_msg = result.error_msg
                _commit_job(job, session)
                return
            if step is dp_accounting and ctx.last_ckpt_path is not None:
                job.artifact_path = ctx.last_ckpt_path
                _commit_job(job, session)
        job.status = "COMPLETE"
        _commit_job(job, session)
        _logger.info("Job %d: COMPLETE (output=%s).", job_id, job.output_path)
    finally:
        if tmp_dir_ctx is not None:
            tmp_dir_ctx.cleanup()
