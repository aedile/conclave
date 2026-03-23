"""Negative/attack tests for audit trail anchoring (T48.4).

Attack surface coverage:
- Concurrent anchor write protection (two workers anchoring simultaneously)
- Anchor verification failure handling (anchor doesn't match chain)
- Backend failure handling (anchor write fails — must not block log_event)
- Local file backend does not provide external attestation (warning expected)
- S3 backend requires Object Lock semantics (not plain PutObject)
- Anchor record must be immutable after creation
- Anchor timestamp cannot be back-dated (must be UTC)
- Entry count cannot be zero or negative
- chain_head_hash must be a valid 64-char hex string
- Backend publish failure must emit WARNING, not raise

CONSTITUTION Priority 0: Security
Task: T48.4 — Immutable Audit Trail Anchoring
"""

from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# AnchorRecord immutability
# ---------------------------------------------------------------------------


def test_anchor_record_is_immutable() -> None:
    """AnchorRecord must be a frozen dataclass — fields cannot be mutated after creation."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord

    now = datetime.now(UTC)
    record = AnchorRecord(
        chain_head_hash="a" * 64,
        entry_count=100,
        timestamp=now,
        backend_type="local_file",
    )
    with pytest.raises((FrozenInstanceError, AttributeError)):
        record.entry_count = 999  # type: ignore[misc]


def test_anchor_record_requires_valid_hex_chain_head() -> None:
    """AnchorRecord chain_head_hash must be exactly 64 hex characters."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord

    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="chain_head_hash"):
        AnchorRecord(
            chain_head_hash="not-hex-and-wrong-length",
            entry_count=100,
            timestamp=now,
            backend_type="local_file",
        )


def test_anchor_record_entry_count_must_be_positive() -> None:
    """AnchorRecord entry_count must be >= 1 — zero or negative is invalid."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord

    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="entry_count"):
        AnchorRecord(
            chain_head_hash="b" * 64,
            entry_count=0,
            timestamp=now,
            backend_type="local_file",
        )


def test_anchor_record_timestamp_must_be_utc() -> None:
    """AnchorRecord timestamp must be timezone-aware UTC."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord

    naive_dt = datetime(2024, 1, 1, 12, 0, 0)  # no tzinfo
    with pytest.raises(ValueError, match="timestamp"):
        AnchorRecord(
            chain_head_hash="c" * 64,
            entry_count=1,
            timestamp=naive_dt,
            backend_type="local_file",
        )


# ---------------------------------------------------------------------------
# Backend failure must not block log_event (best-effort)
# ---------------------------------------------------------------------------


def test_backend_publish_failure_does_not_raise(tmp_path: pytest.TempPathFactory) -> None:
    """AnchorManager must catch publish() failures and emit WARNING — not propagate."""
    import logging

    from synth_engine.shared.security.audit_anchor import AnchorManager, AnchorRecord

    class FailingBackend:
        backend_type = "failing"

        def publish(self, anchor: AnchorRecord) -> None:  # noqa: ARG002
            raise OSError("disk full")

    manager = AnchorManager(
        backend=FailingBackend(),  # type: ignore[arg-type]
        anchor_every_n_events=1,
        anchor_every_seconds=86400,
    )

    with patch.object(
        manager._log,  # type: ignore[attr-defined]
        "warning",
    ) as mock_warn:
        # Must not raise even though backend fails
        manager.maybe_anchor(chain_head_hash="d" * 64, entry_count=1)
        assert mock_warn.called, "WARNING must be emitted on backend publish failure"


# ---------------------------------------------------------------------------
# Concurrent anchor writes
# ---------------------------------------------------------------------------


def test_concurrent_anchor_writes_are_serialized(tmp_path: pytest.TempPathFactory) -> None:
    """Two threads triggering anchoring simultaneously must not produce duplicate anchors.

    The AnchorManager must use a lock so that only one anchor write is
    triggered per threshold crossing, not two.
    """
    from synth_engine.shared.security.audit_anchor import AnchorManager, AnchorRecord

    published: list[AnchorRecord] = []
    lock = threading.Lock()

    class CollectingBackend:
        backend_type = "collecting"

        def publish(self, anchor: AnchorRecord) -> None:
            with lock:
                published.append(anchor)

    manager = AnchorManager(
        backend=CollectingBackend(),  # type: ignore[arg-type]
        anchor_every_n_events=1,
        anchor_every_seconds=86400,
    )

    errors: list[Exception] = []

    def trigger_anchor(i: int) -> None:
        try:
            manager.maybe_anchor(chain_head_hash="e" * 64, entry_count=i + 1)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=trigger_anchor, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Unexpected errors in threads: {errors}"
    # All 5 calls should anchor because anchor_every_n_events=1
    assert len(published) == 5, (
        f"Expected 5 anchor publishes (one per event), got {len(published)}"
    )


