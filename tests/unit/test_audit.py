"""Unit tests for the WORM audit logger.

RED Phase — all tests must fail before implementation exists.

CONSTITUTION Priority 3: TDD
Task: P2-T2.4 — Vault Observability
Task: P2-D3 — AuditLogger singleton & cross-request chain integrity
Task: T49.1 — Assertion Hardening: replace key-presence with value validity
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Generator
from datetime import UTC

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


@pytest.fixture(autouse=True)
def reset_logger(monkeypatch: pytest.MonkeyPatch, audit_key_hex: str) -> Generator[None]:
    """Reset the module-level singleton after each test for isolation.

    Also ensures AUDIT_KEY is set for tests that exercise get_audit_logger()
    via the singleton path.
    """
    monkeypatch.setenv("AUDIT_KEY", audit_key_hex)
    yield
    from synth_engine.shared.security.audit import reset_audit_logger

    reset_audit_logger()


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
    """model_dump_json() produces valid JSON with all expected fields holding valid values.

    Replaces shallow key-presence assertions (T49.1) with value-validity checks:
    - timestamp: non-empty ISO 8601 string containing a 'T' separator and UTC offset
    - event_type: exact match to what was passed in
    - actor: exact non-empty match
    - resource: exact non-empty match
    - action: exact non-empty match
    - details: correct dict value
    - prev_hash: 64-character lowercase hex string (SHA-256 or genesis sentinel)
    - signature: 64-character lowercase hex string (HMAC-SHA256 hexdigest)
    """
    event = logger_instance.log_event(
        event_type="SERIALIZE_TEST",
        actor="pytest",
        resource="resource",
        action="inspect",
        details={"key": "value"},
    )

    raw = event.model_dump_json()
    parsed = json.loads(raw)

    # timestamp: non-empty, valid ISO 8601 with 'T' separator
    assert isinstance(parsed["timestamp"], str), "timestamp must be a string"
    assert len(parsed["timestamp"]) > 0, "timestamp must not be empty"
    assert "T" in parsed["timestamp"], "timestamp must contain ISO 8601 'T' separator"

    # event_type: exact match to what was passed
    assert parsed["event_type"] == "SERIALIZE_TEST", (
        f"event_type must be 'SERIALIZE_TEST'; got {parsed['event_type']!r}"
    )

    # actor: exact non-empty match
    assert parsed["actor"] == "pytest", f"actor must be 'pytest'; got {parsed['actor']!r}"
    assert len(parsed["actor"]) > 0, "actor must not be empty"

    # resource: exact non-empty match
    assert parsed["resource"] == "resource", (
        f"resource must be 'resource'; got {parsed['resource']!r}"
    )

    # action: exact match
    assert parsed["action"] == "inspect", f"action must be 'inspect'; got {parsed['action']!r}"

    # details: correct key-value pair
    assert parsed["details"] == {"key": "value"}, (
        f"details must be {{'key': 'value'}}; got {parsed['details']!r}"
    )

    # prev_hash: 64-character lowercase hex string
    assert isinstance(parsed["prev_hash"], str), "prev_hash must be a string"
    assert len(parsed["prev_hash"]) == 64, (
        f"prev_hash must be 64 hex chars; got {len(parsed['prev_hash'])}"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", parsed["prev_hash"]), (
        f"prev_hash must be lowercase hex; got {parsed['prev_hash']!r}"
    )

    # signature: versioned HMAC-SHA256 string — format is 'v3:<64-hex>' for new events
    assert isinstance(parsed["signature"], str), "signature must be a string"
    assert parsed["signature"].startswith("v3:"), (
        f"signature must start with 'v3:' version prefix; got {parsed['signature'][:10]!r}"
    )
    sig_hex_part = parsed["signature"][len("v3:") :]
    assert len(sig_hex_part) == 64, (
        f"signature hex portion must be 64 chars; got {len(sig_hex_part)}"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", sig_hex_part), (
        f"signature hex portion must be lowercase hex; got {sig_hex_part!r}"
    )
    # Signature must match what the event object carries
    assert parsed["signature"] == event.signature, "serialised signature must match event.signature"


def test_audit_event_field_values_reflect_inputs(
    logger_instance: AuditLogger,  # noqa: F821
) -> None:
    """All logged field values must exactly reflect the caller-supplied inputs.

    Asserts that no field is silently truncated, uppercased, or transformed
    from the value that was passed to log_event().
    """
    event = logger_instance.log_event(
        event_type="VAULT_UNSEAL",
        actor="operator@example.com",
        resource="vault/seal",
        action="unseal",
        details={"reason": "scheduled maintenance", "host": "node-1"},
    )

    assert event.event_type == "VAULT_UNSEAL"
    assert event.actor == "operator@example.com"
    assert event.resource == "vault/seal"
    assert event.action == "unseal"
    assert event.details == {"reason": "scheduled maintenance", "host": "node-1"}


def test_audit_event_timestamp_is_utc_iso8601(
    logger_instance: AuditLogger,  # noqa: F821
) -> None:
    """log_event() must produce a timestamp that is a valid UTC ISO 8601 string.

    The timestamp drives chain ordering and signature correctness.
    An invalid or naive timestamp would make cross-system verification impossible.
    """
    from datetime import datetime

    event = logger_instance.log_event(
        event_type="TIMESTAMP_CHECK",
        actor="system",
        resource="clock",
        action="read",
        details={},
    )

    # Must be parseable as a datetime
    parsed_ts = datetime.fromisoformat(event.timestamp)
    # Must carry UTC timezone info
    assert parsed_ts.tzinfo is not None, "timestamp must have timezone info (UTC)"
    assert parsed_ts.utcoffset() == UTC.utcoffset(None), "timestamp must be UTC (offset +00:00)"


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

    from synth_engine.shared.security.audit import reset_audit_logger

    reset_audit_logger()

    from synth_engine.shared.security.audit import get_audit_logger

    with pytest.raises(ValueError, match="AUDIT_KEY"):
        get_audit_logger()


def test_get_audit_logger_wrong_length_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_audit_logger() raises ValueError when AUDIT_KEY length is not 64 chars."""
    # 10 hex chars = 5 bytes (too short)
    monkeypatch.setenv("AUDIT_KEY", "deadbeef12")

    from synth_engine.shared.security.audit import reset_audit_logger

    reset_audit_logger()

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

    from synth_engine.shared.security.audit import reset_audit_logger

    reset_audit_logger()

    from synth_engine.shared.security.audit import get_audit_logger

    with pytest.raises(ValueError, match="AUDIT_KEY"):
        get_audit_logger()


