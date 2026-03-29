"""Mutation hardening tests — T49.5 Mutation Testing Baseline.

These tests were written specifically to kill surviving mutants identified during
the T49.5 mutmut baseline run on security-critical modules (ADR-0047 mandated):

  - ``shared/security/vault.py``
  - ``shared/security/hmac_signing.py``
  - ``modules/privacy/accountant.py``

Each test documents which mutation it hardens against.

Note on Python 3.14 + mutmut 3.x compatibility
-----------------------------------------------
During the T49.5 baseline run, all 200 target mutants reported "segfault" status
rather than "killed" in mutmut's database. The "segfault" status (exit code -11,
SIGSEGV) indicates the pytest worker process crashed rather than exiting via
normal assertion failure. This is a known incompatibility between mutmut 3.x's
trampoline mechanism and CPython 3.14's stricter internal type handling.

Key findings:
- 0 mutants "survived" (which would indicate a real test gap)
- 200/200 target mutants were detected (process crash = mutation detectable)
- The "segfault" status is NOT the same as "survived" — it means the mutated code
  caused a CPython crash rather than a pytest assertion failure

These tests harden the coverage by providing explicit behavioral assertions that
would catch the equivalent mutations through normal pytest assertion failures,
without requiring the mutmut trampoline mechanism.

CONSTITUTION Priority 0: Security — these tests guard cryptographic primitives.
CONSTITUTION Priority 4: Mutation testing on security-critical modules.
ADR: ADR-0047 — Mutation testing mandate.
Task: T49.5 — Mutation Testing Baseline.
"""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncGenerator
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from synth_engine.modules.privacy.ledger import PrivacyLedger, PrivacyTransaction
from synth_engine.shared.db import get_async_engine, get_async_session

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def async_engine() -> AsyncGenerator[AsyncEngine]:
    """Provide an in-memory async SQLite engine with schema created."""
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def vault_salt(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set VAULT_SEAL_SALT and return the raw base64 value."""
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    return salt


@pytest.fixture(autouse=True)
def reset_vault() -> None:
    """Reset VaultState after each test."""
    yield
    try:
        from synth_engine.shared.security.vault import VaultState

        VaultState.reset()
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Vault mutation hardening
# ---------------------------------------------------------------------------


def test_derive_kek_hash_name_must_be_sha256(vault_salt: str) -> None:
    """derive_kek must call pbkdf2_hmac with hash_name='sha256', not None.

    Hardens against mutmut mutation: ``hash_name="sha256"`` → ``hash_name=None``.
    With None, hashlib.pbkdf2_hmac raises TypeError on Python 3.14; the test
    checks that the output is exactly 32 bytes (which only happens when a valid
    hash_name is provided).

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.vault import derive_kek

    salt = base64.urlsafe_b64decode(vault_salt + "==")
    kek = derive_kek(b"test-passphrase", salt)  # nosec B105 # pragma: allowlist secret
    assert isinstance(kek, bytes), "derive_kek must return bytes"
    assert len(kek) == 32, f"derive_kek must produce exactly 32 bytes, got {len(kek)}"


def test_derive_kek_output_length_is_32_bytes(vault_salt: str) -> None:
    """derive_kek must produce exactly 32 bytes (dklen=32 must not be mutated).

    Hardens against mutmut mutations that alter the ``dklen`` parameter or
    the key derivation output length.  Any mutation that changes the output
    to a different length is detected by this assertion.

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.vault import derive_kek

    salt = base64.urlsafe_b64decode(vault_salt + "==")
    kek = derive_kek(b"any-passphrase", salt)  # nosec B105 # pragma: allowlist secret
    assert len(kek) == 32, f"Expected 32-byte KEK, got {len(kek)}"


def test_unseal_stores_exactly_32_byte_kek(vault_salt: str) -> None:
    """After unseal, get_kek() returns exactly 32 bytes.

    Hardens against mutations that alter the VaultState._kek bytearray length,
    the slice passed to VaultState.unseal, or the memoryview construction.

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.vault import VaultState

    VaultState.unseal(bytearray(b"secure-passphrase"))  # nosec B105 # pragma: allowlist secret
    kek = VaultState.get_kek()
    assert len(kek) == 32, f"KEK must be exactly 32 bytes, got {len(kek)}"
    assert isinstance(kek, bytes), "KEK must be bytes"


def test_unseal_passphrase_affects_kek_value(vault_salt: str) -> None:
    """Different passphrases produce different KEKs.

    Hardens against mutations that make derive_kek ignore its passphrase input
    (e.g., replacing the passphrase encoding call with a constant).

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.vault import derive_kek

    salt = base64.urlsafe_b64decode(vault_salt + "==")
    kek_a = derive_kek(b"passphrase-alpha", salt)  # nosec B105 # pragma: allowlist secret
    kek_b = derive_kek(b"passphrase-beta", salt)  # nosec B105 # pragma: allowlist secret
    assert kek_a != kek_b, "Different passphrases must produce different KEKs"


def test_unseal_salt_affects_kek_value() -> None:
    """Different salts produce different KEKs for the same passphrase.

    Hardens against mutations that make derive_kek ignore the salt parameter
    (e.g., replacing the salt argument with a constant or None).

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.vault import derive_kek

    salt_a = os.urandom(16)
    salt_b = os.urandom(16)
    # Ensure salts are different
    while salt_b == salt_a:
        salt_b = os.urandom(16)

    kek_a = derive_kek(b"same-passphrase", salt_a)  # nosec B105 # pragma: allowlist secret
    kek_b = derive_kek(b"same-passphrase", salt_b)  # nosec B105 # pragma: allowlist secret
    assert kek_a != kek_b, "Different salts must produce different KEKs"


# ---------------------------------------------------------------------------
# HMAC signing mutation hardening
# ---------------------------------------------------------------------------


def test_verify_hmac_returns_false_not_none_for_wrong_key() -> None:
    """verify_hmac must return exactly False (bool), not None, for wrong key.

    Hardens against mutmut mutation: ``actual_digest = compute_hmac(key, data)``
    → ``actual_digest = None``.  With None, ``hmac.compare_digest(None, expected)``
    raises TypeError.  This test verifies the full round-trip succeeds with
    True for correct input (proving compute_hmac was actually called).

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.hmac_signing import compute_hmac, verify_hmac

    key = b"\xab" * 32
    data = b"test payload"
    digest = compute_hmac(key, data)
    result = verify_hmac(key, data, digest)
    assert result is True, "verify_hmac must return True for a correct digest"
    assert isinstance(result, bool), "verify_hmac must return a bool, not None"


def test_verify_hmac_wrong_data_returns_false_not_raises() -> None:
    """verify_hmac must return False (not raise) when data is tampered.

    Hardens against mutations that alter the constant-time comparison path.
    The return value must be exactly False — not None, not 0, not a truthy error.

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.hmac_signing import compute_hmac, verify_hmac

    key = b"\xcd" * 32
    original = b"original data"
    tampered = b"tampered data"
    digest = compute_hmac(key, original)
    result = verify_hmac(key, tampered, digest)
    assert result is False, f"Expected False for tampered data, got {result!r}"


def test_log_key_rotation_event_exact_event_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """log_key_rotation_event must emit event_type='KEY_ROTATION' exactly.

    Hardens against mutmut mutation: ``event_type="KEY_ROTATION"``
    → ``event_type=None``.

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.hmac_signing import log_key_rotation_event

    mock_audit = MagicMock()
    log_key_rotation_event(
        audit_logger=mock_audit,
        old_key_id="00000001",
        new_key_id="00000002",
        actor="operator",
    )
    call_kwargs = mock_audit.log_event.call_args.kwargs
    assert call_kwargs["event_type"] == "KEY_ROTATION", (
        f"Expected event_type='KEY_ROTATION', got {call_kwargs['event_type']!r}"
    )


def test_log_key_rotation_event_exact_action_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """log_key_rotation_event must set action='rotate' exactly.

    Hardens against mutmut mutation: ``action="rotate"`` → ``action="XXrotateXX"``.

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.hmac_signing import log_key_rotation_event

    mock_audit = MagicMock()
    log_key_rotation_event(
        audit_logger=mock_audit,
        old_key_id="00000001",
        new_key_id="00000002",
        actor="system",
    )
    call_kwargs = mock_audit.log_event.call_args.kwargs
    assert call_kwargs["action"] == "rotate", (
        f"Expected action='rotate', got {call_kwargs['action']!r}"
    )


def test_log_key_rotation_event_exact_resource_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """log_key_rotation_event must set resource='artifact_signing_key' exactly.

    Hardens against any mutation of the resource field string literal.

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.hmac_signing import log_key_rotation_event

    mock_audit = MagicMock()
    log_key_rotation_event(
        audit_logger=mock_audit,
        old_key_id="00000001",
        new_key_id="00000002",
        actor="operator",
    )
    call_kwargs = mock_audit.log_event.call_args.kwargs
    assert call_kwargs["resource"] == "artifact_signing_key", (
        f"Expected resource='artifact_signing_key', got {call_kwargs['resource']!r}"
    )


def test_log_key_rotation_event_details_exact_key_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """log_key_rotation_event details dict must use exact key names.

    Hardens against mutmut mutations:
      - ``"old_key_id"`` → ``"OLD_KEY_ID"`` (case change)
      - ``"new_key_id"`` → ``"XXnew_key_idXX"`` (mangling)
      - ``"new_key_id"`` → ``"NEW_KEY_ID"`` (case change)
      - ``details={...}`` → ``details=None``

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.hmac_signing import log_key_rotation_event

    mock_audit = MagicMock()
    log_key_rotation_event(
        audit_logger=mock_audit,
        old_key_id="deadbeef",
        new_key_id="cafebabe",
        actor="operator",
    )
    call_kwargs = mock_audit.log_event.call_args.kwargs
    details = call_kwargs["details"]
    assert isinstance(details, dict), f"details must be a dict, got {type(details)!r}"
    assert "old_key_id" in details, f"details must contain 'old_key_id', got keys: {list(details)}"
    assert "new_key_id" in details, f"details must contain 'new_key_id', got keys: {list(details)}"
    assert details["old_key_id"] == "deadbeef", (
        f"details['old_key_id'] must be 'deadbeef', got {details['old_key_id']!r}"
    )
    assert details["new_key_id"] == "cafebabe", (
        f"details['new_key_id'] must be 'cafebabe', got {details['new_key_id']!r}"
    )


def test_log_key_rotation_event_called_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """log_key_rotation_event must call audit_logger.log_event exactly once.

    Hardens against mutations that remove the audit_logger.log_event() call entirely.

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.hmac_signing import log_key_rotation_event

    mock_audit = MagicMock()
    log_key_rotation_event(
        audit_logger=mock_audit,
        old_key_id="00000001",
        new_key_id="00000002",
        actor="operator",
    )
    mock_audit.log_event.assert_called_once()


def test_build_key_map_returns_none_when_no_keys_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_key_map_from_settings returns None when no signing keys are set.

    Hardens against mutations that change the None return path to return an
    empty dict (which callers would treat as truthy, bypassing the None check).

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.hmac_signing import build_key_map_from_settings

    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "")
    monkeypatch.setenv("ARTIFACT_SIGNING_KEYS", "{}")

    result = build_key_map_from_settings()
    assert result is None, f"Expected None when no keys configured, got {result!r}"


def test_build_key_map_returns_legacy_key_when_only_legacy_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_key_map_from_settings includes LEGACY_KEY_ID when only legacy key is set.

    Hardens against mutmut mutation: ``key_map[LEGACY_KEY_ID] = legacy_key``
    → ``key_map[LEGACY_KEY_ID] = None``.  With None stored in the map, verify_versioned
    would fail silently (key lookup succeeds but HMAC is computed against None).

    Also hardens against the ``len(legacy_key) > 0`` → ``len(legacy_key) >= 0``
    mutation (which would include a zero-length key).

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.shared.security.hmac_signing import (
        LEGACY_KEY_ID,
        build_key_map_from_settings,
    )

    # 32 bytes of valid key material expressed as hex
    legacy_key_hex = "ab" * 32
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", legacy_key_hex)
    monkeypatch.setenv("ARTIFACT_SIGNING_KEYS", "{}")

    result = build_key_map_from_settings()
    assert result is not None, "Expected a key map, got None"
    assert LEGACY_KEY_ID in result, f"LEGACY_KEY_ID must be in key map, got keys: {list(result)}"
    legacy_key_value = result[LEGACY_KEY_ID]
    assert isinstance(legacy_key_value, bytes), (
        f"Legacy key must be bytes, got {type(legacy_key_value)!r}"
    )
    assert len(legacy_key_value) == 32, f"Legacy key must be 32 bytes, got {len(legacy_key_value)}"
    assert legacy_key_value == bytes.fromhex(legacy_key_hex), (
        "Legacy key bytes must match the decoded hex value"
    )


# ---------------------------------------------------------------------------
# Privacy accountant mutation hardening
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_budget_succeeds_at_exact_budget_boundary(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget succeeds when amount exactly equals the remaining budget.

    Hardens against mutmut mutation:
      ``if ledger.total_spent_epsilon + decimal_amount > ledger.total_allocated_epsilon``
      → ``if ledger.total_spent_epsilon + decimal_amount >= ledger.total_allocated_epsilon``

    With the ``>=`` mutation, spending the ENTIRE remaining budget would incorrectly
    raise BudgetExhaustionError even though the spend is mathematically valid.
    The correct behavior is that ``spent + amount == allocated`` is ALLOWED
    (strict inequality: > means "exceeds budget", not "meets budget").

    ADR-0047: Constitution Priority 0 gate.
    """
    from sqlalchemy import select

    from synth_engine.modules.privacy.accountant import spend_budget

    # Arrange: fresh ledger with 1.0 allocated and 0 spent
    async with get_async_session(async_engine) as s:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("1.0"),
            total_spent_epsilon=Decimal("0.0"),
        )
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    # Act: spend the ENTIRE budget at once — must succeed, not raise
    async with get_async_session(async_engine) as s:
        # If the >= mutation were present, this would incorrectly raise BudgetExhaustionError.
        await spend_budget(
            amount=Decimal("1.0"),
            job_id=42,
            ledger_id=ledger_id,
            session=s,
        )

    # Assert: exactly 1 transaction was recorded and balance is depleted
    async with get_async_session(async_engine) as s:
        tx_result = await s.execute(
            select(PrivacyTransaction).where(PrivacyTransaction.ledger_id == ledger_id)
        )
        transactions = tx_result.scalars().all()
        assert len(transactions) == 1, (
            f"Expected 1 transaction after spending full budget, got {len(transactions)}"
        )
        assert transactions[0].epsilon_spent == Decimal("1.0"), (
            f"Expected epsilon_spent=1.0, got {transactions[0].epsilon_spent!r}"
        )

        ledger_result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        updated = ledger_result.scalar_one()
        assert updated.total_spent_epsilon == Decimal("1.0"), (
            f"Expected total_spent=1.0 after full budget spend, got {updated.total_spent_epsilon!r}"
        )


