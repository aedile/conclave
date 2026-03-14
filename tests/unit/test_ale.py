"""Unit tests for Application-Level Encryption (ALE).

Tests verify Fernet-based EncryptedString TypeDecorator behaviour:
transparent encrypt-on-write, decrypt-on-read, None pass-through, and
robust failure when the ALE_KEY environment variable is absent or malformed.

Also verifies ALE-Vault KEK wiring via HKDF-SHA256: when the vault is
unsealed, get_fernet() derives the ALE key from the vault KEK; when the
vault is sealed, get_fernet() falls back to the ALE_KEY env var.

CONSTITUTION Priority 3: TDD — Red Phase
Task: P2-T2.2 — Secure Database Layer
Fix:  P2-debt-D1 — ALE-Vault KEK wiring
"""

import base64
import os

import pytest
from cryptography.fernet import Fernet, InvalidToken


@pytest.fixture(autouse=True)
def _reset_fernet(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[misc]
    """Reset the lru_cache on get_fernet and seal the vault after every test.

    This fixture runs automatically for every test in this module so that
    changes to the ALE_KEY environment variable and vault state propagate
    correctly to get_fernet() regardless of test ordering.
    """
    from synth_engine.shared.security.ale import _reset_fernet_cache
    from synth_engine.shared.security.vault import VaultState

    yield  # type: ignore[misc]
    _reset_fernet_cache()
    VaultState.reset()


@pytest.fixture
def ale_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provision a fresh Fernet key in the environment for ALE tests."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ALE_KEY", key)
    return key


@pytest.fixture
def vault_salt_env_for_ale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set VAULT_SEAL_SALT environment variable for vault-wiring ALE tests."""
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)


@pytest.fixture
def unsealed_vault(vault_salt_env_for_ale: None, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[misc]
    """Unseal VaultState with a known passphrase for ALE tests."""
    from synth_engine.shared.security.vault import VaultState

    VaultState.unseal("ale-test-passphrase")
    yield  # type: ignore[misc]
    VaultState.reset()


# ---------------------------------------------------------------------------
# EncryptedString — process_bind_param
# ---------------------------------------------------------------------------


def test_encrypted_string_encrypts_on_bind(ale_key: str) -> None:
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


def test_encrypted_string_decrypts_on_result(ale_key: str) -> None:
    """process_result_value must decrypt a Fernet token to the original string."""
    from synth_engine.shared.security.ale import EncryptedString, get_fernet

    fernet = get_fernet()
    token = fernet.encrypt(b"hello world").decode()

    col = EncryptedString()
    assert col.process_result_value(token, None) == "hello world"


def test_encrypted_string_none_passthrough(ale_key: str) -> None:
    """None values must pass through both directions untouched (nullable columns)."""
    from synth_engine.shared.security.ale import EncryptedString

    col = EncryptedString()
    assert col.process_bind_param(None, None) is None
    assert col.process_result_value(None, None) is None


# ---------------------------------------------------------------------------
# EncryptedString — empty string handling
# ---------------------------------------------------------------------------


def test_encrypted_string_empty_string_roundtrip(ale_key: str) -> None:
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
# get_fernet — key loading (sealed vault / env-var path)
# ---------------------------------------------------------------------------


def test_missing_ale_key_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_fernet() must raise RuntimeError when vault sealed and ALE_KEY absent."""
    monkeypatch.delenv("ALE_KEY", raising=False)

    from synth_engine.shared.security.ale import _reset_fernet_cache, get_fernet

    _reset_fernet_cache()
    with pytest.raises(RuntimeError, match="ALE_KEY environment variable not set"):
        get_fernet()


