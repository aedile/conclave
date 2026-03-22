"""Negative/attack tests for ModelArtifact artifact signature hardening.

Attack-first TDD per Rule 22 (CLAUDE.md). These tests define security
invariants that MUST hold before feature tests are written.

T47.6 — Harden Model Artifact Signature Verification
"""

from __future__ import annotations

import os
import pickle  # nosec B403 — constructing adversarial payloads for security tests
import struct
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from synth_engine.modules.synthesizer.models import ModelArtifact
from synth_engine.shared.exceptions import ArtifactTamperingError
from synth_engine.shared.security import SecurityError
from synth_engine.shared.security.hmac_signing import HMAC_DIGEST_SIZE, compute_hmac

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Test key constants
# ---------------------------------------------------------------------------

_VALID_KEY_32: bytes = os.urandom(32)
_OTHER_KEY_32: bytes = os.urandom(32)
_SHORT_KEY_1: bytes = b"k"
_SHORT_KEY_16: bytes = os.urandom(16)
_SHORT_KEY_31: bytes = os.urandom(31)


def _make_artifact(table_name: str = "attack_test") -> ModelArtifact:
    """Return a minimal ModelArtifact for adversarial test construction.

    Args:
        table_name: Name to assign to the test artifact.

    Returns:
        A ModelArtifact instance with stub fields.
    """
    return ModelArtifact(
        table_name=table_name,
        model=MagicMock(),
        column_names=["id"],
        column_dtypes={"id": "int64"},
        column_nullables={"id": False},
    )


# ---------------------------------------------------------------------------
# ATTACK: Crafted preamble — 32-byte preamble crafted to look signed, but
# the HMAC over the remaining bytes does not match the signing key.
# This tests that _detect_signed_format + verify_hmac together prevent
# a crafted-header bypass attack.
# ---------------------------------------------------------------------------


def test_crafted_preamble_with_key_fails_hmac() -> None:
    """Crafted 32-byte preamble + valid signing key must raise SecurityError.

    An attacker crafts a file whose first 32 bytes look like an HMAC header
    but whose payload is arbitrary.  Even with a valid key, HMAC mismatch
    must be detected before unpickling.
    """
    artifact = _make_artifact()
    with tempfile.TemporaryDirectory() as tmpdir:
        # Build a legitimate pickle payload so byte 32 starts with 0x80 (valid format)
        pickle_payload = pickle.dumps(artifact, protocol=pickle.HIGHEST_PROTOCOL)  # nosec B301
        # Craft a 32-byte preamble that is NOT the real HMAC over this payload
        crafted_preamble = b"\xde\xad\xbe\xef" * 8  # 32 bytes of garbage
        crafted_file = Path(tmpdir) / "crafted.pkl"
        crafted_file.write_bytes(crafted_preamble + pickle_payload)

        with pytest.raises(SecurityError, match="HMAC verification failed"):
            ModelArtifact.load(str(crafted_file), signing_key=_VALID_KEY_32)


# ---------------------------------------------------------------------------
# ATTACK: isinstance check — valid HMAC over a non-ModelArtifact pickle.
# A compromised signing key could produce an artifact that passes HMAC
# but is not a ModelArtifact.
# ---------------------------------------------------------------------------


def test_isinstance_check_after_unpickle_raises_artifact_tampering_error() -> None:
    """Valid HMAC over a non-ModelArtifact pickle must raise ArtifactTamperingError.

    Even when HMAC passes (key is valid), if the unpickled object is not a
    ModelArtifact instance, loading must be rejected.  A compromised key
    could craft a valid-HMAC artifact that executes attacker-controlled code.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Pickle a plain dict (NOT a ModelArtifact)
        evil_payload = pickle.dumps({"injected": "data"}, protocol=pickle.HIGHEST_PROTOCOL)  # nosec B301
        # Sign it with our valid key so HMAC passes
        real_hmac = compute_hmac(_VALID_KEY_32, evil_payload)
        crafted_file = Path(tmpdir) / "tampered.pkl"
        crafted_file.write_bytes(real_hmac + evil_payload)

        with pytest.raises(ArtifactTamperingError):
            ModelArtifact.load(str(crafted_file), signing_key=_VALID_KEY_32)


# ---------------------------------------------------------------------------
# ATTACK: Signing key length enforcement on save()
# ---------------------------------------------------------------------------


def test_signing_key_shorter_than_32_bytes_raises_on_save() -> None:
    """save() with a 1-byte signing key must raise ValueError.

    Keys shorter than 32 bytes provide insufficient security strength and
    must be rejected at the API boundary, not silently accepted.
    """
    artifact = _make_artifact()
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "short_key.pkl"
        with pytest.raises(ValueError, match="32 bytes"):
            artifact.save(str(save_path), signing_key=_SHORT_KEY_1)


def test_signing_key_16_bytes_raises_on_save() -> None:
    """save() with a 16-byte signing key must raise ValueError."""
    artifact = _make_artifact()
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "short_key.pkl"
        with pytest.raises(ValueError, match="32 bytes"):
            artifact.save(str(save_path), signing_key=_SHORT_KEY_16)


def test_signing_key_31_bytes_raises_on_save() -> None:
    """save() with a 31-byte (one short) signing key must raise ValueError."""
    artifact = _make_artifact()
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "short_key.pkl"
        with pytest.raises(ValueError, match="32 bytes"):
            artifact.save(str(save_path), signing_key=_SHORT_KEY_31)


def test_signing_key_empty_raises_on_save() -> None:
    """save() with signing_key=b'' must raise ValueError (existing behavior preserved)."""
    artifact = _make_artifact()
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "empty_key.pkl"
        with pytest.raises(ValueError, match="signing_key must not be empty"):
            artifact.save(str(save_path), signing_key=b"")


# ---------------------------------------------------------------------------
# ATTACK: Signing key length enforcement on load()
# ---------------------------------------------------------------------------


def test_signing_key_shorter_than_32_bytes_raises_on_load() -> None:
    """load() with a 1-byte signing key must raise ValueError.

    Short keys on load must be rejected before any file I/O or HMAC work.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a valid signed artifact first (using a 32-byte key) so the
        # file exists — we want to confirm rejection is from key validation,
        # not FileNotFoundError.
        artifact = _make_artifact()
        valid_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(valid_path), signing_key=_VALID_KEY_32)

        with pytest.raises(ValueError, match="32 bytes"):
            ModelArtifact.load(str(valid_path), signing_key=_SHORT_KEY_1)


