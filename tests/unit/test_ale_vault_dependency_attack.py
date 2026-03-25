"""Negative/attack tests for ALE vault dependency enforcement (T48.5).

Attack surface coverage:
- Sealed vault ALE operations MUST fail with VaultSealedError — no env var fallback
- ALE_KEY env var must have no effect when vault is sealed (old fallback path removed)
- get_fernet() when sealed must raise VaultSealedError (not RuntimeError)
- EncryptedString.process_bind_param must raise VaultSealedError when sealed
- EncryptedString.process_result_value must raise VaultSealedError when sealed
- Incomplete fallback removal: confirm _load_ale_key_from_env does not exist
- Race condition mitigation: after sealing, subsequent ALE calls fail
- Huey task-style: validate vault before ALE operations (sealed => fail permanently)

CONSTITUTION Priority 0: Security
Task: T48.5 — ALE Vault Dependency Enforcement
"""

from __future__ import annotations

import base64
import os
from collections.abc import Generator

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _ensure_vault_sealed_and_no_ale_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Ensure vault is sealed and ALE_KEY is absent before each attack test."""
    from synth_engine.shared.security.ale import _reset_fernet_cache
    from synth_engine.shared.security.vault import VaultState

    VaultState.reset()
    _reset_fernet_cache()
    monkeypatch.delenv("ALE_KEY", raising=False)
    get_settings_mock_clear()

    yield

    VaultState.reset()
    _reset_fernet_cache()
    get_settings_mock_clear()


def get_settings_mock_clear() -> None:
    """Clear the settings cache to ensure ALE_KEY env var changes propagate."""
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# get_fernet() when sealed MUST raise VaultSealedError
# ---------------------------------------------------------------------------


def test_get_fernet_raises_vault_sealed_error_when_sealed() -> None:
    """get_fernet() must raise VaultSealedError when vault is sealed — no fallback.

    After T48.5, ALE_KEY env var provides NO fallback.  Even with ALE_KEY set,
    a sealed vault must prevent all ALE operations.
    """
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import get_fernet
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed(), "Vault must be sealed for this test"

    with pytest.raises(VaultSealedError):
        get_fernet()


def test_get_fernet_does_not_fall_back_to_env_var_when_sealed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_fernet() must NOT use ALE_KEY env var as a fallback when vault is sealed.

    This is the core security invariant of T48.5: the old fallback is removed.
    """
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import get_fernet
    from synth_engine.shared.security.vault import VaultState

    # Provide a valid ALE_KEY — should have NO effect after T48.5
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ALE_KEY", key)
    get_settings_mock_clear()

    assert VaultState.is_sealed()

    with pytest.raises(VaultSealedError, match="[Vv]ault"):
        get_fernet()


def test_get_fernet_raises_vault_sealed_error_not_runtime_error() -> None:
    """VaultSealedError must be raised (not RuntimeError) when vault is sealed.

    The spec-challenger identified that the original fallback raised RuntimeError
    for missing ALE_KEY; after T48.5 it must be VaultSealedError.
    """
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import get_fernet

    with pytest.raises(VaultSealedError):
        get_fernet()

    # Confirm it is NOT a RuntimeError subclass
    try:
        get_fernet()
    except VaultSealedError:
        pass  # correct
    except RuntimeError as exc:
        pytest.fail(f"get_fernet() raised RuntimeError instead of VaultSealedError: {exc}")


# ---------------------------------------------------------------------------
# _load_ale_key_from_env must not exist (or be dead code)
# ---------------------------------------------------------------------------


def test_load_ale_key_from_env_is_removed() -> None:
    """_load_ale_key_from_env must not be callable — the fallback path is removed.

    T48.5 requires the env var fallback be removed entirely.  The function
    must either not exist or be replaced with a no-op stub that still raises.
    """
    import synth_engine.shared.security.ale as ale_module

    # The function should not exist at all, OR if it exists it must be a
    # no-op that indicates the fallback is disabled.
    if hasattr(ale_module, "_load_ale_key_from_env"):
        # If it exists, calling it must NOT return usable key material —
        # it must raise to signal the fallback is disabled.
        from synth_engine.shared.exceptions import VaultSealedError

        with pytest.raises((VaultSealedError, RuntimeError)):
            ale_module._load_ale_key_from_env()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# EncryptedString — sealed vault must raise VaultSealedError
# ---------------------------------------------------------------------------


def test_encrypted_string_bind_raises_vault_sealed_error_when_sealed() -> None:
    """EncryptedString.process_bind_param must raise VaultSealedError when vault is sealed."""
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import EncryptedString
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed()
    col = EncryptedString()

    with pytest.raises(VaultSealedError):
        col.process_bind_param("secret-data", None)


def test_encrypted_string_result_raises_vault_sealed_error_when_sealed() -> None:
    """EncryptedString.process_result_value must raise VaultSealedError when vault is sealed."""
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import EncryptedString
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed()
    col = EncryptedString()
    some_token = "gAAAAAB" + "x" * 80  # structurally looks like a Fernet token

    with pytest.raises(VaultSealedError):
        col.process_result_value(some_token, None)


# ---------------------------------------------------------------------------
# Sealed vault after unseal — ALE fails correctly after re-sealing
# ---------------------------------------------------------------------------


def test_ale_fails_after_vault_re_seals(monkeypatch: pytest.MonkeyPatch) -> None:
    """ALE operations must fail after the vault is re-sealed.

    This covers the race condition: a task that calls get_fernet() after
    the vault has been sealed mid-operation must get VaultSealedError.
    """
    from synth_engine.shared.exceptions import VaultSealedError
    from synth_engine.shared.security.ale import get_fernet
    from synth_engine.shared.security.vault import VaultState

    # Set up vault unseal
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    VaultState.unseal("test-passphrase")

    # ALE must work when unsealed
    fernet = get_fernet()
    assert callable(fernet.encrypt), (
        "get_fernet() must return a Fernet instance with an encrypt method when vault is unsealed"
    )

    # Seal again
    VaultState.seal()

    # ALE must fail now
    with pytest.raises(VaultSealedError):
        get_fernet()


# ---------------------------------------------------------------------------
# Unsealed vault still works
# ---------------------------------------------------------------------------


def test_get_fernet_works_when_vault_is_unsealed(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_fernet() must still work correctly when vault is unsealed.

    This confirms T48.5 does not break the production vault-key path.
    """
    from synth_engine.shared.security.ale import get_fernet
    from synth_engine.shared.security.vault import VaultState

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    VaultState.unseal("test-passphrase")

    fernet = get_fernet()
    plaintext = b"check-vault-works"
    ciphertext = fernet.encrypt(plaintext)
    assert fernet.decrypt(ciphertext) == plaintext
