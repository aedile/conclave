"""Stateful WORM audit logger — chain management and event signing.

Defines :class:`AuditEvent` and :class:`AuditLogger`.  The logger maintains
a tamper-evident hash chain across all events for the lifetime of a process.

Signature functions are imported from
:mod:`synth_engine.shared.security.audit_signatures` to keep this module
focused on chain management and event lifecycle.

Chain continuity across restarts (T55.3)
-----------------------------------------
On startup, :class:`AuditLogger` reads the last anchor record from the
anchor JSONL file (the same file written by :class:`LocalFileAnchorBackend`).
If a persisted chain head is found, the logger resumes from it:

- ``_prev_hash`` is set to the persisted ``chain_head_hash``.
- ``_entry_count`` is set to the persisted ``entry_count``.
- A ``CHAIN_RESUMED`` audit event is emitted recording the loaded state.

If no anchor file path is provided, the file is missing, empty, or corrupt,
the logger starts from genesis (``_prev_hash = "0" * 64``,
``_entry_count = 0``).  Corrupt/unreadable files emit a WARNING.

CONSTITUTION Priority 0: Security
Task: T58.4 — Split audit.py into signatures/logger/singleton
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

from synth_engine.shared.security.audit_signatures import sign_v1, sign_v2, sign_v3

_AUDIT_LOGGER_NAME = "synth_engine.security.audit"
_GENESIS_HASH = "0" * 64

# Module-level logger for diagnostic messages (not the audit chain logger).
_logger = logging.getLogger(__name__)

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

    def _sign_v1(
        self,
        timestamp: str,
        event_type: str,
        actor: str,
        resource: str,
        action: str,
        prev_hash: str,
    ) -> str:
        """Backward-compat wrapper: delegate to standalone :func:`sign_v1`.

        Args:
            timestamp: ISO-8601 UTC timestamp.
            event_type: Short uppercase event identifier.
            actor: Principal identity.
            resource: Affected resource.
            action: Action verb.
            prev_hash: SHA-256 hex of the previous event JSON.

        Returns:
            Versioned signature string ``v1:<hex>``.
        """
        return sign_v1(self._audit_key, timestamp, event_type, actor, resource, action, prev_hash)

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
        """Backward-compat wrapper: delegate to standalone :func:`sign_v2`.

        Args:
            timestamp: ISO-8601 UTC timestamp.
            event_type: Short uppercase event identifier.
            actor: Principal identity.
            resource: Affected resource.
            action: Action verb.
            prev_hash: SHA-256 hex of the previous event JSON.
            details: Arbitrary string key-value metadata.

        Returns:
            Versioned signature string ``v2:<hex>``.

        Raises:
            ValueError: If the canonical details JSON exceeds 64 KB.
        """
        return sign_v2(
            self._audit_key, timestamp, event_type, actor, resource, action, prev_hash, details
        )

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
        """Backward-compat wrapper: delegate to standalone :func:`sign_v3`.

        Args:
            timestamp: ISO-8601 UTC timestamp.
            event_type: Short uppercase event identifier.
            actor: Principal identity.
            resource: Affected resource.
            action: Action verb.
            prev_hash: SHA-256 hex of the previous event JSON.
            details: Arbitrary string key-value metadata.

        Returns:
            Versioned signature string ``v3:<hex>``.

        Raises:
            ValueError: If the canonical details JSON exceeds 64 KB.
        """
        return sign_v3(
            self._audit_key, timestamp, event_type, actor, resource, action, prev_hash, details
        )

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
        # ValueError from sign_v3 (oversized/non-serializable details) propagates
        # cleanly through the lock acquisition since sign_v3 is called inside the lock.
        with self._lock:
            timestamp = datetime.now(UTC).isoformat()
            signature = sign_v3(
                self._audit_key,
                timestamp,
                event_type,
                actor,
                resource,
                action,
                self._prev_hash,
                details,
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
                expected = sign_v3(
                    self._audit_key,
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
                expected = sign_v2(
                    self._audit_key,
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
            is_valid_v2 = hmac.compare_digest(expected, sig)
            if not is_valid_v2:
                self._log.warning(
                    "Audit HMAC verification failed (v2): event_type=%s timestamp=%s actor=%s",
                    event.event_type,
                    event.timestamp,
                    event.actor,
                )
            return is_valid_v2

        if sig.startswith("v1:"):
            expected_v1 = sign_v1(
                self._audit_key,
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
            else:
                self._log.warning(
                    "Audit HMAC verification failed (v1): event_type=%s timestamp=%s actor=%s",
                    event.event_type,
                    event.timestamp,
                    event.actor,
                )
            return is_valid

        # Unknown version prefix — fail-closed.
        return False
