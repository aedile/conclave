"""Generic HMAC-SHA256 signing primitives for the Synthetic Data Engine.

Provides a :exc:`SecurityError` exception (an alias for
:exc:`~synth_engine.shared.exceptions.ArtifactTamperingError`),
digest-size constants, and functions for HMAC computation, verification,
versioned signing, and key rotation audit logging.

These primitives are shared across modules requiring
message-authentication-code operations.

These primitives were extracted from
``synth_engine.modules.synthesizer.models`` per ADR-0001 (shared/ is the
correct home for cross-cutting security utilities used by two or more
modules).

Design notes:
  - Constant-time comparison is enforced in :func:`verify_hmac` and
    :func:`verify_versioned` via :func:`hmac.compare_digest` to prevent
    timing-oracle attacks.
  - All functions are pure (no I/O, no state) so they are safe to call from
    any module without import-order concerns.
  - Versioned signatures use the format: ``KEY_ID (4 bytes) || HMAC (32 bytes)``.
    The legacy format is a bare 32-byte HMAC with no key ID prefix.
    :func:`verify_versioned` auto-detects the format by length.

Task: P8-T8.2 — Security Hardening (architecture review — extract HMAC primitives)
Task: P26-T26.2 — Exception Hierarchy (SecurityError aliased to ArtifactTamperingError)
Task: T42.1 — Artifact Signing Key Versioning (KEY_ID prefix, multi-key support)
ADR:  ADR-0001 (shared/ placement rules)
"""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING

from synth_engine.shared.exceptions import ArtifactTamperingError

if TYPE_CHECKING:
    from synth_engine.shared.security.audit import AuditLogger

#: Size of the HMAC-SHA256 digest in bytes (fixed: 256 bits / 8 = 32 bytes).
HMAC_DIGEST_SIZE: int = 32

#: Size of the key ID prefix in versioned signatures (4 bytes).
KEY_ID_SIZE: int = 4

#: Sentinel key ID for legacy (pre-versioning) signatures.
#: A 32-byte signature with no key ID prefix is treated as if it were
#: signed with this key ID, enabling backward-compatible verification.
LEGACY_KEY_ID: bytes = b"\x00\x00\x00\x00"

#: Total size of a versioned signature: KEY_ID prefix + HMAC digest.
VERSIONED_SIGNATURE_SIZE: int = KEY_ID_SIZE + HMAC_DIGEST_SIZE

#: Backward-compatible alias.  New code should use :exc:`ArtifactTamperingError`.
SecurityError = ArtifactTamperingError


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


def sign_versioned(key: bytes, key_id: bytes, data: bytes) -> bytes:
    """Compute a versioned HMAC-SHA256 signature over ``data``.

    The signature format is: ``KEY_ID (4 bytes) || HMAC-SHA256 (32 bytes)``.

    The key ID is embedded as-is (caller provides raw bytes).  Callers
    should convert string IDs to bytes using ``bytes.fromhex(key_id_hex)``.

    Args:
        key: Raw signing key bytes.
        key_id: 4-byte key identifier to embed as the signature prefix.
        data: The bytes to authenticate.

    Returns:
        36-byte signature: 4-byte key ID followed by 32-byte HMAC-SHA256.

    Raises:
        ValueError: If ``key_id`` is not exactly :data:`KEY_ID_SIZE` bytes.
    """
    if len(key_id) != KEY_ID_SIZE:
        raise ValueError(f"key_id must be exactly {KEY_ID_SIZE} bytes; got {len(key_id)}")
    digest = compute_hmac(key, data)
    return key_id + digest


def verify_versioned(
    key_map: dict[bytes, bytes],
    data: bytes,
    signature: bytes,
) -> bool:
    """Verify a signature against a map of key IDs to keys.

    Supports two signature formats:

    - **Versioned** (36 bytes): ``KEY_ID (4 bytes) || HMAC-SHA256 (32 bytes)``.
      The key ID is extracted from the prefix and looked up in ``key_map``.

    - **Legacy** (32 bytes): A bare HMAC-SHA256 digest with no key ID.
      Treated as if it were signed with :data:`LEGACY_KEY_ID`.  The
      :data:`LEGACY_KEY_ID` key must be present in ``key_map`` for legacy
      artifacts to verify.

    Any other signature length returns ``False`` immediately.

    Constant-time comparison via :func:`hmac.compare_digest` prevents
    timing-oracle attacks regardless of format.

    Args:
        key_map: Mapping from raw 4-byte key ID bytes to raw key bytes.
            Should contain all known signing keys (active + retired).
        data: The bytes over which the signature was computed.
        signature: The raw signature bytes to verify.

    Returns:
        ``True`` if the signature is valid for any key in ``key_map``.
        ``False`` if the signature is invalid, malformed, or the embedded
        key ID is absent from ``key_map``.
    """
    if len(signature) == VERSIONED_SIGNATURE_SIZE:
        # Versioned format: extract the 4-byte key ID prefix
        key_id = signature[:KEY_ID_SIZE]
        stored_digest = signature[KEY_ID_SIZE:]
    elif len(signature) == HMAC_DIGEST_SIZE:
        # Legacy format: no key ID prefix — treat as LEGACY_KEY_ID
        key_id = LEGACY_KEY_ID
        stored_digest = signature
    else:
        # Unknown format — reject without leaking timing information
        return False

    key = key_map.get(key_id)
    if key is None:
        return False

    actual_digest = compute_hmac(key, data)
    return hmac.compare_digest(actual_digest, stored_digest)


def log_key_rotation_event(
    *,
    audit_logger: AuditLogger,
    old_key_id: str,
    new_key_id: str,
    actor: str,
) -> None:
    """Log a key rotation event to the WORM audit trail.

    Emits a signed ``KEY_ROTATION`` audit event recording the transition
    from ``old_key_id`` to ``new_key_id``.  The event is HMAC-signed and
    chained by the :class:`~synth_engine.shared.security.audit.AuditLogger`
    so it cannot be silently removed.

    Args:
        audit_logger: The :class:`~synth_engine.shared.security.audit.AuditLogger`
            instance (or a duck-typed compatible object in tests).
        old_key_id: Hex string of the previously active signing key ID.
        new_key_id: Hex string of the newly active signing key ID.
        actor: Identity of the principal initiating the rotation.
    """
    audit_logger.log_event(
        event_type="KEY_ROTATION",
        actor=actor,
        resource="artifact_signing_key",
        action="rotate",
        details={
            "old_key_id": old_key_id,
            "new_key_id": new_key_id,
        },
    )
