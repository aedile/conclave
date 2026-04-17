"""Coverage gap tests for P81 CI fix — uncovered error paths.

Targets uncovered lines from the 94.13% coverage run:
- ssrf.py: lines 134-135, 260, 411-412, 414-417, 430-434, 476, 495
- audit_logger.py: lines 151, 159-167, 196-204, 207-214, 337-338, 397-398
- audit_migrations.py: lines 88, 93-99, 104-111

All tests exercise legitimate error-handling paths and edge cases that were
not reached by existing tests.

CONSTITUTION Priority 0: Security — audit integrity error paths must be tested.
CONSTITUTION Priority 3: TDD — coverage gate requires 95% minimum.
Task: P81-CI-FIX — resolve coverage gap after OIDC integration.
"""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile
from unittest.mock import mock_open, patch

import pytest

# ===========================================================================
# ssrf.py error-path coverage
# ===========================================================================


class TestSSRFPrivateHelpers:
    """Edge-case coverage for _is_blocked_ip, _is_rfc1918, _is_loopback."""

    def test_is_blocked_ip_returns_false_on_invalid_ip_string(self) -> None:
        """_is_blocked_ip must return False (not raise) on non-IP input.

        Line 134-135: the ValueError except clause in _is_blocked_ip.
        """
        from synth_engine.shared.ssrf import _is_blocked

        # "not-an-ip" is not a valid IP address — must return False, not raise
        result = _is_blocked("not-an-ip")
        assert result is False, f"Expected False for invalid IP string, got {result!r}"

    def test_is_rfc1918_returns_false_on_invalid_ip_string(self) -> None:
        """_is_rfc1918 must return False (not raise) on non-IP input.

        Lines 411-412: the ValueError except clause in _is_rfc1918.
        """
        from synth_engine.shared.ssrf import _is_rfc1918

        result = _is_rfc1918("definitely-not-an-ip")
        assert result is False, f"Expected False for invalid IP string, got {result!r}"

    def test_is_rfc1918_returns_false_for_ipv6_non_mapped(self) -> None:
        """_is_rfc1918 must return False for a non-IPv4-mapped IPv6 address.

        Lines 414-417: the isinstance(ip, IPv6Address) branch where
        ip.ipv4_mapped is None — the function should return False since
        a pure IPv6 address cannot be in an RFC-1918 IPv4 range.
        """
        from synth_engine.shared.ssrf import _is_rfc1918

        # ::2 is a pure IPv6 address (not IPv4-mapped) — must not match IPv4 ranges
        result = _is_rfc1918("::2")
        assert result is False, f"Expected False for non-mapped IPv6 address, got {result!r}"

    def test_is_loopback_returns_false_on_invalid_ip_string(self) -> None:
        """_is_loopback must return False (not raise) on non-IP input.

        Lines 430-434: the ValueError except clause in _is_loopback.
        """
        from synth_engine.shared.ssrf import _is_loopback

        result = _is_loopback("still-not-an-ip")
        assert result is False, f"Expected False for invalid IP string, got {result!r}"


class TestValidateDeliveryIPsDrift:
    """IP drift warning path in validate_delivery_ips (line 260)."""

    def test_ip_drift_logs_warning_but_does_not_block(self) -> None:
        """When resolved IPs differ from pinned IPs, a WARNING is logged but delivery proceeds.

        Line 260: the _logger.warning call inside the if pinned_ips drift branch.
        """
        from synth_engine.shared.ssrf import validate_delivery_ips

        # Patch socket.getaddrinfo to return a specific IP
        fake_ip = "93.184.216.34"  # example.com — public, not blocked
        fake_addr_infos = [(None, None, None, None, (fake_ip, 80))]

        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.WARNING)
        ssrf_logger = logging.getLogger("synth_engine.shared.ssrf")
        ssrf_logger.addHandler(handler)

        # pinned_ips differ from resolved — should trigger drift warning
        different_pinned = ["1.2.3.4"]  # differs from fake_ip

        try:
            with patch(
                "synth_engine.shared.ssrf.socket.getaddrinfo",
                return_value=fake_addr_infos,
            ):
                with patch("synth_engine.shared.ssrf._is_blocked", return_value=False):
                    # Must not raise — drift is a warning, not a block
                    validate_delivery_ips("example.com", pinned_ips=different_pinned)
            log_output = log_stream.getvalue()
            assert "drift" in log_output.lower() or "pinned" in log_output.lower(), (
                f"Expected IP drift warning in logs, got: {log_output!r}"
            )
        finally:
            ssrf_logger.removeHandler(handler)


