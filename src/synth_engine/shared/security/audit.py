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
- After each event is logged, ``AnchorManager.maybe_anchor`` is called
  (best-effort) to publish periodic tamper-evident anchors to an
  external store.  Anchoring failures never block event logging.

Chain continuity across restarts (T55.3)
-----------------------------------------
On startup, :class:`AuditLogger` reads the last anchor record from the
anchor JSONL file (the same file written by :class:`LocalFileAnchorBackend`).
If a persisted chain head is found, the logger resumes from it:

- ``_prev_hash`` is set to the persisted ``chain_head_hash``.
- ``_entry_count`` is set to the persisted ``entry_count``.
- A ``CHAIN_RESUMED`` audit event is emitted recording the loaded state.

If no anchor file path is provided, the file is missing, empty, or
corrupt, the logger starts from genesis (``_prev_hash = "0" * 64``,
``_entry_count = 0``).  Corrupt/unreadable files emit a WARNING.

This ensures the tamper-evident hash chain is continuous across process
restarts.  Without this feature, each restart would create a new chain
starting from genesis — creating gaps that attackers could exploit to
silently delete events between the last anchor and the restart.

Signature format versioning (T53.2)
------------------------------------
Signatures use a versioned ``<version>:<hex>`` format:

- ``v1:<hex>`` — Legacy format.  HMAC is computed over
  ``timestamp|event_type|actor|resource|action|prev_hash``.
  The ``details`` field is NOT included in the signed payload.
  Supported for backward-compatible verification only.

- ``v2:<hex>`` — Superseded format.  HMAC is computed over
  ``v2|timestamp|event_type|actor|resource|action|prev_hash|<details_json>``
  where ``<details_json>`` is the canonical JSON serialization of the
  details dict (``json.dumps(details, sort_keys=True, separators=(",", ":"))``,
  UTF-8 encoded).  The version string ``v2`` is included IN the HMAC
  computation (not just the prefix) to prevent version downgrade attacks.
  Details payloads exceeding 64 KB (canonical UTF-8) are rejected.
  Supported for backward-compatible verification only (ADV-P53-01: latent
  pipe-delimiter injection vulnerability — use v3 for new events).

- ``v3:<hex>`` — Current format.  Uses length-prefixed encoding to eliminate
  the pipe-delimiter injection vulnerability (ADV-P53-01).  Each field is
  encoded as a 4-byte big-endian length followed by the field's UTF-8 bytes.
  The ``b"v3"`` version literal is prepended to the message bytes (included
  IN the HMAC, not just the prefix) to prevent downgrade attacks.  Canonical
  details JSON is included verbatim.  Details payloads exceeding 64 KB are
  rejected.

All new events use ``v3``.  ``verify_event`` dispatches on the stored
prefix and fails-closed on unknown versions.  v1 and v2 events remain
verifiable for backward compatibility.

CONSTITUTION Priority 0: Security
Task: P2-T2.4 — Vault Observability
Task: P2-D3  — AuditLogger singleton & cross-request chain integrity
Task: T36.1 — Centralize Configuration Into Pydantic Settings Model
Task: T48.4 — Immutable Audit Trail Anchoring (Rule 8 wiring)
Task: T53.2 — Audit HMAC: Include Details Field in Signature
Task: T55.3 — Audit Chain Continuity Across Restarts
Task: ADV-P53-01 — HMAC pipe-delimiter injection fix (length-prefixed v3 format)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from datetime import UTC, datetime

from prometheus_client import Counter
from pydantic import BaseModel

from synth_engine.shared.settings import get_settings

_AUDIT_LOGGER_NAME = "synth_engine.security.audit"
_GENESIS_HASH = "0" * 64

# Maximum byte length of canonical details JSON (64 KB).
# Enforced in _sign_v2 and _sign_v3 to prevent OOM via unbounded detail payloads.
_DETAILS_MAX_BYTES = 64 * 1024  # 64 KB