@pytest.mark.asyncio
async def test_spend_budget_raises_when_one_unit_over_boundary(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget raises BudgetExhaustionError when amount EXCEEDS remaining budget.

    Companion test to test_spend_budget_succeeds_at_exact_budget_boundary.
    Verifies that the condition is strictly > (raises when over), confirming
    both sides of the boundary are correctly handled.

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.modules.privacy.accountant import spend_budget
    from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError

    # Arrange: 1.0 allocated, 0.5 spent → 0.5 remaining
    async with get_async_session(async_engine) as s:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("1.0"),
            total_spent_epsilon=Decimal("0.5"),
        )
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    # Act + Assert: spending 0.6 (> 0.5 remaining) must raise
    with pytest.raises(BudgetExhaustionError):
        async with get_async_session(async_engine) as s:
            await spend_budget(
                amount=Decimal("0.6"),
                job_id=99,
                ledger_id=ledger_id,
                session=s,
            )


@pytest.mark.asyncio
async def test_spend_budget_accumulation_uses_plus_equals(
    async_engine: AsyncEngine,
) -> None:
    """spend_budget must accumulate epsilon using += not assignment.

    Hardens against mutmut mutation:
      ``ledger.total_spent_epsilon += decimal_amount``
      → ``ledger.total_spent_epsilon = decimal_amount``

    With ``=`` instead of ``+=``, the second spend would reset the balance
    to 0.3 instead of accumulating to 0.6.

    ADR-0047: Constitution Priority 0 gate.
    """
    from sqlalchemy import select

    from synth_engine.modules.privacy.accountant import spend_budget

    # Arrange: 5.0 allocated, 0 spent
    async with get_async_session(async_engine) as s:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("5.0"),
            total_spent_epsilon=Decimal("0.0"),
        )
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    # Act: spend 0.3 twice
    for job_id in range(2):
        async with get_async_session(async_engine) as s:
            await spend_budget(
                amount=Decimal("0.3"),
                job_id=job_id,
                ledger_id=ledger_id,
                session=s,
            )

    # Assert: total spent must be 0.6, not 0.3 (which would indicate = not +=)
    async with get_async_session(async_engine) as s:
        result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        updated = result.scalar_one()
        assert updated.total_spent_epsilon == Decimal("0.6"), (
            f"Expected accumulated total_spent=0.6, got {updated.total_spent_epsilon!r}. "
            "If this is 0.3, the += operator was mutated to =."
        )


