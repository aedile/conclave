"""Generic HMAC-SHA256 signing primitives for the Synthetic Data Engine.

Provides a :exc:`SecurityError` exception, a digest-size constant, and two
functions — :func:`compute_hmac` and :func:`verify_hmac` — that are shared
across modules requiring message-authentication-code operations.

These primitives were extracted from
``synth_engine.modules.synthesizer.models`` per ADR-0001 (shared/ is the
correct home for cross-cutting security utilities used by two or more
modules).

Design notes:
  - Constant-time comparison is enforced in :func:`verify_hmac` via
    :func:`hmac.compare_digest` to prevent timing-oracle attacks.
  - All functions are pure (no I/O, no state) so they are safe to call from
    any module without import-order concerns.

Task: P8-T8.2 — Security Hardening (architecture review — extract HMAC primitives)
ADR:  ADR-0001 (shared/ placement rules)
"""

from __future__ import annotations

import hashlib
import hmac

#: Size of the HMAC-SHA256 digest in bytes (fixed: 256 bits / 8 = 32 bytes).
HMAC_DIGEST_SIZE: int = 32


class SecurityError(Exception):
    """Raised when a security invariant is violated.

    Used to signal HMAC signature verification failures so that callers can
    distinguish a tampered or wrongly-keyed artifact from other I/O errors.
    Inherits from :exc:`Exception` so callers can catch it broadly or
    specifically.

    Example::

        try:
            artifact = ModelArtifact.load(path, signing_key=key)
        except SecurityError as exc:
            logger.error("Artifact tampering detected: %s", exc)
            raise
    """


def compute_hmac(key: bytes, data: bytes) -> bytes:
    """Compute HMAC-SHA256 over ``data`` using ``key``.

    Args:
        key: Raw signing key bytes.  Must be non-empty.
        data: The bytes to authenticate.

    Returns:
        32-byte raw HMAC-SHA256 digest.
    """
    return hmac.new(key, data, hashlib.sha256).digest()


def verify_hmac(key: bytes, data: bytes, expected_digest: bytes) -> bool:
    """Verify an HMAC-SHA256 digest using a constant-time comparison.

    Uses :func:`hmac.compare_digest` to prevent timing-oracle attacks.

    Args:
        key: Raw signing key bytes.
        data: The bytes over which the HMAC was originally computed.
        expected_digest: The 32-byte HMAC digest to verify against.

    Returns:
        ``True`` if the computed digest matches ``expected_digest``.
        ``False`` otherwise.
    """
    actual_digest = compute_hmac(key, data)
    return hmac.compare_digest(actual_digest, expected_digest)