def test_malformed_ale_key_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_fernet() must raise ValueError when ALE_KEY is not a valid Fernet key.

    A malformed key (e.g. random ASCII that is not a valid URL-safe base64
    32-byte token) must surface as a ValueError so callers can distinguish
    between a missing key (RuntimeError) and a misconfigured key (ValueError).
    """
    monkeypatch.setenv("ALE_KEY", "not-valid-fernet-key")

    from synth_engine.shared.security.ale import _reset_fernet_cache, get_fernet

    _reset_fernet_cache()
    with pytest.raises(ValueError, match="Fernet key must be 32 url-safe base64-encoded bytes"):
        get_fernet()


# ---------------------------------------------------------------------------
# EncryptedString — corrupted ciphertext
# ---------------------------------------------------------------------------


def test_corrupted_ciphertext_raises_invalid_token(ale_key: str) -> None:
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
# ALE-Vault KEK wiring — new tests for fix/P2-debt-D1
# ---------------------------------------------------------------------------


def test_get_fernet_uses_vault_kek_when_unsealed(unsealed_vault: None, ale_key: str) -> None:
    """get_fernet() must use the vault KEK (not ALE_KEY) when vault is unsealed.

    The returned Fernet must be functional (can encrypt and decrypt data).
    The key derived from the vault KEK differs from the ALE_KEY env var key,
    so we verify by round-tripping data through the returned instance.
    """
    from synth_engine.shared.security.ale import get_fernet

    fernet = get_fernet()
    assert isinstance(fernet, Fernet)

    plaintext = b"vault-secured-pii"
    ciphertext = fernet.encrypt(plaintext)
    assert fernet.decrypt(ciphertext) == plaintext


def test_get_fernet_falls_back_to_env_when_sealed(ale_key: str) -> None:
    """get_fernet() must fall back to ALE_KEY env var when vault is sealed.

    When the vault is sealed, the returned Fernet must be backed by the
    ALE_KEY env var — verified by encrypting with it and decrypting with a
    Fernet constructed directly from the ALE_KEY.
    """
    from synth_engine.shared.security.ale import get_fernet
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed(), "vault must be sealed for this test"

    fernet = get_fernet()
    plaintext = b"env-backed-secret"
    ciphertext = fernet.encrypt(plaintext)

    # Must be decryptable by a Fernet built directly from the ALE_KEY env var
    env_fernet = Fernet(ale_key.encode())
    assert env_fernet.decrypt(ciphertext) == plaintext


def test_vault_and_env_keys_are_different(unsealed_vault: None, ale_key: str) -> None:
    """Vault-derived Fernet key must differ from the ALE_KEY env var key.

    Data encrypted with the vault-derived key must NOT be decryptable using
    the ALE_KEY env var key, proving the two keys are distinct.
    """
    from synth_engine.shared.security.ale import get_fernet

    vault_fernet = get_fernet()
    env_fernet = Fernet(ale_key.encode())

    ciphertext = vault_fernet.encrypt(b"sensitive-datum")

    with pytest.raises(InvalidToken):
        env_fernet.decrypt(ciphertext)


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


def test_fernet_switches_after_unseal(
    vault_salt_env_for_ale: None, ale_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_fernet() must return a different key after unsealing the vault.

    Encrypt while sealed (uses ALE_KEY), then unseal and encrypt again.
    The sealed ciphertext must be decryptable by the env-key Fernet;
    the unsealed ciphertext must be decryptable by the vault-key Fernet
    and NOT by the env-key Fernet.
    """
    from synth_engine.shared.security.ale import get_fernet
    from synth_engine.shared.security.vault import VaultState

    # Sealed state — uses ALE_KEY
    assert VaultState.is_sealed()
    sealed_fernet = get_fernet()
    sealed_ct = sealed_fernet.encrypt(b"sealed-data")

    # Unseal the vault
    VaultState.unseal("ale-test-passphrase")

    # Unsealed state — uses vault KEK
    vault_fernet = get_fernet()
    unsealed_ct = vault_fernet.encrypt(b"unsealed-data")

    # Sealed ciphertext decryptable by env-key Fernet
    env_fernet = Fernet(ale_key.encode())
    assert env_fernet.decrypt(sealed_ct) == b"sealed-data"

    # Unsealed ciphertext decryptable by vault Fernet but NOT by env-key Fernet
    assert vault_fernet.decrypt(unsealed_ct) == b"unsealed-data"
    with pytest.raises(InvalidToken):
        env_fernet.decrypt(unsealed_ct)
