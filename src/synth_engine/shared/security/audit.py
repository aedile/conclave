"""Cryptographically signed WORM audit logger — re-export shim.

This module exists for backward compatibility.  All symbols are now defined
in the following focused sub-modules:

- :mod:`synth_engine.shared.security.audit_signatures` — ``sign_v3`` as the
  public signing function.  ``sign_v1`` and ``sign_v2`` are preserved in
  ``audit_signatures`` for backward-compatible verification and migration,
  but are **NOT** exported from this public shim (T70.2).
- :mod:`synth_engine.shared.security.audit_logger` — :class:`AuditEvent`,
  :class:`AuditLogger` (chain management, ``log_event``, ``verify_event``).
- :mod:`synth_engine.shared.security.audit_singleton` — :func:`get_audit_logger`,
  :func:`reset_audit_logger`, :func:`_load_audit_key`.

All existing callers that use::

    from synth_engine.shared.security.audit import AuditLogger
    from synth_engine.shared.security.audit import get_audit_logger
    from synth_engine.shared.security.audit import AuditEvent

continue to work unchanged — this file re-exports every public name.

Security properties, chain continuity design, and signature format
versioning are documented in the individual sub-modules above.

CONSTITUTION Priority 0: Security
Task: T58.4 — Split audit.py into signatures/logger/singleton
Task: T70.2 — Remove v1/v2 from public API, keep backward-compat verification
"""

from synth_engine.shared.security.audit_logger import (
    _AUDIT_LOGGER_NAME,
    _GENESIS_HASH,
    AUDIT_CHAIN_RESUME_FAILURE_TOTAL,
    AuditEvent,
    AuditLogger,
)
from synth_engine.shared.security.audit_signatures import sign_v3
from synth_engine.shared.security.audit_singleton import (
    _load_audit_key,
    get_audit_logger,
    reset_audit_logger,
)

__all__ = [
    "AUDIT_CHAIN_RESUME_FAILURE_TOTAL",
    "_AUDIT_LOGGER_NAME",
    "_GENESIS_HASH",
    "AuditEvent",
    "AuditLogger",
    "_load_audit_key",
    "get_audit_logger",
    "reset_audit_logger",
    "sign_v3",
]
