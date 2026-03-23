"""Audit trail anchoring for immutable chain attestation (T48.4).

Publishes periodic ``AnchorRecord`` snapshots of the audit chain's current
head to a configurable external store.  This provides tamper-evidence
*beyond* the process boundary: even if ``AUDIT_KEY`` is compromised and
an attacker rewrites the local chain, they cannot silently alter already-
published anchors in an Object Lock bucket.

Backends
--------
- :class:`LocalFileAnchorBackend` — appends JSON lines to a local file.
  **Warning**: provides no external attestation; a compromised host can
  rewrite the file.  Use only for development or air-gapped deployments
  where S3 is unavailable.
- :class:`S3ObjectLockAnchorBackend` — calls ``s3.put_object`` with
  ``ObjectLockMode=COMPLIANCE`` and a retention period.  This is the
  production-grade backend: Object Lock prevents any modification or
  deletion of anchors, even by bucket owners.

Hot-path guarantee
------------------
:meth:`AnchorManager.maybe_anchor` must never block :meth:`AuditLogger.log_event`.
Anchor writes are performed synchronously inside ``maybe_anchor`` but any
exception from the backend is caught and logged at WARNING — the caller
continues unaffected.

Frequency triggers
------------------
An anchor is published when *either* condition is met:

1. This is the first call ever (no prior anchor).
2. ``entry_count >= last_anchored_at_count + anchor_every_n_events``.
3. Seconds since last anchor >= ``anchor_every_seconds``.

Security note
-------------
The ``LocalFileAnchorBackend`` emits a WARNING on every ``publish()`` call
reminding operators that local files provide no external attestation.  Use
:class:`S3ObjectLockAnchorBackend` in production.

CONSTITUTION Priority 0: Security
Task: T48.4 — Immutable Audit Trail Anchoring
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from synth_engine.shared.settings import get_settings

_logger = logging.getLogger("synth_engine.security.audit_anchor")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_chain_head_hash(value: str) -> None:
    """Raise ValueError if value is not a 64-char lowercase hex string.

    Args:
        value: The candidate hash string.

    Raises:
        ValueError: If value fails the 64-char lowercase-hex check.
    """
    if not _HEX64_RE.match(value):
        raise ValueError(
            f"chain_head_hash must be exactly 64 lowercase hex characters; got {value!r}"
        )


# ---------------------------------------------------------------------------
# AnchorRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchorRecord:
    """Immutable snapshot of the audit chain head at a point in time.

    Attributes:
        chain_head_hash: SHA-256 hex of the most recent audit event JSON.
            Must be exactly 64 lowercase hexadecimal characters.
        entry_count: Number of audit events in the chain at this anchor point.
            Must be >= 1.
        timestamp: UTC-aware datetime when the anchor was created.
            Must carry timezone information (tzinfo must not be None).
        backend_type: Identifier for the backend that published this anchor.
            Mirrors :attr:`AnchorBackend.backend_type`.
    """

    chain_head_hash: str
    entry_count: int
    timestamp: datetime
    backend_type: str

    def __post_init__(self) -> None:
        """Validate all fields after frozen dataclass construction.

        Raises:
            ValueError: If chain_head_hash is not 64 lowercase hex chars,
                entry_count is <= 0, or timestamp is timezone-naive.
        """
        _validate_chain_head_hash(self.chain_head_hash)
        if self.entry_count < 1:
            raise ValueError(f"entry_count must be >= 1; got {self.entry_count}")
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC); got a naive datetime")


# ---------------------------------------------------------------------------
# AnchorBackend protocol
# ---------------------------------------------------------------------------


class AnchorBackend(Protocol):
    """Protocol for anchor publication backends.

    All backend implementations must:
    - Expose a ``backend_type`` class attribute (string identifier).
    - Implement ``publish(anchor)`` to persist the anchor record.
    - ``publish()`` may raise exceptions; callers (:class:`AnchorManager`)
      catch and log them at WARNING so the audit hot-path is never blocked.
    """

    backend_type: str

    def publish(self, anchor: AnchorRecord) -> None:
        """Persist *anchor* to the external store.

        Args:
            anchor: The anchor record to publish.
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# LocalFileAnchorBackend
# ---------------------------------------------------------------------------


