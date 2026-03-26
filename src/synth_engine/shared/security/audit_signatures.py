"""HMAC signature computation functions for audit events.

Provides standalone functions for computing v1, v2, and v3 HMAC-SHA256
signatures over audit event fields.  These functions are extracted from
:class:`~synth_engine.shared.security.audit_logger.AuditLogger` so they
can be tested independently and are not tied to the stateful logger
lifecycle.

Signature format versioning
----------------------------
- ``v1:<hex>`` — Legacy format.  HMAC over pipe-delimited fields; details
  NOT included.  Backward-compatible verification only.
- ``v2:<hex>`` — Superseded format.  HMAC over pipe-delimited fields plus
  canonical details JSON.  Latent pipe-delimiter injection vulnerability
  (ADV-P53-01).  Backward-compatible verification only.
- ``v3:<hex>`` — Current format.  Length-prefixed field encoding eliminates
  the pipe-delimiter injection vulnerability.  Used for all new events.

No imports from ``audit_logger.py`` or ``audit_singleton.py`` — this module
is a pure-function leaf with no circular-import risk.

CONSTITUTION Priority 0: Security
Task: T58.4 — Split audit.py into signatures/logger/singleton
"""

from __future__ import annotations

import hashlib
import hmac
import json

#: Maximum byte length of canonical details JSON (64 KB).
#: Enforced in _sign_v2 and _sign_v3 to prevent OOM via unbounded detail payloads.
_DETAILS_MAX_BYTES: int = 64 * 1024  # 64 KB


def sign_v1(
    audit_key: bytes,
    timestamp: str,
    event_type: str,
    actor: str,
    resource: str,
    action: str,
    prev_hash: str,
) -> str:
    """Compute legacy v1 HMAC-SHA256 over the canonical pipe-delimited message.

    The v1 format does NOT include details in the signed payload.  Supported
    solely for backward-compatible verification of events written before the
    T53.2 upgrade.

    Args:
        audit_key: Raw 32-byte HMAC signing key.
        timestamp: ISO-8601 UTC timestamp.
        event_type: Short uppercase event identifier.
        actor: Principal identity.
        resource: Affected resource.
        action: Action verb.
        prev_hash: SHA-256 hex of the previous event's JSON.

    Returns:
        Versioned signature string ``v1:<hex>``.
    """
    message = f"{timestamp}|{event_type}|{actor}|{resource}|{action}|{prev_hash}"
    hex_digest = hmac.new(audit_key, message.encode(), hashlib.sha256).hexdigest()
    return f"v1:{hex_digest}"


def sign_v2(
    audit_key: bytes,
    timestamp: str,
    event_type: str,
    actor: str,
    resource: str,
    action: str,
    prev_hash: str,
    details: dict[str, str],
) -> str:
    """Compute v2 HMAC-SHA256 including the canonical details JSON.

    The version string ``v2`` is included as the first HMAC message component
    to prevent downgrade attacks.  Canonical details serialization uses
    ``json.dumps`` with ``sort_keys=True`` and compact separators.

    Supported for backward-compatible verification only.  New events use
    :func:`sign_v3` to eliminate the pipe-delimiter injection vulnerability
    (ADV-P53-01).

    Args:
        audit_key: Raw 32-byte HMAC signing key.
        timestamp: ISO-8601 UTC timestamp.
        event_type: Short uppercase event identifier.
        actor: Principal identity.
        resource: Affected resource.
        action: Action verb.
        prev_hash: SHA-256 hex of the previous event's JSON.
        details: Arbitrary string key-value metadata.

    Returns:
        Versioned signature string ``v2:<hex>``.

    Raises:
        ValueError: If the canonical details JSON exceeds 64 KB, or if
            ``details`` contains non-JSON-serializable values
            (e.g. ``float('nan')``).
    """
    details_json = json.dumps(details, sort_keys=True, separators=(",", ":"), allow_nan=False)
    details_bytes = details_json.encode("utf-8")
    if len(details_bytes) > _DETAILS_MAX_BYTES:
        raise ValueError(
            f"Audit event details exceed the maximum allowed size of "
            f"{_DETAILS_MAX_BYTES} bytes "
            f"(got {len(details_bytes)} bytes). "
            "Reduce the number of keys or value lengths in details."
        )
    message = f"v2|{timestamp}|{event_type}|{actor}|{resource}|{action}|{prev_hash}|" + details_json
    hex_digest = hmac.new(audit_key, message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"v2:{hex_digest}"


def sign_v3(
    audit_key: bytes,
    timestamp: str,
    event_type: str,
    actor: str,
    resource: str,
    action: str,
    prev_hash: str,
    details: dict[str, str],
) -> str:
    """Compute v3 HMAC-SHA256 using length-prefixed fields.

    Eliminates the pipe-delimiter injection vulnerability present in v1 and v2
    formats (ADV-P53-01).  Each field is encoded as a 4-byte big-endian
    unsigned integer (the field's UTF-8 byte length) followed by the field's
    UTF-8 bytes.  Field boundaries are unambiguous regardless of field content.

    The ``b"v3"`` version literal is prepended to the assembled bytes (not
    merely used as a stored prefix), preventing version-stripping downgrade
    attacks.

    Field encoding order: timestamp, event_type, actor, resource, action,
    prev_hash, details_json.

    Args:
        audit_key: Raw 32-byte HMAC signing key.
        timestamp: ISO-8601 UTC timestamp.
        event_type: Short uppercase event identifier.
        actor: Principal identity.
        resource: Affected resource.
        action: Action verb.
        prev_hash: SHA-256 hex of the previous event's JSON.
        details: Arbitrary string key-value metadata.

    Returns:
        Versioned signature string ``v3:<hex>``.

    Raises:
        ValueError: If the canonical details JSON exceeds 64 KB, or if
            ``details`` contains non-JSON-serializable values
            (e.g. ``float('nan')``).
    """
    details_json = json.dumps(details, sort_keys=True, separators=(",", ":"), allow_nan=False)
    details_bytes = details_json.encode("utf-8")
    if len(details_bytes) > _DETAILS_MAX_BYTES:
        raise ValueError(
            f"Audit event details exceed the maximum allowed size of "
            f"{_DETAILS_MAX_BYTES} bytes "
            f"(got {len(details_bytes)} bytes). "
            "Reduce the number of keys or value lengths in details."
        )

    # Build the length-prefixed message.  b"v3" is the version sentinel —
    # it is part of the HMAC input, not just the stored prefix, preventing
    # version-stripping downgrade attacks.
    parts: list[bytes] = [b"v3"]
    for field_value in (timestamp, event_type, actor, resource, action, prev_hash, details_json):
        encoded = field_value.encode("utf-8")
        parts.append(len(encoded).to_bytes(4, "big"))
        parts.append(encoded)
    message = b"".join(parts)

    hex_digest = hmac.new(audit_key, message, hashlib.sha256).hexdigest()
    return f"v3:{hex_digest}"
