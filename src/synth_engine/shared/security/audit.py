"""Cryptographically signed WORM audit logger.

Every audit event is HMAC-SHA256 signed using a dedicated AUDIT_KEY
(separate from ALE_KEY and JWT_SECRET_KEY). Events form a hash chain:
each event includes the SHA-256 hash of the previous event's JSON,
making the log tamper-evident.

Security properties
-------------------
- Dedicated ``AUDIT_KEY`` (hex-encoded 32 bytes) — separate from ALE and
  JWT keys so a compromise of one key does not affect audit integrity.
- HMAC-SHA256 signatures on the fields that encode *what happened* and
  *in what order*, making individual event forgery computationally
  infeasible.
- Hash-chain linking via ``prev_hash`` (SHA-256 of the previous event's
  JSON) means an adversary cannot silently delete or reorder events.
- ``hmac.compare_digest`` for signature verification prevents
  timing-oracle attacks.
- Events are emitted to ``logging.getLogger("synth_engine.security.audit")``
  at INFO level; log shipping to an append-only store (WORM) is an
  operational concern outside the scope of this module.
- A module-level singleton (protected by ``threading.Lock``) ensures the
  hash chain is continuous across all requests for the lifetime of the
  process.  See ADR-0010 for full rationale.

CONSTITUTION Priority 0: Security
Task: P2-T2.4 — Vault Observability
Task: P2-D3  — AuditLogger singleton & cross-request chain integrity
Task: T36.1 — Centralize Configuration Into Pydantic Settings Model
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
from datetime import UTC, datetime

from pydantic import BaseModel

from synth_engine.shared.settings import get_settings

_AUDIT_LOGGER_NAME = "synth_engine.security.audit"
_GENESIS_HASH = "0" * 64

# ---------------------------------------------------------------------------
# Module-level singleton state
# ---------------------------------------------------------------------------

_audit_logger_instance: AuditLogger | None = None
_audit_logger_lock: threading.Lock = threading.Lock()


class AuditEvent(BaseModel):
    """A single immutable audit record.

    Attributes:
        timestamp: ISO-8601 UTC timestamp of the event.
        event_type: Short uppercase identifier (e.g. ``"VAULT_UNSEAL"``).
        actor: Identity of the principal that performed the action.
        resource: Logical resource affected.
        action: Verb describing what was done (e.g. ``"unseal"``).
        details: Arbitrary string key-value metadata.  Callers MUST NOT
            pass PII field values here; the field is intentionally
            unstructured but its contents are written to the audit log.
        prev_hash: SHA-256 hex of the previous event's JSON, or the
            genesis sentinel ``"0" * 64`` for the first event.
        signature: HMAC-SHA256 hex over the canonical message fields.
    """

    timestamp: str
    event_type: str
    actor: str
    resource: str
    action: str
    details: dict[str, str]
    prev_hash: str
    signature: str


class AuditLogger:
    """Stateful WORM audit logger with HMAC signatures and hash chaining.

    Instances are normally obtained via :func:`get_audit_logger` which
    returns the module-level singleton.  Direct instantiation is
    reserved for unit tests that need an isolated chain.

    Args:
        audit_key: Raw 32-byte HMAC signing key.
    """

    def __init__(self, audit_key: bytes) -> None:
        self._audit_key = audit_key
        self._prev_hash: str = _GENESIS_HASH
        self._log = logging.getLogger(_AUDIT_LOGGER_NAME)
        self._lock: threading.Lock = threading.Lock()

    def _sign(
        self,
        timestamp: str,
        event_type: str,
        actor: str,
        resource: str,
        action: str,
        prev_hash: str,
    ) -> str:
        """Compute HMAC-SHA256 over the canonical pipe-delimited message.

        The signature covers every field that records *what happened*
        (event_type, actor, resource, action) plus *when* (timestamp) and
        *where in the chain* (prev_hash), binding the event to its
        position in the audit log.

        Args:
            timestamp: ISO-8601 UTC timestamp.
            event_type: Short uppercase event identifier.
            actor: Principal identity.
            resource: Affected resource.
            action: Action verb.
            prev_hash: SHA-256 hex of the previous event's JSON.

        Returns:
            Lowercase hex-encoded HMAC-SHA256 digest.
        """
        message = f"{timestamp}|{event_type}|{actor}|{resource}|{action}|{prev_hash}"
        return hmac.new(self._audit_key, message.encode(), hashlib.sha256).hexdigest()

    def log_event(
        self,
        *,
        event_type: str,
        actor: str,
        resource: str,
        action: str,
        details: dict[str, str],
    ) -> AuditEvent:
        """Create, sign, chain, and emit an audit event.

        Builds the event against the current chain head (``_prev_hash``),
        signs it, advances the chain, and logs the JSON representation
        to ``synth_engine.security.audit`` at INFO level.

        This method is thread-safe: an instance-level ``threading.Lock``
        serialises concurrent callers, preserving chain order even when
        called from multiple async route handlers.

        Args:
            event_type: Short uppercase identifier for the event category.
            actor: Identity of the principal performing the action.
            resource: Logical resource being acted upon.
            action: Verb describing the action.
            details: Arbitrary string metadata for the event.  Callers
                MUST NOT pass PII field values here.

        Returns:
            The constructed and signed :class:`AuditEvent`.
        """
        with self._lock:
            timestamp = datetime.now(UTC).isoformat()
            signature = self._sign(timestamp, event_type, actor, resource, action, self._prev_hash)

            event = AuditEvent(
                timestamp=timestamp,
                event_type=event_type,
                actor=actor,
                resource=resource,
                action=action,
                details=details,
                prev_hash=self._prev_hash,
                signature=signature,
            )

            # Advance the chain: next event's prev_hash = SHA-256 of this event's JSON
            self._prev_hash = hashlib.sha256(event.model_dump_json().encode()).hexdigest()

            self._log.info(event.model_dump_json())
            return event

    def verify_event(self, event: AuditEvent) -> bool:
        """Verify that *event*'s signature was produced by this logger's key.

        Recomputes the expected signature from the event's fields and
        compares using ``hmac.compare_digest`` to prevent timing attacks.

        Args:
            event: The :class:`AuditEvent` to verify.

        Returns:
            ``True`` if the signature is valid, ``False`` otherwise.
        """
        expected = self._sign(
            event.timestamp,
            event.event_type,
            event.actor,
            event.resource,
            event.action,
            event.prev_hash,
        )
        return hmac.compare_digest(expected, event.signature)


def _load_audit_key() -> bytes:
    """Read and validate ``AUDIT_KEY`` from :attr:`ConclaveSettings.audit_key`.

    Returns:
        Raw 32-byte HMAC signing key decoded from the hex env var.

    Raises:
        ValueError: If ``AUDIT_KEY`` is absent, wrong length, or not
            valid hexadecimal.
    """
    raw = get_settings().audit_key
    if not raw:
        raise ValueError(
            "AUDIT_KEY environment variable is not set. "
            'Generate with: python3 -c "import os; print(os.urandom(32).hex())"'
        )
    if len(raw) != 64:
        raise ValueError(
            f"AUDIT_KEY must be a 64-character hex string (32 bytes); got {len(raw)} characters."
        )
    try:
        return bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(
            f"AUDIT_KEY must be a valid hex string (0-9, a-f); got invalid characters: {exc}"
        ) from exc


def get_audit_logger() -> AuditLogger:
    """Return the module-level AuditLogger singleton.

    The hash chain is maintained across **all** calls for the lifetime of
    the process.  Each call to :meth:`~AuditLogger.log_event` links to the
    previous event's hash, so deleting or reordering events across any number
    of HTTP requests is detectable by a chain-integrity check.

    The singleton is initialised on first call using :envvar:`AUDIT_KEY`.
    Subsequent calls return the same object without re-reading the
    environment.  Module-level creation is protected by a
    :class:`threading.Lock` to be safe under concurrent first-call
    scenarios.

    Returns:
        The process-wide :class:`AuditLogger` instance.

    """
    global _audit_logger_instance
    with _audit_logger_lock:
        if _audit_logger_instance is None:
            _audit_logger_instance = AuditLogger(_load_audit_key())
        return _audit_logger_instance


def reset_audit_logger() -> None:
    """Reset the module-level AuditLogger singleton to ``None``.

    The next call to :func:`get_audit_logger` will create a fresh instance
    whose hash chain begins at genesis (``prev_hash = "0" * 64``).

    Warning:
        This function exists **solely for test isolation**.  Calling it in
        production code destroys cross-request chain continuity and defeats
        the tamper-evidence guarantee.
    """
    global _audit_logger_instance
    with _audit_logger_lock:
        _audit_logger_instance = None
