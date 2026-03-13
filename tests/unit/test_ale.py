"""Unit tests for Application-Level Encryption (ALE) — RED phase.

Tests verify Fernet-based EncryptedString TypeDecorator behaviour:
transparent encrypt-on-write, decrypt-on-read, None pass-through, and
robust failure when the ALE_KEY environment variable is absent.

CONSTITUTION Priority 3: TDD — Red Phase
Task: P2-T2.2 — Secure Database Layer
"""

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _reset_fernet(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[misc]
    """Reset the lru_cache on get_fernet after every test.

    This fixture runs automatically for every test in this module so that
    changes to the ALE_KEY environment variable propagate correctly to
    get_fernet() regardless of test ordering.
    """
    from synth_engine.shared.security.ale import _reset_fernet_cache

    yield  # type: ignore[misc]
    _reset_fernet_cache()


@pytest.fixture
def ale_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provision a fresh Fernet key in the environment for ALE tests."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ALE_KEY", key)
    return key


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
# get_fernet — key loading
# ---------------------------------------------------------------------------


def test_missing_ale_key_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_fernet() must raise RuntimeError when ALE_KEY is not in the environment."""
    monkeypatch.delenv("ALE_KEY", raising=False)

    from synth_engine.shared.security.ale import _reset_fernet_cache, get_fernet

    _reset_fernet_cache()
    with pytest.raises(RuntimeError, match="ALE_KEY environment variable not set"):
        get_fernet()


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
