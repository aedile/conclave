"""Unit tests for Application-Level Encryption (ALE) — RED phase.

Tests verify Fernet-based EncryptedString TypeDecorator behaviour:
transparent encrypt-on-write, decrypt-on-read, None pass-through, and
robust failure when the ALE_KEY environment variable is absent or malformed.

CONSTITUTION Priority 3: TDD — Red Phase
Task: P2-T2.2 — Secure Database Layer
"""

import pytest
from cryptography.fernet import Fernet, InvalidToken


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
# get_fernet — key loading
# ---------------------------------------------------------------------------


def test_missing_ale_key_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_fernet() must raise RuntimeError when ALE_KEY is not in the environment."""
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
