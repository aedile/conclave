"""Module-level singleton for the AuditLogger.

Provides :func:`get_audit_logger`, :func:`reset_audit_logger`, and
:func:`_load_audit_key`.  These functions manage the process-wide
:class:`~synth_engine.shared.security.audit_logger.AuditLogger` instance.

The singleton pattern ensures the hash chain is continuous across all
requests for the lifetime of the process.  See ADR-0010 for full rationale.

CONSTITUTION Priority 0: Security
Task: T58.4 — Split audit.py into signatures/logger/singleton
Task: T57.5 — Narrow Exception Handling in Audit Logger Singleton
"""

from __future__ import annotations

import logging
import threading

from synth_engine.shared.security.audit_logger import AuditLogger
from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton state
# ---------------------------------------------------------------------------

_audit_logger_instance: AuditLogger | None = None
_audit_logger_lock: threading.Lock = threading.Lock()


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
            except (AttributeError, KeyError, TypeError) as exc:
                # T57.5: Narrow to expected exception types only.
                # AttributeError: settings attribute missing (misconfiguration).
                # KeyError: settings lookup failure (unusual but possible).
                # TypeError: unexpected type in settings access.
                # Unexpected exceptions (RuntimeError, ValueError, etc.) propagate
                # so programming errors surface rather than being silently swallowed.
                _logger.warning(
                    "Failed to read anchor_file_path from settings (%s: %s). "
                    "Audit logger will start from genesis (no anchor resume). "
                    "Audit chain continuity may be broken.",
                    type(exc).__name__,
                    exc,
                )
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
