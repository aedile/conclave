"""SQLModel definition for SynthesisJob — the database record for a synthesis run.

``SynthesisJob`` tracks the full lifecycle of a background synthesis training
task: from initial queuing through training to completion or failure.  The
Huey task (``run_synthesis_job`` in ``tasks.py``) writes status transitions
into this table; the SSE endpoint (T5.1) streams the current record to the
frontend operator UI.

Status lifecycle::

    QUEUED → TRAINING → COMPLETE
                      ↘ FAILED  (OOM guardrail rejection or RuntimeError)

Checkpointing design:

    The task saves a ``ModelArtifact`` snapshot every ``checkpoint_every_n``
    epochs.  On failure the most recent checkpoint can be loaded to resume
    training.  On success the final artifact path is recorded in
    ``artifact_path``.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

Task: P4-T4.2c — Huey Task Wiring & Checkpointing
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Field, SQLModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default checkpoint interval in epochs.
_DEFAULT_CHECKPOINT_EVERY_N: int = 5


# ---------------------------------------------------------------------------
# SynthesisJob model
# ---------------------------------------------------------------------------


class SynthesisJob(SQLModel, table=True):
    """Database record for one synthesis training job.

    Each row tracks a single invocation of ``run_synthesis_job``.  The Huey
    worker updates ``status`` and ``current_epoch`` as training progresses;
    the SSE endpoint reads this record to stream progress to the operator.

    Attributes:
        id: Auto-incremented integer primary key.  ``None`` before the record
            is inserted; assigned by the database on first flush.
        status: Job lifecycle status.  One of ``QUEUED``, ``TRAINING``,
            ``COMPLETE``, or ``FAILED``.
        current_epoch: Training epoch most recently completed.  Updated
            at each checkpoint boundary and on final completion.
        total_epochs: Total number of epochs requested for this job.
        artifact_path: Filesystem path to the final ``ModelArtifact`` pickle
            file.  ``None`` until the job reaches ``COMPLETE``.
        error_msg: Human-readable failure reason.  ``None`` on success; set to
            the OOM guardrail message or exception ``str`` on failure.
        table_name: Name of the database table being synthesised.
        parquet_path: Absolute path to the Parquet file containing the
            source training data written by the subsetting pipeline.
        checkpoint_every_n: Save a ``ModelArtifact`` checkpoint every this
            many epochs.  Defaults to 5.  Callers override for coarser or
            finer checkpointing granularity.  Must be >= 1.
    """

    __tablename__ = "synthesis_job"

    id: int | None = Field(default=None, primary_key=True)
    status: str = Field(default="QUEUED")
    current_epoch: int = Field(default=0)
    total_epochs: int
    artifact_path: str | None = Field(default=None)
    error_msg: str | None = Field(default=None)
    table_name: str
    parquet_path: str
    checkpoint_every_n: int = Field(default=_DEFAULT_CHECKPOINT_EVERY_N)

    def __init__(self, **data: Any) -> None:
        """Initialise SynthesisJob, enforcing checkpoint_every_n >= 1.

        SQLModel ``table=True`` models bypass pydantic field validators in
        ``__init__`` to allow ORM row construction.  This override adds an
        explicit guard before delegating to ``super().__init__``.

        Args:
            **data: Keyword arguments forwarded to the SQLModel base class.

        Raises:
            ValueError: If ``checkpoint_every_n`` is less than 1.  A value of
                0 would cause ``min(0, total - 0) == 0`` in the training loop,
                making ``completed_epochs`` never advance (infinite loop).
        """
        n = data.get("checkpoint_every_n", _DEFAULT_CHECKPOINT_EVERY_N)
        if isinstance(n, int) and n < 1:
            raise ValueError("checkpoint_every_n must be >= 1")
        super().__init__(**data)