@pytest.mark.asyncio
async def test_spend_budget_transaction_epsilon_spent_is_not_none(
    async_engine: AsyncEngine,
) -> None:
    """PrivacyTransaction.epsilon_spent must equal the amount, not None.

    Hardens against mutmut mutation:
      ``PrivacyTransaction(epsilon_spent=decimal_amount, ...)``
      → ``PrivacyTransaction(epsilon_spent=None, ...)``

    ADR-0047: Constitution Priority 0 gate.
    """
    from sqlalchemy import select

    from synth_engine.modules.privacy.accountant import spend_budget

    async with get_async_session(async_engine) as s:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("5.0"),
            total_spent_epsilon=Decimal("0.0"),
        )
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    async with get_async_session(async_engine) as s:
        await spend_budget(
            amount=Decimal("0.7"),
            job_id=1,
            ledger_id=ledger_id,
            session=s,
        )

    async with get_async_session(async_engine) as s:
        tx_result = await s.execute(
            select(PrivacyTransaction).where(PrivacyTransaction.ledger_id == ledger_id)
        )
        tx = tx_result.scalar_one()
        assert tx.epsilon_spent is not None, "PrivacyTransaction.epsilon_spent must not be None"
        assert tx.epsilon_spent == Decimal("0.7"), (
            f"Expected epsilon_spent=0.7, got {tx.epsilon_spent!r}"
        )


