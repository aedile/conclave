"""Vault state and KEK derivation for the Conclave Engine.

The engine boots into a SEALED state. No sensitive operations are
permitted until an operator provides the unseal passphrase, which is
used to derive the Key Encryption Key (KEK) into ephemeral memory only —
it is never written to disk.

Security properties
-------------------
- PBKDF2-HMAC-SHA256 with 600_000 iterations stretches the passphrase
  to a 32-byte Key Encryption Key (KEK).
- The KEK is stored in a ``bytearray`` so its contents can be zeroed
  deterministically on seal (``bytearray`` is mutable; ``bytes`` is not).
- ``VAULT_SEAL_SALT`` is sourced from the environment; it is *not* secret
  (it merely prevents rainbow-table attacks) but must remain consistent
  across restarts so the same passphrase always produces the same KEK.
- The sealed state gate is enforced by :class:`SealGateMiddleware`
  (``bootstrapper/dependencies/vault.py``).

CONSTITUTION Priority 0: Security
Task: P2-T2.4 — Vault Observability
"""

from __future__ import annotations

import base64
import hashlib
import os


class VaultSealedError(Exception):
    """Raised when a caller attempts a sensitive operation on a sealed vault.

    Attributes:
        detail: Human-readable explanation for API consumers.
        status_code: HTTP status code to return (423 Locked).
    """

    def __init__(self, detail: str = "Vault is sealed") -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code: int = 423


class VaultEmptyPassphraseError(ValueError):
    """Raised when the unseal passphrase is empty.

    Allows the /unseal endpoint to catch this by type rather than by
    string-matching ValueError messages (Architecture finding P5-T5.3).
    """


class VaultAlreadyUnsealedError(ValueError):
    """Raised when VaultState.unseal() is called on an already-unsealed vault.

    Allows the /unseal endpoint to catch this by type rather than by
    string-matching ValueError messages (Architecture finding P5-T5.3).
    """


class VaultConfigError(ValueError):
    """Raised when VAULT_SEAL_SALT is missing or does not meet the 16-byte minimum.

    Allows the /unseal endpoint to catch this by type rather than by
    string-matching ValueError messages (Architecture finding P5-T5.3).
    """


def derive_kek(passphrase: str, salt: bytes) -> bytes:
    """Derive a 32-byte Key Encryption Key from *passphrase* and *salt*.

    Uses PBKDF2-HMAC-SHA256 with 600_000 iterations as recommended by
    OWASP for password-based key derivation in 2024+.

    Args:
        passphrase: Operator-provided unseal passphrase.
        salt: Random 16-byte salt (sourced from ``VAULT_SEAL_SALT`` env var).

    Returns:
        32 bytes of key material suitable for AES-256.
    """
    return hashlib.pbkdf2_hmac(
        hash_name="sha256",
        password=passphrase.encode(),
        salt=salt,
        iterations=600_000,
        dklen=32,
    )


class VaultState:
    """Class-level singleton that tracks whether the vault is sealed.

    All state is maintained at the *class* level so that the seal gate
    is enforced across every request without any dependency injection.

    Class Attributes:
        _is_sealed: True while the vault has not been unsealed.
        _kek: Mutable byte buffer holding the derived KEK, or None.
    """

    _is_sealed: bool = True
    _kek: bytearray | None = None

    @classmethod
    def unseal(cls, passphrase: str) -> None:
        """Derive the KEK and transition the vault to the UNSEALED state.

        Reads ``VAULT_SEAL_SALT`` from the environment (base64url-encoded).
        The passphrase is never stored; only the derived KEK is retained
        in an in-memory ``bytearray``.

        This method is idempotent-hostile: calling ``unseal()`` while the
        vault is already unsealed raises ``VaultAlreadyUnsealedError`` to
        prevent silent KEK rotation.  Call ``seal()`` first if a re-unseal
        is intended.

        Args:
            passphrase: Operator-provided unseal passphrase.

        Raises:
            VaultEmptyPassphraseError: If *passphrase* is empty.
            VaultAlreadyUnsealedError: If the vault is already unsealed
                (call ``seal()`` first to re-unseal).
            VaultConfigError: If ``VAULT_SEAL_SALT`` is not set or decodes
                to fewer than 16 bytes.
        """
        if not passphrase:
            raise VaultEmptyPassphraseError("Passphrase must not be empty.")
        if not cls._is_sealed:
            raise VaultAlreadyUnsealedError(
                "Vault is already unsealed. Call seal() before unsealing again."
            )

        raw_salt = os.environ.get("VAULT_SEAL_SALT")
        if not raw_salt:
            raise VaultConfigError(
                "VAULT_SEAL_SALT environment variable is not set. "
                'Generate with: python3 -c "import os, base64; '
                'print(base64.urlsafe_b64encode(os.urandom(16)).decode())"'
            )
        # Add padding so standard base64url decode works regardless of trailing '='
        padded = raw_salt + "=="
        salt = base64.urlsafe_b64decode(padded)
        if len(salt) < 16:
            raise VaultConfigError(
                f"VAULT_SEAL_SALT must decode to at least 16 bytes; got {len(salt)} bytes."
            )

        kek_bytes = derive_kek(passphrase, salt)
        cls._kek = bytearray(kek_bytes)
        cls._is_sealed = False

    @classmethod
    def seal(cls) -> None:
        """Zero the KEK buffer and return the vault to the SEALED state.

        Uses a ``memoryview`` write to zero each byte of the ``bytearray``
        so that the key material is overwritten before being garbage-collected.
        """
        if cls._kek is not None:
            mv = memoryview(cls._kek)
            for i in range(len(mv)):
                mv[i] = 0
            cls._kek = None
        cls._is_sealed = True

    @classmethod
    def is_sealed(cls) -> bool:
        """Return True if the vault is currently sealed.

        Returns:
            Sealed status.
        """
        return cls._is_sealed

    @classmethod
    def get_kek(cls) -> bytes:
        """Return a copy of the Key Encryption Key.

        Returns a ``bytes`` *copy* so callers cannot mutate the internal
        ``bytearray`` accidentally.

        Returns:
            32-byte KEK.

        Raises:
            VaultSealedError: If the vault has not been unsealed.
        """
        if cls._is_sealed or cls._kek is None:
            raise VaultSealedError()
        return bytes(cls._kek)

    @classmethod
    def reset(cls) -> None:
        """Re-seal and clear the KEK.

        **For test isolation only.** Restores the class to its boot state
        so that state does not bleed between test cases.
        """
        cls.seal()
