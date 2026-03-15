"""Pydantic request/response schemas for Jobs endpoints.

These schemas sit at the API boundary.  They are distinct from the
``SynthesisJob`` SQLModel table model in ``modules/synthesizer/job_models.py``
to maintain the one-way dependency flow: bootstrapper → modules.

Task: P5-T5.1 — Task Orchestration API Core
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class JobCreateRequest(BaseModel):
    """Request body for POST /jobs.

    Attributes:
        table_name: Name of the database table to synthesise.
        parquet_path: Absolute path to the Parquet file with training data.
        total_epochs: Number of CTGAN training epochs.
        checkpoint_every_n: Epochs between checkpoint saves (default 5).
    """

    table_name: str = Field(..., description="Database table to synthesise.")
    parquet_path: str = Field(..., description="Path to training Parquet file.")
    total_epochs: int = Field(..., gt=0, description="Total training epochs.")
    checkpoint_every_n: int = Field(default=5, ge=1, description="Epochs between checkpoints.")

    @field_validator("parquet_path")
    @classmethod
    def validate_parquet_path(cls, v: str) -> str:
        """Validate and normalise the parquet_path field.

        Rejects empty strings, paths that do not end with ``.parquet``, and
        any raw value whose resolved form differs from what an honest caller
        would supply (i.e. path-traversal sequences are normalised away by
        ``Path.resolve()`` and the caller receives the canonical form).

        Args:
            v: Raw string value supplied by the caller.

        Returns:
            The resolved absolute path as a string.

        Raises:
            ValueError: If the value is empty, contains only whitespace, or
                does not end with ``.parquet``.
        """
        if not v or not v.strip():
            raise ValueError("parquet_path must not be empty")
        resolved = Path(v).resolve()
        if not str(resolved).endswith(".parquet"):
            raise ValueError("parquet_path must end with .parquet")
        return str(resolved)


class JobResponse(BaseModel):
    """Response body for a single Job.

    Attributes:
        id: Job primary key.
        status: Lifecycle status (QUEUED, TRAINING, COMPLETE, FAILED).
        current_epoch: Most recently completed training epoch.
        total_epochs: Total epochs requested.
        table_name: Name of the database table being synthesised.
        parquet_path: Path to the training Parquet file.
        artifact_path: Path to the final model artifact (None until COMPLETE).
        error_msg: Sanitized failure reason (None on success).
        checkpoint_every_n: Checkpoint interval in epochs.
    """

    id: int
    status: str
    current_epoch: int
    total_epochs: int
    table_name: str
    parquet_path: str
    artifact_path: str | None
    error_msg: str | None
    checkpoint_every_n: int

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    """Paginated list response for GET /jobs.

    Attributes:
        items: List of job response objects.
        next_cursor: Cursor value for the next page (None if last page).
    """

    items: list[JobResponse]
    next_cursor: int | None
