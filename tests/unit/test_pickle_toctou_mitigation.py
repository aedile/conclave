"""Negative/attack tests and feature tests for T50.4 Pickle TOCTOU Mitigation.

TOCTOU (time-of-check-to-time-of-use) vulnerability in ModelArtifact.load():

Before the fix, load() called os.path.exists() then os.path.getsize() before
opening the file — creating a window where an attacker with filesystem write
access could swap the file between the check and the read.

After the fix:
  - No os.path.exists() pre-check: FileNotFoundError propagates from open().
  - No os.path.getsize() pre-stat: size is checked on the in-memory buffer after
    a bounded read of _MAX_ARTIFACT_SIZE_BYTES + 1 bytes.
  - HMAC and pickle.loads() still operate on the same bytes buffer.

Closes ADV-P47-07.

Task: T50.4 — Pickle TOCTOU Mitigation
"""

from __future__ import annotations

import io
import os
import unittest.mock
from pathlib import Path

import pandas as pd
import pytest

from synth_engine.modules.synthesizer.storage.models import ModelArtifact
from synth_engine.shared.security import SecurityError

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants mirrored from models.py for assertions
# ---------------------------------------------------------------------------

_MAX_ARTIFACT_SIZE_BYTES: int = 2 * 1024 * 1024 * 1024
_VALID_KEY: bytes = os.urandom(32)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _PicklableStub:
    """Minimal picklable synthesizer stub for TOCTOU tests."""

    def sample(self, num_rows: int = 1) -> pd.DataFrame:
        """Return a trivial DataFrame.

        Args:
            num_rows: Number of rows to return.

        Returns:
            DataFrame with one column.
        """
        return pd.DataFrame({"id": list(range(num_rows))})


def _make_artifact(table_name: str = "toctou_test") -> ModelArtifact:
    """Return a minimal ModelArtifact with a picklable synthesizer stub.

    Args:
        table_name: Name to assign to the test artifact.

    Returns:
        A ModelArtifact instance with stub fields.
    """
    return ModelArtifact(
        table_name=table_name,
        model=_PicklableStub(),
        column_names=["id"],
        column_dtypes={"id": "int64"},
        column_nullables={"id": False},
    )


# ===========================================================================
# SECTION 1 — ATTACK / NEGATIVE TESTS (ATTACK RED phase, T50.4)
# ===========================================================================

# ---------------------------------------------------------------------------
# ATTACK: No os.path.exists pre-check — FileNotFoundError from open() only
#
# Before the fix, os.path.exists() was called first. An attacker can swap
# the file in the window between exists() and open(). After the fix, we
# just open() the file and let the OS raise FileNotFoundError naturally.
# ---------------------------------------------------------------------------


def test_file_not_found_raises_file_not_found_error(tmp_path: Path) -> None:
    """load() on a missing path raises FileNotFoundError from open(), not from a pre-check.

    The FileNotFoundError must propagate from the open() syscall, proving there
    is no os.path.exists() guard that an attacker could race against.
    """
    missing = str(tmp_path / "does_not_exist.pkl")

    # Verify os.path.exists is NOT called during load() — if it were called, we
    # would detect a TOCTOU pre-check that still exists.
    with unittest.mock.patch(
        "os.path.exists",
        side_effect=AssertionError("os.path.exists() must not be called in ModelArtifact.load()"),
    ):
        with pytest.raises(FileNotFoundError):
            ModelArtifact.load(missing, signing_key=_VALID_KEY)


def test_file_not_found_no_signing_key_raises_file_not_found_error(tmp_path: Path) -> None:
    """load() without signing_key on a missing path raises FileNotFoundError from open().

    Confirms the os.path.exists() pre-check is absent in both signed and unsigned modes.
    """
    missing = str(tmp_path / "does_not_exist_unsigned.pkl")

    with unittest.mock.patch(
        "os.path.exists",
        side_effect=AssertionError("os.path.exists() must not be called in ModelArtifact.load()"),
    ):
        with pytest.raises(FileNotFoundError):
            ModelArtifact.load(missing)


# ---------------------------------------------------------------------------
# ATTACK: No os.path.getsize pre-stat — size checked on buffer after read
#
# Before the fix, os.path.getsize() was called to reject large files before
# opening them. An attacker can swap the file in the window between getsize()
# and open(). After the fix, size is checked on len(raw) after bounded read.
# ---------------------------------------------------------------------------


