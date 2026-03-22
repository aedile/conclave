"""Unit tests for Parquet memory bounds enforcement (T47.7).

Tests cover:
- DatasetTooLargeError exception hierarchy and structured attributes
- Size limit enforcement for both file-path and bytes-input call sites
- Row count limit enforcement
- Boundary conditions (at limit = success, at limit+1 = failure)
- Custom limits via settings
- Size check fires before row-count check
- Error messages include actual and limit values
- Settings validator rejects non-positive limits

Task: T47.7 — Add Memory Bounds to Parquet Loading
"""

from __future__ import annotations

import io
import os
import tempfile
from unittest.mock import patch

import pandas as pd
import pytest

from synth_engine.shared.exceptions import DatasetTooLargeError, SynthEngineError
from synth_engine.shared.settings import ConclaveSettings, get_settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parquet_bytes(n_rows: int) -> bytes:
    """Create a minimal in-memory Parquet payload with ``n_rows`` rows.

    Args:
        n_rows: Number of rows in the produced DataFrame.

    Returns:
        Raw Parquet bytes serialised with PyArrow.
    """
    df = pd.DataFrame({"x": range(n_rows)})
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    return buf.getvalue()


def _write_parquet_file(path: str, n_rows: int) -> None:
    """Write a small Parquet file to *path*.

    Args:
        path: Filesystem path to write to.
        n_rows: Number of rows in the DataFrame written to disk.
    """
    df = pd.DataFrame({"x": range(n_rows)})
    df.to_parquet(path, index=False, engine="pyarrow")


# ---------------------------------------------------------------------------
# Negative / attack tests — committed first per Rule 22
# ---------------------------------------------------------------------------


class TestDatasetTooLargeErrorHierarchy:
    """DatasetTooLargeError must sit within the SynthEngineError hierarchy."""

    def test_dataset_too_large_error_in_exception_hierarchy(self) -> None:
        """DatasetTooLargeError must be a subclass of SynthEngineError."""
        assert issubclass(DatasetTooLargeError, SynthEngineError)

    def test_dataset_too_large_error_has_actual_size_attribute(self) -> None:
        """Structured attribute actual_size is accessible on the raised exception."""
        exc = DatasetTooLargeError(actual_size=300, limit=200, limit_type="bytes")
        assert exc.actual_size == 300

    def test_dataset_too_large_error_has_limit_attribute(self) -> None:
        """Structured attribute limit is accessible on the raised exception."""
        exc = DatasetTooLargeError(actual_size=300, limit=200, limit_type="bytes")
        assert exc.limit == 200

    def test_dataset_too_large_error_has_limit_type_attribute(self) -> None:
        """Structured attribute limit_type is accessible on the raised exception."""
        exc = DatasetTooLargeError(actual_size=300, limit=200, limit_type="bytes")
        assert exc.limit_type == "bytes"

    def test_dataset_too_large_error_limit_type_rows(self) -> None:
        """limit_type='rows' is a valid value for the attribute."""
        exc = DatasetTooLargeError(actual_size=11_000_000, limit=10_000_000, limit_type="rows")
        assert exc.limit_type == "rows"

    def test_error_message_includes_actual_and_limit(self) -> None:
        """Exception message must contain both the actual value and the limit."""
        exc = DatasetTooLargeError(actual_size=999, limit=500, limit_type="bytes")
        msg = str(exc)
        assert "999" in msg
        assert "500" in msg


class TestSettingsValidators:
    """Settings validators must reject non-positive limit values."""

    def test_negative_size_limit_rejected(self) -> None:
        """parquet_max_file_bytes <= 0 must raise a ValidationError."""
        from pydantic import ValidationError

        get_settings.cache_clear()
        with pytest.raises(ValidationError):
            ConclaveSettings(parquet_max_file_bytes=-1)

    def test_zero_size_limit_rejected(self) -> None:
        """parquet_max_file_bytes == 0 must raise a ValidationError."""
        from pydantic import ValidationError

        get_settings.cache_clear()
        with pytest.raises(ValidationError):
            ConclaveSettings(parquet_max_file_bytes=0)

    def test_negative_row_limit_rejected(self) -> None:
        """parquet_max_rows <= 0 must raise a ValidationError."""
        from pydantic import ValidationError

        get_settings.cache_clear()
        with pytest.raises(ValidationError):
            ConclaveSettings(parquet_max_rows=-1)

    def test_zero_row_limit_rejected(self) -> None:
        """parquet_max_rows == 0 must raise a ValidationError."""
        from pydantic import ValidationError

        get_settings.cache_clear()
        with pytest.raises(ValidationError):
            ConclaveSettings(parquet_max_rows=0)