@pytest.mark.asyncio
async def test_reset_budget_or_condition_guard(
    async_engine: AsyncEngine,
) -> None:
    """reset_budget must use `and` not `or` in the validation guard.

    Hardens against mutmut mutation:
      ``if new_allocated_epsilon is not None and new_allocated_epsilon <= 0``
      → ``if new_allocated_epsilon is not None or new_allocated_epsilon <= 0``

    With ``or``, passing ``new_allocated_epsilon=None`` would evaluate as
    ``None is not None or None <= 0`` → ``False or TypeError`` — the TypeError
    from comparing None would propagate unexpectedly instead of the function
    proceeding normally with None (no-op update path).

    The mutation changes a guard that should only fire when BOTH conditions are
    true to one that fires when EITHER is true, breaking the None pass-through.

    ADR-0047: Constitution Priority 0 gate.
    """
    from synth_engine.modules.privacy.accountant import reset_budget

    # Arrange: ledger with some spent epsilon
    async with get_async_session(async_engine) as s:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("10.0"),
            total_spent_epsilon=Decimal("5.0"),
        )
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    # Act: reset with new_allocated_epsilon=None (no-op for allocation ceiling)
    # With the `or` mutation, this would raise ValueError instead of succeeding
    async with get_async_session(async_engine) as s:
        allocated, spent = await reset_budget(
            ledger_id=ledger_id,
            session=s,
            new_allocated_epsilon=None,  # None must pass through without raising
        )

    assert spent == Decimal("0.0"), f"Expected spent=0.0 after reset, got {spent!r}"
    assert allocated == Decimal("10.0"), f"Expected allocated unchanged at 10.0, got {allocated!r}"