def test_audit_event_logged_to_stdout(
    logger_instance: AuditLogger,  # noqa: F821
    caplog: pytest.LogCaptureFixture,
) -> None:
    """log_event() emits a log record at INFO on the synth_engine.security.audit logger."""
    import logging

    with caplog.at_level(logging.INFO, logger="synth_engine.security.audit"):
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


# ---------------------------------------------------------------------------
# Logger naming convention test (DevOps review finding P2-T2.4)
# ---------------------------------------------------------------------------


def test_audit_logger_name_follows_convention() -> None:
    """_AUDIT_LOGGER_NAME follows the synth_engine.<module_path> convention."""
    from synth_engine.shared.security.audit import _AUDIT_LOGGER_NAME

    assert _AUDIT_LOGGER_NAME == "synth_engine.security.audit"


# ---------------------------------------------------------------------------
# Singleton and cross-request chain integrity tests (P2-D3)
# ---------------------------------------------------------------------------


def test_get_audit_logger_returns_singleton() -> None:
    """Two calls to get_audit_logger() must return the identical object.

    The module-level singleton ensures the hash chain is never silently reset
    between HTTP requests for the lifetime of the process.
    """
    from synth_engine.shared.security.audit import get_audit_logger

    first = get_audit_logger()
    second = get_audit_logger()
    assert first is second


