"""Negative/attack tests for T70.3 — Memory-safe vault KEK zeroing.

ATTACK-FIRST TDD — these tests prove:
- After seal(), raw KEK bytes are zeroed (ctypes readback)
- After unseal(), passphrase bytearray is zeroed
- VaultState.unseal() accepts bytes/bytearray, rejects str
- Empty passphrase (bytearray) still raises VaultEmptyPassphraseError

CONSTITUTION Priority 0: Security — KEK and passphrase in memory are P0
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T70.3 — Memory-safe vault operations (C12)
"""

from __future__ import annotations

import base64
import ctypes
import os
from collections.abc import Generator

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_vault_state() -> Generator[None]:
    """Reset VaultState class-level state after each test for isolation."""
    yield
    try:
        from synth_engine.shared.security.vault import VaultState

        VaultState.reset()
    except ImportError:
        pass


@pytest.fixture
def vault_salt_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provision VAULT_SEAL_SALT and return its raw value."""
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    return salt


# ---------------------------------------------------------------------------
# T70.3 AC1 — KEK zeroing uses ctypes.memset; after seal() bytes are zero
# ---------------------------------------------------------------------------


class TestKekZeroedAfterSeal:
    """After seal(), the KEK bytearray bytes must be zero (ctypes.memset)."""

    def test_kek_bytes_zero_after_seal(self, vault_salt_env: str) -> None:
        """After seal(), the raw bytes of the former KEK bytearray must all be 0.

        This test reads the content of the bytearray BEFORE seal() is called
        (saving a reference to the internal buffer), then calls seal(), then
        reads the buffer again to verify the bytes were zeroed by ctypes.memset.

        This is only possible because we retain a reference to the bytearray
        before seal() sets cls._kek = None.  We use ctypes to read the buffer
        at the saved address.
        """
        from synth_engine.shared.security.vault import VaultState

        passphrase = bytearray(b"test-passphrase-for-zero-check")  # nosec B105
        VaultState.unseal(passphrase)

        # Grab a reference to the internal bytearray BEFORE seal() clears it.
        # We must access _kek under the lock here, but for test purposes we
        # access it directly (test-only introspection).
        kek_buf: bytearray = VaultState._kek  # type: ignore[assignment]
        assert kek_buf is not None, "KEK must be set after unseal()"
        assert len(kek_buf) == 32

        # Capture address of the buffer before seal()
        c_array = (ctypes.c_uint8 * len(kek_buf)).from_buffer(kek_buf)
        addr = ctypes.addressof(c_array)

        # Call seal() — must zero the buffer via ctypes.memset
        VaultState.seal()

        # Read the bytes at the saved address: all must be zero
        zeroed = (ctypes.c_uint8 * 32).from_address(addr)
        raw_bytes = bytes(zeroed)
        assert raw_bytes == b"\x00" * 32, (
            f"KEK bytes must be zeroed after seal(), got: {raw_bytes!r}"
        )


# ---------------------------------------------------------------------------
# T70.3 AC2 — unseal() accepts bytes | bytearray, rejects str
# ---------------------------------------------------------------------------


class TestUnsealAcceptsBytesOrBytearray:
    """VaultState.unseal() must accept bytes and bytearray, reject str."""

    def test_unseal_accepts_bytearray_passphrase(self, vault_salt_env: str) -> None:
        """unseal() must succeed when passphrase is a bytearray."""
        from synth_engine.shared.security.vault import VaultState

        passphrase = bytearray(b"test-passphrase")  # nosec B105
        VaultState.unseal(passphrase)
        assert VaultState.is_sealed() is False
        kek = VaultState.get_kek()
        assert len(kek) == 32

    def test_unseal_accepts_bytes_passphrase(self, vault_salt_env: str) -> None:
        """unseal() must succeed when passphrase is bytes."""
        from synth_engine.shared.security.vault import VaultState

        passphrase = b"test-passphrase"  # nosec B105
        VaultState.unseal(passphrase)
        assert VaultState.is_sealed() is False
        kek = VaultState.get_kek()
        assert len(kek) == 32

    def test_unseal_rejects_str_passphrase(self, vault_salt_env: str) -> None:
        """unseal() must raise TypeError when passphrase is a plain str."""
        from synth_engine.shared.security.vault import VaultState

        with pytest.raises(TypeError, match="(?i)bytes|bytearray|passphrase"):
            VaultState.unseal("string-passphrase")  # nosec B105 # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# T70.3 AC3 — Passphrase bytearray is zeroed after PBKDF2 derivation
# ---------------------------------------------------------------------------


class TestPassphraseZeroedAfterDerivation:
    """The passphrase bytearray must be zeroed to all 0s after PBKDF2 runs."""

    def test_passphrase_bytearray_zeroed_after_unseal(self, vault_salt_env: str) -> None:
        """After unseal(), the passphrase bytearray passed by the caller must be zeroed.

        unseal() must zero the passphrase buffer in-place after PBKDF2 runs
        (within the function, before returning).  This allows the caller to
        confirm the buffer was cleared by inspecting the same bytearray object.
        """
        from synth_engine.shared.security.vault import VaultState

        passphrase = bytearray(b"correct-horse-battery-staple")  # nosec B105
        original_len = len(passphrase)

        VaultState.unseal(passphrase)

        # The bytearray must now be all zeros (zeroed by unseal() after PBKDF2)
        assert passphrase == bytearray(original_len), (
            f"Passphrase bytearray must be zeroed after unseal(), "
            f"got: {bytes(passphrase)!r}"
        )


# ---------------------------------------------------------------------------
# T70.3 — Empty passphrase (bytearray) still raises VaultEmptyPassphraseError
# ---------------------------------------------------------------------------


class TestEmptyPassphraseBytearray:
    """Empty bytearray passphrase must raise VaultEmptyPassphraseError (same as str)."""

    def test_empty_bytearray_raises_vault_empty_passphrase_error(
        self, vault_salt_env: str
    ) -> None:
        """unseal(bytearray(b'')) must raise VaultEmptyPassphraseError."""
        from synth_engine.shared.security.vault import VaultEmptyPassphraseError, VaultState

        with pytest.raises(VaultEmptyPassphraseError, match="[Pp]assphrase"):
            VaultState.unseal(bytearray(b""))  # nosec B105

    def test_empty_bytes_raises_vault_empty_passphrase_error(
        self, vault_salt_env: str
    ) -> None:
        """unseal(b'') must raise VaultEmptyPassphraseError."""
        from synth_engine.shared.security.vault import VaultEmptyPassphraseError, VaultState

        with pytest.raises(VaultEmptyPassphraseError, match="[Pp]assphrase"):
            VaultState.unseal(b"")  # nosec B105