# ---------------------------------------------------------------------------
# ADV-P55-04 — Prometheus counter for audit chain resume failures.
# Incremented when _resume_from_anchor() falls back to genesis due to a
# corrupt, unreadable, or invalid anchor file (excludes normal first-boot
# cases where no anchor file exists yet).
# ---------------------------------------------------------------------------
AUDIT_CHAIN_RESUME_FAILURE_TOTAL: Counter = Counter(
    "audit_chain_resume_failure_total",
    "Total number of audit chain resume failures that forced a genesis "
    "restart (corrupt/invalid anchor file — excludes normal first-boot).",
)

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
        signature: Versioned HMAC-SHA256 signature.  Format is
            ``v1:<hex>`` (legacy, details not signed),
            ``v2:<hex>`` (superseded, details included, pipe-delimiter
            vulnerable), or ``v3:<hex>`` (current, length-prefixed,
            collision-resistant).
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

    On initialization, if ``anchor_file_path`` is provided (or falls back
    to the default from settings), the logger attempts to resume the chain
    from the last persisted anchor record.  This ensures chain continuity
    across process restarts (T55.3).

    Args:
        audit_key: Raw 32-byte HMAC signing key.
        anchor_file_path: Optional path to the anchor JSONL file used to
            resume the chain on restart.  When ``None``, the logger starts
            from genesis (backward-compatible).  When provided, the logger
            reads the last line of the file to initialize ``_prev_hash``
            and ``_entry_count``.
    """

    def __init__(
        self,
        audit_key: bytes,
        anchor_file_path: str | None = None,
    ) -> None:
        self._audit_key = audit_key
        self._prev_hash: str = _GENESIS_HASH
        self._entry_count: int = 0
        self._log = logging.getLogger(_AUDIT_LOGGER_NAME)
        self._lock: threading.Lock = threading.Lock()
        self._anchor_file_path: str | None = anchor_file_path

        # Attempt to resume from persisted anchor state (T55.3).
        # Failure is non-fatal: starts from genesis with a WARNING.
        if anchor_file_path is not None:
            self._resume_from_anchor()

    def _load_persisted_chain_head(self) -> tuple[str, int] | None:
        """Read the last anchor record from the anchor JSONL file.

        Reads the anchor file and returns the ``chain_head_hash`` and
        ``entry_count`` from the last non-empty line.  The last line is
        used because :class:`LocalFileAnchorBackend` appends records
        chronologically — the last record is always the most recent.

        Returns:
            A tuple ``(chain_head_hash, entry_count)`` from the last anchor
            record, or ``None`` if:

            - The file does not exist (first boot).
            - The file is empty (first boot).
            - The file is corrupt/unreadable (fail-safe → genesis).

            A WARNING is logged for corrupt/unreadable files but NOT for
            missing or empty files (which are expected on first boot).

        """
        if self._anchor_file_path is None:
            return None

        try:
            with open(self._anchor_file_path, encoding="utf-8") as fh:
                content = fh.read()
        except FileNotFoundError:
            # First boot — no anchor file yet.  Normal, not a warning.
            return None
        except OSError as exc:
            AUDIT_CHAIN_RESUME_FAILURE_TOTAL.inc()
            self._log.warning(
                "AUDIT_CHAIN_CONTINUITY: could not read anchor file '%s': %s. "
                "Starting from genesis.",
                self._anchor_file_path,
                type(exc).__name__,
            )
            return None

        # Find the last non-empty line.
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            # Empty file — first boot scenario.
            return None

        last_line = lines[-1]
        try:
            record = json.loads(last_line)
            chain_head_hash: str = record["chain_head_hash"]
            entry_count: int = int(record["entry_count"])
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            AUDIT_CHAIN_RESUME_FAILURE_TOTAL.inc()
            self._log.warning(
                "AUDIT_CHAIN_CONTINUITY: anchor file '%s' is corrupt (last line: %r): %s. "
                "Starting from genesis.",
                self._anchor_file_path,
                last_line[:80],  # truncate to avoid leaking large content
                type(exc).__name__,
            )
            return None

        # Validate chain_head_hash before returning (red-team F1: T55 review)
        from synth_engine.shared.security.audit_anchor import _validate_chain_head_hash

        try:
            _validate_chain_head_hash(chain_head_hash)
        except ValueError as exc:
            AUDIT_CHAIN_RESUME_FAILURE_TOTAL.inc()
            self._log.warning(
                "AUDIT_CHAIN_CONTINUITY: anchor file '%s' chain_head_hash failed validation: %s. "
                "Starting from genesis.",
                self._anchor_file_path,
                type(exc).__name__,
            )
            return None

        if not isinstance(entry_count, int) or entry_count < 1:
            AUDIT_CHAIN_RESUME_FAILURE_TOTAL.inc()
            self._log.warning(
                "AUDIT_CHAIN_CONTINUITY: anchor file '%s' entry_count invalid: %r. "
                "Starting from genesis.",
                self._anchor_file_path,
                entry_count,
            )
            return None

        return chain_head_hash, entry_count

    def _resume_from_anchor(self) -> None:
        """Load persisted chain state and emit CHAIN_RESUMED event.

        Called during ``__init__`` when ``anchor_file_path`` is set.
        Loads the last anchor record and, if found, sets ``_prev_hash``
        and ``_entry_count`` to the persisted values.

        After setting the chain state, emits a ``CHAIN_RESUMED`` audit
        event so the chain records when and from where the logger resumed.
        This event itself advances the chain: ``_entry_count`` becomes
        ``persisted_entry_count + 1`` after this call.

        If no persisted state exists (first boot), this method is a no-op.
        """
        result = self._load_persisted_chain_head()
        if result is None:
            # First boot or failed load — stay at genesis.
            return

        persisted_hash, persisted_count = result
        self._prev_hash = persisted_hash
        self._entry_count = persisted_count

        # Emit a CHAIN_RESUMED event to record the resumption in the chain.
        # This is done outside _load_persisted_chain_head to keep that method
        # pure (no side effects).
        self.log_event(
            event_type="CHAIN_RESUMED",
            actor="system",
            resource="audit_chain",
            action="resume",
            details={
                "resumed_from_hash": persisted_hash,
                "resumed_from_entry_count": str(persisted_count),
                "anchor_file": self._anchor_file_path or "",
            },
        )

    def _sign_v1(
        self,
        timestamp: str,
        event_type: str,
        actor: str,
        resource: str,
        action: str,
        prev_hash: str,
    ) -> str:
        """Compute legacy v1 HMAC-SHA256 over the canonical pipe-delimited message.

        The v1 format does NOT include details in the signed payload.  It is
        supported solely for backward-compatible verification of events written
        before the T53.2 upgrade.

        Args:
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
        hex_digest = hmac.new(self._audit_key, message.encode(), hashlib.sha256).hexdigest()
        return f"v1:{hex_digest}"

    def _sign_v2(
        self,
        timestamp: str,
        event_type: str,
        actor: str,
        resource: str,
        action: str,
        prev_hash: str,
        details: dict[str, str],
    ) -> str:
        """Compute v2 HMAC-SHA256 including the canonical details JSON.

        The version string ``v2`` is included as the first component of the
        HMAC message (not only as a stored prefix) to prevent downgrade attacks
        where an adversary strips details and relabels the signature as v1.

        Canonical details serialization uses ``json.dumps`` with
        ``sort_keys=True`` and compact separators to ensure determinism
        regardless of insertion order.  ``allow_nan=False`` rejects
        ``float('nan')`` and ``float('inf')``.

        Supported for backward-compatible verification of existing log entries.
        New events use :meth:`_sign_v3` to eliminate the pipe-delimiter
        injection vulnerability (ADV-P53-01).

        Args:
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
        message = (
            f"v2|{timestamp}|{event_type}|{actor}|{resource}|{action}|{prev_hash}|" + details_json
        )
        hex_digest = hmac.new(self._audit_key, message.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"v2:{hex_digest}"

    def _sign_v3(
        self,
        timestamp: str,
        event_type: str,
        actor: str,
        resource: str,
        action: str,
        prev_hash: str,
        details: dict[str, str],
    ) -> str:
        """Compute v3 HMAC-SHA256 using length-prefixed fields.

        Eliminates the pipe-delimiter injection vulnerability present in v1
        and v2 formats (ADV-P53-01).  In those formats, a literal ``|``
        inside any field shifts the field boundary and can produce collisions:
        ``actor="foo|bar", resource="baz"`` and ``actor="foo",
        resource="bar|baz"`` produce identical byte payloads.

        The v3 format encodes each field as a 4-byte big-endian unsigned
        integer (the field's UTF-8 byte length) followed by the field's
        UTF-8 bytes.  Because the length is encoded before the content, field
        boundaries are unambiguous regardless of field content.

        The ``b"v3"`` version literal is prepended to the assembled bytes
        (not merely used as a stored prefix), so stripping the version and
        relabeling the signature as v2 or v1 is detectable.

        Field encoding order: timestamp, event_type, actor, resource, action,
        prev_hash, details_json.  Canonical details JSON is produced with
        ``sort_keys=True``, compact separators, and ``allow_nan=False``.

        Args:
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
        for field in (timestamp, event_type, actor, resource, action, prev_hash, details_json):
            encoded = field.encode("utf-8")
            parts.append(len(encoded).to_bytes(4, "big"))
            parts.append(encoded)
        message = b"".join(parts)

        hex_digest = hmac.new(self._audit_key, message, hashlib.sha256).hexdigest()
        return f"v3:{hex_digest}"

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
        signs it using the v3 length-prefixed format (which eliminates
        pipe-delimiter injection and includes ``details`` in the HMAC),
        advances the chain, and logs the JSON representation to
        ``synth_engine.security.audit`` at INFO level.

        After logging, calls ``AnchorManager.maybe_anchor`` (best-effort) to
        publish a tamper-evident anchor when the configured threshold is
        reached.  Anchoring failures are caught and logged at WARNING so they
        never interrupt event logging.

        This method is thread-safe: an instance-level ``threading.Lock``
        serialises concurrent callers, preserving chain order even when
        called from multiple async route handlers.

        Args:
            event_type: Short uppercase identifier for the event category.
            actor: Identity of the principal performing the action.
            resource: Logical resource being acted upon.
            action: Verb describing the action.
            details: Arbitrary string metadata for the event.  Callers
                MUST NOT pass PII field values here.  Must be JSON-serializable
                (no ``float('nan')`` or ``float('inf')``).  Canonical JSON
                must not exceed 64 KB.

        Returns:
            The constructed and signed :class:`AuditEvent`.

        """
        # ValueError from _sign_v3 (oversized/non-serializable details) propagates
        # cleanly through the lock acquisition since _sign_v3 is called inside the lock.
        with self._lock:
            timestamp = datetime.now(UTC).isoformat()
            signature = self._sign_v3(
                timestamp, event_type, actor, resource, action, self._prev_hash, details
            )

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
            self._entry_count += 1

            # Capture consistent snapshot for anchoring (inside lock for consistency).
            chain_head = self._prev_hash
            entry_count = self._entry_count

            self._log.info(event.model_dump_json())

        # Call maybe_anchor outside the lock: AnchorManager has its own locking.
        # Lazy import avoids circular import (audit_anchor imports nothing from audit).
        # Best-effort: anchoring failures must never break event logging.
        try:
            from synth_engine.shared.security.audit_anchor import get_anchor_manager

            get_anchor_manager().maybe_anchor(chain_head, entry_count)
        except Exception as exc:  # broad catch intentional: anchoring is best-effort
            logging.getLogger(_AUDIT_LOGGER_NAME).warning(
                "Audit anchoring failed (best-effort — event logging unaffected): %s",
                type(exc).__name__,
            )

        return event

    def verify_event(self, event: AuditEvent) -> bool:
        """Verify that *event*'s signature was produced by this logger's key.

        Dispatches on the version prefix stored in ``event.signature``:

        - ``v3:`` — Recomputes the v3 length-prefixed signature and compares
          using ``hmac.compare_digest``.  This is the current format.
        - ``v2:`` — Recomputes the v2 signature (details included in HMAC)
          and compares using ``hmac.compare_digest``.  Supported for
          backward-compatible verification of existing log entries.
        - ``v1:`` — Recomputes the legacy signature (details not included)
          and compares using ``hmac.compare_digest``.  Emits a WARNING to
          prompt migration.  Supported for backward-compatible verification.
        - Any other prefix — Returns ``False`` immediately (fail-closed).

        Args:
            event: The :class:`AuditEvent` to verify.

        Returns:
            ``True`` if the signature is valid for the detected version,
            ``False`` otherwise (including unknown version prefixes).
        """
        sig = event.signature

        if sig.startswith("v3:"):
            try:
                expected = self._sign_v3(
                    event.timestamp,
                    event.event_type,
                    event.actor,
                    event.resource,
                    event.action,
                    event.prev_hash,
                    event.details,
                )
            except ValueError:
                return False
            return hmac.compare_digest(expected, sig)

        if sig.startswith("v2:"):
            try:
                expected = self._sign_v2(
                    event.timestamp,
                    event.event_type,
                    event.actor,
                    event.resource,
                    event.action,
                    event.prev_hash,
                    event.details,
                )
            except ValueError:
                return False
            return hmac.compare_digest(expected, sig)

        if sig.startswith("v1:"):
            expected_v1 = self._sign_v1(
                event.timestamp,
                event.event_type,
                event.actor,
                event.resource,
                event.action,
                event.prev_hash,
            )
            is_valid = hmac.compare_digest(expected_v1, sig)
            if is_valid:
                self._log.warning(
                    "Audit event uses deprecated v1 signature format. Migrate to v3 by Phase 60."
                )
            return is_valid

        # Unknown version prefix — fail-closed.
        return False


def _load_audit_key() -> bytes:
    """Read and validate ``AUDIT_KEY`` from :attr:`ConclaveSettings.audit_key`.

    Returns:
        Raw 32-byte HMAC signing key decoded from the hex env var.

    Raises:
        ValueError: If ``AUDIT_KEY`` is absent, wrong length, or not
            valid hexadecimal.
    """
    raw = get_settings().audit_key.get_secret_value()
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

    On first call, the logger attempts to resume the chain from the last
    persisted anchor record (T55.3).  The anchor file path is read from
    :attr:`~synth_engine.shared.settings.ConclaveSettings.anchor_file_path`.

    Returns:
        The process-wide :class:`AuditLogger` instance.

    """
    global _audit_logger_instance
    with _audit_logger_lock:
        if _audit_logger_instance is None:
            audit_key = _load_audit_key()
            anchor_file_path: str | None
            try:
                anchor_file_path = get_settings().anchor_file_path
            except Exception:  # broad catch: missing/invalid settings must not crash
                anchor_file_path = None
            _audit_logger_instance = AuditLogger(
                audit_key=audit_key,
                anchor_file_path=anchor_file_path,
            )
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
