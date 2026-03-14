"""Unit tests for EphemeralStorageClient in the synthesizer module.

RED phase: these tests fail before implementation exists.

Task: P4-T4.1 — GPU Passthrough & Ephemeral Storage
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Acceptance criteria:
#   1. EphemeralStorageClient with mock backend: upload DataFrame → download
#      back → assert equality.
#   2. FORCE_CPU=true → EphemeralStorageClient logs CPU fallback at INFO level;
#      no error raised.
#   3. GPU detection path is mocked (patch torch.cuda.is_available) — do not
#      require hardware in CI.
# ---------------------------------------------------------------------------


class InMemoryBackend:
    """In-memory storage backend for unit tests.

    Implements the StorageBackend Protocol without requiring MinIO.
    """

    def __init__(self) -> None:
        """Initialise an empty in-memory store."""
        self._store: dict[str, bytes] = {}

    def put(self, bucket: str, key: str, data: bytes) -> None:
        """Store bytes under bucket/key.

        Args:
            bucket: Bucket name (unused in this implementation; keys are global).
            key: Object key.
            data: Raw bytes to store.
        """
        self._store[f"{bucket}/{key}"] = data

    def get(self, bucket: str, key: str) -> bytes:
        """Retrieve bytes stored under bucket/key.

        Args:
            bucket: Bucket name.
            key: Object key.

        Returns:
            Raw bytes previously stored at that key.

        Raises:
            KeyError: If the key does not exist in the store.
        """
        return self._store[f"{bucket}/{key}"]


# ---------------------------------------------------------------------------
# Test: upload + download round-trip
# ---------------------------------------------------------------------------


def test_upload_download_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uploading a DataFrame and downloading it back yields equal data.

    This test uses an InMemoryBackend so MinIO is not required.
    """
    monkeypatch.delenv("FORCE_CPU", raising=False)

    from synth_engine.modules.synthesizer.storage import EphemeralStorageClient

    backend = InMemoryBackend()
    client = EphemeralStorageClient(bucket="synth-ephemeral", backend=backend)

    original_df = pd.DataFrame({"id": [1, 2, 3], "value": ["a", "b", "c"]})
    client.upload_parquet("table_customers.parquet", original_df)
    downloaded_df = client.download_parquet("table_customers.parquet")

    pd.testing.assert_frame_equal(
        original_df.reset_index(drop=True),
        downloaded_df.reset_index(drop=True),
    )


# ---------------------------------------------------------------------------
# Test: FORCE_CPU=true logs CPU fallback at INFO
# ---------------------------------------------------------------------------


def test_force_cpu_logs_info(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Setting FORCE_CPU=true causes EphemeralStorageClient to log CPU fallback at INFO.

    No error should be raised.
    """
    monkeypatch.setenv("FORCE_CPU", "true")

    with caplog.at_level(logging.INFO, logger="synth_engine.modules.synthesizer.storage"):
        from synth_engine.modules.synthesizer import storage

        # Force re-evaluation of the env var by re-importing the module's
        # detection function directly.
        storage._log_device_selection()  # type: ignore[attr-defined]

    assert any(
        "cpu" in record.message.lower() and record.levelno == logging.INFO
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Test: GPU detection mocked — torch.cuda.is_available patched
# ---------------------------------------------------------------------------


def test_gpu_detection_mocked_available(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When torch.cuda.is_available returns True and FORCE_CPU is not set,
    device selection logs 'cuda' at INFO level.
    """
    monkeypatch.delenv("FORCE_CPU", raising=False)

    with patch("torch.cuda.is_available", return_value=True):
        with caplog.at_level(logging.INFO, logger="synth_engine.modules.synthesizer.storage"):
            from synth_engine.modules.synthesizer import storage

            storage._log_device_selection()  # type: ignore[attr-defined]

    assert any(
        "cuda" in record.message.lower() and record.levelno == logging.INFO
        for record in caplog.records
    )


def test_gpu_detection_mocked_unavailable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When torch.cuda.is_available returns False and FORCE_CPU is not set,
    device selection logs 'cpu' at INFO level.
    """
    monkeypatch.delenv("FORCE_CPU", raising=False)

    with patch("torch.cuda.is_available", return_value=False):
        with caplog.at_level(logging.INFO, logger="synth_engine.modules.synthesizer.storage"):
            from synth_engine.modules.synthesizer import storage

            storage._log_device_selection()  # type: ignore[attr-defined]

    assert any(
        "cpu" in record.message.lower() and record.levelno == logging.INFO
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Test: FORCE_CPU overrides GPU availability
# ---------------------------------------------------------------------------


def test_force_cpu_overrides_gpu(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """FORCE_CPU=true overrides GPU availability: device is CPU even if CUDA
    claims to be available.
    """
    monkeypatch.setenv("FORCE_CPU", "true")

    with patch("torch.cuda.is_available", return_value=True):
        with caplog.at_level(logging.INFO, logger="synth_engine.modules.synthesizer.storage"):
            from synth_engine.modules.synthesizer import storage

            device = storage._log_device_selection()  # type: ignore[attr-defined]

    assert device == "cpu"
    assert any("cpu" in record.message.lower() for record in caplog.records)


# ---------------------------------------------------------------------------
# Test: EphemeralStorageClient bucket config is injected via constructor
# ---------------------------------------------------------------------------


def test_client_uses_injected_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """EphemeralStorageClient stores data under the configured bucket name."""
    monkeypatch.delenv("FORCE_CPU", raising=False)

    from synth_engine.modules.synthesizer.storage import EphemeralStorageClient

    backend = InMemoryBackend()
    client = EphemeralStorageClient(bucket="my-custom-bucket", backend=backend)

    df = pd.DataFrame({"x": [10, 20]})
    client.upload_parquet("test.parquet", df)

    # Verify the internal store used the correct bucket prefix.
    assert "my-custom-bucket/test.parquet" in backend._store


# ---------------------------------------------------------------------------
# Test: download of non-existent key raises KeyError (backend contract)
# ---------------------------------------------------------------------------


def test_download_nonexistent_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Downloading a key that was never uploaded raises KeyError."""
    monkeypatch.delenv("FORCE_CPU", raising=False)

    from synth_engine.modules.synthesizer.storage import EphemeralStorageClient

    backend = InMemoryBackend()
    client = EphemeralStorageClient(bucket="synth-ephemeral", backend=backend)

    with pytest.raises(KeyError):
        client.download_parquet("does_not_exist.parquet")


# ---------------------------------------------------------------------------
# Marker so pytest can selectively run these tests
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit
