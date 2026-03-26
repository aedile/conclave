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
- Empty-passphrase detection occurs AFTER ``derive_kek()`` to eliminate
  the timing oracle that would otherwise let an attacker distinguish an
  empty passphrase (μs) from a wrong passphrase (~100 ms).  Both paths
  now incur the full PBKDF2 cost before any error is raised (T38.2).
- A class-level ``threading.Lock`` serialises concurrent ``unseal()``,
  ``seal()``, and ``get_kek()`` calls so that multi-worker uvicorn
  deployments cannot race on the ``_is_sealed`` / ``_kek`` state.

:exc:`VaultSealedError` is defined in :mod:`synth_engine.shared.exceptions`
and re-exported here for backward compatibility.

:exc:`VaultEmptyPassphraseError`, :exc:`VaultAlreadyUnsealedError`, and
:exc:`VaultConfigError` are also defined in :mod:`synth_engine.shared.exceptions`
and re-exported here for backward compatibility.  They previously inherited
``ValueError``; they now inherit ``SynthEngineError`` (T34.1).

CONSTITUTION Priority 0: Security
Task: P2-T2.4 — Vault Observability
Task: P26-T26.2 — Exception Hierarchy (VaultSealedError moved to shared)
Task: T34.1 — Unify Vault Exceptions Under SynthEngineError
Task: T38.2 — Eliminate vault unseal timing side-channel
Task: fix/review-critical-issues — Add threading.Lock to VaultState
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
from typing import ClassVar

from synth_engine.shared.exceptions import (
    VaultAlreadyUnsealedError,
    VaultConfigError,
    VaultEmptyPassphraseError,
    VaultSealedError,
)

__all__ = [
    "VaultAlreadyUnsealedError",
    "VaultConfigError",
    "VaultEmptyPassphraseError",
    "VaultSealedError",
    "VaultState",
    "derive_kek",
]


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

    A class-level ``threading.Lock`` serialises all mutating operations
    (``unseal``, ``seal``, ``get_kek``) to prevent races in multi-worker
    uvicorn deployments.  The pattern mirrors :class:`AuditLogger`.

    Class Attributes:
        _lock: Serialises concurrent unseal/seal/get_kek calls.
        _is_sealed: True while the vault has not been unsealed.
        _kek: Mutable byte buffer holding the derived KEK, or None.
    """

    _lock: ClassVar[threading.Lock] = threading.Lock()
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

        Timing note (T38.2): The empty-passphrase check is performed AFTER
        ``derive_kek()`` to avoid a timing oracle.  An attacker cannot
        distinguish an empty passphrase from a wrong passphrase by measuring
        response time — both paths incur the full PBKDF2 cost.

        Thread-safety note: The ``_is_sealed`` check and the KEK assignment
        are performed atomically under ``_lock``, preventing a race where two
        concurrent callers both pass the sealed check and overwrite each
        other's derived KEK.  The expensive PBKDF2 derivation runs outside
        the lock to avoid holding it for ~100 ms.

        Args:
            passphrase: Operator-provided unseal passphrase.

        Raises:
            VaultAlreadyUnsealedError: If the vault is already unsealed
                (call ``seal()`` first to re-unseal).
            VaultConfigError: If ``VAULT_SEAL_SALT`` is not set or decodes
                to fewer than 16 bytes.
            VaultEmptyPassphraseError: If *passphrase* is empty (checked
                after PBKDF2 to prevent timing oracle).
        """
        # Read and validate the salt before acquiring the lock — no shared
        # state is accessed here, so no race is possible.
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

        # Always derive the KEK — even for empty passphrases — to prevent a
        # timing oracle distinguishing empty vs wrong passphrase (T38.2).
        # PBKDF2 runs OUTSIDE the lock: it is pure computation with no shared
        # state access and takes ~100 ms.  Holding the lock during derivation
        # would serialize all unseal callers unnecessarily.
        kek_bytes = derive_kek(passphrase, salt)

        # Empty-passphrase check AFTER the expensive PBKDF2 call.
        if not passphrase:
            raise VaultEmptyPassphraseError("Passphrase must not be empty.")

        # Acquire the lock only for the short critical section that reads and
        # writes shared class state.  This prevents two concurrent callers
        # from both passing the _is_sealed check and overwriting each other.
        with cls._lock:
            if not cls._is_sealed:
                raise VaultAlreadyUnsealedError(
                    "Vault is already unsealed. Call seal() before unsealing again."
                )
            cls._kek = bytearray(kek_bytes)
            cls._is_sealed = False

    @classmethod
    def seal(cls) -> None:
        """Zero the KEK buffer and return the vault to the SEALED state.

        Uses a ``memoryview`` write to zero each byte of the ``bytearray``
        so that the key material is overwritten before being garbage-collected.

        Thread-safety: the mutation of ``_kek`` and ``_is_sealed`` is
        performed under ``_lock``.
        """
        with cls._lock:
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

        Thread-safety: the read of ``_is_sealed`` and ``_kek`` is
        performed under ``_lock`` for consistency.

        Returns:
            32-byte KEK.

        Raises:
            VaultSealedError: If the vault has not been unsealed.
        """
        with cls._lock:
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
