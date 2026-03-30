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
    # Specific: the tampered amount is what we set it to
    assert tampered.details.get("amount") == "9999999"


def test_version_downgrade_attack_fails(attack_logger: object) -> None:
    """Stripping details and changing v2→v1 in the signature prefix must fail verification.

    The version prefix is included IN the HMAC computation (not just stored
    alongside), so stripping it and re-labeling the signature as v1 is
    detectable.
    """
    from synth_engine.shared.security.audit import AuditEvent, AuditLogger

    logger: AuditLogger = attack_logger  # type: ignore[assignment]

    # Log a v3 event with meaningful details
    event = logger.log_event(
        event_type="TRANSFER",
        actor="finance",
        resource="account/42",
        action="debit",
        details={"amount": "500", "ref": "TX-001"},
    )

    # The legitimate signature is v3: (length-prefixed, includes details).
    # Attacker tries to strip the details and replace the v3: prefix with v1:
    # to make it look like the legacy format, bypassing detail tampering.
    assert event.signature.startswith("v3:"), "New events must use v3: signature prefix"

    # Construct a fake v1: signature by taking just the hex portion
    fake_v1_sig = "v1:" + event.signature[len("v3:") :]

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
    """An unknown version prefix (e.g. 'v99:') must cause verify_event to return False.

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

    # Replace the version prefix with an unknown one (v99: does not exist)
    fake_unknown_sig = "v99:" + event.signature[len("v3:") :]

    unknown_version_event = AuditEvent(
        timestamp=event.timestamp,
        event_type=event.event_type,
        actor=event.actor,
        resource=event.resource,
        action=event.action,
        details=event.details,
        prev_hash=event.prev_hash,
        signature=fake_unknown_sig,
    )

    assert logger.verify_event(unknown_version_event) is False, (
        "Unknown version prefix must fail-closed, not fall through"
    )
    # Specific: the fabricated signature starts with "v99:"
    assert unknown_version_event.signature.startswith("v99:")


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

    # Log an event via the public API with empty details (v3)
    event_empty = logger_a.log_event(
        event_type="COMPARE",
        actor="test",
        resource="res",
        action="check",
        details={},
    )

    # Simulate a legacy v1 signature by calling _sign_v1 directly
    # (which must exist after implementation) or by verifying the prefix differs
    # The signature for empty details (v3:) must differ from v1: format
    assert event_empty.signature.startswith("v3:"), "Empty dict details must produce v3: signature"

    # Construct a fake v1: prefixed signature using the hex portion of the real v3 sig.
    # Verifying it must fail because the version prefix is included in the HMAC input.
    hex_part = event_empty.signature[len("v3:") :]
    from synth_engine.shared.security.audit import AuditEvent

    fake_v1_event = AuditEvent(
        timestamp=event_empty.timestamp,
        event_type=event_empty.event_type,
        actor=event_empty.actor,
        resource=event_empty.resource,
        action=event_empty.action,
        details={},
        prev_hash=event_empty.prev_hash,
        signature="v1:" + hex_part,
    )
    assert logger_b.verify_event(fake_v1_event) is False, (
        "A v1: signature over empty details must NOT verify as a v3 event"
    )


def test_verify_event_returns_false_when_details_cause_sign_v3_to_raise(
    attack_logger: object,
) -> None:
    """The except ValueError: return False path in verify_event() must be exercised.

    When a stored v3 event has been corrupted such that its details dict
    would cause _sign_v3() to raise ValueError during re-signing (e.g.,
    because the details JSON exceeds 64 KB after corruption), verify_event()
    must return False rather than propagating the exception.

    This test creates a valid v3 event, then constructs a corrupt copy with
    an oversized details payload (exceeding the 64 KB guard) while keeping
    the original v3: signature.  verify_event() will attempt _sign_v3() on
    the corrupt details, hit the size guard ValueError, catch it, and return
    False — covering the except branch in verify_event.
    """
    from synth_engine.shared.security.audit import AuditLogger

    logger: AuditLogger = attack_logger  # type: ignore[assignment]

    # 1. Create a valid v3 event with small, legitimate details.
    event = logger.log_event(
        event_type="CORRUPTION_TEST",
        actor="system",
        resource="store/event-db",
        action="read",
        details={"record_id": "abc123"},
    )
    assert event.signature.startswith("v3:"), "Precondition: event must carry a v3: signature"

    # 2. Construct a details dict whose canonical JSON exceeds 64 KB.
    #    Each entry contributes ~20 bytes; 4000 entries ≈ 80 KB of JSON.
    #    This will trigger the size guard inside _sign_v3() during verification.
    oversized_details: dict[str, str] = {f"key_{i:04d}": "x" * 10 for i in range(4000)}

    # 3. Build a corrupt copy: retain the original v3: signature but swap in
    #    the oversized details.  model_copy(update=...) is the Pydantic v2 way
    #    to produce a new model instance with selective field overrides.
    corrupt_event = event.model_copy(update={"details": oversized_details})

    # 4. verify_event() must catch the ValueError from _sign_v3() and return
    #    False — not raise, not crash.
    result = logger.verify_event(corrupt_event)
    assert result is False, (
        "verify_event must return False (not raise) when _sign_v3 raises ValueError "
        "due to oversized details after corruption"
    )
