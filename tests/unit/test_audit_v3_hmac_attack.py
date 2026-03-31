"""Attack tests for v3 HMAC length-prefixed signature format (ADV-P53-01).

ATTACK RED Phase — these tests define security requirements for the v3 format.
They must FAIL before the v3 implementation exists and PASS after.

The pipe-delimiter injection vulnerability (ADV-P53-01): in v1/v2 formats,
field boundaries are marked by literal '|' characters.  An adversary who can
inject a '|' into one field can shift boundaries and construct a second input
that hashes to the same HMAC:

    actor="foo|bar", resource="baz"
    -> bytes: "...foo|bar|baz..."

    actor="foo", resource="bar|baz"
    -> bytes: "...foo|bar|baz..."

Both produce identical HMAC payloads.  The v3 format uses 4-byte big-endian
length prefixes, making every field boundary unambiguous.

CONSTITUTION Priority 0: Security
CONSTITUTION Priority 3: TDD (attack-first, Rule 22)
Task: ADV-P53-01 — HMAC pipe-delimiter injection fix
"""

from __future__ import annotations

import hashlib
import hmac
import os

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def v3_key() -> bytes:
    """Return a deterministic 32-byte HMAC key for v3 attack tests."""
    return os.urandom(32)


@pytest.fixture
def v3_logger(v3_key: bytes) -> object:
    """Return a fresh AuditLogger for v3 attack tests."""
    from synth_engine.shared.security.audit import AuditLogger

    return AuditLogger(v3_key)


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure singleton is reset after every test and AUDIT_KEY is valid."""
    monkeypatch.setenv("AUDIT_KEY", os.urandom(32).hex())
    yield  # type: ignore[misc]
    from synth_engine.shared.security.audit import reset_audit_logger

    reset_audit_logger()


# ---------------------------------------------------------------------------
# ATTACK TESTS — pipe-delimiter collision prevention (ADV-P53-01)
# ---------------------------------------------------------------------------


def test_v3_pipe_in_actor_produces_different_sig_than_pipe_in_resource(
    v3_key: bytes,
) -> None:
    """v3 format must produce distinct signatures when '|' appears in different fields.

    This is the core ADV-P53-01 attack: shift a pipe character from one field
    to an adjacent field boundary.  Under v1/v2 the resulting byte strings
    are identical.  Under v3 length-prefixed encoding they MUST differ because
    each field's byte length is encoded before its content.

    Scenario:
        Event A: actor="foo|bar", resource="baz"
        Event B: actor="foo",     resource="bar|baz"

    In v1/v2 both produce "...foo|bar|baz..." — same bytes, same HMAC.
    In v3 they produce different length-prefixed byte sequences.
    """
    from synth_engine.shared.security.audit import AuditLogger

    logger_a = AuditLogger(v3_key)
    logger_b = AuditLogger(v3_key)

    event_a = logger_a.log_event(
        event_type="ACCESS",
        actor="foo|bar",
        resource="baz",
        action="read",
        details={},
    )

    event_b = logger_b.log_event(
        event_type="ACCESS",
        actor="foo",
        resource="bar|baz",
        action="read",
        details={},
    )

    assert event_a.signature.startswith("v3:"), "New events must use v3: signature prefix"
    assert event_b.signature.startswith("v3:"), "New events must use v3: signature prefix"
    assert event_a.signature != event_b.signature, (
        "ADV-P53-01: pipe-in-actor vs pipe-in-resource must produce DIFFERENT v3 signatures. "
        "If they are equal, length-prefixed encoding is not working correctly."
    )


def test_v3_pipe_in_event_type_vs_actor_boundary(v3_key: bytes) -> None:
    """Shifting a '|' across the event_type/actor boundary must change the v3 signature.

    Event A: event_type="FOO|BAR", actor="baz"
    Event B: event_type="FOO",     actor="BAR|baz"

    v1/v2: identical byte payload.  v3: different length prefixes -> different HMAC.
    """
    from synth_engine.shared.security.audit import AuditLogger

    logger_a = AuditLogger(v3_key)
    logger_b = AuditLogger(v3_key)

    event_a = logger_a.log_event(
        event_type="FOO|BAR",
        actor="baz",
        resource="res",
        action="act",
        details={},
    )

    event_b = logger_b.log_event(
        event_type="FOO",
        actor="BAR|baz",
        resource="res",
        action="act",
        details={},
    )

    assert event_a.signature != event_b.signature, (
        "ADV-P53-01: pipe shifted across event_type/actor boundary must produce "
        "different v3 signatures."
    )


def test_v3_version_literal_included_in_hmac(v3_key: bytes) -> None:
    """The 'v3' version literal must be included IN the HMAC bytes, not just the prefix.

    An attacker stripping the version literal and re-labeling as an older
    version must produce a DIFFERENT HMAC.  This is verified by manually
    computing what the HMAC would be WITHOUT the 'v3' prefix bytes and
    confirming it does NOT match the stored hex.
    """
    from synth_engine.shared.security.audit import AuditLogger

    logger = AuditLogger(v3_key)
    event = logger.log_event(
        event_type="VAULT_UNSEAL",
        actor="admin",
        resource="vault/master",
        action="unseal",
        details={"reason": "scheduled"},
    )

    assert event.signature.startswith("v3:"), "New events must use v3: prefix"
    stored_hex = event.signature[len("v3:") :]

    # Recompute what the HMAC would be if we OMITTED the b"v3" version bytes
    # (simulating an attacker who strips the version from the payload).
    import json

    details_json = json.dumps(
        {"reason": "scheduled"}, sort_keys=True, separators=(",", ":"), allow_nan=False
    )

    def _encode_field(s: str) -> bytes:
        encoded = s.encode("utf-8")
        return len(encoded).to_bytes(4, "big") + encoded

    # Build message WITHOUT the leading b"v3" bytes
    no_version_message = b"".join(
        _encode_field(f)
        for f in [
            event.timestamp,
            event.event_type,
            event.actor,
            event.resource,
            event.action,
            event.prev_hash,
            details_json,
        ]
    )

    no_version_hex = hmac.new(v3_key, no_version_message, hashlib.sha256).hexdigest()

    assert no_version_hex != stored_hex, (
        "v3 HMAC must include the 'v3' version literal in the message. "
        "If the version-stripped HMAC matches the stored HMAC, the version "
        "literal is NOT being included, leaving the system vulnerable to "
        "version-stripping downgrade attacks."
    )


def test_v3_verify_event_accepts_valid_v3_signature(v3_logger: object) -> None:
    """verify_event must accept a legitimately signed v3 event.

    This is the affirmative counterpart to the attack tests: the v3 verifier
    must not be so strict that it rejects its own output.
    """
    from synth_engine.shared.security.audit import AuditLogger

    logger: AuditLogger = v3_logger  # type: ignore[assignment]

    event = logger.log_event(
        event_type="DATA_ACCESS",
        actor="pipeline",
        resource="schema/orders",
        action="read",
        details={"rows": "1000"},
    )

    assert event.signature.startswith("v3:")
    assert logger.verify_event(event) is True, (
        "verify_event must return True for a valid v3-signed event"
    )


def test_v3_tampered_actor_fails_verification(v3_logger: object) -> None:
    """Mutating the actor field on a v3 event must invalidate the signature."""
    from synth_engine.shared.security.audit import AuditEvent, AuditLogger

    logger: AuditLogger = v3_logger  # type: ignore[assignment]

    event = logger.log_event(
        event_type="LOGIN",
        actor="alice",
        resource="dashboard",
        action="view",
        details={"ip": "10.0.0.1"},
    )

    tampered = AuditEvent(
        timestamp=event.timestamp,
        event_type=event.event_type,
        actor="mallory",  # tampered
        resource=event.resource,
        action=event.action,
        details=event.details,
        prev_hash=event.prev_hash,
        signature=event.signature,
    )

    assert logger.verify_event(tampered) is False, (
        "verify_event must return False when actor is tampered on a v3 event"
    )
    # Specific: the tampered event has actor="mallory"
    assert tampered.actor == "mallory"


def test_v3_tampered_details_fails_verification(v3_logger: object) -> None:
    """Mutating the details dict on a v3 event must invalidate the signature."""
    from synth_engine.shared.security.audit import AuditEvent, AuditLogger

    logger: AuditLogger = v3_logger  # type: ignore[assignment]

    event = logger.log_event(
        event_type="PAYMENT",
        actor="billing",
        resource="invoice/001",
        action="create",
        details={"amount": "100", "currency": "USD"},
    )

    tampered = AuditEvent(
        timestamp=event.timestamp,
        event_type=event.event_type,
        actor=event.actor,
        resource=event.resource,
        action=event.action,
        details={"amount": "9999999", "currency": "USD"},  # tampered
        prev_hash=event.prev_hash,
        signature=event.signature,
    )

    assert logger.verify_event(tampered) is False, (
        "verify_event must return False when details is tampered on a v3 event"
    )
    # Specific: tampered details contains the forged amount
    assert tampered.details.get("amount") == "9999999"


def test_v3_downgrade_to_v2_attack_fails(v3_key: bytes) -> None:
    """Replacing the v3: prefix with v2: on a v3 signature must fail verification.

    An attacker cannot relabel a v3 signature as v2 to bypass the collision
    resistance property, because the version literal 'v3' (as bytes) is
    embedded in the HMAC input, not just in the stored prefix.
    """
    from synth_engine.shared.security.audit import AuditEvent, AuditLogger

    logger = AuditLogger(v3_key)
    event = logger.log_event(
        event_type="TRANSFER",
        actor="finance",
        resource="account/99",
        action="debit",
        details={"amount": "1000"},
    )

    assert event.signature.startswith("v3:")
    # Replace v3: prefix with v2:, keep the same hex digest
    fake_v2_sig = "v2:" + event.signature[len("v3:") :]

    downgraded = AuditEvent(
        timestamp=event.timestamp,
        event_type=event.event_type,
        actor=event.actor,
        resource=event.resource,
        action=event.action,
        details=event.details,
        prev_hash=event.prev_hash,
        signature=fake_v2_sig,
    )

    assert logger.verify_event(downgraded) is False, (
        "v3->v2 downgrade attack: relabeled signature must not pass v2 verification"
    )


def test_v1_events_still_verify_with_v3_logger(v3_key: bytes) -> None:
    """v1-signed events must still verify correctly when using a v3-capable logger.

    Backward compatibility: existing log entries signed with v1 must remain
    verifiable.  The v3 upgrade must not break the v1 verification path.
    """
    from synth_engine.shared.security.audit import AuditEvent, AuditLogger
    from synth_engine.shared.security.audit_signatures import sign_v1

    logger = AuditLogger(v3_key)

    # Construct a genuine v1 signature using the standalone sign_v1 function.
    ts = "2024-01-01T00:00:00+00:00"
    sig_v1 = sign_v1(
        v3_key,
        ts,
        "LEGACY",
        "old_system",
        "db/legacy",
        "read",
        "0" * 64,
    )

    v1_event = AuditEvent(
        timestamp=ts,
        event_type="LEGACY",
        actor="old_system",
        resource="db/legacy",
        action="read",
        details={},
        prev_hash="0" * 64,
        signature=sig_v1,
    )

    assert logger.verify_event(v1_event) is True, (
        "v1-signed events must still verify correctly after v3 upgrade (backward compat)"
    )
    # Specific: v1 signature starts with "v1:"
    assert v1_event.signature.startswith("v1:")


def test_v2_events_still_verify_with_v3_logger(v3_key: bytes) -> None:
    """v2-signed events must still verify correctly when using a v3-capable logger.

    Backward compatibility: existing log entries signed with v2 must remain
    verifiable.  The v3 upgrade must not break the v2 verification path.
    """
    from synth_engine.shared.security.audit import AuditEvent, AuditLogger
    from synth_engine.shared.security.audit_signatures import sign_v2

    logger = AuditLogger(v3_key)

    ts = "2024-06-15T12:00:00+00:00"
    sig_v2 = sign_v2(
        v3_key,
        ts,
        "DATA_EXPORT",
        "etl_job",
        "warehouse/table",
        "export",
        "a" * 64,
        {"rows": "5000"},
    )

    v2_event = AuditEvent(
        timestamp=ts,
        event_type="DATA_EXPORT",
        actor="etl_job",
        resource="warehouse/table",
        action="export",
        details={"rows": "5000"},
        prev_hash="a" * 64,
        signature=sig_v2,
    )

    assert logger.verify_event(v2_event) is True, (
        "v2-signed events must still verify correctly after v3 upgrade (backward compat)"
    )
    # Specific: v2 signature starts with "v2:"
    assert v2_event.signature.startswith("v2:")


def test_new_events_use_v3_prefix(v3_logger: object) -> None:
    """log_event must produce v3: signatures after the upgrade.

    This documents the contract: once v3 is implemented, all NEW events
    must carry the v3: prefix.
    """
    from synth_engine.shared.security.audit import AuditLogger

    logger: AuditLogger = v3_logger  # type: ignore[assignment]

    event = logger.log_event(
        event_type="SCHEMA_SCAN",
        actor="ingestion",
        resource="db/public",
        action="scan",
        details={"table_count": "42"},
    )

    assert event.signature.startswith("v3:"), (
        "All new events must use the v3: signature prefix after the v3 upgrade"
    )