# ---------------------------------------------------------------------------
# Feature tests — file-path call site
# ---------------------------------------------------------------------------


class TestReadParquetBoundedFilePath:
    """_read_parquet_bounded must enforce limits when given a file path."""

    def setup_method(self) -> None:
        """Clear the settings LRU cache before every test."""
        get_settings.cache_clear()

    def teardown_method(self) -> None:
        """Clear the settings LRU cache after every test."""
        get_settings.cache_clear()

    def test_parquet_file_exceeds_size_limit_raises(self) -> None:
        """File whose byte size exceeds the configured limit raises DatasetTooLargeError."""
        from synth_engine.modules.synthesizer.engine import _read_parquet_bounded

        data = _make_parquet_bytes(100)
        file_size = len(data)
        # Set limit to one byte less than actual
        limit = file_size - 1

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
            fh.write(data)
            path = fh.name

        try:
            settings = ConclaveSettings(parquet_max_file_bytes=limit, parquet_max_rows=10_000_000)
            with patch(
                "synth_engine.modules.synthesizer.engine.get_settings", return_value=settings
            ):
                with pytest.raises(DatasetTooLargeError) as exc_info:
                    _read_parquet_bounded(path)
            assert exc_info.value.limit_type == "bytes"
            assert exc_info.value.actual_size == file_size
            assert exc_info.value.limit == limit
        finally:
            os.unlink(path)

    def test_parquet_file_at_size_limit_loads(self) -> None:
        """File whose byte size equals the limit exactly must load without error."""
        from synth_engine.modules.synthesizer.engine import _read_parquet_bounded

        data = _make_parquet_bytes(100)
        file_size = len(data)

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
            fh.write(data)
            path = fh.name

        try:
            settings = ConclaveSettings(
                parquet_max_file_bytes=file_size, parquet_max_rows=10_000_000
            )
            with patch(
                "synth_engine.modules.synthesizer.engine.get_settings", return_value=settings
            ):
                df = _read_parquet_bounded(path)
            assert len(df) == 100
        finally:
            os.unlink(path)

    def test_parquet_row_count_exceeds_limit_raises(self) -> None:
        """DataFrame whose row count exceeds the configured limit raises DatasetTooLargeError."""
        from synth_engine.modules.synthesizer.engine import _read_parquet_bounded

        data = _make_parquet_bytes(10)

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
            fh.write(data)
            path = fh.name

        try:
            # limit = 9 rows, file has 10 rows
            settings = ConclaveSettings(
                parquet_max_file_bytes=10 * 1024 * 1024, parquet_max_rows=9
            )
            with patch(
                "synth_engine.modules.synthesizer.engine.get_settings", return_value=settings
            ):
                with pytest.raises(DatasetTooLargeError) as exc_info:
                    _read_parquet_bounded(path)
            assert exc_info.value.limit_type == "rows"
            assert exc_info.value.actual_size == 10
            assert exc_info.value.limit == 9
        finally:
            os.unlink(path)

    def test_parquet_row_count_at_limit_loads(self) -> None:
        """DataFrame whose row count equals the limit exactly must load without error."""
        from synth_engine.modules.synthesizer.engine import _read_parquet_bounded

        data = _make_parquet_bytes(10)

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
            fh.write(data)
            path = fh.name

        try:
            settings = ConclaveSettings(
                parquet_max_file_bytes=10 * 1024 * 1024, parquet_max_rows=10
            )
            with patch(
                "synth_engine.modules.synthesizer.engine.get_settings", return_value=settings
            ):
                df = _read_parquet_bounded(path)
            assert len(df) == 10
        finally:
            os.unlink(path)

    def test_size_check_fires_before_row_check(self) -> None:
        """When both size and row limits would fail, the error raised must be for 'bytes'."""
        from synth_engine.modules.synthesizer.engine import _read_parquet_bounded

        data = _make_parquet_bytes(5)
        file_size = len(data)

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
            fh.write(data)
            path = fh.name

        try:
            # Both limits set too low — size check must win
            settings = ConclaveSettings(parquet_max_file_bytes=file_size - 1, parquet_max_rows=1)
            with patch(
                "synth_engine.modules.synthesizer.engine.get_settings", return_value=settings
            ):
                with pytest.raises(DatasetTooLargeError) as exc_info:
                    _read_parquet_bounded(path)
            assert exc_info.value.limit_type == "bytes"
        finally:
            os.unlink(path)

    def test_custom_size_limit_via_settings(self) -> None:
        """A custom parquet_max_file_bytes value set via settings is respected."""
        from synth_engine.modules.synthesizer.engine import _read_parquet_bounded

        data = _make_parquet_bytes(5)
        file_size = len(data)
        custom_limit = file_size - 1  # one byte too small

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
            fh.write(data)
            path = fh.name

        try:
            settings = ConclaveSettings(
                parquet_max_file_bytes=custom_limit, parquet_max_rows=10_000_000
            )
            with patch(
                "synth_engine.modules.synthesizer.engine.get_settings", return_value=settings
            ):
                with pytest.raises(DatasetTooLargeError) as exc_info:
                    _read_parquet_bounded(path)
            assert exc_info.value.limit == custom_limit
        finally:
            os.unlink(path)

    def test_custom_row_limit_via_settings(self) -> None:
        """A custom parquet_max_rows value set via settings is respected."""
        from synth_engine.modules.synthesizer.engine import _read_parquet_bounded

        data = _make_parquet_bytes(20)

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
            fh.write(data)
            path = fh.name

        try:
            settings = ConclaveSettings(
                parquet_max_file_bytes=10 * 1024 * 1024, parquet_max_rows=15
            )
            with patch(
                "synth_engine.modules.synthesizer.engine.get_settings", return_value=settings
            ):
                with pytest.raises(DatasetTooLargeError) as exc_info:
                    _read_parquet_bounded(path)
            assert exc_info.value.limit == 15
            assert exc_info.value.limit_type == "rows"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Feature tests — bytes call site (storage.py)
