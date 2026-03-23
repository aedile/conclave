"""Application-Level Encryption (ALE) for the Conclave Engine.

Provides a Fernet-based ``EncryptedString`` SQLAlchemy ``TypeDecorator``
that transparently encrypts string values before writing them to the
database and decrypts them on read.

Key derivation strategy (vault-only design — T48.5)
----------------------------------------------------
``get_fernet()`` derives the ALE encryption key from the vault Key Encryption
Key (KEK) using HKDF-SHA256.  This ties the ALE key lifecycle directly to the
vault unseal state: the same operator passphrase that unseals the vault
consistently produces the same ALE key without ever persisting it to disk or
environment variables.

When the vault is **sealed**, ``get_fernet()`` raises
:exc:`~synth_engine.shared.exceptions.VaultSealedError`.  There is **no
ALE_KEY environment variable fallback**.  This ensures that sealing the vault
actually protects encrypted database columns — an attacker who forces a vault
seal cannot fall back to a weaker env-var key.

An audit event with ``event_type="ALE_REJECTED_VAULT_SEALED"`` is emitted
whenever a sealed-vault rejection occurs, providing observability for operators
investigating why ALE operations are failing.

HKDF parameters (public, not secret)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- Algorithm: HMAC-SHA256
- Length: 32 bytes (256-bit AES key material)
- Salt: ``b"conclave-ale-v1"`` — a fixed, versioned public label; not
  secret; prevents cross-context key reuse
- Info: ``b"application-level-encryption"`` — context label for this
  specific key purpose

Security properties
-------------------
- Symmetric encryption via ``cryptography.fernet.Fernet`` (AES-128-CBC +
  HMAC-SHA256) provides authenticated encryption (AEAD semantics).
- ``get_fernet()`` is **not** cached: HKDF is fast, and caching across vault
  state transitions would return a stale key after a seal/unseal cycle.
- ``_reset_fernet_cache()`` is retained as a no-op for backward compatibility
  with test fixtures written before vault wiring was added; calling it is
  always safe and has no effect.
- ``ALE_KEY`` environment variable is intentionally removed as a fallback
  (T48.5).  Use ``generate_ale_key()`` to provision a test key when writing
  integration tests that unseal the vault.

Usage
-----
Annotate any SQLModel / SQLAlchemy column with ``EncryptedString`` to
enable transparent field-level encryption::

    from synth_engine.shared.security.ale import EncryptedString

    class Patient(BaseModel, table=True):
        ssn: str = Field(sa_column=Column(EncryptedString()))

CONSTITUTION Priority 0: Security
Task: P2-T2.2 — Secure Database Layer
Fix:  P2-debt-D1 — ALE-Vault KEK wiring
Task: T36.1 — Centralize Configuration Into Pydantic Settings Model
Task: T48.5 — ALE Vault Dependency Enforcement (remove ALE_KEY fallback)
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import String
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

from synth_engine.shared.exceptions import VaultSealedError
from synth_engine.shared.security.vault import VaultState

_logger = logging.getLogger("synth_engine.security.ale")

# ---------------------------------------------------------------------------
# HKDF parameters — fixed public labels (not secret)
# ---------------------------------------------------------------------------
_HKDF_SALT: bytes = b"conclave-ale-v1"
_HKDF_INFO: bytes = b"application-level-encryption"
_HKDF_LENGTH: int = 32


def generate_ale_key() -> str:
    """Generate a fresh Fernet-compatible ALE key.

    This utility is intended for one-time key provisioning (e.g., during
    initial host setup).  The returned key should be stored in a secrets
    manager or Docker secret, **never** in source control.

    Returns:
        A URL-safe base64-encoded 32-byte string that can be used directly
        as the value of the ``ALE_KEY`` environment variable.
    """
    return Fernet.generate_key().decode()


def _derive_ale_key_from_kek(kek: bytes) -> bytes:
    """Derive a 32-byte ALE key from the vault Key Encryption Key via HKDF-SHA256.

    Uses fixed public labels (salt and info) to provide domain separation and
    version the derivation context.  The same KEK always produces the same
    ALE key (HKDF is deterministic).

    Args:
        kek: 32-byte Key Encryption Key obtained from :class:`VaultState`.

    Returns:
        32 bytes of ALE key material, suitable for Fernet after base64
        URL-safe encoding.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_HKDF_LENGTH,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    return hkdf.derive(kek)