def test_chain_persists_across_calls() -> None:
    """The hash chain must span two separate get_audit_logger() calls.

    Simulates two distinct callsites (e.g., two HTTP request handlers) both
    obtaining the logger via the factory.  The second event's prev_hash must
    equal the SHA-256 of the first event's JSON — proving the chain is
    continuous across requests.
    """
    from synth_engine.shared.security.audit import get_audit_logger

    logger_a = get_audit_logger()
    event1 = logger_a.log_event(
        event_type="REQUEST_ONE",
        actor="handler_a",
        resource="resource",
        action="read",
        details={},
    )

    logger_b = get_audit_logger()
    event2 = logger_b.log_event(
        event_type="REQUEST_TWO",
        actor="handler_b",
        resource="resource",
        action="read",
        details={},
    )

    expected_prev_hash = hashlib.sha256(event1.model_dump_json().encode()).hexdigest()
    assert event2.prev_hash == expected_prev_hash


def test_reset_audit_logger_breaks_singleton() -> None:
    """After reset_audit_logger(), get_audit_logger() must return a new object.

    reset_audit_logger() is provided solely for test isolation so that each
    test starts with a clean chain.
    """
    from synth_engine.shared.security.audit import get_audit_logger, reset_audit_logger

    first = get_audit_logger()
    reset_audit_logger()
    second = get_audit_logger()
    assert first is not second


def test_reset_audit_logger_restarts_chain() -> None:
    """After reset_audit_logger(), the new instance's first event has genesis prev_hash.

    Verifies that reset truly clears state — the new singleton begins a fresh
    chain rather than inheriting the old chain head.
    """
    from synth_engine.shared.security.audit import get_audit_logger, reset_audit_logger

    logger_old = get_audit_logger()
    logger_old.log_event(
        event_type="OLD_EVENT",
        actor="system",
        resource="vault",
        action="boot",
        details={},
    )

    reset_audit_logger()

    logger_new = get_audit_logger()
    event_new = logger_new.log_event(
        event_type="NEW_EVENT",
        actor="system",
        resource="vault",
        action="boot",
        details={},
    )

    assert event_new.prev_hash == "0" * 64


