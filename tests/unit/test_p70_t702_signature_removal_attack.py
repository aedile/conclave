"""Negative/attack tests for T70.2 — Legacy audit signature v1/v2 removal.

ATTACK-FIRST TDD — these tests prove:
- New audit entries always use v3 format
- v1/v2 entries still verify (backward compat) but emit WARNINGs
- sign_v1/sign_v2 are not importable from the public API
- Migration tool converts v1/v2 entries to v3
- Tampered entries are refused by the migration tool
- Pipe injection on v3 format fails verification

CONSTITUTION Priority 0: Security — audit chain integrity is P0
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T70.2 — Remove Legacy Audit Signature Formats v1/v2 (C11)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audit_key() -> bytes:
    """Generate a fresh 32-byte audit key for test isolation."""
    return os.urandom(32)


def _make_test_logger(audit_key: bytes) -> Any:
    """Build a fresh AuditLogger with no chain history (test isolation)."""
    from synth_engine.shared.security.audit_logger import AuditLogger

    return AuditLogger(audit_key=audit_key)


# ---------------------------------------------------------------------------
# T70.2 — New audit entries are always v3
# ---------------------------------------------------------------------------


class TestSignAuditEntryAlwaysV3:
    """sign_audit_entry() (via log_event) must always produce v3: signatures."""

    def test_sign_audit_entry_always_v3(self) -> None:
        """log_event must produce a v3: prefixed signature for every new entry."""
        key = _make_audit_key()
        logger = _make_test_logger(key)

        event = logger.log_event(
            event_type="TEST_EVENT",
            actor="test-actor",
            resource="test-resource",
            action="test-action",
            details={"key": "value"},
        )

        assert event.signature.startswith("v3:"), (
            f"Expected v3: prefix, got signature: {event.signature[:10]!r}"
        )


# ---------------------------------------------------------------------------
# T70.2 — v1/v2 still verify but emit WARNING
# ---------------------------------------------------------------------------


class TestV1V2BackwardCompatVerification:
    """v1 and v2 entries must still verify but must emit deprecation WARNINGs."""

    def test_v1_still_verifies_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """v1 signature must verify True and emit a deprecation WARNING."""
        from synth_engine.shared.security.audit_logger import AuditEvent
        from synth_engine.shared.security.audit_signatures import sign_v1

        key = _make_audit_key()
        logger = _make_test_logger(key)

        # Build a v1-signed event manually
        timestamp = "2024-01-01T00:00:00+00:00"
        sig = sign_v1(
            key,
            timestamp,
            "OLD_EVENT",
            "actor",
            "resource",
            "action",
            "0" * 64,
        )
        event = AuditEvent(
            timestamp=timestamp,
            event_type="OLD_EVENT",
            actor="actor",
            resource="resource",
            action="action",
            details={},
            prev_hash="0" * 64,
            signature=sig,
        )

        with caplog.at_level(logging.WARNING):
            result = logger.verify_event(event)

        assert result is True
        # Must emit a WARNING about deprecated v1 format
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("v1" in msg.lower() or "deprecated" in msg.lower() or "warn" in msg.lower()
                   for msg in warning_msgs), (
            f"Expected WARNING about v1, got: {warning_msgs}"
        )

    def test_v2_still_verifies_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """v2 signature must verify True and emit a deprecation WARNING."""
        from synth_engine.shared.security.audit_logger import AuditEvent
        from synth_engine.shared.security.audit_signatures import sign_v2

        key = _make_audit_key()
        logger = _make_test_logger(key)

        timestamp = "2024-01-01T00:00:00+00:00"
        details: dict[str, str] = {"info": "test"}
        sig = sign_v2(
            key,
            timestamp,
            "OLD_EVENT_V2",
            "actor",
            "resource",
            "action",
            "0" * 64,
            details,
        )
        event = AuditEvent(
            timestamp=timestamp,
            event_type="OLD_EVENT_V2",
            actor="actor",
            resource="resource",
            action="action",
            details=details,
            prev_hash="0" * 64,
            signature=sig,
        )

        with caplog.at_level(logging.WARNING):
            result = logger.verify_event(event)

        assert result is True
        # Must emit a WARNING about deprecated v2 format
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("v2" in msg.lower() or "deprecated" in msg.lower()
                   for msg in warning_msgs), (
            f"Expected WARNING about v2, got: {warning_msgs}"
        )


# ---------------------------------------------------------------------------
# T70.2 — sign_v1/sign_v2 not importable from public API
# ---------------------------------------------------------------------------


class TestSignV1V2NotPublicApi:
    """sign_v1 and sign_v2 must not be importable from the public audit API."""

    def test_sign_v1_not_importable_from_public_api(self) -> None:
        """sign_v1 must not be accessible from synth_engine.shared.security.audit.__all__."""
        from synth_engine.shared.security import audit as audit_module

        # sign_v1 must not be in __all__
        assert "sign_v1" not in audit_module.__all__, (
            "sign_v1 must not be in the public __all__ of audit.py — "
            "it is a deprecated private function"
        )

    def test_sign_v2_not_importable_from_public_api(self) -> None:
        """sign_v2 must not be accessible from synth_engine.shared.security.audit.__all__."""
        from synth_engine.shared.security import audit as audit_module

        # sign_v2 must not be in __all__
        assert "sign_v2" not in audit_module.__all__, (
            "sign_v2 must not be in the public __all__ of audit.py — "
            "it is a deprecated private function"
        )


# ---------------------------------------------------------------------------
# T70.2 — Migration tool converts v1/v2 entries to v3
# ---------------------------------------------------------------------------


class TestMigrationTool:
    """The migrate_audit_signatures migration tool must convert v1/v2 entries."""

    def _make_jsonl_with_v1_entry(
        self, key: bytes, tmpdir: str
    ) -> str:
        """Create a JSONL audit log file with one v1 entry.

        Args:
            key: Audit HMAC key.
            tmpdir: Temp directory path for the file.

        Returns:
            Path to the created JSONL file.
        """
        from synth_engine.shared.security.audit_signatures import sign_v1

        timestamp = "2024-01-01T00:00:00+00:00"
        sig = sign_v1(key, timestamp, "OLD", "actor", "res", "act", "0" * 64)
        entry = {
            "timestamp": timestamp,
            "event_type": "OLD",
            "actor": "actor",
            "resource": "res",
            "action": "act",
            "details": {},
            "prev_hash": "0" * 64,
            "signature": sig,
        }
        path = os.path.join(tmpdir, "audit.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(entry) + "\n")
        return path

    def test_migration_converts_v1_to_v3(self) -> None:
        """migrate_audit_signatures must re-sign v1 entries as v3."""
        from synth_engine.shared.security.audit_migrations import migrate_audit_signatures

        key = _make_audit_key()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_jsonl_with_v1_entry(key, tmpdir)
            out_path = os.path.join(tmpdir, "audit_migrated.jsonl")

            # Run migration
            migrate_audit_signatures(input_path=path, output_path=out_path, audit_key=key)

            # Verify output has v3 signature
            with open(out_path) as f:
                lines = f.readlines()

            assert len(lines) == 1
            migrated = json.loads(lines[0])
            assert migrated["signature"].startswith("v3:"), (
                f"Expected v3: signature after migration, got: {migrated['signature'][:10]!r}"
            )

    def test_migration_refuses_tampered_entry(self) -> None:
        """Migration must refuse (skip with ERROR) entries with invalid signatures."""
        from synth_engine.shared.security.audit_migrations import migrate_audit_signatures

        key = _make_audit_key()
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a v1 entry then tamper with actor field
            path = self._make_jsonl_with_v1_entry(key, tmpdir)

            # Tamper: modify the actor field (invalidates HMAC)
            with open(path) as f:
                entry = json.loads(f.read())
            entry["actor"] = "tampered-actor"
            with open(path, "w") as f:
                f.write(json.dumps(entry) + "\n")

            out_path = os.path.join(tmpdir, "audit_migrated.jsonl")

            # Migration must skip tampered entries
            migrate_audit_signatures(input_path=path, output_path=out_path, audit_key=key)

            # Tampered entry must NOT appear in output
            with open(out_path) as f:
                lines = f.readlines()

            assert len(lines) == 0, (
                "Tampered entry must not appear in migration output"
            )

    def test_migration_skips_unrecognized_format(self) -> None:
        """Migration must skip entries with unrecognized signature format (no crash)."""
        from synth_engine.shared.security.audit_migrations import migrate_audit_signatures

        key = _make_audit_key()
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = {
                "timestamp": "2024-01-01T00:00:00+00:00",
                "event_type": "UNKNOWN",
                "actor": "actor",
                "resource": "res",
                "action": "act",
                "details": {},
                "prev_hash": "0" * 64,
                "signature": "unknown_format:abc123",
            }
            path = os.path.join(tmpdir, "audit.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps(entry) + "\n")

            out_path = os.path.join(tmpdir, "audit_migrated.jsonl")
            # Must not crash
            migrate_audit_signatures(input_path=path, output_path=out_path, audit_key=key)

            # Unrecognized format must be skipped (empty output)
            with open(out_path) as f:
                lines = f.readlines()
            assert len(lines) == 0


# ---------------------------------------------------------------------------
# T70.2 — Pipe injection on v3 format fails verification
# ---------------------------------------------------------------------------


class TestPipeInjectionV3:
    """Pipe-delimiter injection must fail verification on v3 format."""

    def test_pipe_injection_v3_fails_verification(self) -> None:
        """v3 signature for 'a|b' actor must not verify for 'a' + '|b' resource.

        The v3 length-prefix encoding eliminates the pipe-injection vulnerability
        present in v1/v2 formats (ADV-P53-01).  Injecting '|' into a field value
        must produce a different HMAC than splitting the same bytes at the pipe.
        """
        from synth_engine.shared.security.audit_logger import AuditEvent
        from synth_engine.shared.security.audit_signatures import sign_v3

        key = _make_audit_key()
        logger = _make_test_logger(key)

        # Create an event with pipe in actor field (injection attempt)
        timestamp = "2024-01-01T00:00:00+00:00"
        legitimate_sig = sign_v3(
            key,
            timestamp,
            "TEST",
            "actor|injected_resource",  # pipe in actor
            "",  # empty resource
            "action",
            "0" * 64,
            {},
        )

        # Build the "injected" version — same raw bytes, different field split
        injected_event = AuditEvent(
            timestamp=timestamp,
            event_type="TEST",
            actor="actor",
            resource="injected_resource",  # attacker moves |injected_resource to resource
            action="action",
            details={},
            prev_hash="0" * 64,
            signature=legitimate_sig,
        )

        # The injected event must NOT verify — v3 length prefixing prevents this
        result = logger.verify_event(injected_event)
        assert result is False, (
            "Pipe injection must not produce a valid v3 signature — "
            "length-prefix encoding should prevent field boundary confusion"
        )
