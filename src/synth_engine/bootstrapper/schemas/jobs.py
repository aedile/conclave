"""Pydantic request/response schemas for Jobs endpoints.

These schemas sit at the API boundary.  They are distinct from the
``SynthesisJob`` SQLModel table model in ``modules/synthesizer/job_models.py``
to maintain the one-way dependency flow: bootstrapper → modules.

Task: P5-T5.1 — Task Orchestration API Core
Task: P22-T22.1 — Job Schema DP Parameters
Task: P23-T23.1 — Generation Step in Huey Task
Task: P23-T23.2 — /jobs/{id}/download Endpoint (review findings fix)
Task: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class JobCreateRequest(BaseModel):
    """Request body for POST /jobs.

    Attributes:
        table_name: Name of the database table to synthesise.  Must match
            ``^[a-zA-Z0-9_]+$`` (alphanumeric and underscore only) to prevent
            Content-Disposition header injection and SQL injection vectors.
        parquet_path: Absolute path to the Parquet file with training data.
        total_epochs: Number of CTGAN training epochs.
        num_rows: Number of synthetic rows to generate after training.
            Must be >= 1.
        checkpoint_every_n: Epochs between checkpoint saves (default 5).
        enable_dp: Whether to use DP-SGD training (default True,
            privacy-by-design per OWASP A04).
        noise_multiplier: Gaussian noise ratio for DP-SGD (default 1.1,
            per ADR-0025 calibration).  Must be > 0.
        max_grad_norm: Gradient clipping bound for DP-SGD (default 1.0).
            Must be > 0.
    """

    table_name: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9_]+$",
        max_length=255,
        description=(
            "Database table to synthesise. Alphanumeric and underscore characters only. "
            "Maximum 255 characters (PostgreSQL identifier limit). T68.6."
        ),
    )
    parquet_path: str = Field(
        ...,
        max_length=1024,
        description="Path to training Parquet file. Maximum 1024 characters. T68.6.",
    )
    total_epochs: int = Field(
        ...,
        gt=0,
        le=10000,
        description="Total training epochs. Maximum 10000.",
    )
    num_rows: int = Field(
        ...,
        gt=0,
        le=10_000_000,
        description="Number of synthetic rows to generate. Maximum 10,000,000.",
    )
    checkpoint_every_n: int = Field(default=5, ge=1, description="Epochs between checkpoints.")
    # Defense-in-depth: these Field constraints are duplicated as __init__ guards
    # in modules/synthesizer/job_models.py.  Both must be updated together.
    enable_dp: bool = Field(default=True, description="Enable DP-SGD training.")
    noise_multiplier: float = Field(
        default=1.1,
        gt=0,
        le=100.0,
        description="Gaussian noise ratio for DP-SGD (ADR-0025).",
    )
    max_grad_norm: float = Field(
        default=1.0,
        gt=0,
        le=100.0,
        description="Gradient clipping bound for DP-SGD.",
    )

    @field_validator("parquet_path")
    @classmethod
    def validate_parquet_path(cls, v: str) -> str:
        """Validate and normalise the parquet_path field.

        Rejects empty strings, paths that do not end with ``.parquet``, and
        any raw value whose resolved form is outside CONCLAVE_DATA_DIR (the
        configured sandbox directory, T69.7).  Path.resolve() is called on
        both the input path and conclave_data_dir before is_relative_to()
        so that symlinks and relative components are fully resolved and
        cannot be used to escape the sandbox.

        Args:
            v: Raw string value supplied by the caller.

        Returns:
            The resolved absolute path as a string.

        Raises:
            ValueError: If the value is empty, contains only whitespace,
                does not end with ``.parquet``, or resolves to a path
                outside CONCLAVE_DATA_DIR.
        """
        if not v or not v.strip():
            raise ValueError("parquet_path must not be empty")
        resolved = Path(v).resolve()
        if not str(resolved).endswith(".parquet"):
            raise ValueError("parquet_path must end with .parquet")

        # T69.7: Sandbox check — resolved path must be inside CONCLAVE_DATA_DIR.
        # Both paths are fully resolved before comparison to prevent traversal via
        # symlinks or relative components.
        from synth_engine.shared.settings import get_settings

        settings = get_settings()
        data_dir = Path(settings.conclave_data_dir).resolve()
        try:
            resolved.relative_to(data_dir)
        except ValueError:
            raise ValueError(
                f"parquet_path must be inside the allowed data directory. "
                f"Resolved path {str(resolved)!r} is outside CONCLAVE_DATA_DIR {str(data_dir)!r}."
            ) from None
        return str(resolved)


class JobResponse(BaseModel):
    """Response body for a single Job.

    Attributes:
        id: Job primary key.
        status: Lifecycle status (QUEUED, TRAINING, GENERATING, COMPLETE, FAILED).
        current_epoch: Most recently completed training epoch.
        total_epochs: Total epochs requested.
        num_rows: Number of synthetic rows to generate after training.
        table_name: Name of the database table being synthesised.
        parquet_path: Path to the training Parquet file.
        artifact_path: Path to the final model artifact pickle (None until COMPLETE).
        output_path: Path to the generated synthetic Parquet file
            (None until generation completes).
        error_msg: Sanitized failure reason (None on success).
        checkpoint_every_n: Checkpoint interval in epochs.
        enable_dp: Whether DP-SGD training is enabled.
        noise_multiplier: Gaussian noise ratio used for DP-SGD.
        max_grad_norm: Gradient clipping bound used for DP-SGD.
        actual_epsilon: Actual epsilon spent after training (None until set
            by T22.2 training task).
        owner_id: Operator identity who owns this job (T39.2 IDOR protection).
    """

    id: int
    status: str
    current_epoch: int
    total_epochs: int
    num_rows: int
    table_name: str
    parquet_path: str
    artifact_path: str | None
    output_path: str | None
    error_msg: str | None
    checkpoint_every_n: int
    enable_dp: bool
    noise_multiplier: float
    max_grad_norm: float
    actual_epsilon: float | None
    owner_id: str = ""

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    """Paginated list response for GET /jobs.

    Attributes:
        items: List of job response objects.
        next_cursor: Cursor value for the next page (None if last page).
    """

    items: list[JobResponse]
    next_cursor: int | None