class LocalFileAnchorBackend:
    """Append-only local-file anchor backend.

    Appends each :class:`AnchorRecord` as a single JSON line to the
    configured file.  The file is created if it does not exist.

    Warning:
        This backend provides **no external attestation**.  A compromised host
        can rewrite the file.  Use :class:`S3ObjectLockAnchorBackend` in
        production deployments where tamper-resistance is required.

    Args:
        anchor_file_path: Path to the anchor JSONL file.
    """

    backend_type: str = "local_file"

    def __init__(self, anchor_file_path: str) -> None:
        self._path = anchor_file_path
        self._lock = threading.Lock()

    def publish(self, anchor: AnchorRecord) -> None:
        """Append *anchor* as a JSON line to the anchor file.

        Emits a WARNING on every call reminding operators that this backend
        provides no external attestation.

        Args:
            anchor: The anchor record to persist.

        Raises:
            OSError: If the file cannot be opened or written.
        """  # noqa: DOC502
        _logger.warning(
            "LocalFileAnchorBackend: anchor written to local file '%s'. "
            "WARNING — this provides no external attestation. "
            "A compromised host can rewrite this file. "
            "Use S3ObjectLockAnchorBackend in production.",
            self._path,
        )
        record: dict[str, Any] = {
            "chain_head_hash": anchor.chain_head_hash,
            "entry_count": anchor.entry_count,
            "timestamp": anchor.timestamp.isoformat(),
            "backend_type": anchor.backend_type,
        }
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# S3ObjectLockAnchorBackend
# ---------------------------------------------------------------------------


class S3ObjectLockAnchorBackend:
    """S3 Object Lock anchor backend for production tamper-resistant anchoring.

    Calls ``s3_client.put_object`` with ``ObjectLockMode=COMPLIANCE`` and a
    calculated ``ObjectLockRetainUntilDate``.  Object Lock prevents any
    modification or deletion of anchor records, even by the bucket owner —
    protecting anchors even if ``AUDIT_KEY`` is compromised.

    Args:
        s3_client: A boto3 S3 client (or compatible mock).
        bucket: S3 bucket name.  The bucket must have Object Lock enabled.
        prefix: Key prefix for anchor objects (e.g. ``"anchors/"``).
        retention_days: Number of days for COMPLIANCE mode retention.
    """

    backend_type: str = "s3_object_lock"

    def __init__(
        self,
        s3_client: Any,  # boto3 has no py.typed; see ADR-0048
        bucket: str,
        prefix: str,
        retention_days: int,
    ) -> None:
        self._s3 = s3_client
        self._bucket = bucket
        self._prefix = prefix
        self._retention_days = retention_days

    def publish(self, anchor: AnchorRecord) -> None:
        """Upload *anchor* to S3 with COMPLIANCE Object Lock retention.

        The S3 key is ``{prefix}{timestamp_iso}_{chain_head_hash[:16]}.json``
        to ensure uniqueness and easy chronological sorting.

        Args:
            anchor: The anchor record to persist.

        Raises:
            Exception: Any boto3 error propagates to the caller
                (:class:`AnchorManager` catches and logs it).
        """  # noqa: DOC502
        from datetime import timedelta

        retain_until = anchor.timestamp + timedelta(days=self._retention_days)
        key = (
            f"{self._prefix}"
            f"{anchor.timestamp.strftime('%Y%m%dT%H%M%SZ')}_"
            f"{anchor.chain_head_hash[:16]}.json"
        )
        body = json.dumps(
            {
                "chain_head_hash": anchor.chain_head_hash,
                "entry_count": anchor.entry_count,
                "timestamp": anchor.timestamp.isoformat(),
                "backend_type": anchor.backend_type,
            }
        ).encode()
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=retain_until.isoformat(),
        )


# ---------------------------------------------------------------------------
# AnchorManager
# ---------------------------------------------------------------------------