def test_signing_key_empty_raises_on_load() -> None:
    """load() with signing_key=b'' must raise ValueError (existing behavior preserved)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact = _make_artifact()
        valid_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(valid_path), signing_key=_VALID_KEY_32)

        with pytest.raises(ValueError, match="signing_key must not be empty"):
            ModelArtifact.load(str(valid_path), signing_key=b"")


# ---------------------------------------------------------------------------
# ATTACK: Audit trail on verification failure
# ---------------------------------------------------------------------------


def test_audit_trail_emitted_on_hmac_verification_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failed HMAC verification must emit an audit event.

    The audit log captures security-relevant failures so operators can detect
    integrity breach attempts.  The event must appear even when the error is
    propagated to the caller.
    """
    artifact = _make_artifact()
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(save_path), signing_key=_VALID_KEY_32)

        with caplog.at_level("WARNING"):
            with pytest.raises(SecurityError):
                ModelArtifact.load(str(save_path), signing_key=_OTHER_KEY_32)

        # The audit event must have been emitted to some logger
        audit_messages = [r.message for r in caplog.records]
        assert any("ARTIFACT_VERIFICATION_FAILURE" in msg for msg in audit_messages), (
            f"Expected ARTIFACT_VERIFICATION_FAILURE audit event in log records; got: {audit_messages}"
        )


def test_audit_trail_emitted_on_tampering_detection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ArtifactTamperingError from isinstance check must emit an audit event."""
    with tempfile.TemporaryDirectory() as tmpdir:
        evil_payload = pickle.dumps({"bad": "actor"}, protocol=pickle.HIGHEST_PROTOCOL)  # nosec B301
        real_hmac = compute_hmac(_VALID_KEY_32, evil_payload)
        crafted_file = Path(tmpdir) / "tampered.pkl"
        crafted_file.write_bytes(real_hmac + evil_payload)

        with caplog.at_level("WARNING"):
            with pytest.raises(ArtifactTamperingError):
                ModelArtifact.load(str(crafted_file), signing_key=_VALID_KEY_32)

        audit_messages = [r.message for r in caplog.records]
        assert any("ARTIFACT_VERIFICATION_FAILURE" in msg for msg in audit_messages), (
            f"Expected ARTIFACT_VERIFICATION_FAILURE audit event; got: {audit_messages}"
        )


# ---------------------------------------------------------------------------
# ATTACK: File size limit — oversized files must be rejected
# ---------------------------------------------------------------------------


def test_load_file_exceeding_size_limit_raises(tmp_path: Path) -> None:
    """load() must raise ValueError when the file exceeds the size limit.

    This prevents memory exhaustion attacks via crafted oversized artifacts.
    The check must happen before reading the file contents.
    """
    oversized_file = tmp_path / "oversized.pkl"
    # Write a valid-looking but tiny file and then test with a mocked size.
    # We mock os.path.getsize to avoid writing gigabytes in tests.
    oversized_file.write_bytes(b"\x00" * 64)

    import unittest.mock

    # Simulate a file that reports 3 GiB in size
    _3_GIB = 3 * 1024 * 1024 * 1024
    with unittest.mock.patch("os.path.getsize", return_value=_3_GIB):
        with pytest.raises(ValueError, match="[Ff]ile.*too large|size.*limit|2.*GiB|2.*GB"):
            ModelArtifact.load(str(oversized_file), signing_key=_VALID_KEY_32)


# ---------------------------------------------------------------------------
# ATTACK: exactly 32-byte file + signing_key must be caught by existing check
# (file too short to contain valid HMAC header + payload)
# ---------------------------------------------------------------------------


def test_load_exactly_32_bytes_with_key_raises_security_error() -> None:
    """load() with a 32-byte file and signing_key must raise SecurityError.

    A 32-byte file cannot contain both the HMAC header and a valid pickle
    payload.  The existing 'too short' check must fire.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        short_file = Path(tmpdir) / "exactly32.pkl"
        short_file.write_bytes(os.urandom(32))  # exactly HMAC_DIGEST_SIZE bytes

        with pytest.raises(SecurityError, match="HMAC verification failed"):
            ModelArtifact.load(str(short_file), signing_key=_VALID_KEY_32)
