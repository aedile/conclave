"""Unit tests for Application-Level Encryption (ALE).

Tests verify Fernet-based EncryptedString TypeDecorator behaviour:
transparent encrypt-on-write, decrypt-on-read, None pass-through, and
correct VaultSealedError when the vault is sealed.

Also verifies ALE-Vault KEK wiring via HKDF-SHA256: get_fernet() derives
the ALE key from the vault KEK and raises VaultSealedError when sealed
(T48.5 — ALE_KEY env var fallback removed).

CONSTITUTION Priority 3: TDD — Red Phase
Task: P2-T2.2 — Secure Database Layer
Fix:  P2-debt-D1 — ALE-Vault KEK wiring
Task: T48.5 — ALE Vault Dependency Enforcement
"""

from __future__ import annotations

import base64
import os
from collections.abc import Generator, Iterator

import pytest
from cryptography.fernet import Fernet, InvalidToken


@pytest.fixture(autouse=True)
def _reset_fernet(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Seal vault and reset Fernet cache before and after every test in this module.

    Runs both in setup (before yield) and teardown (after yield) to guarantee
    that vault state from other test modules cannot bleed into ALE tests —
    regardless of test execution order within the full suite.

    This fixture runs automatically for every test in this module so that
    changes to the ALE_KEY environment variable and vault state propagate
    correctly to get_fernet() regardless of test ordering.
    """
    from synth_engine.shared.security.ale import _reset_fernet_cache
    from synth_engine.shared.security.vault import VaultState
    from synth_engine.shared.settings import get_settings

    # Setup: seal the vault in case a prior test (in another module) left it unsealed.
    VaultState.reset()
    _reset_fernet_cache()
    get_settings.cache_clear()
    monkeypatch.delenv("ALE_KEY", raising=False)

    yield

    # Teardown: restore sealed state so subsequent tests start clean.
    _reset_fernet_cache()
    VaultState.reset()
    get_settings.cache_clear()


@pytest.fixture
def vault_salt_env_for_ale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set VAULT_SEAL_SALT environment variable for vault-wiring ALE tests."""
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)


