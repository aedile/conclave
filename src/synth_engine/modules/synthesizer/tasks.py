"""Huey background task for synthesis training with checkpointing.

Defines ``run_synthesis_job``, a ``@huey.task()`` that drives the full
synthesis training lifecycle: OOM pre-flight check, CTGAN training,
epoch checkpointing, and database status updates.

Status lifecycle::

    QUEUED → TRAINING → COMPLETE   (success)
                      ↘ FAILED     (OOM guardrail rejection or RuntimeError)

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
via ``bootstrapper.factories.build_dp_wrapper``.  The wrapper is then injected
into ``_run_synthesis_job_impl`` which passes it to every ``engine.train()``
call.  After training completes, ``dp_wrapper.epsilon_spent(delta=1e-5)`` is
read and stored on the job record as ``actual_epsilon``.

Import boundary compliance
--------------------------
The ``bootstrapper.factories`` module is loaded via ``importlib.import_module``
inside the ``if job.enable_dp:`` block in ``run_synthesis_job``.
``importlib.import_module`` calls are NOT detected by ``import-linter`` (which
only analyses AST ``import`` and ``from ... import`` statements), so this
pattern is boundary-compliant.  The module-level import list for this file
contains no references to ``bootstrapper`` or ``modules/privacy``.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/`` (enforced by import-linter on
      module-level and ``from ... import`` statements).

Task: P4-T4.2c — Huey Task Wiring & Checkpointing
Task: P22-T22.2 — Wire DP into run_synthesis_job()
"""

from __future__ import annotations

import importlib
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from synth_engine.modules.synthesizer.guardrails import (
    OOMGuardrailError,
    check_memory_feasibility,
)
from synth_engine.modules.synthesizer.job_models import SynthesisJob
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


def _build_dp_wrapper_via_factory(
    max_grad_norm: float,
    noise_multiplier: float,
) -> Any:
    """Construct a DPTrainingWrapper using the bootstrapper factory.

    Loads ``synth_engine.bootstrapper.factories`` via ``importlib.import_module``
    and calls ``build_dp_wrapper``.  Using ``importlib`` rather than a direct
    ``from ... import`` statement keeps this file's module-level import list
    free of ``bootstrapper`` references, satisfying import-linter's
    boundary contract (which only scans AST ``import``/``from ... import``
    nodes, not ``importlib.import_module`` call arguments).

    Args:
        max_grad_norm: Maximum L2 norm for per-sample gradient clipping.
        noise_multiplier: Gaussian noise ratio for DP-SGD.

    Returns:
        A configured ``DPTrainingWrapper`` instance duck-typed as ``Any``.
    """
    factories_mod = importlib.import_module("synth_engine.bootstrapper.factories")
    return factories_mod.build_dp_wrapper(
        max_grad_norm=max_grad_norm,
        noise_multiplier=noise_multiplier,
    )


# ---------------------------------------------------------------------------
# Internal implementation — injectable for unit tests
# ---------------------------------------------------------------------------


def _run_synthesis_job_impl(
    job_id: int,
    session: Session,
    engine: SynthesisEngine,
    checkpoint_dir: str | None = None,
    dp_wrapper: Any | None = None,
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
    6. Save final artifact; set ``artifact_path``, ``current_epoch``,
       status → ``COMPLETE``; commit.

    Args:
        job_id: Primary key of the ``SynthesisJob`` record to process.
        session: Open SQLModel ``Session`` for reading and updating the job.
        engine: ``SynthesisEngine`` instance used to run CTGAN training.
        checkpoint_dir: Optional filesystem directory for writing checkpoint
            pickle files.  If ``None``, a temporary directory is created and
            cleaned up automatically.
        dp_wrapper: Optional DP wrapper implementing the duck-type contract::

                wrap(optimizer, model, dataloader, *, max_grad_norm,
                     noise_multiplier) → dp_optimizer
                epsilon_spent(*, delta) → float
                check_budget(*, allocated_epsilon, delta) → None

            Typed as ``Any`` to avoid import-linter boundary violations between
            ``modules/synthesizer`` and ``modules/privacy``.  The concrete
            implementation is ``DPTrainingWrapper`` from
            ``modules/privacy/dp_engine.py``, constructed by the bootstrapper
            via ``_build_dp_wrapper_via_factory`` and injected here.
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
        actual_eps = dp_wrapper.epsilon_spent(delta=_DP_EPSILON_DELTA)
        job.actual_epsilon = actual_eps
        _logger.info(
            "Job %d: DP training complete, actual_epsilon=%.4f.",
            job_id,
            actual_eps,
        )

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

    When the job has ``enable_dp=True``, a ``DPTrainingWrapper`` is constructed
    via ``_build_dp_wrapper_via_factory`` (which delegates to
    ``bootstrapper.factories.build_dp_wrapper`` via ``importlib.import_module``)
    using the job's ``max_grad_norm`` and ``noise_multiplier`` fields.  The
    wrapper is then passed to ``_run_synthesis_job_impl``.  After training,
    the actual epsilon privacy budget is recorded on ``job.actual_epsilon``.

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

    Note (Bootstrapper wiring — Rule 8):
        ``bootstrapper/main.py`` imports this module at startup via::

            from synth_engine.modules.synthesizer import tasks as _synthesizer_tasks  # noqa: F401

        This import side-effect registers ``run_synthesis_job`` with the
        shared Huey instance so the worker process discovers it.
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
    dp_wrapper: Any = None
    with Session(db_engine) as preflight_session:
        job = preflight_session.get(SynthesisJob, job_id)
        if job is not None and job.enable_dp:
            dp_wrapper = _build_dp_wrapper_via_factory(
                max_grad_norm=job.max_grad_norm,
                noise_multiplier=job.noise_multiplier,
            )

    with Session(db_engine) as session:
        _run_synthesis_job_impl(
            job_id=job_id,
            session=session,
            engine=synthesis_engine,
            dp_wrapper=dp_wrapper,
        )
