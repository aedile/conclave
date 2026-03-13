"""Application-Level Encryption (ALE) for the Conclave Engine.

Provides a Fernet-based ``EncryptedString`` SQLAlchemy ``TypeDecorator``
that transparently encrypts string values before writing them to the
database and decrypts them on read.  The Fernet symmetric key is sourced
exclusively from the ``ALE_KEY`` environment variable, which must be
provisioned at runtime (never baked into the image or committed to VCS).

Security properties
-------------------
- Symmetric encryption via ``cryptography.fernet.Fernet`` (AES-128-CBC +
  HMAC-SHA256) provides authenticated encryption with associated data
  (AEAD) semantics.
- The key is loaded once at startup via an ``lru_cache``-backed factory;
  subsequent calls incur no environment-variable look-up overhead.
- ``_reset_fernet_cache()`` is provided for test isolation only and is
  **not** part of the public API.

Usage
-----
Annotate any SQLModel / SQLAlchemy column with ``EncryptedString`` to
enable transparent field-level encryption::

    from synth_engine.shared.security.ale import EncryptedString

    class Patient(BaseModel, table=True):
        ssn: str = Field(sa_column=Column(EncryptedString()))

CONSTITUTION Priority 0: Security
Task: P2-T2.2 — Secure Database Layer
"""

from __future__ import annotations

import functools
import os
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import String
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator


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


@functools.lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    """Return the singleton Fernet instance backed by the ALE_KEY env var.

    The instance is cached after the first call so that subsequent
    encrypt/decrypt operations pay no environment-variable lookup cost.

    Returns:
        A :class:`cryptography.fernet.Fernet` instance ready for use.

    Raises:
        RuntimeError: If the ``ALE_KEY`` environment variable is not set.
    """
    key = os.environ.get("ALE_KEY")
    if not key:
        raise RuntimeError("ALE_KEY environment variable not set")
    return Fernet(key.encode())


def _reset_fernet_cache() -> None:
    """Clear the ``get_fernet`` LRU cache.

    **For test isolation only.**  Clears the cached Fernet instance so
    that changes to the ``ALE_KEY`` environment variable in monkeypatched
    test fixtures take effect immediately.
    """
    get_fernet.cache_clear()


class EncryptedString(TypeDecorator[str]):
    """SQLAlchemy TypeDecorator for transparent Fernet field-level encryption.

    On write (``process_bind_param``) the plaintext string is UTF-8 encoded
    and encrypted with the Fernet key sourced from ``ALE_KEY``.  The
    resulting token is stored as a base64 URL-safe string.

    On read (``process_result_value``) the stored token is decrypted and
    returned as a UTF-8 decoded string.

    ``None`` values are passed through unchanged in both directions to
    support nullable database columns.

    Example::

        from sqlmodel import Field
        from sqlalchemy import Column
        from synth_engine.shared.security.ale import EncryptedString

        class Sensitive(BaseModel, table=True):
            token: str | None = Field(
                default=None, sa_column=Column(EncryptedString())
            )
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect | None) -> str | None:
        """Encrypt *value* before writing to the database.

        Args:
            value: The plaintext string to encrypt, or ``None``.
            dialect: The SQLAlchemy dialect in use (ignored).

        Returns:
            A Fernet token encoded as a UTF-8 string, or ``None`` if
            *value* is ``None``.
        """
        if value is None:
            return None
        fernet = get_fernet()
        token: bytes = fernet.encrypt(str(value).encode())
        return token.decode()

    def process_result_value(self, value: Any, dialect: Dialect | None) -> str | None:
        """Decrypt *value* retrieved from the database.

        Args:
            value: The Fernet token string to decrypt, or ``None``.
            dialect: The SQLAlchemy dialect in use (ignored).

        Returns:
            The decrypted plaintext string, or ``None`` if *value* is
            ``None``.
        """
        if value is None:
            return None
        fernet = get_fernet()
        plaintext: bytes = fernet.decrypt(str(value).encode())
        return plaintext.decode()
