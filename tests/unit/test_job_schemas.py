"""Unit tests for bootstrapper/schemas/jobs.py — parquet_path validation.

Covers DevOps finding D1: unvalidated parquet_path in JobCreateRequest.

Task: P5-T5.1 — Task Orchestration API Core (DevOps fix)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

pytestmark = pytest.mark.unit

_VALID_BASE = {
    "table_name": "customers",
    "total_epochs": 5,
    "num_rows": 100,
}


class TestParquetPathValidator:
    """Tests for JobCreateRequest.validate_parquet_path."""

    def test_valid_absolute_parquet_path_is_accepted(self) -> None:
        """A normal absolute .parquet path must be accepted and returned resolved."""
        req = JobCreateRequest(**_VALID_BASE, parquet_path="/tmp/data.parquet")
        assert req.parquet_path.endswith(".parquet")

    def test_relative_parquet_path_is_resolved_to_absolute(self) -> None:
        """A relative path ending in .parquet must be resolved to an absolute path."""
        req = JobCreateRequest(**_VALID_BASE, parquet_path="data/customers.parquet")
        assert req.parquet_path.startswith("/")
        assert req.parquet_path.endswith(".parquet")

    def test_empty_string_is_rejected(self) -> None:
        """An empty string must raise ValidationError."""
        with pytest.raises(ValidationError, match="parquet_path must not be empty"):
            JobCreateRequest(**_VALID_BASE, parquet_path="")

    def test_whitespace_only_is_rejected(self) -> None:
        """A whitespace-only string must raise ValidationError."""
        with pytest.raises(ValidationError, match="parquet_path must not be empty"):
            JobCreateRequest(**_VALID_BASE, parquet_path="   ")

    def test_path_without_parquet_extension_is_rejected(self) -> None:
        """A path that does not end with .parquet must raise ValidationError."""
        with pytest.raises(ValidationError, match="parquet_path must end with .parquet"):
            JobCreateRequest(**_VALID_BASE, parquet_path="/tmp/data.csv")

    def test_path_traversal_sequence_is_resolved_not_blocked(self) -> None:
        """A path containing .. is resolved to its canonical form.

        The validator normalises traversal sequences via Path.resolve() rather
        than raising an error — the canonical resolved path is returned.  A
        caller who supplies ``/tmp/../tmp/data.parquet`` receives
        ``/tmp/data.parquet`` back, which is the correct safe behaviour.
        """
        req = JobCreateRequest(**_VALID_BASE, parquet_path="/tmp/../tmp/data.parquet")
        # After resolve(), the traversal is gone
        assert ".." not in req.parquet_path
        assert req.parquet_path.endswith(".parquet")

    def test_path_traversal_to_non_parquet_is_rejected(self) -> None:
        """A traversal path that resolves to a non-.parquet name must be rejected."""
        with pytest.raises(ValidationError, match="parquet_path must end with .parquet"):
            JobCreateRequest(**_VALID_BASE, parquet_path="/etc/../etc/passwd")

    def test_returned_path_is_absolute(self) -> None:
        """The returned parquet_path must always be an absolute path string."""
        req = JobCreateRequest(**_VALID_BASE, parquet_path="/data/train.parquet")
        assert req.parquet_path.startswith("/")