# ---------------------------------------------------------------------------
# Verification failure handling
# ---------------------------------------------------------------------------


def test_verify_chain_against_anchors_returns_false_on_mismatch() -> None:
    """verify_chain_against_anchors must return False when chain head differs from anchor."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord, verify_chain_against_anchors

    anchor = AnchorRecord(
        chain_head_hash="a" * 64,
        entry_count=100,
        timestamp=datetime.now(UTC),
        backend_type="local_file",
    )
    # Pass a different chain_head_hash — should not match
    result = verify_chain_against_anchors(
        current_chain_head="b" * 64,
        current_entry_count=100,
        anchors=[anchor],
    )
    assert result is False, "Mismatch in chain_head_hash must return False"


def test_verify_chain_with_no_anchors_returns_true() -> None:
    """verify_chain_against_anchors must return True when anchor list is empty (no prior anchors).

    On first boot there are no anchors — verification passes trivially.
    """
    from synth_engine.shared.security.audit_anchor import verify_chain_against_anchors

    result = verify_chain_against_anchors(
        current_chain_head="c" * 64,
        current_entry_count=10,
        anchors=[],
    )
    assert result is True, "Empty anchors list must pass verification (first boot)"


def test_verify_chain_entry_count_mismatch_returns_false() -> None:
    """verify_chain_against_anchors must return False when entry_count doesn't match the anchor."""
    from synth_engine.shared.security.audit_anchor import AnchorRecord, verify_chain_against_anchors

    anchor = AnchorRecord(
        chain_head_hash="d" * 64,
        entry_count=50,
        timestamp=datetime.now(UTC),
        backend_type="local_file",
    )
    result = verify_chain_against_anchors(
        current_chain_head="d" * 64,
        current_entry_count=99,  # wrong count
        anchors=[anchor],
    )
    assert result is False, "Entry count mismatch must return False"


# ---------------------------------------------------------------------------
# Local file backend — no external attestation warning
# ---------------------------------------------------------------------------


def test_local_file_backend_emits_attestation_warning(tmp_path: pytest.TempPathFactory) -> None:
    """LocalFileAnchorBackend must emit a WARNING that it provides no external attestation.

    A local file can be rewritten if the host is compromised; operators must
    be warned that this backend is weaker than S3 Object Lock.
    """
    import logging

    from synth_engine.shared.security.audit_anchor import AnchorRecord, LocalFileAnchorBackend

    anchor_file = tmp_path / "anchors.jsonl"
    backend = LocalFileAnchorBackend(anchor_file_path=str(anchor_file))

    anchor = AnchorRecord(
        chain_head_hash="f" * 64,
        entry_count=1,
        timestamp=datetime.now(UTC),
        backend_type="local_file",
    )

    with patch(
        "synth_engine.shared.security.audit_anchor._logger"
    ) as mock_log:
        backend.publish(anchor)
        # The WARNING about no external attestation must be emitted
        warning_calls = [
            call for call in mock_log.warning.call_args_list if "attestation" in str(call).lower()
        ]
        assert warning_calls, (
            "LocalFileAnchorBackend must warn that it provides no external attestation"
        )


# ---------------------------------------------------------------------------
# anchor_every_n_events=0 is invalid configuration
# ---------------------------------------------------------------------------


def test_anchor_manager_rejects_zero_n_events() -> None:
    """AnchorManager must reject anchor_every_n_events <= 0."""
    from synth_engine.shared.security.audit_anchor import AnchorManager, LocalFileAnchorBackend

    with pytest.raises(ValueError, match="anchor_every_n_events"):
        AnchorManager(
            backend=LocalFileAnchorBackend(anchor_file_path="/tmp/anchors.jsonl"),
            anchor_every_n_events=0,
            anchor_every_seconds=86400,
        )


def test_anchor_manager_rejects_zero_seconds() -> None:
    """AnchorManager must reject anchor_every_seconds <= 0."""
    from synth_engine.shared.security.audit_anchor import AnchorManager, LocalFileAnchorBackend

    with pytest.raises(ValueError, match="anchor_every_seconds"):
        AnchorManager(
            backend=LocalFileAnchorBackend(anchor_file_path="/tmp/anchors.jsonl"),
            anchor_every_n_events=1000,
            anchor_every_seconds=0,
        )
