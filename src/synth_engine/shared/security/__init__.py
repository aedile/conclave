"""Shared security utilities for the Conclave Engine.

This sub-package provides cross-cutting security primitives used by two
or more modules, currently:

- :mod:`synth_engine.shared.security.ale` — Application-Level Encryption
  (Fernet-based ``EncryptedString`` SQLAlchemy TypeDecorator).
- :mod:`synth_engine.shared.security.hmac_signing` — Generic HMAC-SHA256
  signing primitives: :exc:`SecurityError`, :func:`compute_hmac`,
  :func:`verify_hmac`, and :data:`HMAC_DIGEST_SIZE`.
"""

from synth_engine.shared.security.hmac_signing import (
    HMAC_DIGEST_SIZE,
    SecurityError,
    compute_hmac,
    verify_hmac,
)

__all__ = [
    "HMAC_DIGEST_SIZE",
    "SecurityError",
    "compute_hmac",
    "verify_hmac",
]