class AnchorManager:
    """Manages periodic audit chain anchoring.

    Calls ``backend.publish()`` when *either* of the following conditions
    is met:

    1. This is the first-ever call (``_last_anchor_time`` is ``None``).
    2. ``entry_count - _last_anchored_entry_count >= anchor_every_n_events``.
    3. Seconds elapsed since ``_last_anchor_time >= anchor_every_seconds``.

    Thread safety: an internal :class:`threading.Lock` serialises calls to
    :meth:`maybe_anchor` so that two concurrent threads cannot both cross
    the threshold and trigger duplicate anchors.

    Backend failures are caught and logged at WARNING.  The caller (the
    audit logger's ``log_event``) is never blocked or interrupted.

    Args:
        backend: An :class:`AnchorBackend` implementation.
        anchor_every_n_events: Publish an anchor every N events.  Must be >= 1.
        anchor_every_seconds: Publish an anchor at most once per this many seconds.
            Must be >= 1.

    Raises:
        ValueError: If ``anchor_every_n_events <= 0`` or
            ``anchor_every_seconds <= 0``.
    """

    def __init__(
        self,
        backend: AnchorBackend,
        anchor_every_n_events: int,
        anchor_every_seconds: int,
    ) -> None:
        if anchor_every_n_events <= 0:
            raise ValueError(f"anchor_every_n_events must be > 0; got {anchor_every_n_events}")
        if anchor_every_seconds <= 0:
            raise ValueError(f"anchor_every_seconds must be > 0; got {anchor_every_seconds}")

        self._backend = backend
        self._anchor_every_n_events = anchor_every_n_events
        self._anchor_every_seconds = anchor_every_seconds
        self._last_anchor_time: datetime | None = None
        self._last_anchored_entry_count: int = 0
        self._lock = threading.Lock()
        self._log = logging.getLogger("synth_engine.security.audit_anchor")

    def maybe_anchor(self, chain_head_hash: str, entry_count: int) -> None:
        """Check thresholds and publish an anchor if warranted.

        This method is safe to call from ``AuditLogger.log_event``'s hot path:
        backend failures are caught and logged at WARNING, never propagated.

        Args:
            chain_head_hash: Current chain head hash (64 lowercase hex chars).
            entry_count: Current number of events in the chain.
        """
        with self._lock:
            should_anchor = self._should_anchor(entry_count)
            if not should_anchor:
                return
            anchor = AnchorRecord(
                chain_head_hash=chain_head_hash,
                entry_count=entry_count,
                timestamp=datetime.now(UTC),
                backend_type=self._backend.backend_type,
            )
            # Update state before the publish so concurrent threads don't
            # re-trigger even if publish is slow.
            self._last_anchor_time = anchor.timestamp
            self._last_anchored_entry_count = entry_count

        # Publish outside the lock so concurrent log_event callers aren't blocked.
        # We already updated state above so no re-triggering can happen.
        try:
            self._backend.publish(anchor)
        except Exception as exc:
            self._log.warning(
                "Audit anchor publish failed (best-effort — audit chain unaffected): %s",
                exc,
            )

    def _should_anchor(self, entry_count: int) -> bool:
        """Return True if an anchor should be published now.

        Args:
            entry_count: Current event count in the chain.

        Returns:
            True if an anchor should be published.
        """
        # First-ever anchor — anchor immediately.
        if self._last_anchor_time is None:
            return True

        # Entry count threshold.
        if entry_count - self._last_anchored_entry_count >= self._anchor_every_n_events:
            return True

        # Time interval threshold.
        elapsed = (datetime.now(UTC) - self._last_anchor_time).total_seconds()
        if elapsed >= self._anchor_every_seconds:
            return True

        return False


# ---------------------------------------------------------------------------
# verify_chain_against_anchors
# ---------------------------------------------------------------------------


def verify_chain_against_anchors(
    current_chain_head: str,
    current_entry_count: int,
    anchors: list[AnchorRecord],
) -> bool:
    """Verify the current chain head against the most recent published anchor.

    On first boot with no prior anchors, returns ``True`` trivially (no
    anchor to verify against).

    Args:
        current_chain_head: The current chain-head hash (64 lowercase hex).
        current_entry_count: The current number of events in the chain.
        anchors: List of all known anchor records.  May be empty.

    Returns:
        ``True`` if the current chain is consistent with the most recent
        anchor (or no anchors exist), ``False`` if a mismatch is detected.
    """
    if not anchors:
        return True  # First boot — no anchors to verify against.

    # Use the anchor with the highest entry_count as the authoritative reference.
    latest = max(anchors, key=lambda a: a.entry_count)

    return (
        current_chain_head == latest.chain_head_hash and current_entry_count == latest.entry_count
    )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_anchor_manager_instance: AnchorManager | None = None
_anchor_manager_lock: threading.Lock = threading.Lock()


def get_anchor_manager() -> AnchorManager:
    """Return the module-level :class:`AnchorManager` singleton.

    Constructs the manager on first call using :func:`get_settings`.
    The backend is selected via :attr:`ConclaveSettings.anchor_backend`:

    - ``"local_file"`` → :class:`LocalFileAnchorBackend`
    - ``"s3_object_lock"`` → requires additional configuration (not
      auto-constructed; raise :exc:`ValueError` if attempted without wiring).

    Returns:
        The process-wide :class:`AnchorManager` instance.

    Raises:
        ValueError: If :attr:`ConclaveSettings.anchor_backend` is not
            ``"local_file"`` and no explicit backend wiring has been provided.
    """
    global _anchor_manager_instance
    with _anchor_manager_lock:
        if _anchor_manager_instance is None:
            settings = get_settings()
            backend: AnchorBackend
            if settings.anchor_backend == "local_file":
                backend = LocalFileAnchorBackend(
                    anchor_file_path=settings.anchor_file_path,
                )
            else:
                raise ValueError(
                    f"Unsupported anchor_backend '{settings.anchor_backend}'. "
                    "S3ObjectLockAnchorBackend requires explicit wiring in bootstrapper."
                )
            _anchor_manager_instance = AnchorManager(
                backend=backend,
                anchor_every_n_events=settings.anchor_every_n_events,
                anchor_every_seconds=settings.anchor_every_seconds,
            )
        return _anchor_manager_instance


def reset_anchor_manager() -> None:
    """Reset the module-level singleton to ``None``.

    Warning:
        This function exists **solely for test isolation**.
    """
    global _anchor_manager_instance
    with _anchor_manager_lock:
        _anchor_manager_instance = None