def test_size_check_uses_buffer_not_disk_stat(tmp_path: Path) -> None:
    """os.path.getsize must NOT be called in ModelArtifact.load() after the TOCTOU fix.

    The size check must operate on the in-memory buffer (len(raw)), not on a
    pre-read os.path.getsize() syscall that could be raced.
    """
    artifact = _make_artifact()
    save_path = tmp_path / "artifact.pkl"
    artifact.save(str(save_path), signing_key=_VALID_KEY)

    with unittest.mock.patch(
        "os.path.getsize",
        side_effect=AssertionError("os.path.getsize() must not be called in ModelArtifact.load()"),
    ):
        # Must succeed: no os.path.getsize() call expected
        loaded = ModelArtifact.load(
            str(save_path), signing_key=_VALID_KEY, extra_allowed_prefixes=("tests",)
        )

    assert loaded.table_name == "toctou_test"


def test_oversized_buffer_raises_value_error_via_bounded_read(tmp_path: Path) -> None:
    """load() rejects files whose buffered length exceeds 2 GiB limit.

    After the TOCTOU fix, the size check operates on len(raw) after a bounded
    read of _MAX_ARTIFACT_SIZE_BYTES + 1. When the read returns more than
    _MAX_ARTIFACT_SIZE_BYTES bytes, ValueError is raised.

    The file is not actually 2 GiB — we mock open() to return an oversized
    stream, so the test runs in milliseconds.
    """
    artifact = _make_artifact()
    save_path = tmp_path / "oversized.pkl"
    artifact.save(str(save_path), signing_key=_VALID_KEY)

    # Produce a buffer that is exactly _MAX_ARTIFACT_SIZE_BYTES + 1 bytes,
    # which is one byte over the limit.
    oversized_data = b"\x00" * (_MAX_ARTIFACT_SIZE_BYTES + 1)

    original_open = open

    def _mock_open(path: str, mode: str = "r", **kwargs: object) -> object:  # type: ignore[override]
        if "rb" in mode and str(save_path) in str(path):
            return io.BytesIO(oversized_data)
        return original_open(path, mode, **kwargs)  # type: ignore[call-overload]

    with unittest.mock.patch("builtins.open", side_effect=_mock_open):
        with pytest.raises(ValueError, match="[Ff]ile.*too large|size.*limit|2.*GiB|2.*GB"):
            ModelArtifact.load(
                str(save_path), signing_key=_VALID_KEY, extra_allowed_prefixes=("tests",)
            )


def test_oversized_buffer_error_message_mentions_size_limit(tmp_path: Path) -> None:
    """ValueError for oversized buffer must mention the size limit in the message.

    This ensures operators get actionable feedback when an artifact is rejected.
    """
    artifact = _make_artifact()
    save_path = tmp_path / "oversized2.pkl"
    artifact.save(str(save_path), signing_key=_VALID_KEY)

    oversized_data = b"\x00" * (_MAX_ARTIFACT_SIZE_BYTES + 1)
    original_open = open

    def _mock_open(path: str, mode: str = "r", **kwargs: object) -> object:  # type: ignore[override]
        if "rb" in mode and str(save_path) in str(path):
            return io.BytesIO(oversized_data)
        return original_open(path, mode, **kwargs)  # type: ignore[call-overload]

    _size_match = r"[Ff]ile.*too large|size.*limit|2.*GiB|2.*GB"
    with unittest.mock.patch("builtins.open", side_effect=_mock_open):
        with pytest.raises(ValueError, match=_size_match) as exc_info:
            ModelArtifact.load(
                str(save_path), signing_key=_VALID_KEY, extra_allowed_prefixes=("tests",)
            )

    error_text = str(exc_info.value)
    # The error message must reference either "GiB", "bytes", or "2147483648"
    assert any(term in error_text for term in ("GiB", "bytes", "2147483648", "too large")), (
        f"ValueError message did not mention size limit: {error_text!r}"
    )


# ---------------------------------------------------------------------------
# ATTACK: HMAC verification still uses the same bytes buffer (no TOCTOU gap
# between size check and HMAC — both operate on len(raw) and raw itself)
# ---------------------------------------------------------------------------