@pytest.fixture
def unsealed_vault(vault_salt_env_for_ale: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Unseal VaultState with a known passphrase for ALE tests."""
    from synth_engine.shared.security.vault import VaultState

    VaultState.unseal("ale-test-passphrase")
    yield
    VaultState.reset()


# ---------------------------------------------------------------------------
# EncryptedString — process_bind_param
# ---------------------------------------------------------------------------


def test_encrypted_string_encrypts_on_bind(unsealed_vault: None) -> None:
    """process_bind_param must encrypt plaintext so the stored value differs.

    The round-trip must also be lossless: decrypting the stored token must
    yield the original plaintext.
    """
    from synth_engine.shared.security.ale import EncryptedString

    col = EncryptedString()
    ciphertext = col.process_bind_param("secret", None)

    assert ciphertext is not None
    assert ciphertext != "secret", "ciphertext must differ from plaintext"

    # Round-trip: ciphertext must decrypt back to the original value
    plaintext = col.process_result_value(ciphertext, None)
    assert plaintext == "secret"


def test_encrypted_string_decrypts_on_result(unsealed_vault: None) -> None:
    """process_result_value must decrypt a Fernet token to the original string."""
    from synth_engine.shared.security.ale import EncryptedString, get_fernet

    fernet = get_fernet()
    token = fernet.encrypt(b"hello world").decode()

    col = EncryptedString()
    assert col.process_result_value(token, None) == "hello world"


def test_encrypted_string_none_passthrough_sealed() -> None:
    """None values must pass through both directions untouched even when vault is sealed.

    None values never trigger vault access — this allows nullable columns to
    work even before the vault is unsealed.
    """
    from synth_engine.shared.security.ale import EncryptedString
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed()
    col = EncryptedString()
    assert col.process_bind_param(None, None) is None
    assert col.process_result_value(None, None) is None


# ---------------------------------------------------------------------------
# EncryptedString — empty string handling
# ---------------------------------------------------------------------------


def test_encrypted_string_empty_string_roundtrip(unsealed_vault: None) -> None:
    """process_bind_param on empty string must return ciphertext, not empty or None.

    An empty string is a valid PII field value (e.g. a cleared field) and must
    be encrypted like any other non-None value.  The round-trip must yield the
    original empty string.
    """
    from synth_engine.shared.security.ale import EncryptedString

    col = EncryptedString()
    ciphertext = col.process_bind_param("", None)

    assert ciphertext is not None, "empty string must produce a ciphertext, not None"
    assert ciphertext != "", "empty string must produce a non-empty ciphertext"

    # Round-trip: decrypting the ciphertext must recover the empty string
    plaintext = col.process_result_value(ciphertext, None)
    assert plaintext == ""


# ---------------------------------------------------------------------------
# get_fernet — sealed vault raises VaultSealedError (T48.5)
# ---------------------------------------------------------------------------


def test_get_fernet_raises_vault_sealed_error_when_sealed() -> None:
    """get_fernet() must raise VaultSealedError when vault is sealed (T48.5).

    The ALE_KEY env var fallback has been removed.  Sealing the vault
    protects all encrypted data — callers must unseal before ALE operations.
    """
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import get_fernet
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed()
    with pytest.raises(VaultSealedError):
        get_fernet()


def test_missing_ale_key_is_irrelevant_vault_sealed_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VaultSealedError must be raised regardless of whether ALE_KEY is set.

    After T48.5, ALE_KEY has no effect when the vault is sealed.  The vault-
    sealed check fires before any key-loading attempt.
    """
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import get_fernet
    from synth_engine.shared.security.vault import VaultState

    # Provide a valid ALE_KEY — should have NO effect when vault is sealed.
    monkeypatch.setenv("ALE_KEY", Fernet.generate_key().decode())
    assert VaultState.is_sealed()

    with pytest.raises(VaultSealedError):
        get_fernet()


# ---------------------------------------------------------------------------
# EncryptedString — corrupted ciphertext
# ---------------------------------------------------------------------------


def test_corrupted_ciphertext_raises_invalid_token(unsealed_vault: None) -> None:
    """process_result_value must raise InvalidToken for corrupted ciphertext.

    If a stored ciphertext is truncated, tampered with, or otherwise invalid
    the Fernet layer must reject it with cryptography.fernet.InvalidToken so
    callers can handle integrity failures explicitly rather than silently
    receiving garbage plaintext.
    """
    from synth_engine.shared.security.ale import EncryptedString

    col = EncryptedString()
    with pytest.raises(InvalidToken):
        col.process_result_value("corrupted-not-a-fernet-token", None)


# ---------------------------------------------------------------------------
# generate_ale_key
# ---------------------------------------------------------------------------


def test_generate_ale_key_produces_valid_fernet_key() -> None:
    """generate_ale_key() must return a string accepted by the Fernet constructor."""
    from synth_engine.shared.security.ale import generate_ale_key

    key = generate_ale_key()
    assert isinstance(key, str)
    # This will raise InvalidToken / ValueError if the key is malformed
    Fernet(key.encode())


# ---------------------------------------------------------------------------
# ALE-Vault KEK wiring — vault-first design
# ---------------------------------------------------------------------------


def test_get_fernet_uses_vault_kek_when_unsealed(unsealed_vault: None) -> None:
    """get_fernet() must use the vault KEK when vault is unsealed.

    The returned Fernet must be functional (can encrypt and decrypt data).
    """
    from synth_engine.shared.security.ale import get_fernet

    fernet = get_fernet()
    assert isinstance(fernet, Fernet)

    plaintext = b"vault-secured-pii"
    ciphertext = fernet.encrypt(plaintext)
    assert fernet.decrypt(ciphertext) == plaintext


def test_vault_kek_fernet_is_not_decryptable_with_random_key(unsealed_vault: None) -> None:
    """Data encrypted with vault-derived key must not be decryptable with a random key.

    This confirms that the vault KEK produces a unique, distinct key —
    a different Fernet key cannot decrypt vault-encrypted ciphertext.
    """
    from synth_engine.shared.security.ale import get_fernet

    vault_fernet = get_fernet()
    random_fernet = Fernet(Fernet.generate_key())

    ciphertext = vault_fernet.encrypt(b"sensitive-datum")

    with pytest.raises(InvalidToken):
        random_fernet.decrypt(ciphertext)


def test_hkdf_derivation_is_deterministic() -> None:
    """_derive_ale_key_from_kek must produce the same output for the same KEK.

    HKDF is a deterministic function: identical inputs must always produce
    identical outputs.  This property is essential for consistent encryption
    across application restarts with the same vault passphrase.
    """
    from synth_engine.shared.security.ale import _derive_ale_key_from_kek

    kek = b"\xab" * 32  # arbitrary deterministic KEK
    key1 = _derive_ale_key_from_kek(kek)
    key2 = _derive_ale_key_from_kek(kek)
    assert key1 == key2, "HKDF must be deterministic for identical KEK inputs"


def test_fernet_raises_sealed_error_when_vault_is_sealed_between_calls(
    vault_salt_env_for_ale: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_fernet() must raise VaultSealedError after vault is re-sealed.

    If the vault is unsealed, ALE works.  After sealing, ALE must fail
    with VaultSealedError — even in the same process.
    """
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import get_fernet
    from synth_engine.shared.security.vault import VaultState

    # Unseal and verify ALE works
    VaultState.unseal("ale-test-passphrase-2")
    fernet = get_fernet()
    assert fernet is not None

    # Seal and verify ALE fails
    VaultState.seal()
    with pytest.raises(VaultSealedError):
        get_fernet()
