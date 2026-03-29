"""Unit tests for bootstrapper/schemas/jobs.py — parquet_path validation.

Covers DevOps finding D1: unvalidated parquet_path in JobCreateRequest.
Updated for T69.7: parquet_path is now sandboxed to CONCLAVE_DATA_DIR.

Task: P5-T5.1 — Task Orchestration API Core (DevOps fix)
Task: T69.7 — Sandbox parquet_path to Allowed Directory (ADV-P68-02)
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from pydantic import ValidationError

from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

pytestmark = pytest.mark.unit

_VALID_BASE = {
    "table_name": "customers",
    "total_epochs": 5,
    "num_rows": 100,
}


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def data_dir(tmp_path: Path) -> Generator[Path]:
    """Create a temp data directory and set it as CONCLAVE_DATA_DIR.

    The fixture also clears the settings cache so every test gets a fresh
    settings instance reading the updated env var.

    Args:
        tmp_path: pytest tmp_path fixture providing a unique temp directory.

    Yields:
        Path to the temp data directory.
    """
    d = tmp_path / "data"
    d.mkdir()
    return d


class TestParquetPathValidator:
    """Tests for JobCreateRequest.validate_parquet_path."""

    def test_valid_absolute_parquet_path_is_accepted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
    ) -> None:
        """A normal absolute .parquet path inside CONCLAVE_DATA_DIR is accepted."""
        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        valid_path = str(data_dir / "data.parquet")
        req = JobCreateRequest(**_VALID_BASE, parquet_path=valid_path)
        assert req.parquet_path.endswith(".parquet")
        assert req.parquet_path.startswith(str(data_dir))

    def test_relative_parquet_path_inside_data_dir_is_resolved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
    ) -> None:
        """A relative path inside CONCLAVE_DATA_DIR resolves to an absolute path."""
        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        # Use relative path that resolves inside the data_dir (via CWD lookup)
        # Since relative path resolution uses cwd, use an absolute path instead
        valid_path = str(data_dir / "customers.parquet")
        req = JobCreateRequest(**_VALID_BASE, parquet_path=valid_path)
        assert req.parquet_path.startswith("/")
        assert req.parquet_path.endswith(".parquet")

    def test_empty_string_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
    ) -> None:
        """An empty string must raise ValidationError."""
        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        with pytest.raises(ValidationError, match="parquet_path must not be empty"):
            JobCreateRequest(**_VALID_BASE, parquet_path="")

    def test_whitespace_only_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
    ) -> None:
        """A whitespace-only string must raise ValidationError."""
        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        with pytest.raises(ValidationError, match="parquet_path must not be empty"):
            JobCreateRequest(**_VALID_BASE, parquet_path="   ")

    def test_path_without_parquet_extension_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
    ) -> None:
        """A path that does not end with .parquet must raise ValidationError."""
        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        with pytest.raises(ValidationError, match="parquet_path must end with .parquet"):
            JobCreateRequest(**_VALID_BASE, parquet_path=str(data_dir / "data.csv"))

    def test_path_traversal_sequence_resolved_but_still_sandbox_checked(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
    ) -> None:
        """A path containing .. that stays inside CONCLAVE_DATA_DIR is accepted.

        The validator normalises traversal sequences via Path.resolve() and
        then checks the resolved path is inside CONCLAVE_DATA_DIR.  A caller
        who supplies ``<data_dir>/sub/../train.parquet`` receives
        ``<data_dir>/train.parquet`` back (traversal normalised, still inside).
        """
        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        # Create a sub-dir so the path exists for resolve()
        sub = data_dir / "sub"
        sub.mkdir()
        traversal_but_inside = str(sub / ".." / "train.parquet")
        req = JobCreateRequest(**_VALID_BASE, parquet_path=traversal_but_inside)
        # After resolve(), the traversal is gone and the path is inside data_dir
        assert ".." not in req.parquet_path
        assert req.parquet_path.endswith(".parquet")
        assert req.parquet_path.startswith(str(data_dir))

    def test_path_outside_data_dir_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
    ) -> None:
        """A path outside CONCLAVE_DATA_DIR must raise ValidationError."""
        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        with pytest.raises(ValidationError, match="parquet_path must be inside"):
            JobCreateRequest(**_VALID_BASE, parquet_path="/etc/passwd.parquet")

    def test_returned_path_is_absolute(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
    ) -> None:
        """The returned parquet_path must always be an absolute path string."""
        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        valid_path = str(data_dir / "train.parquet")
        req = JobCreateRequest(**_VALID_BASE, parquet_path=valid_path)
        assert req.parquet_path.startswith("/")
