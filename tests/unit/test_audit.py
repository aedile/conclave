"""Unit tests for the WORM audit logger.

RED Phase — all tests must fail before implementation exists.

CONSTITUTION Priority 3: TDD
Task: P2-T2.4 — Vault Observability
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_key_hex() -> str:
    """Return a random 32-byte hex AUDIT_KEY for testing."""
    return os.urandom(32).hex()


@pytest.fixture
def audit_key_bytes(audit_key_hex: str) -> bytes:
    """Return the raw bytes form of audit_key_hex."""
    return bytes.fromhex(audit_key_hex)


@pytest.fixture
def logger_instance(audit_key_bytes: bytes) -> AuditLogger:  # noqa: F821
    """Return a fresh AuditLogger instance."""
    from synth_engine.shared.security.audit import AuditLogger

    return AuditLogger(audit_key_bytes)


# ---------------------------------------------------------------------------
# Signature and integrity tests
# ---------------------------------------------------------------------------


def test_audit_event_has_valid_signature(logger_instance: AuditLogger) -> None:  # noqa: F821
    """A freshly created event verifies correctly against its own signature."""
    event = logger_instance.log_event(
        event_type="TEST",
        actor="pytest",
        resource="test-resource",
        action="read",
        details={"note": "unit test"},
    )

    assert logger_instance.verify_event(event) is True


def test_tampered_event_fails_verification(logger_instance: AuditLogger) -> None:  # noqa: F821
    """Mutating any event field causes verify_event to return False."""
    from synth_engine.shared.security.audit import AuditEvent

    event = logger_instance.log_event(
        event_type="TEST",
        actor="pytest",
        resource="target",
        action="read",
        details={},
    )

    # Pydantic v2: model_copy allows field overrides
    tampered = AuditEvent(
        timestamp=event.timestamp,
        event_type=event.event_type,
        actor=event.actor,
        resource=event.resource,
        action="TAMPERED_ACTION",
        details=event.details,
        prev_hash=event.prev_hash,
        signature=event.signature,
    )

    assert logger_instance.verify_event(tampered) is False


def test_audit_events_form_chain(logger_instance: AuditLogger) -> None:  # noqa: F821
    """The second event's prev_hash equals the SHA-256 hex of the first event's JSON."""
    event1 = logger_instance.log_event(
        event_type="FIRST",
        actor="pytest",
        resource="res",
        action="create",
        details={},
    )
    event2 = logger_instance.log_event(
        event_type="SECOND",
        actor="pytest",
        resource="res",
        action="update",
        details={},
    )

    expected_prev_hash = hashlib.sha256(event1.model_dump_json().encode()).hexdigest()
    assert event2.prev_hash == expected_prev_hash


def test_first_event_has_genesis_prev_hash(logger_instance: AuditLogger) -> None:  # noqa: F821
    """The very first event has prev_hash == '0' * 64 (genesis)."""
    event = logger_instance.log_event(
        event_type="GENESIS",
        actor="system",
        resource="vault",
        action="boot",
        details={},
    )

    assert event.prev_hash == "0" * 64


def test_audit_event_is_json_serializable(logger_instance: AuditLogger) -> None:  # noqa: F821
    """model_dump_json() produces valid JSON with all expected fields."""
    event = logger_instance.log_event(
        event_type="SERIALIZE_TEST",
        actor="pytest",
        resource="resource",
        action="inspect",
        details={"key": "value"},
    )

    raw = event.model_dump_json()
    parsed = json.loads(raw)

    for field in (
        "timestamp",
        "event_type",
        "actor",
        "resource",
        "action",
        "details",
        "prev_hash",
        "signature",
    ):
        assert field in parsed


# ---------------------------------------------------------------------------
# get_audit_logger factory tests
# ---------------------------------------------------------------------------


def test_get_audit_logger_returns_instance(
    audit_key_hex: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_audit_logger() returns an AuditLogger when AUDIT_KEY is valid."""
    monkeypatch.setenv("AUDIT_KEY", audit_key_hex)

    from synth_engine.shared.security.audit import AuditLogger, get_audit_logger

    logger = get_audit_logger()
    assert isinstance(logger, AuditLogger)


def test_get_audit_logger_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_audit_logger() raises ValueError when AUDIT_KEY is not set."""
    monkeypatch.delenv("AUDIT_KEY", raising=False)

    from synth_engine.shared.security.audit import get_audit_logger

    with pytest.raises(ValueError, match="AUDIT_KEY"):
        get_audit_logger()


def test_get_audit_logger_wrong_length_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_audit_logger() raises ValueError when AUDIT_KEY length is not 64 chars."""
    # 10 hex chars = 5 bytes (too short)
    monkeypatch.setenv("AUDIT_KEY", "deadbeef12")

    from synth_engine.shared.security.audit import get_audit_logger

    with pytest.raises(ValueError, match="AUDIT_KEY"):
        get_audit_logger()


def test_get_audit_logger_malformed_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_audit_logger() raises ValueError when AUDIT_KEY has non-hex chars.

    Uses a 64-character string that contains characters outside [0-9a-f]
    to exercise the bytes.fromhex() error path.
    """
    # 64 chars, correct length, but 'z' and 'g' are not valid hex digits
    bad_key = "z" * 32 + "g" * 32
    monkeypatch.setenv("AUDIT_KEY", bad_key)

    from synth_engine.shared.security.audit import get_audit_logger

    with pytest.raises(ValueError, match="AUDIT_KEY"):
        get_audit_logger()


def test_audit_event_logged_to_stdout(
    logger_instance: AuditLogger,  # noqa: F821
    caplog: pytest.LogCaptureFixture,
) -> None:
    """log_event() emits a log record at INFO on the conclave.audit logger."""
    import logging

    with caplog.at_level(logging.INFO, logger="conclave.audit"):
        event = logger_instance.log_event(
            event_type="LOG_TEST",
            actor="pytest",
            resource="stdout",
            action="verify",
            details={},
        )

    assert any("LOG_TEST" in record.message for record in caplog.records), (
        f"Expected 'LOG_TEST' in log records; got: {[r.message for r in caplog.records]}"
    )
    # Verify the logged message is valid JSON
    matching = [r for r in caplog.records if "LOG_TEST" in r.message]
    assert matching
    parsed = json.loads(matching[0].message)
    assert parsed["event_type"] == "LOG_TEST"
    assert parsed["signature"] == event.signature