def _emit_ale_sealed_audit_event() -> None:
    """Emit an ALE_REJECTED_VAULT_SEALED audit event via the audit logger.

    Called when ``get_fernet()`` rejects an ALE operation because the vault
    is sealed.  The audit entry provides observability for operators who need
    to understand why ALE operations are failing.

    Failures in audit logging are caught and logged at WARNING to avoid
    masking the primary VaultSealedError.
    """
    try:
        from synth_engine.shared.security.audit import get_audit_logger

        get_audit_logger().log_event(
            event_type="ALE_REJECTED_VAULT_SEALED",
            actor="ale",
            resource="ale_key",
            action="get_fernet",
            details={"reason": "vault_sealed"},
        )
    except Exception as exc:
        _logger.warning("Failed to emit ALE_REJECTED_VAULT_SEALED audit event: %s", exc)


def get_fernet() -> Fernet:
    """Return a Fernet instance for ALE encryption/decryption.

    Key selection (vault-only design — T48.5)
    ------------------------------------------
    The ALE key is **always** derived from the vault KEK via HKDF-SHA256.
    There is no ``ALE_KEY`` environment variable fallback.

    When the vault is sealed, :exc:`~synth_engine.shared.exceptions.VaultSealedError`
    is raised and an ``ALE_REJECTED_VAULT_SEALED`` audit event is emitted.
    Unsealing the vault (``POST /unseal``) is the only way to restore ALE
    functionality.

    This function is **not cached** so that it reflects the current vault
    state on every call.  HKDF is fast; the overhead is negligible.

    Returns:
        A :class:`cryptography.fernet.Fernet` instance ready for use.

    Raises:
        VaultSealedError: If the vault is sealed.  Call ``POST /unseal``
            before performing ALE operations.
    """
    if VaultState.is_sealed():
        _emit_ale_sealed_audit_event()
        raise VaultSealedError(
            "Vault is sealed — ALE operations require an unsealed vault. "
            "Call POST /unseal before performing ALE operations."
        )

    kek = VaultState.get_kek()
    raw_key = _derive_ale_key_from_kek(kek)
    return Fernet(base64.urlsafe_b64encode(raw_key))


def _reset_fernet_cache() -> None:
    """No-op retained for backward compatibility with pre-vault-wiring tests.

    Previously this function cleared an ``lru_cache`` on ``get_fernet()``.
    The cache was removed when vault KEK wiring was added (fix/P2-debt-D1)
    because caching across vault state changes is incorrect.  ``get_fernet()``
    now reflects the current vault state on every call, making a cache reset
    unnecessary.

    Calling this function is always safe; it has no side effects.
    """


class EncryptedString(TypeDecorator[str]):
    """SQLAlchemy TypeDecorator for transparent Fernet field-level encryption.

    On write (``process_bind_param``) the plaintext string is UTF-8 encoded
    and encrypted with the Fernet key derived from the vault KEK.  The
    resulting token is stored as a base64 URL-safe string.

    On read (``process_result_value``) the stored token is decrypted and
    returned as a UTF-8 decoded string.

    ``None`` values are passed through unchanged in both directions to
    support nullable database columns — no vault interaction occurs.

    Example::

        from sqlmodel import Field
        from sqlalchemy import Column
        from synth_engine.shared.security.ale import EncryptedString

        class Sensitive(BaseModel, table=True):
            token: str | None = Field(
                default=None, sa_column=Column(EncryptedString())
            )

    Raises:
        VaultSealedError: If the vault is sealed when any non-None value
            is processed.  ``None`` values pass through without vault access.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, _dialect: Dialect | None) -> str | None:
        """Encrypt *value* before writing to the database.

        Args:
            value: The plaintext string to encrypt, or ``None``.
            _dialect: The SQLAlchemy dialect in use (intentionally unused).

        Returns:
            A Fernet token encoded as a UTF-8 string, or ``None`` if
            *value* is ``None``.

        Raises:
            VaultSealedError: If the vault is sealed and *value* is not ``None``.
        """  # noqa: DOC502
        if value is None:
            return None
        fernet = get_fernet()
        token: bytes = fernet.encrypt(str(value).encode())
        return token.decode()

    def process_result_value(self, value: Any, _dialect: Dialect | None) -> str | None:
        """Decrypt *value* retrieved from the database.

        Args:
            value: The Fernet token string to decrypt, or ``None``.
            _dialect: The SQLAlchemy dialect in use (intentionally unused).

        Returns:
            The decrypted plaintext string, or ``None`` if *value* is
            ``None``.

        Raises:
            VaultSealedError: If the vault is sealed and *value* is not ``None``.
            cryptography.fernet.InvalidToken: If *value* is not a valid
                Fernet token for the current key — indicating corrupted or
                tampered ciphertext in the database.
        """  # noqa: DOC502
        if value is None:
            return None
        fernet = get_fernet()
        plaintext: bytes = fernet.decrypt(str(value).encode())
        return plaintext.decode()