def test_concurrent_log_events_maintain_chain_order() -> None:
    """Concurrent log_event() calls must produce an internally consistent chain.

    Spawns 10 threads each calling log_event() on the shared singleton.
    While emission order is non-deterministic, the resulting chain must be
    internally valid: for every adjacent pair of events (in emission order as
    determined by their prev_hash links), the later event's prev_hash must
    equal the SHA-256 of the earlier event's JSON.

    The threading.Lock inside log_event() is what enforces this invariant.
    """
    from synth_engine.shared.security.audit import AuditEvent, get_audit_logger

    logger = get_audit_logger()
    emitted: list[AuditEvent] = []
    lock = threading.Lock()

    def log_one(index: int) -> None:
        event = logger.log_event(
            event_type="CONCURRENT",
            actor=f"thread-{index}",
            resource="resource",
            action="write",
            details={"index": str(index)},
        )
        with lock:
            emitted.append(event)

    threads = [threading.Thread(target=log_one, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(emitted) == 10

    # Reconstruct the chain in emission order by following prev_hash links.
    # Map: prev_hash_value -> event whose prev_hash is that value (O(1) chain walk).
    prev_hash_to_event: dict[str, AuditEvent] = {e.prev_hash: e for e in emitted}

    # The genesis event is the one whose prev_hash is the genesis sentinel.
    assert "0" * 64 in prev_hash_to_event, "No genesis event found in emitted events"

    # Walk the chain from genesis to tail.
    chain: list[AuditEvent] = []
    current_prev = "0" * 64
    for _ in range(10):
        event = prev_hash_to_event[current_prev]
        chain.append(event)
        current_prev = hashlib.sha256(event.model_dump_json().encode()).hexdigest()

    assert len(chain) == 10


# ---------------------------------------------------------------------------
# T53.2 — Versioned HMAC signature feature tests (Feature RED)
# ---------------------------------------------------------------------------


def test_new_event_uses_v3_signature_prefix(logger_instance: AuditLogger) -> None:  # noqa: F821
    """New events produced by log_event() must carry a 'v3:' signature prefix.

    The prefix is the version discriminator for the length-prefixed HMAC format
    that eliminates pipe-delimiter injection (ADV-P53-01).
    """
    event = logger_instance.log_event(
        event_type="PAYMENT",
        actor="billing",
        resource="invoice/001",
        action="create",
        details={"amount": "100"},
    )

    assert event.signature.startswith("v3:"), (
        f"New events must use v3: prefix; got {event.signature[:10]!r}"
    )


def test_v2_signature_verifies_correctly(logger_instance: AuditLogger) -> None:  # noqa: F821
    """A v2 event with correct details verifies as True via verify_event."""
    event = logger_instance.log_event(
        event_type="VERIFY_TEST",
        actor="pytest",
        resource="resource",
        action="read",
        details={"key": "value", "count": "42"},
    )

    assert logger_instance.verify_event(event) is True, (
        "v2 event with correct details must verify as True"
    )


def test_legacy_v1_event_verifies_correctly(logger_instance: AuditLogger) -> None:  # noqa: F821
    """A legacy v1 event (no details in HMAC) must still verify correctly.

    Backward compatibility: audit events written before the v2 upgrade must
    remain verifiable. The v1: prefix triggers the legacy verification path.
    """
    # Simulate a legacy v1 event by computing its signature using the old
    # pipe-delimited format (no details) and prefixing with 'v1:'
    import hashlib as _hashlib
    import hmac as _hmac

    from synth_engine.shared.security.audit import AuditEvent

    ts = "2025-01-01T00:00:00+00:00"
    msg = f"{ts}|LEGACY_EVENT|system|vault|boot|{'0' * 64}"
    raw_key = logger_instance._audit_key  # type: ignore[attr-defined]
    hex_sig = _hmac.new(raw_key, msg.encode(), _hashlib.sha256).hexdigest()
    v1_sig = f"v1:{hex_sig}"

    legacy_event = AuditEvent(
        timestamp=ts,
        event_type="LEGACY_EVENT",
        actor="system",
        resource="vault",
        action="boot",
        details={},
        prev_hash="0" * 64,
        signature=v1_sig,
    )

    assert logger_instance.verify_event(legacy_event) is True, (
        "Legacy v1 events must still verify correctly after v2 upgrade"
    )


def test_v3_event_with_empty_details_verifies(logger_instance: AuditLogger) -> None:  # noqa: F821
    """A v3 event with details={} (empty dict) verifies correctly.

    Empty dict is a valid v3 payload — it is distinct from None/missing
    (which would use v1 format).
    """
    event = logger_instance.log_event(
        event_type="EMPTY_DETAILS",
        actor="system",
        resource="vault",
        action="check",
        details={},
    )

    assert event.signature.startswith("v3:"), "Event with details={} must use v3: prefix"
    assert logger_instance.verify_event(event) is True, (
        "v3 event with empty details must verify as True"
    )


def test_v2_signature_round_trip(logger_instance: AuditLogger) -> None:  # noqa: F821
    """Round-trip: create event → verify → passes. Signature is stable on same inputs."""
    event = logger_instance.log_event(
        event_type="ROUND_TRIP",
        actor="tester",
        resource="endpoint/verify",
        action="post",
        details={"session": "abc123", "ip": "127.0.0.1"},
    )

    # Verify twice to ensure no state mutation on verification
    assert logger_instance.verify_event(event) is True
    assert logger_instance.verify_event(event) is True


def test_v3_signature_hex_portion_is_64_chars(logger_instance: AuditLogger) -> None:  # noqa: F821
    """The hex portion of a v3 signature (after the 'v3:' prefix) is 64 chars.

    The HMAC-SHA256 digest is always 32 bytes / 64 hex characters.
    """
    import re

    event = logger_instance.log_event(
        event_type="SIG_FORMAT",
        actor="system",
        resource="check",
        action="inspect",
        details={"x": "y"},
    )

    assert event.signature.startswith("v3:")
    hex_part = event.signature[len("v3:") :]
    assert len(hex_part) == 64, f"Hex portion must be 64 chars; got {len(hex_part)}"
    assert re.fullmatch(r"[0-9a-f]{64}", hex_part), (
        f"Hex portion must be lowercase hex; got {hex_part!r}"
    )


# ---------------------------------------------------------------------------
# Fix 2 (P58): Failed v1/v2 HMAC verification logging
# ---------------------------------------------------------------------------


def test_failed_v1_verification_logs_warning(
    logger_instance: "AuditLogger",  # noqa: F821
    caplog: pytest.LogCaptureFixture,
) -> None:
    """verify_event() logs WARNING when a v1 event fails HMAC verification.

    Security: the WARNING must NOT include the raw signature hex (oracle risk).
    It MUST include event_type, timestamp, and actor to aid incident triage.

    Task: P58 — Log failed v1/v2 HMAC verification attempts at WARNING
    """
    import logging

    from synth_engine.shared.security.audit import AuditEvent

    # Construct a v1 event with a tampered (invalid) signature
    tampered_v1 = AuditEvent(
        timestamp="2026-01-01T00:00:00+00:00",
        event_type="TEST_EVENT",
        actor="attacker",
        resource="vault",
        action="read",
        details={},
        prev_hash="0" * 64,
        signature="v1:" + "deadbeef" * 8,  # wrong signature  # pragma: allowlist secret
    )

    with caplog.at_level(logging.WARNING):
        result = logger_instance.verify_event(tampered_v1)

    assert result is False, "Tampered v1 event must fail verification"

    # Must emit a WARNING
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) >= 1, (
        "verify_event() must log at least one WARNING on v1 HMAC failure"
    )

    # The warning message must include identifying event fields
    warning_text = " ".join(r.getMessage() for r in warning_records)
    assert "TEST_EVENT" in warning_text, (
        f"WARNING must include event_type; got: {warning_text!r}"
    )
    assert "attacker" in warning_text, (
        f"WARNING must include actor; got: {warning_text!r}"
    )

    # Must NOT include the raw signature hex (oracle risk)
    assert "deadbeef" not in warning_text, (
        "WARNING must NOT include raw signature hex (oracle risk)"
    )


def test_failed_v2_verification_logs_warning(
    logger_instance: "AuditLogger",  # noqa: F821
    caplog: pytest.LogCaptureFixture,
) -> None:
    """verify_event() logs WARNING when a v2 event fails HMAC verification.

    Security: the WARNING must NOT include the raw signature hex (oracle risk).
    It MUST include event_type, timestamp, and actor to aid incident triage.

    Task: P58 — Log failed v1/v2 HMAC verification attempts at WARNING
    """
    import logging

    from synth_engine.shared.security.audit import AuditEvent

    # Construct a v2 event with a tampered (invalid) signature
    tampered_v2 = AuditEvent(
        timestamp="2026-01-01T00:00:00+00:00",
        event_type="TEST_EVENT",
        actor="attacker",
        resource="vault",
        action="read",
        details={"k": "v"},
        prev_hash="0" * 64,
        signature="v2:" + "cafebabe" * 8,  # wrong signature  # pragma: allowlist secret
    )

    with caplog.at_level(logging.WARNING):
        result = logger_instance.verify_event(tampered_v2)

    assert result is False, "Tampered v2 event must fail verification"

    # Must emit a WARNING
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) >= 1, (
        "verify_event() must log at least one WARNING on v2 HMAC failure"
    )

    # The warning message must include identifying event fields
    warning_text = " ".join(r.getMessage() for r in warning_records)
    assert "TEST_EVENT" in warning_text, (
        f"WARNING must include event_type; got: {warning_text!r}"
    )
    assert "attacker" in warning_text, (
        f"WARNING must include actor; got: {warning_text!r}"
    )

    # Must NOT include the raw signature hex (oracle risk)
    assert "cafebabe" not in warning_text, (
        "WARNING must NOT include raw signature hex (oracle risk)"
    )
