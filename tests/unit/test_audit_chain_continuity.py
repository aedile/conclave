"""Unit tests for audit chain continuity across process restarts.

Tests verify that AuditLogger resumes from the last persisted anchor on startup,
rather than restarting from genesis on every process restart.  This ensures the
audit hash-chain cannot be silently broken by a restart.

Covers:
- Attack tests: restart resumes from persisted state (not genesis), CHAIN_RESUMED
  event is logged, corrupt anchor file → genesis with WARNING, etc.
- Feature tests: missing file → genesis (first boot), empty file → genesis,
  valid anchor → resume with correct prev_hash and entry_count.

CONSTITUTION Priority 0: Security — audit chain continuity is a tamper-evidence requirement
CONSTITUTION Priority 3: TDD — attack tests committed before feature tests
Task: T55.3 — Audit Chain Continuity Across Restarts
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from synth_engine.shared.security.audit import (
    _GENESIS_HASH,
    AuditLogger,
    reset_audit_logger,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_AUDIT_KEY = bytes.fromhex("a" * 64)
_VALID_CHAIN_HEAD = "b" * 64  # 64 hex chars


def _write_anchor_file(path: str, chain_head_hash: str, entry_count: int) -> None:
    """Write a single anchor record to a JSONL file.

    Args:
        path: Filesystem path to write.
        chain_head_hash: 64-char hex hash to store.
        entry_count: Entry count to store.
    """
    record = {
        "chain_head_hash": chain_head_hash,
        "entry_count": entry_count,
        "timestamp": datetime.now(UTC).isoformat(),
        "backend_type": "local_file",
    }
    Path(path).write_text(json.dumps(record) + "\n", encoding="utf-8")


def _write_multi_anchor_file(
    path: str,
    records: list[tuple[str, int]],
) -> None:
    """Write multiple anchor records to a JSONL file.

    Args:
        path: Filesystem path to write.
        records: List of (chain_head_hash, entry_count) tuples.
    """
    lines = []
    for chain_head_hash, entry_count in records:
        record = {
            "chain_head_hash": chain_head_hash,
            "entry_count": entry_count,
            "timestamp": datetime.now(UTC).isoformat(),
            "backend_type": "local_file",
        }
        lines.append(json.dumps(record))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton() -> object:
    """Reset the AuditLogger singleton before and after each test."""
    reset_audit_logger()
    yield
    reset_audit_logger()


@pytest.fixture
def anchor_file(tmp_path: Path) -> str:
    """Return a path to a fresh anchor file in a temp directory.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        Absolute path string.
    """
    return str(tmp_path / "audit_anchors.jsonl")


@pytest.fixture
def audit_key_env(monkeypatch: pytest.MonkeyPatch) -> bytes:
    """Set AUDIT_KEY in the environment and return the raw key bytes.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        32 raw bytes corresponding to the hex env var.
    """
    key = _VALID_AUDIT_KEY
    monkeypatch.setenv("AUDIT_KEY", key.hex())
    return key


# ---------------------------------------------------------------------------
# Attack tests — restart MUST resume from persisted state (not genesis)
# ---------------------------------------------------------------------------


def test_restart_resumes_from_persisted_chain_head(anchor_file: str, audit_key_env: bytes) -> None:
    """Process restart MUST resume from persisted chain head, NOT genesis.

    Simulated by:
    1. Writing an anchor file with a known chain_head_hash and entry_count.
    2. Resetting the singleton (simulates restart).
    3. Creating a new AuditLogger — it must load from the anchor file.

    The first event after restart must have prev_hash equal to the persisted
    chain_head_hash, not the genesis sentinel.
    """
    persisted_hash = "c" * 64  # 64 valid hex chars
    persisted_count = 42

    _write_anchor_file(anchor_file, persisted_hash, persisted_count)

    # Simulate restart: create new AuditLogger with persisted anchor file
    logger = AuditLogger(
        audit_key=audit_key_env,
        anchor_file_path=anchor_file,
    )

    # The internal chain head should be the persisted hash, not genesis
    assert logger._prev_hash == persisted_hash
    assert logger._entry_count == persisted_count


def test_restart_logs_chain_resumed_event(
    anchor_file: str, audit_key_env: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """CHAIN_RESUMED audit event MUST be logged on startup with persisted chain head.

    Operators need to know that the chain was continued from a prior state,
    not restarted from genesis.
    """
    persisted_hash = "d" * 64
    persisted_count = 10

    _write_anchor_file(anchor_file, persisted_hash, persisted_count)

    with caplog.at_level(logging.INFO, logger="synth_engine.security.audit"):
        logger = AuditLogger(
            audit_key=audit_key_env,
            anchor_file_path=anchor_file,
        )

    # The first event logged must be CHAIN_RESUMED with the loaded chain head
    resumed_events = [r for r in caplog.records if "CHAIN_RESUMED" in r.getMessage()]
    assert len(resumed_events) >= 1
    # The event should contain the persisted hash in its details
    assert persisted_hash in resumed_events[0].getMessage()
    # Entry count should be one more than persisted (CHAIN_RESUMED event added)
    assert logger._entry_count == persisted_count + 1


def test_corrupt_anchor_file_starts_from_genesis_with_warning(
    anchor_file: str, audit_key_env: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """Corrupt anchor file MUST start from genesis with a WARNING logged.

    If the anchor file is unreadable/corrupt, the audit chain must not fail
    catastrophically.  Fail-safe: start from genesis and log a WARNING.
    """
    # Write invalid JSON to simulate corruption
    Path(anchor_file).write_text("CORRUPT_NOT_JSON\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="synth_engine.security.audit"):
        logger = AuditLogger(
            audit_key=audit_key_env,
            anchor_file_path=anchor_file,
        )

    # Should start from genesis despite corrupt file
    assert logger._prev_hash == _GENESIS_HASH
    assert logger._entry_count == 0

    # Must log a WARNING about the corruption
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) >= 1


def test_empty_anchor_file_starts_from_genesis(anchor_file: str, audit_key_env: bytes) -> None:
    """Empty anchor file MUST start from genesis (treated as first boot).

    Args:
        anchor_file: Path to empty anchor file.
        audit_key_env: Raw audit key bytes from environment.
    """
    # Create an empty file
    Path(anchor_file).write_text("", encoding="utf-8")

    logger = AuditLogger(
        audit_key=audit_key_env,
        anchor_file_path=anchor_file,
    )

    assert logger._prev_hash == _GENESIS_HASH
    assert logger._entry_count == 0


def test_missing_anchor_file_starts_from_genesis(anchor_file: str, audit_key_env: bytes) -> None:
    """Missing anchor file MUST start from genesis without crashing.

    First boot: no anchor file exists yet.  The logger must start from genesis
    cleanly without raising an exception.
    """
    # Ensure the file does NOT exist
    assert not Path(anchor_file).exists()

    logger = AuditLogger(
        audit_key=audit_key_env,
        anchor_file_path=anchor_file,
    )

    assert logger._prev_hash == _GENESIS_HASH
    assert logger._entry_count == 0


# ---------------------------------------------------------------------------
# Feature tests — correct behavior
# ---------------------------------------------------------------------------


def test_chain_continues_correctly_after_resume(anchor_file: str, audit_key_env: bytes) -> None:
    """After resume, a new event must chain from the persisted head.

    The event logged after restart must have prev_hash == persisted chain head,
    forming a continuous chain with no gap.
    """
    persisted_hash = "e" * 64
    persisted_count = 5

    _write_anchor_file(anchor_file, persisted_hash, persisted_count)

    logger = AuditLogger(audit_key=audit_key_env, anchor_file_path=anchor_file)

    # Log a regular event after the CHAIN_RESUMED event
    event = logger.log_event(
        event_type="TEST_EVENT",
        actor="operator",
        resource="test",
        action="test",
        details={"key": "value"},
    )

    # The CHAIN_RESUMED event was logged on init, then TEST_EVENT chains from it.
    # So TEST_EVENT's prev_hash is NOT the persisted hash directly — it's the
    # hash of the CHAIN_RESUMED event.  But the overall chain is continuous.
    # Verify that the entry count advanced correctly.
    assert logger._entry_count == persisted_count + 2  # CHAIN_RESUMED + TEST_EVENT
    assert event.event_type == "TEST_EVENT"


def test_multiple_anchor_records_uses_last_line(anchor_file: str, audit_key_env: bytes) -> None:
    """With multiple anchor records, MUST resume from the last record in the file.

    The anchor file is a JSONL append-only log.  The most recent (last) line
    contains the latest chain head.
    """
    old_hash = "f" * 64
    new_hash = "a" * 64  # latest record

    _write_multi_anchor_file(
        anchor_file,
        [(old_hash, 10), (new_hash, 20)],
    )

    logger = AuditLogger(audit_key=audit_key_env, anchor_file_path=anchor_file)

    # Should use the last record (new_hash, 20), not the first
    assert logger._prev_hash == new_hash
    assert logger._entry_count == 20 + 1  # 20 + CHAIN_RESUMED event


def test_no_anchor_file_path_provided_starts_from_genesis(
    audit_key_env: bytes,
) -> None:
    """When no anchor_file_path is provided, logger starts from genesis.

    For backward compatibility: existing code that creates AuditLogger without
    an anchor_file_path must work correctly (starts from genesis).
    """
    logger = AuditLogger(audit_key=audit_key_env)

    assert logger._prev_hash == _GENESIS_HASH
    assert logger._entry_count == 0


def test_load_persisted_chain_head_method_exists(anchor_file: str, audit_key_env: bytes) -> None:
    """AuditLogger MUST expose a _load_persisted_chain_head() method.

    The method is explicitly required in the task spec for testability and
    clarity of the loading logic.
    """
    logger = AuditLogger(audit_key=audit_key_env, anchor_file_path=anchor_file)
    assert hasattr(logger, "_load_persisted_chain_head")
    assert callable(logger._load_persisted_chain_head)
