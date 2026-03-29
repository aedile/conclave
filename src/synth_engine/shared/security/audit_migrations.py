"""Audit log migration tool — upgrade v1/v2 signed entries to v3 format.

Reads a JSONL audit log file, verifies each entry's signature, and
re-signs valid v1/v2 entries with v3 format.  Entries that are:

- Already v3: written as-is (no-op migration pass).
- v1/v2 with a valid signature: re-signed with v3 and written.
- v1/v2 with an INVALID signature (tampered): skipped with ERROR log.
- Unrecognized format: skipped with WARNING log (no crash).
- Unparseable JSON: skipped with ERROR log (no crash).

The migration is written to a NEW output file.  The input is never modified.

Usage::

    from synth_engine.shared.security.audit_migrations import migrate_audit_signatures

    migrate_audit_signatures(
        input_path="/var/log/audit.jsonl",
        output_path="/var/log/audit_migrated.jsonl",
        audit_key=key_bytes,
    )

CONSTITUTION Priority 0: Security — tampered entries must be rejected,
    not silently migrated.  Audit chain integrity is Priority 0.
Task: T70.2 — Remove legacy v1/v2 signing from public API, add migration tool
"""

from __future__ import annotations

import json
import logging

from synth_engine.shared.security.audit_logger import AuditEvent, AuditLogger
from synth_engine.shared.security.audit_signatures import sign_v3

_logger = logging.getLogger(__name__)


def migrate_audit_signatures(
    *,
    input_path: str,
    output_path: str,
    audit_key: bytes,
) -> None:
    """Re-sign v1/v2 audit log entries as v3 format.

    Reads ``input_path`` line-by-line, parses each entry, verifies the
    signature, and writes a v3-signed copy to ``output_path``.

    Behavior by signature format:
    - ``v3:`` — Passes through as-is (already current format).
    - ``v1:`` or ``v2:`` with valid signature — Re-signed as v3.
    - ``v1:`` or ``v2:`` with INVALID signature — Skipped; ERROR logged.
      The tampered entry is NOT written to the output.
    - Any other prefix — Skipped; WARNING logged.  No crash.
    - Unparseable JSON — Skipped; ERROR logged.  No crash.

    Args:
        input_path: Absolute path to the source JSONL audit log file.
        output_path: Absolute path to the output JSONL file.  Created or
            overwritten.  The input file is never modified.
        audit_key: Raw 32-byte HMAC key used both for signature verification
            and for signing migrated entries.

    Raises:
        OSError: If the input file cannot be opened for reading, or if the
            output file cannot be opened for writing.
    """
    # Build a stateless logger for verification only.
    # We do NOT use the chain (log_event) because migration re-signs without
    # advancing the chain state — the chain hashes are preserved from the
    # original entries.
    verifier = AuditLogger(audit_key=audit_key)

    migrated_count = 0
    skipped_tampered = 0
    skipped_unknown = 0
    passthrough_v3 = 0

    with (
        open(input_path, encoding="utf-8") as infile,
        open(output_path, "w", encoding="utf-8") as outfile,
    ):
        for line_num, raw_line in enumerate(infile, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            # Parse JSON — skip on error.
            try:
                entry_dict = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                _logger.error(
                    "migrate_audit_signatures: line %d is not valid JSON (%s) — skipping.",
                    line_num,
                    type(exc).__name__,
                )
                continue

            # Build AuditEvent — skip on missing/invalid fields.
            try:
                event = AuditEvent(**entry_dict)
            except Exception as exc:
                _logger.error(
                    "migrate_audit_signatures: line %d failed AuditEvent "
                    "construction (%s) — skipping.",
                    line_num,
                    type(exc).__name__,
                )
                continue

            sig = event.signature

            # v3 — already current format, pass through.
            if sig.startswith("v3:"):
                outfile.write(event.model_dump_json() + "\n")
                passthrough_v3 += 1
                continue

            # v1/v2 — verify then re-sign.
            if sig.startswith("v1:") or sig.startswith("v2:"):
                is_valid = verifier.verify_event(event)
                if not is_valid:
                    _logger.error(
                        "migrate_audit_signatures: line %d has INVALID %s signature "
                        "(event_type=%s timestamp=%s) — skipping tampered entry.",
                        line_num,
                        sig[:2],
                        event.event_type,
                        event.timestamp,
                    )
                    skipped_tampered += 1
                    continue

                # Re-sign with v3; preserve all other fields unchanged.
                new_sig = sign_v3(
                    audit_key,
                    event.timestamp,
                    event.event_type,
                    event.actor,
                    event.resource,
                    event.action,
                    event.prev_hash,
                    event.details,
                )
                migrated_entry = event.model_copy(update={"signature": new_sig})
                outfile.write(migrated_entry.model_dump_json() + "\n")
                migrated_count += 1
                continue

            # Unrecognized format — skip without crashing.
            _logger.warning(
                "migrate_audit_signatures: line %d has unrecognized signature "
                "format %r — skipping.",
                line_num,
                sig[:20],
            )
            skipped_unknown += 1

    _logger.info(
        "migrate_audit_signatures complete: migrated=%d passthrough_v3=%d "
        "skipped_tampered=%d skipped_unknown=%d",
        migrated_count,
        passthrough_v3,
        skipped_tampered,
        skipped_unknown,
    )
