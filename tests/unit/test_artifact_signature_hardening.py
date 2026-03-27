"""Negative/attack tests and feature tests for ModelArtifact artifact signature hardening.

Attack-first TDD per Rule 22 (CLAUDE.md). Section 1 contains security
invariant tests (attack tests). Section 2 contains feature tests that
verify the new positive behaviors introduced in T47.6.

T47.6 — Harden Model Artifact Signature Verification
"""

from __future__ import annotations

import io
import os
import pickle  # nosec B403 — constructing adversarial payloads for security tests
import tempfile
import unittest.mock
from pathlib import Path

import pandas as pd
import pytest

from synth_engine.modules.synthesizer.storage.artifact import ModelArtifact
from synth_engine.shared.exceptions import ArtifactTamperingError
from synth_engine.shared.security import SecurityError
from synth_engine.shared.security.hmac_signing import compute_hmac

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Test key constants
# ---------------------------------------------------------------------------

_VALID_KEY_32: bytes = os.urandom(32)
_OTHER_KEY_32: bytes = os.urandom(32)
_SHORT_KEY_1: bytes = b"k"
_SHORT_KEY_16: bytes = os.urandom(16)
_SHORT_KEY_31: bytes = os.urandom(31)


class _PicklableStub:
    """Minimal picklable synthesizer stub for hardening tests."""

    def sample(self, num_rows: int = 1) -> pd.DataFrame:
        """Return a trivial DataFrame.

        Args:
            num_rows: Number of rows to return.

        Returns:
            DataFrame with one column.
        """
        return pd.DataFrame({"id": list(range(num_rows))})


def _make_artifact(table_name: str = "attack_test") -> ModelArtifact:
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
# SECTION 1 — ATTACK / NEGATIVE TESTS
# ===========================================================================

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

        audit_messages = [r.message for r in caplog.records]
        assert any("ARTIFACT_VERIFICATION_FAILURE" in msg for msg in audit_messages), (
            "Expected ARTIFACT_VERIFICATION_FAILURE audit event in log records; "
            f"got: {audit_messages}"
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
    """load() must raise ValueError when the buffered read exceeds the size limit.

    This prevents memory exhaustion attacks via crafted oversized artifacts.
    After the TOCTOU fix (T50.4), the size check operates on len(raw) after a
    bounded read — not on os.path.getsize().  We mock builtins.open to return
    an oversized buffer so the test runs in milliseconds without writing 2 GiB.
    """
    oversized_file = tmp_path / "oversized.pkl"
    oversized_file.write_bytes(b"\x00" * 64)

    _max = 2 * 1024 * 1024 * 1024
    oversized_data = b"\x00" * (_max + 1)
    _real_open = open

    def _mock_open(path: str, mode: str = "r", **kwargs: object) -> object:  # type: ignore[override]
        if "rb" in mode and str(oversized_file) in str(path):
            return io.BytesIO(oversized_data)
        return _real_open(path, mode, **kwargs)  # type: ignore[call-overload]

    with unittest.mock.patch("builtins.open", side_effect=_mock_open):
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


# ===========================================================================
# SECTION 2 — FEATURE TESTS (failing before implementation)
# ===========================================================================

# ---------------------------------------------------------------------------
# FEATURE: _detect_signed_format function exists with correct name and docstring
# ---------------------------------------------------------------------------


def test_detect_signed_format_function_exists_with_correct_name() -> None:
    """The format-detector function must be named _detect_signed_format.

    The rename from _looks_signed to _detect_signed_format clarifies that
    this is a format detector, NOT a security check.
    """
    import synth_engine.modules.synthesizer.storage.models as models_mod

    assert hasattr(models_mod, "_detect_signed_format"), (
        "_detect_signed_format not found — rename from _looks_signed not complete"
    )


def test_looks_signed_removed() -> None:
    """The old name _looks_signed must no longer exist on the module."""
    import synth_engine.modules.synthesizer.storage.models as models_mod

    assert not hasattr(models_mod, "_looks_signed"), (
        "_looks_signed still present — rename to _detect_signed_format incomplete"
    )


def test_detect_signed_format_docstring_says_not_security_check() -> None:
    """_detect_signed_format docstring must state it is NOT a security check.

    This prevents future developers from misusing the function as a security
    gate (it is a format detector only — for better error messages).
    """
    import synth_engine.modules.synthesizer.storage.models as models_mod

    doc = models_mod._detect_signed_format.__doc__ or ""
    assert "NOT a security check" in doc, (
        f"_detect_signed_format docstring must say 'NOT a security check'; got: {doc!r}"
    )


# ---------------------------------------------------------------------------
# FEATURE: Minimum key length enforcement — 32 bytes is the minimum accepted
# ---------------------------------------------------------------------------


def test_signing_key_exactly_32_bytes_accepted_by_save() -> None:
    """save() must accept a key of exactly 32 bytes (the minimum valid length)."""
    artifact = _make_artifact(table_name="edge_32")
    key_32 = os.urandom(32)
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        # Must not raise
        artifact.save(str(save_path), signing_key=key_32)
        assert Path(save_path).exists()


def test_signing_key_exactly_32_bytes_accepted_by_load() -> None:
    """load() must accept a key of exactly 32 bytes (the minimum valid length)."""
    artifact = _make_artifact(table_name="edge_32_load")
    key_32 = os.urandom(32)
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(save_path), signing_key=key_32)
        loaded = ModelArtifact.load(
            str(save_path), signing_key=key_32, extra_allowed_prefixes=("tests",)
        )
        assert loaded.table_name == "edge_32_load"


def test_signing_key_longer_than_32_bytes_accepted_by_save() -> None:
    """save() must accept a key longer than 32 bytes (e.g. 64 bytes)."""
    artifact = _make_artifact(table_name="long_key")
    key_64 = os.urandom(64)
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(save_path), signing_key=key_64)
        assert Path(save_path).exists()


# ---------------------------------------------------------------------------
# FEATURE: isinstance guard rejects non-ModelArtifact in unsigned mode too
# ---------------------------------------------------------------------------


def test_isinstance_check_on_unsigned_non_model_artifact(tmp_path: Path) -> None:
    """load() without signing_key must also reject non-ModelArtifact unpickled objects.

    The isinstance guard must fire regardless of whether a signing key is used,
    preventing malicious unsigned pickle files from executing arbitrary code.
    """
    evil_payload = pickle.dumps([1, 2, 3], protocol=pickle.HIGHEST_PROTOCOL)  # nosec B301
    # Make it start with 0x80 (pickle opcode) so _detect_signed_format returns False
    assert evil_payload[0] == 0x80
    unsigned_file = tmp_path / "evil_unsigned.pkl"
    unsigned_file.write_bytes(evil_payload)

    with pytest.raises(ArtifactTamperingError):
        ModelArtifact.load(str(unsigned_file))


# ---------------------------------------------------------------------------
# FEATURE: File size limit — files within the limit load normally
# ---------------------------------------------------------------------------


def test_load_file_within_size_limit_succeeds() -> None:
    """load() must succeed for files within the 2 GiB size limit."""
    artifact = _make_artifact(table_name="size_ok")
    key = os.urandom(32)
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = Path(tmpdir) / "artifact.pkl"
        artifact.save(str(save_path), signing_key=key)
        # Must not raise — real file is only a few KB
        loaded = ModelArtifact.load(
            str(save_path), signing_key=key, extra_allowed_prefixes=("tests",)
        )
        assert loaded.table_name == "size_ok"