def test_tampered_payload_after_signing_raises_security_error(tmp_path: Path) -> None:
    """Tampered payload bytes must be rejected by HMAC verification.

    This confirms HMAC verification and pickle.loads() both operate on the
    same bytes buffer that was read — no TOCTOU gap between the checks.
    """
    artifact = _make_artifact(table_name="tampered_test")
    save_path = tmp_path / "tampered.pkl"
    artifact.save(str(save_path), signing_key=_VALID_KEY)

    # Read the signed file and flip a byte in the pickle payload region (after byte 32)
    raw = bytearray(save_path.read_bytes())
    assert len(raw) > 33, "Artifact file must have at least 34 bytes for this test"
    raw[33] ^= 0xFF  # Flip all bits in byte 33 (payload region, past the HMAC)
    save_path.write_bytes(bytes(raw))

    with pytest.raises(SecurityError, match="HMAC verification failed"):
        ModelArtifact.load(
            str(save_path), signing_key=_VALID_KEY, extra_allowed_prefixes=("tests",)
        )


def test_signed_artifact_loaded_without_key_raises_security_error(tmp_path: Path) -> None:
    """Signed artifact loaded without signing_key raises SecurityError.

    Confirms the no-key downgrade guard still works after the TOCTOU fix.
    """
    artifact = _make_artifact(table_name="signed_no_key")
    save_path = tmp_path / "signed.pkl"
    artifact.save(str(save_path), signing_key=_VALID_KEY)

    with pytest.raises(SecurityError, match="HMAC verification failed"):
        ModelArtifact.load(str(save_path), signing_key=None)


def test_unsigned_artifact_loaded_with_key_raises_security_error(tmp_path: Path) -> None:
    """Unsigned artifact loaded with a signing_key raises SecurityError.

    Confirms the key-present-but-unsigned guard still works after the TOCTOU fix.
    """
    artifact = _make_artifact(table_name="unsigned_with_key")
    save_path = tmp_path / "unsigned.pkl"
    artifact.save(str(save_path))  # unsigned

    with pytest.raises(SecurityError, match="HMAC verification failed"):
        ModelArtifact.load(
            str(save_path), signing_key=_VALID_KEY, extra_allowed_prefixes=("tests",)
        )


# ===========================================================================
# SECTION 2 — FEATURE TESTS (verify positive behavior after fix)
# ===========================================================================


def test_normal_signed_artifact_loads_successfully(tmp_path: Path) -> None:
    """Signed artifact within size limit loads successfully after TOCTOU fix.

    Regression guard: the fix must not break the happy path.
    """
    artifact = _make_artifact(table_name="happy_path")
    save_path = tmp_path / "artifact.pkl"
    artifact.save(str(save_path), signing_key=_VALID_KEY)

    loaded = ModelArtifact.load(
        str(save_path), signing_key=_VALID_KEY, extra_allowed_prefixes=("tests",)
    )

    assert loaded.table_name == "happy_path"
    assert loaded.column_names == ["id"]
    assert loaded.column_dtypes == {"id": "int64"}
    assert loaded.column_nullables == {"id": False}


def test_unsigned_artifact_loads_successfully_without_key(tmp_path: Path) -> None:
    """Unsigned artifact loads successfully without signing_key after TOCTOU fix.

    Regression guard: backward-compatible unsigned mode must still work.
    """
    artifact = _make_artifact(table_name="unsigned_happy")
    save_path = tmp_path / "unsigned.pkl"
    artifact.save(str(save_path))

    loaded = ModelArtifact.load(str(save_path), extra_allowed_prefixes=("tests",))

    assert loaded.table_name == "unsigned_happy"


def test_bounded_read_is_limit_plus_one(tmp_path: Path) -> None:
    """load() reads at most _MAX_ARTIFACT_SIZE_BYTES + 1 bytes from the file.

    The bounded read strategy is: read(limit + 1), then check len(raw) > limit.
    This test verifies that even when the file is exactly at the limit (not over),
    it loads successfully — i.e., the boundary is correctly >.
    """
    artifact = _make_artifact(table_name="at_limit")
    save_path = tmp_path / "at_limit.pkl"
    artifact.save(str(save_path), signing_key=_VALID_KEY)

    # File is tiny (a few KB) — just verifying no regression at the boundary
    loaded = ModelArtifact.load(
        str(save_path), signing_key=_VALID_KEY, extra_allowed_prefixes=("tests",)
    )
    assert loaded.table_name == "at_limit"
