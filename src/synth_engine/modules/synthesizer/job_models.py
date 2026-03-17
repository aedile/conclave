"""SQLModel definition for SynthesisJob — the database record for a synthesis run.

``SynthesisJob`` tracks the full lifecycle of a background synthesis training
task: from initial queuing through training to completion or failure.  The
Huey task (``run_synthesis_job`` in ``tasks.py``) writes status transitions
into this table; the SSE endpoint (T5.1) streams the current record to the
frontend operator UI.

Status lifecycle::

    QUEUED → TRAINING → GENERATING → COMPLETE
                                   ↘ FAILED  (OOM, RuntimeError, BudgetExhaustion)

Checkpointing design:

    The task saves a ``ModelArtifact`` snapshot every ``checkpoint_every_n``
    epochs.  On failure the most recent checkpoint can be loaded to resume
    training.  On success the final artifact path is recorded in
    ``artifact_path``.

Differential Privacy parameters (P22-T22.1):

    Three fields control DP-SGD training via the Opacus API:
    ``enable_dp``, ``noise_multiplier`` (Gaussian noise ratio, see ADR-0025),
    and ``max_grad_norm`` (gradient clipping bound).  All three default to
    privacy-maximising values (OWASP A04).  ``actual_epsilon`` is written by
    the training task after completion (T22.2).

Generation parameters (P23-T23.1):

    ``num_rows`` controls how many synthetic rows are generated after training.
    ``output_path`` records the filesystem path to the generated Parquet file
    (written after training completes).  ``artifact_path`` continues to point
    to the final model pickle checkpoint (backward-compatible, Option B).

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

Task: P4-T4.2c — Huey Task Wiring & Checkpointing
Task: P22-T22.1 — Job Schema DP Parameters
Task: P23-T23.1 — Generation Step in Huey Task
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Field, SQLModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default checkpoint interval in epochs.
_DEFAULT_CHECKPOINT_EVERY_N: int = 5

#: Default noise multiplier for DP-SGD (ADR-0025 calibration).
_DEFAULT_NOISE_MULTIPLIER: float = 1.1

#: Default gradient clipping bound for DP-SGD.
_DEFAULT_MAX_GRAD_NORM: float = 1.0


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
            ``GENERATING``, ``COMPLETE``, or ``FAILED``.
        current_epoch: Training epoch most recently completed.  Updated
            at each checkpoint boundary and on final completion.
        total_epochs: Total number of epochs requested for this job.
        num_rows: Number of synthetic rows to generate after training.
            Must be >= 1.  Required field (no default).
        artifact_path: Filesystem path to the final ``ModelArtifact`` pickle
            file.  ``None`` until the job reaches ``COMPLETE``.
        output_path: Filesystem path to the generated synthetic Parquet file.
            ``None`` until generation completes.  Distinct from
            ``artifact_path`` (Option B: pickle vs Parquet separation).
        error_msg: Human-readable failure reason.  ``None`` on success; set to
            the OOM guardrail message or exception ``str`` on failure.
        table_name: Name of the database table being synthesised.
        parquet_path: Absolute path to the Parquet file containing the
            source training data written by the subsetting pipeline.
        checkpoint_every_n: Save a ``ModelArtifact`` checkpoint every this
            many epochs.  Defaults to 5.  Callers override for coarser or
            finer checkpointing granularity.  Must be >= 1.
        enable_dp: Whether to use DP-SGD training.  Defaults to ``True``
            (privacy-by-design, OWASP A04).
        noise_multiplier: Gaussian noise ratio for DP-SGD.  Defaults to
            ``1.1`` (ADR-0025 calibration).  Must be > 0.
        max_grad_norm: Gradient clipping bound for DP-SGD.  Defaults to
            ``1.0``.  Must be > 0.
        actual_epsilon: Actual epsilon privacy budget spent after training.
            Set by the training task (T22.2).  ``None`` until training
            completes with DP enabled.
    """

    __tablename__ = "synthesis_job"

    id: int | None = Field(default=None, primary_key=True)
    status: str = Field(default="QUEUED")
    current_epoch: int = Field(default=0)
    total_epochs: int
    num_rows: int
    artifact_path: str | None = Field(default=None)
    output_path: str | None = Field(default=None)
    error_msg: str | None = Field(default=None)
    table_name: str
    parquet_path: str
    checkpoint_every_n: int = Field(default=_DEFAULT_CHECKPOINT_EVERY_N)
    enable_dp: bool = Field(default=True)
    noise_multiplier: float = Field(default=_DEFAULT_NOISE_MULTIPLIER)
    max_grad_norm: float = Field(default=_DEFAULT_MAX_GRAD_NORM)
    actual_epsilon: float | None = Field(default=None)

    # Defense-in-depth: these guards duplicate the Pydantic Field constraints in
    # bootstrapper/schemas/jobs.py.  Both must be updated together.
    def __init__(self, **data: Any) -> None:
        """Initialise SynthesisJob, enforcing field constraints.

        SQLModel ``table=True`` models bypass pydantic field validators in
        ``__init__`` to allow ORM row construction.  This override adds
        explicit guards before delegating to ``super().__init__``.

        Args:
            **data: Keyword arguments forwarded to the SQLModel base class.

        Raises:
            ValueError: If ``num_rows`` is less than 1.  Generation requires
                at least one row; zero or negative values are rejected.
            ValueError: If ``checkpoint_every_n`` is less than 1.  A value of
                0 would cause ``min(0, total - 0) == 0`` in the training loop,
                making ``completed_epochs`` never advance (infinite loop).
            ValueError: If ``noise_multiplier`` is not strictly positive or
                exceeds 100.0.  The Opacus API requires a positive noise
                multiplier; values above 100.0 are almost certainly erroneous.
            ValueError: If ``max_grad_norm`` is not strictly positive or
                exceeds 100.0.  Gradient clipping requires a positive bound;
                values above 100.0 are almost certainly erroneous.
        """
        # F3 fix: enforce num_rows >= 1 (docstring mandates this, __init__ must guard it).
        num_rows = data.get("num_rows")
        if isinstance(num_rows, int) and num_rows < 1:
            raise ValueError("num_rows must be >= 1")

        n = data.get("checkpoint_every_n", _DEFAULT_CHECKPOINT_EVERY_N)
        if isinstance(n, int) and n < 1:
            raise ValueError("checkpoint_every_n must be >= 1")

        noise = data.get("noise_multiplier", _DEFAULT_NOISE_MULTIPLIER)
        if isinstance(noise, int | float) and noise <= 0:
            raise ValueError("noise_multiplier must be > 0")
        if isinstance(noise, int | float) and noise > 100.0:
            raise ValueError("noise_multiplier must be <= 100.0")

        grad_norm = data.get("max_grad_norm", _DEFAULT_MAX_GRAD_NORM)
        if isinstance(grad_norm, int | float) and grad_norm <= 0:
            raise ValueError("max_grad_norm must be > 0")
        if isinstance(grad_norm, int | float) and grad_norm > 100.0:
            raise ValueError("max_grad_norm must be <= 100.0")

        super().__init__(**data)
