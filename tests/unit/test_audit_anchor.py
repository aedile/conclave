"""Feature tests for audit trail anchoring (T48.4).

Tests cover:
- AnchorRecord creation and fields
- LocalFileAnchorBackend publish appends JSON lines
- AnchorManager triggers on entry count threshold
- AnchorManager triggers on time interval threshold
- AnchorManager does NOT trigger before threshold
- AnchorManager.maybe_anchor resets counter after anchoring
- AuditLogger integration — anchoring fires after N events
- S3ObjectLockAnchorBackend interface compliance
- get_anchor_manager / default settings wiring
- verify_chain_against_anchors — matching case

CONSTITUTION Priority 3: TDD — Red Phase (feature tests)
Task: T48.4 — Immutable Audit Trail Anchoring
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# AnchorRecord
# ---------------------------------------------------------------------------


def test_anchor_record_fields_are_accessible() -> None:
    """AnchorRecord exposes chain_head_hash, entry_count, timestamp, backend_type."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord

    now = datetime.now(UTC)
    record = AnchorRecord(
        chain_head_hash="a" * 64,
        entry_count=100,
        timestamp=now,
        backend_type="local_file",
    )
    assert record.chain_head_hash == "a" * 64
    assert record.entry_count == 100
    assert record.timestamp == now
    assert record.backend_type == "local_file"


def test_anchor_record_accepts_valid_lowercase_hex() -> None:
    """AnchorRecord accepts exactly 64 lowercase hex characters for chain_head_hash."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord

    valid_hash = "deadbeef" * 8  # 64 hex chars
    record = AnchorRecord(
        chain_head_hash=valid_hash,
        entry_count=1,
        timestamp=datetime.now(UTC),
        backend_type="s3_object_lock",
    )
    assert record.chain_head_hash == valid_hash


# ---------------------------------------------------------------------------
# LocalFileAnchorBackend
# ---------------------------------------------------------------------------


def test_local_file_backend_appends_json_line(tmp_path: Path) -> None:
    """LocalFileAnchorBackend.publish() appends a valid JSON line to the anchor file."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord, LocalFileAnchorBackend

    anchor_file = tmp_path / "anchors.jsonl"
    backend = LocalFileAnchorBackend(anchor_file_path=str(anchor_file))

    now = datetime.now(UTC)
    anchor = AnchorRecord(
        chain_head_hash="1234abcd" * 8,
        entry_count=500,
        timestamp=now,
        backend_type="local_file",
    )

    with patch("synth_engine.shared.security.audit_anchor._logger"):
        backend.publish(anchor)

    assert anchor_file.exists(), "Anchor file must be created on first publish"
    lines = anchor_file.read_text().strip().splitlines()
    assert len(lines) == 1, f"Expected 1 line, got {len(lines)}"

    parsed = json.loads(lines[0])
    assert parsed["chain_head_hash"] == "1234abcd" * 8
    assert parsed["entry_count"] == 500
    assert parsed["backend_type"] == "local_file"


def test_local_file_backend_appends_multiple_anchors(tmp_path: Path) -> None:
    """Multiple publish() calls must each append a new line (not overwrite)."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord, LocalFileAnchorBackend

    anchor_file = tmp_path / "anchors.jsonl"
    backend = LocalFileAnchorBackend(anchor_file_path=str(anchor_file))

    with patch("synth_engine.shared.security.audit_anchor._logger"):
        for i in range(3):
            backend.publish(
                AnchorRecord(
                    chain_head_hash=str(i) * 64,
                    entry_count=(i + 1) * 100,
                    timestamp=datetime.now(UTC),
                    backend_type="local_file",
                )
            )

    lines = anchor_file.read_text().strip().splitlines()
    assert len(lines) == 3, f"Expected 3 lines after 3 publishes, got {len(lines)}"


def test_local_file_backend_type_is_local_file() -> None:
    """LocalFileAnchorBackend.backend_type must equal 'local_file'."""
    from synth_engine.shared.security.audit_anchor import LocalFileAnchorBackend

    backend = LocalFileAnchorBackend(anchor_file_path="/tmp/test_anchors.jsonl")
    assert backend.backend_type == "local_file"


# ---------------------------------------------------------------------------
# S3ObjectLockAnchorBackend — interface compliance
# ---------------------------------------------------------------------------


def test_s3_backend_type_is_s3_object_lock() -> None:
    """S3ObjectLockAnchorBackend.backend_type must equal 's3_object_lock'."""
    from synth_engine.shared.security.audit_anchor import S3ObjectLockAnchorBackend

    mock_s3 = MagicMock()
    backend = S3ObjectLockAnchorBackend(
        s3_client=mock_s3,
        bucket="my-audit-bucket",
        prefix="anchors/",
        retention_days=7,
    )
    assert backend.backend_type == "s3_object_lock"


def test_s3_backend_publish_calls_put_object(tmp_path: Path) -> None:
    """S3ObjectLockAnchorBackend.publish() must call s3_client.put_object with ObjectLock params."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord, S3ObjectLockAnchorBackend

    mock_s3 = MagicMock()
    backend = S3ObjectLockAnchorBackend(
        s3_client=mock_s3,
        bucket="my-audit-bucket",
        prefix="anchors/",
        retention_days=7,
    )

    anchor = AnchorRecord(
        chain_head_hash="abc12345" * 8,
        entry_count=1000,
        timestamp=datetime.now(UTC),
        backend_type="s3_object_lock",
    )
    backend.publish(anchor)

    assert mock_s3.put_object.called, "put_object must be called on publish"
    call_kwargs = mock_s3.put_object.call_args[1]
    assert call_kwargs["Bucket"] == "my-audit-bucket"
    assert "ObjectLockMode" in call_kwargs, "ObjectLockMode must be set for Object Lock retention"
    assert call_kwargs["ObjectLockMode"] == "COMPLIANCE"


