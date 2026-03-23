"""Feature tests for ALE vault dependency enforcement (T48.5).

Tests cover:
- Sealed vault raises VaultSealedError on get_fernet()
- Sealed vault raises VaultSealedError on EncryptedString operations
- Unsealed vault ALE operations succeed
- AuditLogger emits ALE_REJECTED_VAULT_SEALED event when sealed
- config_validation warns when vault is sealed at startup
- VaultSealedError has correct detail and status_code

CONSTITUTION Priority 3: TDD — Red Phase (feature tests)
Task: T48.5 — ALE Vault Dependency Enforcement
"""

from __future__ import annotations

import base64
import logging
import os
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def _isolate_vault_and_ale(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Seal vault and clear settings cache before/after each test."""
    from synth_engine.shared.security.ale import _reset_fernet_cache
    from synth_engine.shared.security.vault import VaultState
    from synth_engine.shared.settings import get_settings

    VaultState.reset()
    _reset_fernet_cache()
    get_settings.cache_clear()
    monkeypatch.delenv("ALE_KEY", raising=False)

    yield

    VaultState.reset()
    _reset_fernet_cache()
    get_settings.cache_clear()


@pytest.fixture
def unsealed_vault(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Unseal vault with a test passphrase."""
    from synth_engine.shared.security.vault import VaultState

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    VaultState.unseal("ale-vault-dep-test-passphrase")
    yield
    VaultState.reset()


# ---------------------------------------------------------------------------
# get_fernet() — sealed raises VaultSealedError
# ---------------------------------------------------------------------------


def test_get_fernet_raises_vault_sealed_error_when_sealed() -> None:
    """get_fernet() must raise VaultSealedError when vault is sealed."""
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import get_fernet
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed()
    with pytest.raises(VaultSealedError) as exc_info:
        get_fernet()

    # VaultSealedError must have detail and status_code attributes
    assert exc_info.value.status_code == 423
    assert "sealed" in exc_info.value.detail.lower()


def test_get_fernet_works_when_unsealed(unsealed_vault: None) -> None:
    """get_fernet() must succeed and return a functional Fernet when vault is unsealed."""
    from cryptography.fernet import Fernet

    from synth_engine.shared.security.ale import get_fernet

    fernet = get_fernet()
    assert isinstance(fernet, Fernet)
    assert fernet.decrypt(fernet.encrypt(b"test")) == b"test"


# ---------------------------------------------------------------------------
# EncryptedString — sealed raises VaultSealedError
# ---------------------------------------------------------------------------


def test_encrypted_string_bind_raises_vault_sealed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """EncryptedString.process_bind_param raises VaultSealedError when vault is sealed."""
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import EncryptedString
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed()
    col = EncryptedString()

    with pytest.raises(VaultSealedError):
        col.process_bind_param("sensitive-data", None)


def test_encrypted_string_result_raises_vault_sealed_error() -> None:
    """EncryptedString.process_result_value raises VaultSealedError when vault is sealed."""
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import EncryptedString
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed()
    col = EncryptedString()
    token = "gAAAAAB" + "x" * 80

    with pytest.raises(VaultSealedError):
        col.process_result_value(token, None)


def test_encrypted_string_works_when_unsealed(unsealed_vault: None) -> None:
    """EncryptedString round-trip works when vault is unsealed."""
    from synth_engine.shared.security.ale import EncryptedString

    col = EncryptedString()
    ciphertext = col.process_bind_param("pii-data", None)
    assert ciphertext is not None
    assert ciphertext != "pii-data"
    assert col.process_result_value(ciphertext, None) == "pii-data"


def test_encrypted_string_none_passthrough_sealed() -> None:
    """EncryptedString None passthrough must work even when vault is sealed.

    None values should bypass ALE entirely — no vault needed.
    """
    from synth_engine.shared.security.ale import EncryptedString
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed()
    col = EncryptedString()
    assert col.process_bind_param(None, None) is None
    assert col.process_result_value(None, None) is None


# ---------------------------------------------------------------------------
# Audit logging on ALE rejection
# ---------------------------------------------------------------------------


def test_audit_event_emitted_on_ale_rejection_when_sealed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An audit event must be emitted when ALE operation is rejected due to sealed vault.

    The event_type must be 'ALE_REJECTED_VAULT_SEALED' and actor must indicate
    the ALE subsystem.
    """
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import get_fernet
    from synth_engine.shared.security.audit import reset_audit_logger
    from synth_engine.shared.security.vault import VaultState
    from synth_engine.shared.settings import get_settings

    audit_key = os.urandom(32).hex()
    monkeypatch.setenv("AUDIT_KEY", audit_key)
    # Force settings to re-read AUDIT_KEY from env after monkeypatch.
    get_settings.cache_clear()
    reset_audit_logger()

    assert VaultState.is_sealed()

    with caplog.at_level(logging.INFO, logger="synth_engine.security.audit"):
        with pytest.raises(VaultSealedError):
            get_fernet()

    messages = [r.getMessage() for r in caplog.records]
    ale_rejection_events = [m for m in messages if "ALE_REJECTED_VAULT_SEALED" in m]
    assert ale_rejection_events, f"Expected ALE_REJECTED_VAULT_SEALED audit event; got: {messages}"


# ---------------------------------------------------------------------------
# config_validation warns when vault is sealed at startup
# ---------------------------------------------------------------------------


def test_config_validation_warns_when_vault_sealed_at_startup(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """validate_config must emit a WARNING when the vault is sealed at startup.

    The vault is often sealed at boot (before /unseal is called).  This is
    not a fatal error — startup must succeed — but operators should be warned
    that ALE operations will fail until the vault is unsealed.
    """
    from synth_engine.shared.security.vault import VaultState
    from synth_engine.shared.settings import get_settings

    # Provide minimally valid config
    audit_key = os.urandom(32).hex()
    monkeypatch.setenv("AUDIT_KEY", audit_key)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    get_settings.cache_clear()

    assert VaultState.is_sealed()

    from synth_engine.bootstrapper.config_validation import validate_config

    with caplog.at_level(logging.WARNING):
        validate_config()  # Must NOT raise

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    vault_sealed_warnings = [
        m for m in warning_messages if "vault" in m.lower() and "sealed" in m.lower()
    ]
    assert vault_sealed_warnings, (
        f"Expected vault-sealed WARNING in config_validation; got: {warning_messages}"
    )