class TestValidateOIDCIssuerURLEdgeCases:
    """Additional edge cases for validate_oidc_issuer_url."""

    def test_url_with_no_hostname_rejected(self) -> None:
        """URL that parses with no hostname component is rejected.

        Line 476: the `if not hostname` guard.
        urlparse('http:///no-host-here') returns hostname=None, so ValueError is raised.
        """
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match="(?i)(hostname|invalid)"):
            validate_oidc_issuer_url("http:///no-host-here")

    def test_ipv4_mapped_ipv6_loopback_is_blocked(self) -> None:
        """IPv4-mapped IPv6 loopback (::ffff:127.0.0.1) must be blocked.

        Line 495: the IPv4-mapped IPv6 unwrap branch in validate_oidc_issuer_url.
        This ensures the unwrap happens and loopback detection fires correctly.
        """
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        # ::ffff:127.0.0.1 maps to 127.0.0.1 — must be blocked as loopback
        with pytest.raises(ValueError, match="(?i)(loopback|forbidden)"):
            validate_oidc_issuer_url("http://[::ffff:127.0.0.1]/")

    def test_ipv4_mapped_ipv6_metadata_is_blocked(self) -> None:
        """IPv4-mapped IPv6 cloud metadata IP (::ffff:169.254.169.254) must be blocked.

        Line 495: the IPv4-mapped IPv6 unwrap branch — after unwrap, the
        metadata IP check fires.
        """
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match="(?i)(forbidden|metadata|cloud)"):
            validate_oidc_issuer_url("http://[::ffff:169.254.169.254]/")


# ===========================================================================
# audit_logger.py error-path coverage
# ===========================================================================