# ---------------------------------------------------------------------------


class TestReadParquetBoundedBytes:
    """_read_parquet_bounded_bytes must enforce limits on raw bytes input."""

    def setup_method(self) -> None:
        """Clear the settings LRU cache before every test."""
        get_settings.cache_clear()

    def teardown_method(self) -> None:
        """Clear the settings LRU cache after every test."""
        get_settings.cache_clear()

    def test_parquet_bytes_exceeds_size_limit_raises(self) -> None:
        """Bytes payload whose length exceeds the configured limit raises DatasetTooLargeError."""
        from synth_engine.modules.synthesizer.storage import _read_parquet_bounded_bytes

        data = _make_parquet_bytes(50)
        payload_size = len(data)
        limit = payload_size - 1

        settings = ConclaveSettings(parquet_max_file_bytes=limit, parquet_max_rows=10_000_000)
        with patch(
            "synth_engine.modules.synthesizer.storage.get_settings", return_value=settings
        ):
            with pytest.raises(DatasetTooLargeError) as exc_info:
                _read_parquet_bounded_bytes(data)
        assert exc_info.value.limit_type == "bytes"
        assert exc_info.value.actual_size == payload_size
        assert exc_info.value.limit == limit

    def test_parquet_bytes_at_size_limit_loads(self) -> None:
        """Bytes payload whose length equals the limit exactly must load without error."""
        from synth_engine.modules.synthesizer.storage import _read_parquet_bounded_bytes

        data = _make_parquet_bytes(50)
        payload_size = len(data)

        settings = ConclaveSettings(
            parquet_max_file_bytes=payload_size, parquet_max_rows=10_000_000
        )
        with patch(
            "synth_engine.modules.synthesizer.storage.get_settings", return_value=settings
        ):
            df = _read_parquet_bounded_bytes(data)
        assert len(df) == 50

    def test_parquet_bytes_row_count_exceeds_limit_raises(self) -> None:
        """Bytes whose loaded row count exceeds limit raises DatasetTooLargeError."""
        from synth_engine.modules.synthesizer.storage import _read_parquet_bounded_bytes

        data = _make_parquet_bytes(10)

        settings = ConclaveSettings(parquet_max_file_bytes=10 * 1024 * 1024, parquet_max_rows=9)
        with patch(
            "synth_engine.modules.synthesizer.storage.get_settings", return_value=settings
        ):
            with pytest.raises(DatasetTooLargeError) as exc_info:
                _read_parquet_bounded_bytes(data)
        assert exc_info.value.limit_type == "rows"
        assert exc_info.value.actual_size == 10
        assert exc_info.value.limit == 9