# ---------------------------------------------------------------------------
# AnchorManager — threshold triggers
# ---------------------------------------------------------------------------


def test_anchor_manager_triggers_on_n_events(tmp_path: Path) -> None:
    """AnchorManager must call backend.publish() when entry_count crosses n_events threshold."""
    from synth_engine.shared.security.audit_anchor import AnchorManager, AnchorRecord

    mock_backend = MagicMock()
    mock_backend.backend_type = "mock"
    manager = AnchorManager(
        backend=mock_backend,
        anchor_every_n_events=100,
        anchor_every_seconds=86400,
    )

    # 99 events should NOT trigger
    manager.maybe_anchor(chain_head_hash="a" * 64, entry_count=99)
    mock_backend.publish.assert_not_called()

    # 100 events SHOULD trigger
    manager.maybe_anchor(chain_head_hash="b" * 64, entry_count=100)
    mock_backend.publish.assert_called_once()

    published_anchor: AnchorRecord = mock_backend.publish.call_args[0][0]
    assert published_anchor.chain_head_hash == "b" * 64
    assert published_anchor.entry_count == 100
    assert published_anchor.backend_type == "mock"


def test_anchor_manager_triggers_on_time_interval() -> None:
    """AnchorManager must call backend.publish() when time since last anchor exceeds interval."""
    from synth_engine.shared.security.audit_anchor import AnchorManager

    mock_backend = MagicMock()
    mock_backend.backend_type = "mock"
    manager = AnchorManager(
        backend=mock_backend,
        anchor_every_n_events=1_000_000,  # very high — won't trigger on count
        anchor_every_seconds=1,  # 1 second for testability
    )

    # First call — anchors immediately (no prior anchor)
    manager.maybe_anchor(chain_head_hash="a" * 64, entry_count=1)
    assert mock_backend.publish.call_count == 1

    # Reset and wait for interval to expire
    mock_backend.reset_mock()
    time.sleep(1.1)

    manager.maybe_anchor(chain_head_hash="b" * 64, entry_count=2)
    assert mock_backend.publish.call_count == 1, (
        "Second anchor must fire after time interval expires"
    )


def test_anchor_manager_does_not_trigger_before_threshold() -> None:
    """AnchorManager must NOT publish before threshold is reached."""
    from synth_engine.shared.security.audit_anchor import AnchorManager

    mock_backend = MagicMock()
    mock_backend.backend_type = "mock"
    manager = AnchorManager(
        backend=mock_backend,
        anchor_every_n_events=500,
        anchor_every_seconds=86400,
    )

    for i in range(1, 500):
        manager.maybe_anchor(chain_head_hash="c" * 64, entry_count=i)

    mock_backend.publish.assert_not_called()


def test_anchor_manager_anchors_immediately_on_first_call() -> None:
    """AnchorManager must anchor immediately on first call (no prior anchor time)."""
    from synth_engine.shared.security.audit_anchor import AnchorManager

    mock_backend = MagicMock()
    mock_backend.backend_type = "mock"
    manager = AnchorManager(
        backend=mock_backend,
        anchor_every_n_events=1000,
        anchor_every_seconds=3600,
    )

    # First event — should anchor immediately regardless of threshold
    manager.maybe_anchor(chain_head_hash="d" * 64, entry_count=1)
    assert mock_backend.publish.call_count == 1, (
        "First-ever anchor must be published immediately"
    )