class TestAuditLoggerLoadPersistedChainHeadEdgeCases:
    """Error-path coverage for _load_persisted_chain_head."""

    def _make_key(self) -> bytes:
        """Return a 32-byte HMAC key."""
        return b"x" * 32

    def test_returns_none_when_no_anchor_file_path(self) -> None:
        """_load_persisted_chain_head returns None when anchor_file_path is None.

        Line 151: the `if self._anchor_file_path is None: return None` guard.
        This runs when AuditLogger is instantiated without an anchor_file_path
        and _load_persisted_chain_head is called directly.
        """
        from synth_engine.shared.security.audit_logger import AuditLogger

        logger = AuditLogger(audit_key=self._make_key())
        # _anchor_file_path is None — must return None
        result = logger._load_persisted_chain_head()
        assert result is None, f"Expected None when no anchor_file_path, got {result!r}"

    def test_oserror_reading_anchor_file_starts_from_genesis(self) -> None:
        """OSError while reading the anchor file logs a WARNING and returns None.

        Lines 159-167: the OSError except clause in _load_persisted_chain_head.
        """
        from synth_engine.shared.security.audit_logger import AuditLogger

        key = self._make_key()
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            anchor_path = f.name

        try:
            # Bypass _resume_from_anchor during __init__ — patch it out
            with patch.object(AuditLogger, "_resume_from_anchor"):
                logger = AuditLogger(audit_key=key, anchor_file_path=anchor_path)
                logger._anchor_file_path = anchor_path

            # Now mock open to raise OSError on the next call
            mocked = mock_open()
            mocked.side_effect = OSError("permission denied")
            with patch("builtins.open", mocked):
                result = logger._load_persisted_chain_head()

            assert result is None, (
                f"Expected None after OSError reading anchor file, got {result!r}"
            )
        finally:
            os.unlink(anchor_path)

    def test_invalid_chain_head_hash_returns_none_with_warning(self) -> None:
        """Invalid chain_head_hash triggers WARNING and returns None.

        Lines 196-204: the ValueError from _validate_chain_head_hash.
        """
        from synth_engine.shared.security.audit_logger import AuditLogger

        key = self._make_key()
        # Write an anchor record with an invalid (non-hex) chain_head_hash
        invalid_record = json.dumps(
            {
                "chain_head_hash": "not-a-valid-hex-hash",
                "entry_count": 5,
            }
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(invalid_record + "\n")
            anchor_path = f.name

        try:
            with patch.object(AuditLogger, "_resume_from_anchor"):
                logger = AuditLogger(audit_key=key, anchor_file_path=anchor_path)
                logger._anchor_file_path = anchor_path

            result = logger._load_persisted_chain_head()
            assert result is None, f"Expected None after invalid chain_head_hash, got {result!r}"
        finally:
            os.unlink(anchor_path)

    def test_entry_count_zero_returns_none_with_warning(self) -> None:
        """entry_count < 1 triggers WARNING and returns None.

        Lines 207-214: the `if not isinstance(entry_count, int) or entry_count < 1` guard.
        """
        from synth_engine.shared.security.audit_logger import AuditLogger

        key = self._make_key()
        valid_hash = "a" * 64  # valid 64-char hex string
        invalid_record = json.dumps(
            {
                "chain_head_hash": valid_hash,
                "entry_count": 0,  # invalid — must be >= 1
            }
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(invalid_record + "\n")
            anchor_path = f.name

        try:
            with patch.object(AuditLogger, "_resume_from_anchor"):
                logger = AuditLogger(audit_key=key, anchor_file_path=anchor_path)
                logger._anchor_file_path = anchor_path

            result = logger._load_persisted_chain_head()
            assert result is None, f"Expected None for entry_count=0, got {result!r}"
        finally:
            os.unlink(anchor_path)


class TestAuditLoggerAnchoringFailure:
    """Line 337-338: anchoring exception must not propagate from log_event."""

    def test_anchoring_exception_does_not_propagate(self) -> None:
        """Anchoring failure is best-effort — must not interrupt log_event.

        Lines 337-338: the broad except clause around get_anchor_manager().
        """
        from synth_engine.shared.security.audit_logger import AuditLogger

        key = b"y" * 32
        logger = AuditLogger(audit_key=key)

        # Simulate get_anchor_manager raising an unexpected exception.
        # The lazy import targets audit_anchor — patch it there.
        with patch(
            "synth_engine.shared.security.audit_anchor.get_anchor_manager",
            side_effect=RuntimeError("anchor manager unavailable"),
        ):
            # Must not raise — anchoring is best-effort
            event = logger.log_event(
                event_type="TEST_ANCHOR_FAIL",
                actor="test",
                resource="test",
                action="test",
                details={},
            )

        assert event.event_type == "TEST_ANCHOR_FAIL", (
            "log_event must return the event even when anchoring fails"
        )


class TestAuditLoggerVerifyEventEdgeCases:
    """Line 397-398: ValueError in _sign_v2 during verify_event."""

    def test_v2_sign_value_error_returns_false(self) -> None:
        """If _sign_v2 raises ValueError during verify_event, return False.

        Lines 397-398: the except ValueError clause in the v2 branch.
        """
        from synth_engine.shared.security.audit_logger import AuditEvent, AuditLogger

        key = b"z" * 32
        logger = AuditLogger(audit_key=key)

        # Construct a fake v2 event
        event = AuditEvent(
            timestamp="2024-01-01T00:00:00+00:00",
            event_type="TEST",
            actor="actor",
            resource="res",
            action="act",
            details={},
            prev_hash="0" * 64,
            signature="v2:abc123",
        )

        # Patch _sign_v2 to raise ValueError (e.g. oversized details)
        with patch(
            "synth_engine.shared.security.audit_logger._sign_v2",
            side_effect=ValueError("oversized"),
        ):
            result = logger.verify_event(event)

        assert result is False, f"Expected False when _sign_v2 raises ValueError, got {result!r}"


# ===========================================================================
# audit_migrations.py error-path coverage
# ===========================================================================


class TestMigrateAuditSignaturesEdgeCases:
    """Error-path coverage for migrate_audit_signatures."""

    def test_empty_lines_are_skipped_silently(self) -> None:
        """Empty lines in the input JSONL are skipped without error.

        Line 88: the `if not raw_line: continue` guard.
        """
        from synth_engine.shared.security.audit_migrations import migrate_audit_signatures

        key = b"m" * 32

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "audit.jsonl")
            output_path = os.path.join(tmpdir, "audit_out.jsonl")

            # Write only blank lines — no actual entries
            with open(input_path, "w", encoding="utf-8") as f:
                f.write("\n\n\n")

            # Must not crash
            migrate_audit_signatures(
                input_path=input_path,
                output_path=output_path,
                audit_key=key,
            )

            with open(output_path, encoding="utf-8") as f:
                lines = [ln.strip() for ln in f.readlines() if ln.strip()]

            assert lines == [], f"Expected empty output for blank-line input, got {lines!r}"

    def test_invalid_json_line_is_skipped_with_error_log(self) -> None:
        """A line that is not valid JSON is skipped (ERROR logged, no crash).

        Lines 93-99: the json.JSONDecodeError except clause.
        """
        from synth_engine.shared.security.audit_migrations import migrate_audit_signatures

        key = b"m" * 32

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "audit.jsonl")
            output_path = os.path.join(tmpdir, "audit_out.jsonl")

            with open(input_path, "w", encoding="utf-8") as f:
                f.write("this is { not : valid json }\n")

            # Must not crash
            migrate_audit_signatures(
                input_path=input_path,
                output_path=output_path,
                audit_key=key,
            )

            with open(output_path, encoding="utf-8") as f:
                lines = [ln.strip() for ln in f.readlines() if ln.strip()]

            assert lines == [], f"Expected invalid JSON line to be skipped, got output: {lines!r}"

    def test_audit_event_construction_failure_is_skipped(self) -> None:
        """A valid JSON line that fails AuditEvent construction is skipped.

        Lines 104-111: the broad Exception except clause around AuditEvent(**entry_dict).
        """
        from synth_engine.shared.security.audit_migrations import migrate_audit_signatures

        key = b"m" * 32

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "audit.jsonl")
            output_path = os.path.join(tmpdir, "audit_out.jsonl")

            # Valid JSON but missing required AuditEvent fields
            bad_entry = json.dumps({"foo": "bar", "not_an_audit_event": True})
            with open(input_path, "w", encoding="utf-8") as f:
                f.write(bad_entry + "\n")

            # Must not crash
            migrate_audit_signatures(
                input_path=input_path,
                output_path=output_path,
                audit_key=key,
            )

            with open(output_path, encoding="utf-8") as f:
                lines = [ln.strip() for ln in f.readlines() if ln.strip()]

            assert lines == [], (
                f"Expected AuditEvent construction failure to be skipped, got: {lines!r}"
            )
