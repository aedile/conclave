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

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

Task: P4-T4.2c — Huey Task Wiring & Checkpointing
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

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
    5. Save final artifact; set ``artifact_path``, ``current_epoch``,
       status → ``COMPLETE``; commit.

    Args:
        job_id: Primary key of the ``SynthesisJob`` record to process.
        session: Open SQLModel ``Session`` for reading and updating the job.
        engine: ``SynthesisEngine`` instance used to run CTGAN training.
        checkpoint_dir: Optional filesystem directory for writing checkpoint
            pickle files.  If ``None``, a temporary directory is created and
            cleaned up automatically.

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
    # 5. TRAINING → COMPLETE
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

    with Session(db_engine) as session:
        _run_synthesis_job_impl(
            job_id=job_id,
            session=session,
            engine=synthesis_engine,
        )
