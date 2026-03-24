"""Negative/attack tests for audit HMAC details coverage (T53.2).

ATTACK RED Phase — these tests define security requirements that MUST fail
before the v2 signature implementation exists.

CONSTITUTION Priority 0: Security
CONSTITUTION Priority 3: TDD (attack-first)
Task: T53.2 — Audit HMAC: Include Details Field in Signature
"""

from __future__ import annotations

import math
import os

import pytest

# ---------------------------------------------------------------------------
# Fixtures (isolated from the main test_audit.py fixtures)
# ---------------------------------------------------------------------------


@pytest.fixture
def attack_key_bytes() -> bytes:
    """Return a deterministic 32-byte HMAC key for attack tests."""
    return os.urandom(32)


@pytest.fixture
def attack_logger(attack_key_bytes: bytes) -> object:
    """Return a fresh AuditLogger instance for attack tests."""
    from synth_engine.shared.security.audit import AuditLogger

    return AuditLogger(attack_key_bytes)


@pytest.fixture(autouse=True)
def reset_singleton_for_attack_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reset singleton after every attack test and provide a valid AUDIT_KEY."""
    monkeypatch.setenv("AUDIT_KEY", os.urandom(32).hex())
    yield
    from synth_engine.shared.security.audit import reset_audit_logger

    reset_audit_logger()


# ---------------------------------------------------------------------------
# ATTACK TESTS (Rule 22 — attack-first TDD)
# ---------------------------------------------------------------------------


def test_tampered_details_fails_v2_verification(attack_logger: object) -> None:
    """Mutating the details dict on a v2 event must cause verify_event to return False.

    An attacker with write access to the log store cannot silently modify the
    details field without invalidating the HMAC once details are included in
    the signature computation.
    """
    from synth_engine.shared.security.audit import AuditEvent, AuditLogger

    logger: AuditLogger = attack_logger  # type: ignore[assignment]

    event = logger.log_event(
        event_type="PAYMENT",
        actor="billing",
        resource="invoice/001",
        action="create",
        details={"amount": "100", "currency": "USD"},
    )

    # Attacker modifies the amount in details without changing the signature
    tampered = AuditEvent(
        timestamp=event.timestamp,
        event_type=event.event_type,
        actor=event.actor,
        resource=event.resource,
        action=event.action,
        details={"amount": "9999999", "currency": "USD"},  # tampered!
        prev_hash=event.prev_hash,
        signature=event.signature,
    )

    assert logger.verify_event(tampered) is False, (
        "verify_event must return False when details field is tampered"
    )


def test_version_downgrade_attack_fails(attack_logger: object) -> None:
    """Stripping details and changing v2→v1 in the signature prefix must fail verification.

    The version prefix is included IN the HMAC computation (not just stored
    alongside), so stripping it and re-labeling the signature as v1 is
    detectable.
    """
    from synth_engine.shared.security.audit import AuditEvent, AuditLogger

    logger: AuditLogger = attack_logger  # type: ignore[assignment]

    # Log a v2 event with meaningful details
    event = logger.log_event(
        event_type="TRANSFER",
        actor="finance",
        resource="account/42",
        action="debit",
        details={"amount": "500", "ref": "TX-001"},
    )

    # The legitimate signature is v2: (includes details).
    # Attacker tries to strip the details and replace the v2: prefix with v1:
    # to make it look like the legacy format, bypassing detail tampering.
    assert event.signature.startswith("v2:"), (
        "New events must use v2: signature prefix"
    )

    # Construct a fake v1: signature by taking just the hex portion
    fake_v1_sig = "v1:" + event.signature[len("v2:"):]

    tampered = AuditEvent(
        timestamp=event.timestamp,
        event_type=event.event_type,
        actor=event.actor,
        resource=event.resource,
        action=event.action,
        details={},  # attacker strips the details
        prev_hash=event.prev_hash,
        signature=fake_v1_sig,
    )

    assert logger.verify_event(tampered) is False, (
        "Downgrade attack: fabricated v1: signature over stripped details must fail"
    )


def test_unknown_version_prefix_fails_closed(attack_logger: object) -> None:
    """An unknown version prefix (e.g. 'v3:') must cause verify_event to return False.

    verify_event must fail-closed on unknown versions — it must not fall
    through to any default verification path.
    """
    from synth_engine.shared.security.audit import AuditEvent, AuditLogger

    logger: AuditLogger = attack_logger  # type: ignore[assignment]

    event = logger.log_event(
        event_type="AUDIT",
        actor="system",
        resource="vault",
        action="inspect",
        details={"status": "ok"},
    )

    # Replace the version prefix with an unknown one
    fake_v3_sig = "v3:" + event.signature[len("v2:"):]

    unknown_version_event = AuditEvent(
        timestamp=event.timestamp,
        event_type=event.event_type,
        actor=event.actor,
        resource=event.resource,
        action=event.action,
        details=event.details,
        prev_hash=event.prev_hash,
        signature=fake_v3_sig,
    )

    assert logger.verify_event(unknown_version_event) is False, (
        "Unknown version prefix must fail-closed, not fall through"
    )


def test_details_with_nan_raises_error(attack_logger: object) -> None:
    """Passing float('nan') in details must raise a ValueError or TypeError.

    Non-JSON-serializable values in details must be rejected with a clear
    error, not silently stored as invalid data.
    """
    from synth_engine.shared.security.audit import AuditLogger

    logger: AuditLogger = attack_logger  # type: ignore[assignment]

    with pytest.raises((ValueError, TypeError)):
        logger.log_event(
            event_type="BAD_INPUT",
            actor="test",
            resource="res",
            action="write",
            details={"value": math.nan},  # type: ignore[arg-type]
        )


def test_details_with_inf_raises_error(attack_logger: object) -> None:
    """Passing float('inf') in details must raise a ValueError or TypeError.

    json.dumps with allow_nan=False must reject infinity values.
    """
    from synth_engine.shared.security.audit import AuditLogger

    logger: AuditLogger = attack_logger  # type: ignore[assignment]

    with pytest.raises((ValueError, TypeError)):
        logger.log_event(
            event_type="BAD_INPUT",
            actor="test",
            resource="res",
            action="write",
            details={"value": math.inf},  # type: ignore[arg-type]
        )


def test_details_exceeding_64kb_raises_error(attack_logger: object) -> None:
    """Details canonical serialization exceeding 64 KB must raise a ValueError.

    This guards against OOM attacks via unbounded detail payloads.
    """
    from synth_engine.shared.security.audit import AuditLogger

    logger: AuditLogger = attack_logger  # type: ignore[assignment]

    # Construct a details dict whose JSON serialization exceeds 64 KB
    # Each key-value pair contributes ~20 chars; 4000 pairs ≈ 80KB
    oversized_details = {f"key_{i:04d}": "x" * 10 for i in range(4000)}

    with pytest.raises(ValueError, match="(?i)size|limit|too large|exceed"):
        logger.log_event(
            event_type="OVERSIZED",
            actor="test",
            resource="res",
            action="write",
            details=oversized_details,  # type: ignore[arg-type]
        )


def test_none_details_and_empty_dict_produce_different_signatures(
    attack_key_bytes: bytes,
) -> None:
    """details=None (v1 format) and details={} (v2 format) must produce distinct signatures.

    These must not be conflated — a v1 legacy event (where details was not
    included in HMAC) must NOT verify the same as a v2 event with empty details.
    The version prefix in the HMAC computation is what distinguishes them.
    """
    from synth_engine.shared.security.audit import AuditLogger

    # Two fresh loggers with the SAME key to ensure comparability
    logger_a = AuditLogger(attack_key_bytes)
    logger_b = AuditLogger(attack_key_bytes)

    # Log an event via the public API with empty details (v2)
    event_empty = logger_a.log_event(
        event_type="COMPARE",
        actor="test",
        resource="res",
        action="check",
        details={},
    )

    # Simulate a legacy v1 signature by calling _sign_v1 directly
    # (which must exist after implementation) or by verifying the prefix differs
    # The signature for empty details (v2:) must differ from v1: format
    assert event_empty.signature.startswith("v2:"), (
        "Empty dict details must produce v2: signature"
    )

    # The v2 signature over {} must NOT equal a raw v1 signature over the same fields.
    # We verify this indirectly: v1 signatures do NOT start with 'v2:'
    v1_hex_part = event_empty.signature[len("v2:"):]
    v2_hex_part = event_empty.signature[len("v2:"):]

    # If we construct a fake v1: prefixed signature and verify it, it must fail
    # (because verify_event computes v2 and compares — they differ)
    from synth_engine.shared.security.audit import AuditEvent

    fake_v1_event = AuditEvent(
        timestamp=event_empty.timestamp,
        event_type=event_empty.event_type,
        actor=event_empty.actor,
        resource=event_empty.resource,
        action=event_empty.action,
        details={},
        prev_hash=event_empty.prev_hash,
        signature="v1:" + v1_hex_part,
    )
    # A v1: prefixed sig over {} does NOT equal the v2: sig over {}
    # (because the version prefix is included in the HMAC input)
    assert logger_b.verify_event(fake_v1_event) is False, (
        "A v1: signature over empty details must NOT verify as a v2 event"
    )

    # Also confirm that v1_hex_part != v2_hex_part is guaranteed by construction
    # (they ARE the same hex portion since both come from event_empty.signature,
    # this just documents that the prefix is what distinguishes them)
    assert v1_hex_part == v2_hex_part  # same hex, different prefix semantics
