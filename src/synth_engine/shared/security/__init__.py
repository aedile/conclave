"""Shared security utilities for the Conclave Engine.

This sub-package provides cross-cutting security primitives used by two
or more modules, currently:

- :mod:`synth_engine.shared.security.ale` — Application-Level Encryption
  (Fernet-based ``EncryptedString`` SQLAlchemy TypeDecorator).
- :mod:`synth_engine.shared.security.hmac_signing` — Generic HMAC-SHA256
  signing primitives: :exc:`SecurityError`, :func:`compute_hmac`,
  :func:`verify_hmac`, :data:`HMAC_DIGEST_SIZE`,
  :data:`KEY_ID_SIZE`, :data:`LEGACY_KEY_ID`,
  :func:`sign_versioned`, :func:`verify_versioned`,
  :func:`build_key_map_from_settings`,
  :func:`log_key_rotation_event`.
"""

from synth_engine.shared.security.hmac_signing import (
    HMAC_DIGEST_SIZE,
    KEY_ID_SIZE,
    LEGACY_KEY_ID,
    VERSIONED_SIGNATURE_SIZE,
    SecurityError,
    build_key_map_from_settings,
    compute_hmac,
    log_key_rotation_event,
    sign_versioned,
    verify_hmac,
    verify_versioned,
)

__all__ = [
    "HMAC_DIGEST_SIZE",
    "KEY_ID_SIZE",
    "LEGACY_KEY_ID",
    "VERSIONED_SIGNATURE_SIZE",
    "SecurityError",
    "build_key_map_from_settings",
    "compute_hmac",
    "log_key_rotation_event",
    "sign_versioned",
    "verify_hmac",
    "verify_versioned",
]