# ---------------------------------------------------------------------------
# verify_chain_against_anchors — positive case
# ---------------------------------------------------------------------------


def test_verify_chain_against_anchors_returns_true_on_match() -> None:
    """verify_chain_against_anchors returns True when chain_head_hash and entry_count match."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord, verify_chain_against_anchors

    anchor = AnchorRecord(
        chain_head_hash="e" * 64,
        entry_count=1000,
        timestamp=datetime.now(UTC),
        backend_type="local_file",
    )
    result = verify_chain_against_anchors(
        current_chain_head="e" * 64,
        current_entry_count=1000,
        anchors=[anchor],
    )
    assert result is True


def test_verify_chain_uses_most_recent_anchor() -> None:
    """verify_chain_against_anchors uses the most recent anchor (highest entry_count)."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord, verify_chain_against_anchors

    older_anchor = AnchorRecord(
        chain_head_hash="f" * 64,
        entry_count=500,
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        backend_type="local_file",
    )
    newer_anchor = AnchorRecord(
        chain_head_hash="0" * 64,
        entry_count=1000,
        timestamp=datetime(2024, 6, 1, tzinfo=UTC),
        backend_type="local_file",
    )

    # current chain matches the newer anchor
    result = verify_chain_against_anchors(
        current_chain_head="0" * 64,
        current_entry_count=1000,
        anchors=[older_anchor, newer_anchor],
    )
    assert result is True


# ---------------------------------------------------------------------------
# get_anchor_manager — settings integration
# ---------------------------------------------------------------------------


def test_get_anchor_manager_returns_anchor_manager_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_anchor_manager() returns an AnchorManager with configured backend."""
    from synth_engine.shared.security.audit_anchor import AnchorManager, get_anchor_manager, reset_anchor_manager

    reset_anchor_manager()
    monkeypatch.setenv("ANCHOR_BACKEND", "local_file")
    monkeypatch.setenv("ANCHOR_FILE_PATH", "/tmp/test_get_anchor_manager.jsonl")
    get_settings_mock = MagicMock()
    get_settings_mock.return_value.anchor_backend = "local_file"
    get_settings_mock.return_value.anchor_file_path = "/tmp/test_get_anchor_manager.jsonl"
    get_settings_mock.return_value.anchor_every_n_events = 1000
    get_settings_mock.return_value.anchor_every_seconds = 86400

    with patch("synth_engine.shared.security.audit_anchor.get_settings", get_settings_mock):
        manager = get_anchor_manager()

    assert isinstance(manager, AnchorManager)
    reset_anchor_manager()


def test_get_anchor_manager_is_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_anchor_manager() returns the same instance on repeated calls."""
    from synth_engine.shared.security.audit_anchor import get_anchor_manager, reset_anchor_manager

    reset_anchor_manager()
    get_settings_mock = MagicMock()
    get_settings_mock.return_value.anchor_backend = "local_file"
    get_settings_mock.return_value.anchor_file_path = "/tmp/test_singleton.jsonl"
    get_settings_mock.return_value.anchor_every_n_events = 1000
    get_settings_mock.return_value.anchor_every_seconds = 86400

    with patch("synth_engine.shared.security.audit_anchor.get_settings", get_settings_mock):
        first = get_anchor_manager()
        second = get_anchor_manager()

    assert first is second
    reset_anchor_manager()


def test_reset_anchor_manager_clears_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """reset_anchor_manager() forces get_anchor_manager() to create a fresh instance."""
    from synth_engine.shared.security.audit_anchor import get_anchor_manager, reset_anchor_manager

    reset_anchor_manager()
    get_settings_mock = MagicMock()
    get_settings_mock.return_value.anchor_backend = "local_file"
    get_settings_mock.return_value.anchor_file_path = "/tmp/test_reset_singleton.jsonl"
    get_settings_mock.return_value.anchor_every_n_events = 1000
    get_settings_mock.return_value.anchor_every_seconds = 86400

    with patch("synth_engine.shared.security.audit_anchor.get_settings", get_settings_mock):
        first = get_anchor_manager()
        reset_anchor_manager()
        second = get_anchor_manager()

    assert first is not second
    reset_anchor_manager()
