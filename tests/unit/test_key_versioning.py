"""Unit tests for artifact signing key versioning (T42.1).

Acceptance Criteria verified here:
  AC1: Signatures include a key ID prefix.
  AC2: Multiple signing keys supported concurrently.
  AC3: Active key used for new signatures; any key verifies old signatures.
  AC4: Legacy (pre-versioning) artifacts remain verifiable.
  AC5: Key rotation logged to audit trail.
  AC6: sign with key A → verify with key A, rotate to key B → old artifact
       still verifiable, new artifact signed with key B.

Tests are pure unit tests — no database, no filesystem, no network.

CONSTITUTION Priority 3: TDD RED Phase.
Task: T42.1 — Implement Artifact Signing Key Versioning
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Imports under test — these will fail until the implementation exists.
# ---------------------------------------------------------------------------

from synth_engine.shared.security.hmac_signing import (
    HMAC_DIGEST_SIZE,
    KEY_ID_SIZE,
    LEGACY_KEY_ID,
    sign_versioned,
    verify_versioned,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# AC1: Signatures include a key ID prefix
# ---------------------------------------------------------------------------


class TestSignVersionedFormat:
    """Tests for the format of versioned signatures."""

    def test_sign_versioned_returns_key_id_plus_digest(self) -> None:
        """sign_versioned must return KEY_ID_SIZE + HMAC_DIGEST_SIZE bytes."""
        key = b"\xab" * 32
        key_id = b"\x00\x00\x00\x01"
        data = b"test artifact bytes"
        signature = sign_versioned(key=key, key_id=key_id, data=data)
        assert len(signature) == KEY_ID_SIZE + HMAC_DIGEST_SIZE

    def test_sign_versioned_signature_starts_with_key_id(self) -> None:
        """First KEY_ID_SIZE bytes of signature must equal the key_id."""
        key = b"\xcd" * 32
        key_id = b"\x00\x00\x00\x02"
        data = b"some payload"
        signature = sign_versioned(key=key, key_id=key_id, data=data)
        assert signature[:KEY_ID_SIZE] == key_id

    def test_key_id_size_is_four_bytes(self) -> None:
        """KEY_ID_SIZE must be 4 bytes per spec."""
        assert KEY_ID_SIZE == 4

    def test_legacy_key_id_is_zero(self) -> None:
        """LEGACY_KEY_ID must be 0x00000000 (four zero bytes)."""
        assert LEGACY_KEY_ID == b"\x00\x00\x00\x00"

    def test_sign_versioned_is_deterministic(self) -> None:
        """sign_versioned is pure: same inputs must produce same output."""
        key = b"\xef" * 32
        key_id = b"\x00\x00\x00\x03"
        data = b"deterministic payload"
        sig_a = sign_versioned(key=key, key_id=key_id, data=data)
        sig_b = sign_versioned(key=key, key_id=key_id, data=data)
        assert sig_a == sig_b

    def test_sign_versioned_different_key_ids_produce_different_sigs(self) -> None:
        """Different key IDs must produce different signatures over the same data."""
        key = b"\xab" * 32
        data = b"same payload"
        sig_a = sign_versioned(key=key, key_id=b"\x00\x00\x00\x01", data=data)
        sig_b = sign_versioned(key=key, key_id=b"\x00\x00\x00\x02", data=data)
        # The key IDs differ, so the full signature bytes differ
        assert sig_a != sig_b


# ---------------------------------------------------------------------------
# AC2 + AC3: Multiple keys, active key used for signing
# ---------------------------------------------------------------------------


class TestVerifyVersioned:
    """Tests for verify_versioned with key map."""

    def test_verify_versioned_true_with_correct_key(self) -> None:
        """verify_versioned returns True when the key map contains the signing key."""
        key_bytes = b"\xab" * 32
        key_id = b"\x00\x00\x00\x01"
        data = b"artifact bytes"
        signature = sign_versioned(key=key_bytes, key_id=key_id, data=data)
        key_map = {b"\x00\x00\x00\x01": key_bytes}
        assert verify_versioned(key_map=key_map, data=data, signature=signature) is True

    def test_verify_versioned_false_with_wrong_key(self) -> None:
        """verify_versioned returns False when the stored key is wrong for the embedded key ID."""
        key_bytes = b"\xab" * 32
        wrong_key = b"\xcd" * 32
        key_id = b"\x00\x00\x00\x01"
        data = b"artifact bytes"
        signature = sign_versioned(key=key_bytes, key_id=key_id, data=data)
        key_map = {b"\x00\x00\x00\x01": wrong_key}
        assert verify_versioned(key_map=key_map, data=data, signature=signature) is False

    def test_verify_versioned_false_with_tampered_data(self) -> None:
        """verify_versioned returns False when the artifact data was modified."""
        key_bytes = b"\xab" * 32
        key_id = b"\x00\x00\x00\x01"
        original_data = b"original artifact bytes"
        tampered_data = b"tampered artifact bytes"
        signature = sign_versioned(key=key_bytes, key_id=key_id, data=original_data)
        key_map = {b"\x00\x00\x00\x01": key_bytes}
        assert verify_versioned(key_map=key_map, data=tampered_data, signature=signature) is False

    def test_verify_versioned_false_with_unknown_key_id(self) -> None:
        """verify_versioned returns False when the embedded key ID is absent from key_map."""
        key_bytes = b"\xab" * 32
        key_id = b"\x00\x00\x00\x01"
        data = b"artifact bytes"
        signature = sign_versioned(key=key_bytes, key_id=key_id, data=data)
        # Key map does NOT contain key ID 0x00000001
        key_map = {b"\x00\x00\x00\x02": key_bytes}
        assert verify_versioned(key_map=key_map, data=data, signature=signature) is False

    def test_verify_versioned_key_map_with_multiple_keys(self) -> None:
        """verify_versioned with a multi-key map verifies signatures from any known key."""
        key_a = b"\xaa" * 32
        key_b = b"\xbb" * 32
        key_id_a = b"\x00\x00\x00\x01"
        key_id_b = b"\x00\x00\x00\x02"
        data = b"artifact"
        sig_a = sign_versioned(key=key_a, key_id=key_id_a, data=data)
        sig_b = sign_versioned(key=key_b, key_id=key_id_b, data=data)
        key_map = {key_id_a: key_a, key_id_b: key_b}
        assert verify_versioned(key_map=key_map, data=data, signature=sig_a) is True
        assert verify_versioned(key_map=key_map, data=data, signature=sig_b) is True


# ---------------------------------------------------------------------------
# AC4: Legacy (pre-versioning) artifacts remain verifiable
# ---------------------------------------------------------------------------


class TestLegacyBackwardCompatibility:
    """Tests that pre-versioning (legacy) signatures remain verifiable."""

    def test_verify_versioned_handles_legacy_32_byte_signature(self) -> None:
        """A 32-byte legacy signature (no key ID prefix) must verify with LEGACY_KEY_ID key."""
        import hmac as _hmac
        import hashlib

        legacy_key = b"\xde" * 32
        data = b"legacy artifact data"
        # Legacy signature: raw 32-byte HMAC, no key ID prefix
        legacy_sig = _hmac.new(legacy_key, data, hashlib.sha256).digest()
        assert len(legacy_sig) == HMAC_DIGEST_SIZE

        # Must still verify: the key map must include LEGACY_KEY_ID mapped to the legacy key
        key_map = {LEGACY_KEY_ID: legacy_key}
        assert verify_versioned(key_map=key_map, data=data, signature=legacy_sig) is True

    def test_verify_versioned_legacy_signature_with_wrong_key_returns_false(self) -> None:
        """Legacy signature with wrong key returns False, not raise."""
        import hmac as _hmac
        import hashlib

        legacy_key = b"\xde" * 32
        wrong_key = b"\xef" * 32
        data = b"legacy artifact data"
        legacy_sig = _hmac.new(legacy_key, data, hashlib.sha256).digest()

        key_map = {LEGACY_KEY_ID: wrong_key}
        assert verify_versioned(key_map=key_map, data=data, signature=legacy_sig) is False

    def test_verify_versioned_rejects_malformed_signature(self) -> None:
        """A signature that is neither 32 bytes nor KEY_ID_SIZE+HMAC_DIGEST_SIZE returns False."""
        key_map = {LEGACY_KEY_ID: b"\xde" * 32}
        bad_sig = b"\x00" * 10  # too short, neither legacy nor versioned
        data = b"some data"
        assert verify_versioned(key_map=key_map, data=data, signature=bad_sig) is False


# ---------------------------------------------------------------------------
# AC5: Key rotation logged to audit trail
# ---------------------------------------------------------------------------


class TestKeyRotationAuditLogging:
    """Tests for audit trail logging on key rotation events."""

    def test_log_key_rotation_event_calls_audit_logger(self) -> None:
        """log_key_rotation_event must call AuditLogger.log_event with correct fields."""
        from synth_engine.shared.security.hmac_signing import log_key_rotation_event

        mock_audit = MagicMock()
        log_key_rotation_event(
            audit_logger=mock_audit,
            old_key_id="00000001",
            new_key_id="00000002",
            actor="operator",
        )
        mock_audit.log_event.assert_called_once()
        call_kwargs = mock_audit.log_event.call_args.kwargs
        assert call_kwargs["event_type"] == "KEY_ROTATION"
        assert call_kwargs["actor"] == "operator"
        assert call_kwargs["action"] == "rotate"
        assert "00000001" in call_kwargs["details"].get("old_key_id", "")
        assert "00000002" in call_kwargs["details"].get("new_key_id", "")


# ---------------------------------------------------------------------------
# AC6: Full rotation round-trip
# ---------------------------------------------------------------------------


class TestKeyRotationRoundTrip:
    """Tests for the complete sign/verify lifecycle across a key rotation."""

    def test_sign_with_key_a_verify_with_key_a(self) -> None:
        """Artifact signed with key A verifies correctly when key A is in the map."""
        key_a = b"\xaa" * 32
        key_id_a = b"\x00\x00\x00\x01"
        data = b"artifact content"
        sig = sign_versioned(key=key_a, key_id=key_id_a, data=data)
        key_map = {key_id_a: key_a}
        assert verify_versioned(key_map=key_map, data=data, signature=sig) is True

    def test_rotate_to_key_b_old_artifact_still_verifiable(self) -> None:
        """After rotating to key B, an artifact signed with key A remains verifiable.

        Both key A and key B are in the key map during the rotation window.
        """
        key_a = b"\xaa" * 32
        key_b = b"\xbb" * 32
        key_id_a = b"\x00\x00\x00\x01"
        key_id_b = b"\x00\x00\x00\x02"
        data = b"old artifact content"
        old_sig = sign_versioned(key=key_a, key_id=key_id_a, data=data)

        # After rotation: both keys are in the map
        key_map = {key_id_a: key_a, key_id_b: key_b}
        assert verify_versioned(key_map=key_map, data=data, signature=old_sig) is True

    def test_rotate_to_key_b_new_artifact_signed_with_key_b(self) -> None:
        """New artifacts after rotation are signed with key B and verify with key B."""
        key_a = b"\xaa" * 32
        key_b = b"\xbb" * 32
        key_id_a = b"\x00\x00\x00\x01"
        key_id_b = b"\x00\x00\x00\x02"
        new_data = b"new artifact content"
        new_sig = sign_versioned(key=key_b, key_id=key_id_b, data=new_data)

        # Verify the key ID prefix in the new signature is key B's
        assert new_sig[:KEY_ID_SIZE] == key_id_b

        # Both keys in the map — new signature verifies
        key_map = {key_id_a: key_a, key_id_b: key_b}
        assert verify_versioned(key_map=key_map, data=new_data, signature=new_sig) is True


# ---------------------------------------------------------------------------
# ConclaveSettings multi-key fields
# ---------------------------------------------------------------------------


class TestConclaveSettingsMultiKey:
    """Tests for the new multi-key settings fields."""

    def test_settings_accepts_artifact_signing_keys_dict(self) -> None:
        """ConclaveSettings must accept artifact_signing_keys as a JSON-encoded dict."""
        import json
        from synth_engine.shared.settings import ConclaveSettings, get_settings

        keys_dict = {"00000001": "ab" * 32, "00000002": "cd" * 32}
        env_vars = {
            "ARTIFACT_SIGNING_KEYS": json.dumps(keys_dict),
            "ARTIFACT_SIGNING_KEY_ACTIVE": "00000001",
            "DATABASE_URL": "sqlite:///test.db",
            "AUDIT_KEY": "aa" * 32,
        }
        with patch.dict(os.environ, env_vars, clear=False):
            get_settings.cache_clear()
            s = ConclaveSettings()
            assert s.artifact_signing_keys is not None
            assert "00000001" in s.artifact_signing_keys
            assert "00000002" in s.artifact_signing_keys
            assert s.artifact_signing_key_active == "00000001"

    def test_settings_artifact_signing_keys_defaults_to_empty_dict(self) -> None:
        """When ARTIFACT_SIGNING_KEYS is absent, defaults to empty dict."""
        from synth_engine.shared.settings import ConclaveSettings, get_settings

        env_vars = {
            "DATABASE_URL": "sqlite:///test.db",
            "AUDIT_KEY": "aa" * 32,
        }
        # Remove signing keys env vars if present
        remove_keys = ["ARTIFACT_SIGNING_KEYS", "ARTIFACT_SIGNING_KEY_ACTIVE"]
        with patch.dict(os.environ, env_vars, clear=False):
            for k in remove_keys:
                os.environ.pop(k, None)
            get_settings.cache_clear()
            s = ConclaveSettings()
            assert s.artifact_signing_keys == {}
            assert s.artifact_signing_key_active is None
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# job_finalization — versioned signing
# ---------------------------------------------------------------------------


class TestJobFinalizationVersionedSigning:
    """Tests for _write_parquet_with_signing using versioned keys."""

    def test_write_parquet_writes_versioned_sig_file(self, tmp_path: Path) -> None:
        """_write_parquet_with_signing writes a KEY_ID_SIZE+HMAC_DIGEST_SIZE sig file."""
        import json
        from unittest.mock import MagicMock
        from synth_engine.modules.synthesizer.job_finalization import (
            _write_parquet_with_signing,
        )
        from synth_engine.shared.settings import get_settings

        parquet_path = str(tmp_path / "test.parquet")
        parquet_bytes = b"PAR1\x00fake parquet bytes"

        # Create a mock DataFrame
        mock_df = MagicMock()
        mock_df.to_parquet.side_effect = lambda path, **kw: Path(path).write_bytes(
            parquet_bytes
        )

        key_bytes = b"\xab" * 32
        key_id = "00000001"
        keys_dict = {key_id: key_bytes.hex()}

        env_vars = {
            "ARTIFACT_SIGNING_KEYS": json.dumps(keys_dict),
            "ARTIFACT_SIGNING_KEY_ACTIVE": key_id,
        }
        with patch.dict(os.environ, env_vars, clear=False):
            get_settings.cache_clear()
            _write_parquet_with_signing(mock_df, parquet_path)

        sig_path = Path(parquet_path + ".sig")
        assert sig_path.exists(), "Sidecar .sig file must be written"
        sig_bytes = sig_path.read_bytes()
        # Versioned: KEY_ID_SIZE + HMAC_DIGEST_SIZE
        assert len(sig_bytes) == KEY_ID_SIZE + HMAC_DIGEST_SIZE
        # First 4 bytes must equal the active key ID
        assert sig_bytes[:KEY_ID_SIZE] == bytes.fromhex(key_id)

        get_settings.cache_clear()

    def test_write_parquet_falls_back_to_legacy_when_no_versioned_keys(
        self, tmp_path: Path
    ) -> None:
        """Falls back to legacy single-key signing when only ARTIFACT_SIGNING_KEY is set."""
        from unittest.mock import MagicMock
        from synth_engine.modules.synthesizer.job_finalization import (
            _write_parquet_with_signing,
        )
        from synth_engine.shared.settings import get_settings

        parquet_path = str(tmp_path / "legacy.parquet")
        parquet_bytes = b"PAR1\x00legacy parquet bytes"

        mock_df = MagicMock()
        mock_df.to_parquet.side_effect = lambda path, **kw: Path(path).write_bytes(
            parquet_bytes
        )

        legacy_key = b"\xcd" * 32
        remove_keys = ["ARTIFACT_SIGNING_KEYS", "ARTIFACT_SIGNING_KEY_ACTIVE"]
        env_vars = {"ARTIFACT_SIGNING_KEY": legacy_key.hex()}
        with patch.dict(os.environ, env_vars, clear=False):
            for k in remove_keys:
                os.environ.pop(k, None)
            get_settings.cache_clear()
            _write_parquet_with_signing(mock_df, parquet_path)

        sig_path = Path(parquet_path + ".sig")
        assert sig_path.exists(), "Legacy sidecar must be written"
        sig_bytes = sig_path.read_bytes()
        # Legacy format: exactly HMAC_DIGEST_SIZE bytes (no key ID prefix)
        assert len(sig_bytes) == HMAC_DIGEST_SIZE

        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# jobs_streaming — versioned verification
# ---------------------------------------------------------------------------


class TestJobsStreamingVersionedVerification:
    """Tests for _verify_artifact_signature handling versioned and legacy signatures."""

    def test_verify_accepts_versioned_signature(self, tmp_path: Path) -> None:
        """_verify_artifact_signature returns True for a valid versioned signature."""
        import json
        from synth_engine.bootstrapper.routers.jobs_streaming import (
            _verify_artifact_signature,
        )
        from synth_engine.shared.settings import get_settings
        from synth_engine.shared.security.hmac_signing import sign_versioned

        parquet_bytes = b"PAR1\x00versioned artifact"
        parquet_path = tmp_path / "versioned.parquet"
        parquet_path.write_bytes(parquet_bytes)

        key_bytes = b"\xab" * 32
        key_id_bytes = b"\x00\x00\x00\x01"
        key_id_hex = key_id_bytes.hex()
        sig = sign_versioned(key=key_bytes, key_id=key_id_bytes, data=parquet_bytes)
        sig_path = tmp_path / "versioned.parquet.sig"
        sig_path.write_bytes(sig)

        keys_dict = {key_id_hex: key_bytes.hex()}
        env_vars = {
            "ARTIFACT_SIGNING_KEYS": json.dumps(keys_dict),
            "ARTIFACT_SIGNING_KEY_ACTIVE": key_id_hex,
        }
        with patch.dict(os.environ, env_vars, clear=False):
            get_settings.cache_clear()
            result = _verify_artifact_signature(str(parquet_path))

        assert result is True
        get_settings.cache_clear()

    def test_verify_accepts_legacy_signature(self, tmp_path: Path) -> None:
        """_verify_artifact_signature returns True for a valid legacy 32-byte signature."""
        import hmac as _hmac
        import hashlib
        from synth_engine.bootstrapper.routers.jobs_streaming import (
            _verify_artifact_signature,
        )
        from synth_engine.shared.settings import get_settings

        parquet_bytes = b"PAR1\x00legacy artifact"
        parquet_path = tmp_path / "legacy_verify.parquet"
        parquet_path.write_bytes(parquet_bytes)

        legacy_key = b"\xde" * 32
        legacy_sig = _hmac.new(legacy_key, parquet_bytes, hashlib.sha256).digest()
        sig_path = tmp_path / "legacy_verify.parquet.sig"
        sig_path.write_bytes(legacy_sig)

        # Use only the legacy single key (ARTIFACT_SIGNING_KEY)
        remove_keys = ["ARTIFACT_SIGNING_KEYS", "ARTIFACT_SIGNING_KEY_ACTIVE"]
        env_vars = {"ARTIFACT_SIGNING_KEY": legacy_key.hex()}
        with patch.dict(os.environ, env_vars, clear=False):
            for k in remove_keys:
                os.environ.pop(k, None)
            get_settings.cache_clear()
            result = _verify_artifact_signature(str(parquet_path))

        assert result is True
        get_settings.cache_clear()

    def test_verify_returns_false_for_tampered_versioned_artifact(
        self, tmp_path: Path
    ) -> None:
        """_verify_artifact_signature returns False when versioned signature doesn't match."""
        import json
        from synth_engine.bootstrapper.routers.jobs_streaming import (
            _verify_artifact_signature,
        )
        from synth_engine.shared.settings import get_settings
        from synth_engine.shared.security.hmac_signing import sign_versioned

        parquet_bytes = b"PAR1\x00original content"
        tampered_bytes = b"PAR1\x00tampered content"
        parquet_path = tmp_path / "tampered_versioned.parquet"
        parquet_path.write_bytes(tampered_bytes)  # tampered!

        key_bytes = b"\xab" * 32
        key_id_bytes = b"\x00\x00\x00\x01"
        key_id_hex = key_id_bytes.hex()
        # Sign the original, but write the tampered file
        sig = sign_versioned(key=key_bytes, key_id=key_id_bytes, data=parquet_bytes)
        sig_path = tmp_path / "tampered_versioned.parquet.sig"
        sig_path.write_bytes(sig)

        keys_dict = {key_id_hex: key_bytes.hex()}
        env_vars = {
            "ARTIFACT_SIGNING_KEYS": json.dumps(keys_dict),
            "ARTIFACT_SIGNING_KEY_ACTIVE": key_id_hex,
        }
        with patch.dict(os.environ, env_vars, clear=False):
            get_settings.cache_clear()
            result = _verify_artifact_signature(str(parquet_path))

        assert result is False
        get_settings.cache_clear()
