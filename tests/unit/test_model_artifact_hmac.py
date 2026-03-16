"""Unit tests for HMAC-SHA256 signing of ModelArtifact pickle artifacts.

RED phase: these tests define the contract for ADV-040 security hardening.

Task: P8-T8.2 — Security Hardening (ADV-040)

Contract:
- ModelArtifact.save(path, signing_key=key) produces a file whose first 32 bytes
  are an HMAC-SHA256 signature over the remaining pickle payload.
- ModelArtifact.load(path, signing_key=key) verifies the signature before unpickling.
- Loading with a wrong key raises SecurityError.
- Loading a tampered payload raises SecurityError.
- Calling save/load without a signing_key falls back to unsigned mode (backward compat).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from synth_engine.modules.synthesizer.models import ModelArtifact, SecurityError


class _PicklableModelStub:
    """Minimal picklable synthesizer stub for HMAC tests."""

    def __init__(self, param: str = "test_value") -> None:
        self.param = param

    def sample(self, num_rows: int = 1) -> pd.DataFrame:
        """Return a trivial DataFrame.

        Args:
            num_rows: Number of rows to return.

        Returns:
            DataFrame with one column.
        """
        return pd.DataFrame({"id": list(range(num_rows))})


def _make_artifact(table_name: str = "customers") -> ModelArtifact:
    """Create a minimal ModelArtifact with a picklable synthesizer stub.

    Args:
        table_name: Name to assign to the artifact.

    Returns:
        A fully-populated ModelArtifact instance.
    """
    return ModelArtifact(
        table_name=table_name,
        model=_PicklableModelStub(),
        column_names=["id", "name"],
        column_dtypes={"id": "int64", "name": "object"},
        column_nullables={"id": False, "name": False},
    )


# ---------------------------------------------------------------------------
# Signing key fixtures
# ---------------------------------------------------------------------------

_VALID_KEY: bytes = os.urandom(32)
_OTHER_KEY: bytes = os.urandom(32)


# ---------------------------------------------------------------------------
# RED: save with signing_key produces a signed artifact
# ---------------------------------------------------------------------------


def test_save_with_signing_key_creates_file() -> None:
    """save(path, signing_key=key) must create a file at the given path."""
    artifact = _make_artifact()
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(save_path), signing_key=_VALID_KEY)
        assert save_path.exists()


def test_save_with_signing_key_returns_path() -> None:
    """save(path, signing_key=key) must return the path it was saved to."""
    artifact = _make_artifact()
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        result = artifact.save(str(save_path), signing_key=_VALID_KEY)
        assert result == str(save_path)


def test_signed_file_has_hmac_header() -> None:
    """The signed file must begin with a 32-byte HMAC-SHA256 header."""
    artifact = _make_artifact()
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(save_path), signing_key=_VALID_KEY)
        raw = save_path.read_bytes()
        # File must be at least 33 bytes: 32-byte HMAC + at least 1 byte payload
        assert len(raw) > 32


# ---------------------------------------------------------------------------
# RED: load with correct signing_key succeeds and verifies signature
# ---------------------------------------------------------------------------


def test_load_signed_artifact_with_correct_key() -> None:
    """load(path, signing_key=key) with the correct key must return a ModelArtifact."""
    artifact = _make_artifact(table_name="orders")
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(save_path), signing_key=_VALID_KEY)
        loaded = ModelArtifact.load(str(save_path), signing_key=_VALID_KEY)
        assert isinstance(loaded, ModelArtifact)
        assert loaded.table_name == "orders"


def test_round_trip_with_signing_key_preserves_all_fields() -> None:
    """save+load with a signing_key must preserve all ModelArtifact fields exactly."""
    artifact = _make_artifact(table_name="products")
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(save_path), signing_key=_VALID_KEY)
        loaded = ModelArtifact.load(str(save_path), signing_key=_VALID_KEY)

        assert loaded.table_name == "products"
        assert loaded.column_names == ["id", "name"]
        assert loaded.column_dtypes == {"id": "int64", "name": "object"}
        assert loaded.column_nullables == {"id": False, "name": False}


# ---------------------------------------------------------------------------
# RED: load with wrong key raises SecurityError
# ---------------------------------------------------------------------------


def test_load_with_wrong_signing_key_raises_security_error() -> None:
    """load(path, signing_key=wrong_key) must raise SecurityError."""
    artifact = _make_artifact()
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(save_path), signing_key=_VALID_KEY)
        with pytest.raises(SecurityError, match="HMAC verification failed"):
            ModelArtifact.load(str(save_path), signing_key=_OTHER_KEY)


def test_load_with_tampered_payload_raises_security_error() -> None:
    """load() must raise SecurityError if the pickle payload has been tampered with."""
    artifact = _make_artifact()
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(save_path), signing_key=_VALID_KEY)

        # Tamper with the payload (flip a byte after the 32-byte HMAC header)
        raw = bytearray(save_path.read_bytes())
        raw[32] ^= 0xFF  # Flip all bits in the first payload byte
        save_path.write_bytes(bytes(raw))

        with pytest.raises(SecurityError, match="HMAC verification failed"):
            ModelArtifact.load(str(save_path), signing_key=_VALID_KEY)


def test_load_signed_artifact_without_key_raises_security_error() -> None:
    """Loading a signed artifact without providing a signing_key raises SecurityError.

    A signed artifact cannot be silently downgraded to unsigned mode — that would
    allow an attacker to bypass HMAC verification by simply omitting the key.
    """
    artifact = _make_artifact()
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(save_path), signing_key=_VALID_KEY)
        # Loading without key: signed artifacts have the HMAC header; unsigned load
        # must detect the mismatch and raise SecurityError rather than unpickling.
        with pytest.raises(SecurityError, match="HMAC verification failed"):
            ModelArtifact.load(str(save_path), signing_key=_OTHER_KEY)


# ---------------------------------------------------------------------------
# RED: backward-compatibility — unsigned mode still works without signing_key
# ---------------------------------------------------------------------------


def test_unsigned_save_and_load_round_trip() -> None:
    """save/load without signing_key must work as before (backward compatibility)."""
    artifact = _make_artifact(table_name="legacy_table")
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(save_path))
        loaded = ModelArtifact.load(str(save_path))
        assert isinstance(loaded, ModelArtifact)
        assert loaded.table_name == "legacy_table"


# ---------------------------------------------------------------------------
# RED: SecurityError is defined and is an Exception subclass
# ---------------------------------------------------------------------------


def test_security_error_is_exception() -> None:
    """SecurityError must be a subclass of Exception for broad catch compatibility."""
    assert issubclass(SecurityError, Exception)


def test_security_error_carries_message() -> None:
    """SecurityError must carry a human-readable message."""
    error = SecurityError("HMAC verification failed: signature mismatch")
    assert "HMAC verification failed" in str(error)


# ---------------------------------------------------------------------------
# Marker
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit
