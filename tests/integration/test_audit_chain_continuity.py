"""Integration test: audit chain continuity across process restarts (T55.3 AC6).

Exercises the REAL ``AuditLogger`` + ``LocalFileAnchorBackend`` + ``AnchorManager``
path end-to-end — no mocks.  Verifies that:

1. An ``AuditLogger`` wired with a real anchor file path (tmp_path) writes
   anchor records via ``AnchorManager`` as events are logged.
2. Resetting the singleton (simulating a process restart) and creating a new
   ``AuditLogger`` with the same anchor file causes the chain to resume from
   the persisted head — not genesis.
3. A ``CHAIN_RESUMED`` event is logged immediately after the new logger is
   constructed.
4. Hash-chain continuity holds: the ``CHAIN_RESUMED`` event's ``prev_hash``
   equals the chain head persisted in the anchor file.

Requirements:
- No external infra required (local file backend only).

Marks: ``integration``

CONSTITUTION Priority 0: Security — audit trail tamper-evidence.
Task: T55.3 — Audit Chain Continuity Across Restarts (QA BLOCKER AC6)
"""

from __future__ import annotations

import hashlib
import json
import logging
import unittest.mock
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from synth_engine.shared.security.audit import AuditEvent, AuditLogger

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AUDIT_KEY_HEX = "a" * 64  # 32 bytes of 0xAA — valid 64-char lowercase hex
_AUDIT_KEY_BYTES = bytes.fromhex(_AUDIT_KEY_HEX)

# AnchorManager threshold: anchor every 1 event so tests don't need to log many
_ANCHOR_EVERY_N = 1
_ANCHOR_EVERY_SECONDS = 3600  # time-based trigger disabled (large value)


def _make_logger(anchor_file_path: str) -> AuditLogger:
    """Construct an isolated AuditLogger wired to a real LocalFileAnchorBackend.

    Wires the AnchorManager singleton to use the local-file backend at
    ``anchor_file_path`` before constructing the AuditLogger so that
    ``maybe_anchor`` is called after each event.

    Args:
        anchor_file_path: Path to the anchor JSONL file (must exist or be
            creatable by the test).

    Returns:
        A fresh AuditLogger instance (NOT the module singleton).
    """
    import synth_engine.shared.security.audit_anchor as _anchor_module
    from synth_engine.shared.security.audit import AuditLogger
    from synth_engine.shared.security.audit_anchor import (
        AnchorManager,
        LocalFileAnchorBackend,
        reset_anchor_manager,
    )

    # Reset the anchor manager singleton so we can wire our own backend.
    reset_anchor_manager()

    backend = LocalFileAnchorBackend(anchor_file_path=anchor_file_path)
    manager = AnchorManager(
        backend=backend,
        anchor_every_n_events=_ANCHOR_EVERY_N,
        anchor_every_seconds=_ANCHOR_EVERY_SECONDS,
    )

    # Wire our manager as the singleton so AuditLogger.log_event uses it.
    _anchor_module._anchor_manager_instance = manager  # type: ignore[attr-defined]

    return AuditLogger(
        audit_key=_AUDIT_KEY_BYTES,
        anchor_file_path=anchor_file_path,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuditChainContinuity:
    """End-to-end chain continuity tests using real file backend."""

    def test_chain_resumes_from_persisted_head(self, tmp_path: Path) -> None:
        """Chain resumes from anchor file after singleton reset (process restart sim).

        Steps:
        1. Create logger_1 wired to a real anchor file.
        2. Log enough events to trigger an anchor write (threshold = 1).
        3. Read the persisted chain_head_hash from the anchor file.
        4. Reset singletons (simulating process restart).
        5. Create logger_2 with the same anchor file.
        6. Verify logger_2's first non-resume event prev_hash != genesis.
        """
        from synth_engine.shared.security.audit import reset_audit_logger
        from synth_engine.shared.security.audit_anchor import reset_anchor_manager

        anchor_file = str(tmp_path / "anchors.jsonl")

        # --- Phase 1: First process lifetime ---
        logger_1 = _make_logger(anchor_file)

        # Log an event — threshold=1 triggers an anchor write immediately.
        event_1 = logger_1.log_event(
            event_type="TEST_EVENT",
            actor="integration_test",
            resource="audit_chain",
            action="write",
            details={"phase": "1"},
        )

        # Verify anchor file was written.
        assert Path(anchor_file).exists(), "Anchor file must exist after first event"
        anchor_lines = [line for line in Path(anchor_file).read_text().splitlines() if line.strip()]
        assert len(anchor_lines) >= 1, "At least one anchor record must be written"

        # Read the last anchor record — this is what the next process will pick up.
        last_anchor = json.loads(anchor_lines[-1])
        persisted_hash: str = last_anchor["chain_head_hash"]
        persisted_count: int = last_anchor["entry_count"]

        # Sanity: persisted_hash is a 64-char lowercase hex string.
        assert len(persisted_hash) == 64, "chain_head_hash must be 64 chars"
        assert persisted_hash == persisted_hash.lower(), "chain_head_hash must be lowercase"
        assert persisted_count >= 1, "entry_count must be at least 1"

        # Verify event_1's hash is part of the chain (it forms the anchor's chain head).
        expected_head = hashlib.sha256(event_1.model_dump_json().encode()).hexdigest()
        assert persisted_hash == expected_head, (
            f"Anchor chain_head_hash {persisted_hash!r} must equal "
            f"SHA-256 of event_1 JSON {expected_head!r}"
        )

        # --- Phase 2: Simulate process restart ---
        reset_audit_logger()
        reset_anchor_manager()

        # --- Phase 3: Second process lifetime — resume from anchor ---
        logger_2 = _make_logger(anchor_file)

        # Log a probe event after resumption.
        probe_event = logger_2.log_event(
            event_type="PROBE",
            actor="integration_test",
            resource="audit_chain",
            action="probe",
            details={"phase": "2"},
        )

        # After resuming, prev_hash must NOT be genesis (all zeros).
        genesis = "0" * 64
        assert probe_event.prev_hash != genesis, (
            "After resuming from anchor, prev_hash must not be genesis"
        )

    def test_chain_resumed_event_is_emitted(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """CHAIN_RESUMED event is written to the audit log after resuming.

        Verifies that the second AuditLogger emits a CHAIN_RESUMED event
        whose ``resumed_from_hash`` matches the persisted chain_head_hash.
        """
        from synth_engine.shared.security.audit import reset_audit_logger
        from synth_engine.shared.security.audit_anchor import reset_anchor_manager

        anchor_file = str(tmp_path / "anchors2.jsonl")

        # --- Phase 1: Create an anchor ---
        logger_1 = _make_logger(anchor_file)
        logger_1.log_event(
            event_type="SEED_EVENT",
            actor="integration_test",
            resource="audit_chain",
            action="seed",
            details={},
        )

        anchor_lines = [line for line in Path(anchor_file).read_text().splitlines() if line.strip()]
        persisted_hash: str = json.loads(anchor_lines[-1])["chain_head_hash"]

        # --- Phase 2: Simulate restart and capture audit log output ---
        reset_audit_logger()
        reset_anchor_manager()

        audit_logger_name = "synth_engine.security.audit"
        with caplog.at_level(logging.INFO, logger=audit_logger_name):
            _make_logger(anchor_file)
            # CHAIN_RESUMED is emitted during __init__ → _resume_from_anchor()

        # Find the CHAIN_RESUMED event in captured log records.
        resumed_events = [
            json.loads(record.message)
            for record in caplog.records
            if record.name == audit_logger_name and "CHAIN_RESUMED" in record.message
        ]
        assert len(resumed_events) >= 1, "CHAIN_RESUMED event must appear in the audit log"

        resumed_event = resumed_events[0]
        assert resumed_event["event_type"] == "CHAIN_RESUMED", "event_type must be CHAIN_RESUMED"
        assert resumed_event["details"]["resumed_from_hash"] == persisted_hash, (
            f"CHAIN_RESUMED.details.resumed_from_hash must equal persisted hash {persisted_hash!r}"
        )

    def test_chain_continuity_prev_hash_links(self, tmp_path: Path) -> None:
        """Hash chain links correctly from anchor through CHAIN_RESUMED to next event.

        Verifies that after resuming:
        - CHAIN_RESUMED.prev_hash == persisted anchor chain_head_hash
        - Next event's prev_hash == SHA-256 of CHAIN_RESUMED event JSON
        """
        import synth_engine.shared.security.audit_anchor as _anchor_module
        from synth_engine.shared.security.audit import AuditLogger
        from synth_engine.shared.security.audit_anchor import (
            AnchorManager,
            LocalFileAnchorBackend,
            reset_anchor_manager,
        )

        anchor_file = str(tmp_path / "anchors3.jsonl")

        # --- Phase 1: Seed an anchor ---
        logger_1 = _make_logger(anchor_file)
        logger_1.log_event(
            event_type="SEED",
            actor="integration_test",
            resource="audit_chain",
            action="seed",
            details={},
        )

        anchor_lines = [line for line in Path(anchor_file).read_text().splitlines() if line.strip()]
        persisted_hash: str = json.loads(anchor_lines[-1])["chain_head_hash"]

        # --- Phase 2: Restart — wire manager and capture CHAIN_RESUMED via mock ---
        reset_anchor_manager()

        backend = LocalFileAnchorBackend(anchor_file_path=anchor_file)
        manager = AnchorManager(
            backend=backend,
            anchor_every_n_events=_ANCHOR_EVERY_N,
            anchor_every_seconds=_ANCHOR_EVERY_SECONDS,
        )
        _anchor_module._anchor_manager_instance = manager  # type: ignore[attr-defined]

        events_emitted: list[AuditEvent] = []
        orig_log_event = AuditLogger.log_event

        def capturing_log_event(self: AuditLogger, **kwargs: object) -> AuditEvent:
            ev = orig_log_event(self, **kwargs)
            events_emitted.append(ev)
            return ev

        with unittest.mock.patch.object(AuditLogger, "log_event", capturing_log_event):
            logger_3 = AuditLogger(
                audit_key=_AUDIT_KEY_BYTES,
                anchor_file_path=anchor_file,
            )

        assert len(events_emitted) >= 1, "At least CHAIN_RESUMED must be emitted"
        chain_resumed = events_emitted[0]
        assert chain_resumed.event_type == "CHAIN_RESUMED", (
            f"First emitted event must be CHAIN_RESUMED, got {chain_resumed.event_type!r}"
        )
        # CHAIN_RESUMED.prev_hash == persisted anchor chain_head_hash
        assert chain_resumed.prev_hash == persisted_hash, (
            f"CHAIN_RESUMED.prev_hash must equal persisted anchor hash {persisted_hash!r}, "
            f"got {chain_resumed.prev_hash!r}"
        )

        # Log a follow-up event and verify linkage.
        next_event = logger_3.log_event(
            event_type="POST_RESUME",
            actor="integration_test",
            resource="audit_chain",
            action="post_resume",
            details={},
        )
        expected_prev = hashlib.sha256(chain_resumed.model_dump_json().encode()).hexdigest()
        assert next_event.prev_hash == expected_prev, (
            f"POST_RESUME.prev_hash must equal SHA-256 of CHAIN_RESUMED JSON; "
            f"got {next_event.prev_hash!r}, expected {expected_prev!r}"
        )

    def test_first_boot_no_anchor_file_starts_from_genesis(self, tmp_path: Path) -> None:
        """Logger starts from genesis when no anchor file exists (first boot).

        Verifies that on first boot (no prior anchor file), the logger starts
        from genesis and the first event's prev_hash is the genesis sentinel.
        """
        from synth_engine.shared.security.audit import AuditLogger
        from synth_engine.shared.security.audit_anchor import reset_anchor_manager

        reset_anchor_manager()
        anchor_file = str(tmp_path / "nonexistent_anchors.jsonl")

        logger = AuditLogger(
            audit_key=_AUDIT_KEY_BYTES,
            anchor_file_path=anchor_file,
        )
        event = logger.log_event(
            event_type="FIRST_BOOT",
            actor="integration_test",
            resource="audit_chain",
            action="boot",
            details={},
        )
        genesis = "0" * 64
        assert event.prev_hash == genesis, (
            f"On first boot, prev_hash must be genesis (all zeros); got {event.prev_hash!r}"
        )

    def test_corrupt_anchor_file_falls_back_to_genesis(self, tmp_path: Path) -> None:
        """Logger falls back to genesis when anchor file is corrupt.

        Verifies that a corrupt anchor JSONL file is handled safely: the logger
        logs a WARNING and starts from genesis rather than crashing.
        """
        from synth_engine.shared.security.audit import AuditLogger
        from synth_engine.shared.security.audit_anchor import reset_anchor_manager

        reset_anchor_manager()
        anchor_file = str(tmp_path / "corrupt_anchors.jsonl")
        Path(anchor_file).write_text("this is not valid json\n")

        logger = AuditLogger(
            audit_key=_AUDIT_KEY_BYTES,
            anchor_file_path=anchor_file,
        )
        event = logger.log_event(
            event_type="AFTER_CORRUPT",
            actor="integration_test",
            resource="audit_chain",
            action="boot",
            details={},
        )
        genesis = "0" * 64
        assert event.prev_hash == genesis, (
            "After corrupt anchor file, prev_hash must fall back to genesis"
        )

    def test_invalid_chain_head_hash_in_anchor_falls_back_to_genesis(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Logger rejects and falls back to genesis for invalid chain_head_hash.

        Verifies that the red-team finding (chain_head_hash not validated) is
        fixed: a non-hex or wrong-length chain_head_hash in the anchor file
        causes the logger to emit a WARNING and start from genesis.
        """
        from synth_engine.shared.security.audit import AuditLogger
        from synth_engine.shared.security.audit_anchor import reset_anchor_manager

        reset_anchor_manager()
        anchor_file = str(tmp_path / "bad_hash_anchors.jsonl")

        # Write an anchor record with an invalid chain_head_hash.
        bad_record = {
            "chain_head_hash": "not-a-valid-hash",
            "entry_count": 5,
            "timestamp": "2026-03-25T00:00:00+00:00",
            "backend_type": "local_file",
        }
        Path(anchor_file).write_text(json.dumps(bad_record) + "\n")

        audit_logger_name = "synth_engine.security.audit"
        with caplog.at_level(logging.WARNING, logger=audit_logger_name):
            logger = AuditLogger(
                audit_key=_AUDIT_KEY_BYTES,
                anchor_file_path=anchor_file,
            )

        event = logger.log_event(
            event_type="AFTER_BAD_HASH",
            actor="integration_test",
            resource="audit_chain",
            action="boot",
            details={},
        )
        genesis = "0" * 64
        assert event.prev_hash == genesis, (
            "After anchor with invalid chain_head_hash, must fall back to genesis"
        )

        # Verify a WARNING was logged about the validation failure.
        warning_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "chain_head_hash" in r.message.lower()
        ]
        assert len(warning_records) >= 1, (
            "A WARNING must be logged when chain_head_hash fails validation"
        )
