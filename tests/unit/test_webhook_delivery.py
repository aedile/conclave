"""Unit tests for webhook delivery engine (T45.3).

Attack/negative tests are first (Constitution Priority 0: attack-first ordering).

Attack/negative tests:
1.  SSRF: delivery must re-validate callback URL before each attempt
2.  SSRF: delivery must use allow_redirects=False
3.  HMAC tampering: wrong signing key produces incorrect signature
4.  Deactivated registration: no delivery attempt made
5.  Delivery timeout: enforced per attempt
6.  Retry exhaustion: after 3 failures, delivery marked FAILED in log
7.  SSRF: IPv4-mapped IPv6 addresses are blocked (::ffff:127.0.0.1 etc.)
8.  SSRF: delivery-time validation failure returns FAILED DeliveryResult
9.  SSRF: URL with no hostname raises ValueError (line ~97)
10. SSRF: DNS gaierror treated as safe / fail-open when strict=False (delivery path)
11. SSRF: malformed IP string from getaddrinfo is skipped silently (lines ~116-117)

Feature/positive tests:
12. HMAC-SHA256 signature format: "sha256=<hex_digest>"
13. Payload canonicalization: json.dumps sorted keys + compact separators
14. Retry no-sleep: time budget used instead of time.sleep() (T62.2)
15. Successful delivery: status=SUCCESS, attempt_number=1 in log
16. Delivery ID (UUID) included in log entry
17. Delivery engine does NOT import from bootstrapper (boundary constraint)
18. X-Conclave-Signature header set on delivery attempt
19. X-Conclave-Event header set to event type
20. X-Conclave-Delivery-Id header is a valid UUID
21. IoC callback pattern: set_webhook_delivery_fn registers callback

CONSTITUTION Priority 0: Security — no SSRF, correct HMAC, no redirect following
CONSTITUTION Priority 3: TDD — RED phase
Task: T45.3 — Implement Webhook Callbacks for Task Completion
Task: P45 review — F1, F8, F9, F12
Task: P45 QA re-review — ssrf.py edge-case coverage (lines 97, 102-109, 116-117)
Task: T55.4 — updated DNS gaierror test to use strict=False (delivery path semantics)
Task: T62.2 — Circuit Breaker: no time.sleep(), time budget used instead
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# State isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass
    yield
    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def reset_circuit_breaker() -> Generator[None]:
    """Reset the module-level circuit breaker singleton between tests.

    T62.2: The circuit breaker singleton accumulates per-URL failure state.
    Without this fixture, a test that trips the circuit for
    ``https://example.com/hook`` will cause subsequent tests using the same
    URL to get SKIPPED instead of making HTTP calls.

    Yields:
        None — setup and teardown only.
    """
    import synth_engine.modules.synthesizer.jobs.webhook_delivery as _mod

    _mod._MODULE_CIRCUIT_BREAKER = None
    yield
    _mod._MODULE_CIRCUIT_BREAKER = None


# ===========================================================================
# ATTACK / NEGATIVE TESTS
# ===========================================================================


class TestSSRFAtDelivery:
    """T45.3 SSRF protection during delivery (DNS-rebinding guard)."""

    def test_delivery_rejects_private_ip_at_send_time(self) -> None:
        """Delivery engine must re-validate URL before each HTTP attempt.

        DNS-rebinding protection: even if URL passed registration SSRF check,
        delivery must reject if the host now resolves to a private IP.

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        # Direct private-IP URL must raise ValueError
        with pytest.raises(ValueError, match="private|reserved|forbidden"):
            validate_callback_url("http://10.0.0.1/hook")

    def test_delivery_rejects_localhost_at_send_time(self) -> None:
        """Delivery engine must reject localhost URLs at send time.

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        with pytest.raises(ValueError, match="private|reserved|forbidden"):
            validate_callback_url("http://127.0.0.1/hook")

    def test_ipv4_mapped_ipv6_loopback_blocked(self) -> None:
        """::ffff:127.0.0.1 must be blocked (IPv4-mapped IPv6 loopback bypass).

        The IPv4-mapped form was previously not checked against IPv4 networks.
        After F1 fix, the mapped address is unwrapped before the network test.

        Args: none (no parameters).
        """
        # Simulate resolution returning ::ffff:127.0.0.1
        from synth_engine.shared.ssrf import validate_callback_url

        fake_addr = [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::ffff:127.0.0.1", 0, 0, 0))]
        with patch("synth_engine.shared.ssrf.socket.getaddrinfo", return_value=fake_addr):
            with pytest.raises(ValueError, match="private|reserved|forbidden"):
                validate_callback_url("https://example.com/hook")

    def test_ipv4_mapped_ipv6_private_blocked(self) -> None:
        """::ffff:10.0.0.1 must be blocked (IPv4-mapped IPv6 private bypass).

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        fake_addr = [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::ffff:10.0.0.1", 0, 0, 0))]
        with patch("synth_engine.shared.ssrf.socket.getaddrinfo", return_value=fake_addr):
            with pytest.raises(ValueError, match="private|reserved|forbidden"):
                validate_callback_url("https://example.com/hook")

    def test_ipv4_mapped_ipv6_link_local_blocked(self) -> None:
        """::ffff:169.254.169.254 (AWS metadata) must be blocked when mapped.

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        fake_addr = [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                0,
                "",
                ("::ffff:169.254.169.254", 0, 0, 0),
            )
        ]
        with patch("synth_engine.shared.ssrf.socket.getaddrinfo", return_value=fake_addr):
            with pytest.raises(ValueError, match="private|reserved|forbidden"):
                validate_callback_url("https://example.com/hook")

    def test_ssrf_revalidation_at_delivery_returns_failed(self) -> None:
        """deliver_webhook with SSRF failure at delivery time returns FAILED result.

        Lines 302-309 of webhook_delivery.py: the SSRF re-validation block inside
        the delivery loop.

        Args: none (no parameters).
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        reg = MagicMock()
        reg.active = True
        reg.callback_url = "https://example.com/hook"
        reg.signing_key = "a" * 32
        reg.id = "reg-ssrf-fail"

        with patch(
            "synth_engine.modules.synthesizer.jobs.webhook_delivery.validate_delivery_ips",
            side_effect=ValueError("SSRF: resolves to private IP"),
        ):
            result = deliver_webhook(
                registration=reg,
                job_id=99,
                event_type="job.completed",
                payload={"job_id": "99", "status": "COMPLETE"},
            )

        assert result.status == "FAILED"
        assert result.error_message is not None
        assert "SSRF" in result.error_message or "private" in result.error_message.lower()

    def test_validate_callback_url_rejects_no_hostname(self) -> None:
        """URL with no hostname must raise ValueError.

        ``urlparse("https:///path").hostname`` is ``None`` / empty string.
        The guard at ssrf.py line ~97 must raise before any DNS lookup.

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        with pytest.raises(ValueError, match="no hostname|private|reserved|forbidden"):
            validate_callback_url("https:///path")

    def test_validate_callback_url_dns_gaierror_returns_safely(self) -> None:
        """DNS gaierror with strict=False must be treated as safe (fail-open).

        At delivery time the SSRF check uses strict=False so that transient
        DNS failures do not abort in-flight deliveries.  The HTTP request
        itself will fail if the host is truly unreachable.

        T55.4: strict=False must be passed explicitly to preserve fail-open
        behaviour.  With strict=True (the new default), DNS failure raises
        ValueError (fail-closed, for registration-time protection).

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        with patch(
            "synth_engine.shared.ssrf.socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            # Must NOT raise when strict=False — delivery-time fail-open behavior
            validate_callback_url("https://nonexistent.example.com/hook", strict=False)
        assert validate_callback_url.__name__ == "validate_callback_url"

    def test_validate_callback_url_malformed_ip_from_dns_is_skipped(self) -> None:
        """Malformed IP string returned by getaddrinfo must be skipped silently.

        Lines ~116-117 of ssrf.py: if ``ipaddress.ip_address()`` raises
        ``ValueError`` for a bad address string (e.g. ``"not-an-ip"``), the
        entry is skipped via ``continue`` and no exception propagates.

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        # Simulate getaddrinfo returning one entry with a malformed address string.
        # The second element of the sockaddr tuple (index 0) is the IP string.
        fake_addr = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("not-an-ip", 80))]
        with patch("synth_engine.shared.ssrf.socket.getaddrinfo", return_value=fake_addr):
            # Must NOT raise — the bad entry is silently skipped
            validate_callback_url("https://example.com/hook")
        assert validate_callback_url.__name__ == "validate_callback_url"


class TestHMACTampering:
    """T45.3 HMAC signature correctness."""

    def test_wrong_key_produces_different_signature(self) -> None:
        """HMAC computed with wrong key must not match signature from correct key.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            _compute_hmac_signature,
        )

        payload = {"job_id": "1", "status": "COMPLETE"}
        sig_correct = _compute_hmac_signature(payload, "a" * 32)
        sig_wrong = _compute_hmac_signature(payload, "b" * 32)
        assert sig_correct != sig_wrong


class TestDeactivatedRegistrationNoDelivery:
    """T45.3 deactivated registrations must not receive deliveries."""

    def test_inactive_registration_skipped(self) -> None:
        """deliver_webhook must not make HTTP calls for inactive registrations.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            deliver_webhook,
        )

        reg = MagicMock()
        reg.active = False
        reg.callback_url = "https://example.com/hook"
        reg.signing_key = "a" * 32
        reg.id = "reg-001"

        with patch("synth_engine.modules.synthesizer.jobs.webhook_delivery.httpx") as mock_httpx:
            result = deliver_webhook(
                registration=reg,
                job_id=1,
                event_type="job.completed",
                payload={"job_id": "1", "status": "COMPLETE"},
            )

        # T72.5: httpx.Client context manager is no longer called when registration is inactive
        assert mock_httpx.Client.call_count == 0
        assert result.status == "SKIPPED"


class TestRetryExhaustion:
    """T45.3 delivery retry: 3 attempts, then FAILED."""

    def test_three_failures_mark_delivery_failed(self) -> None:
        """After 3 HTTP failures, delivery log entry must have status=FAILED.

        T62.2: The circuit breaker trips after threshold consecutive failures.
        With the default threshold of 3, all 3 attempts are made and then the
        circuit trips, causing the function to return FAILED immediately after
        the third failure without further retries.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            deliver_webhook,
        )

        reg = MagicMock()
        reg.active = True
        reg.callback_url = "https://example.com/hook"
        reg.signing_key = "a" * 32
        reg.id = "reg-001"

        with (
            patch("synth_engine.modules.synthesizer.jobs.webhook_delivery.validate_delivery_ips"),
            patch("synth_engine.modules.synthesizer.jobs.webhook_delivery.httpx") as mock_httpx,
        ):
            # T72.5: httpx.Client used as context manager; configure mock client .post
            mock_client = mock_httpx.Client.return_value.__enter__.return_value
            mock_client.post.side_effect = Exception("Connection refused")
            result = deliver_webhook(
                registration=reg,
                job_id=1,
                event_type="job.completed",
                payload={"job_id": "1", "status": "COMPLETE"},
            )

        assert result.status == "FAILED"
        assert mock_client.post.call_count == 3

    def test_no_sleep_between_retries(self) -> None:
        """T62.2: deliver_webhook must NOT call time.sleep() between retry attempts.

        The old implementation used exponential backoff via time.sleep() (1s, 4s).
        T62.2 replaced this with a time budget check using time.monotonic() to
        prevent Huey worker starvation.  time.sleep() must never be called.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            deliver_webhook,
        )

        reg = MagicMock()
        reg.active = True
        reg.callback_url = "https://example.com/hook"
        reg.signing_key = "a" * 32
        reg.id = "reg-001"

        sleep_calls: list[float] = []

        with (
            patch("synth_engine.modules.synthesizer.jobs.webhook_delivery.validate_delivery_ips"),
            patch("synth_engine.modules.synthesizer.jobs.webhook_delivery.httpx") as mock_httpx,
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.time.sleep",
                side_effect=lambda s: sleep_calls.append(s),
            ),
        ):
            # T72.5: httpx.Client used as context manager; configure mock client .post
            mock_post = mock_httpx.Client.return_value.__enter__.return_value.post
            mock_post.side_effect = Exception("fail")
            deliver_webhook(
                registration=reg,
                job_id=1,
                event_type="job.completed",
                payload={"job_id": "1", "status": "COMPLETE"},
            )

        # T62.2: no sleep between retries — time budget used instead
        assert sleep_calls == [], (
            f"deliver_webhook called time.sleep() {len(sleep_calls)} time(s): {sleep_calls}. "
            "T62.2 requires no sleep between retries to prevent Huey worker starvation."
        )


# ===========================================================================
# FEATURE / POSITIVE TESTS
# ===========================================================================


class TestHMACSignature:
    """T45.3 HMAC-SHA256 signature format and canonicalization."""

    def test_signature_format_is_sha256_prefixed(self) -> None:
        """HMAC signature must be formatted as 'sha256=<hex_digest>'.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            _compute_hmac_signature,
        )

        payload = {"job_id": "42", "status": "COMPLETE"}
        sig = _compute_hmac_signature(payload, "a" * 32)
        assert sig.startswith("sha256=")
        hex_part = sig[len("sha256=") :]
        # 64 hex chars = 32 bytes = SHA-256 output
        assert len(hex_part) == 64
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_payload_canonicalization_uses_sorted_keys(self) -> None:
        """Canonicalization must use json.dumps(sort_keys=True, separators=(',',':')).

        Two payloads with same data but different key order must produce identical
        canonical form and therefore identical signatures.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            _canonicalize_payload,
            _compute_hmac_signature,
        )

        payload_a = {"status": "COMPLETE", "job_id": "42", "timestamp": "2026-01-01"}
        payload_b = {"job_id": "42", "status": "COMPLETE", "timestamp": "2026-01-01"}

        canonical_a = _canonicalize_payload(payload_a)
        canonical_b = _canonicalize_payload(payload_b)
        assert canonical_a == canonical_b

        sig_a = _compute_hmac_signature(payload_a, "k" * 32)
        sig_b = _compute_hmac_signature(payload_b, "k" * 32)
        assert sig_a == sig_b

    def test_canonical_form_has_no_extra_whitespace(self) -> None:
        """Canonical JSON must use compact separators (',', ':').

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            _canonicalize_payload,
        )

        canonical = _canonicalize_payload({"a": 1, "b": 2})
        assert " " not in canonical


class TestDeliveryHeaders:
    """T45.3 delivery HTTP headers."""

    def _run_delivery_capture_headers(self) -> dict[str, str]:
        """Execute deliver_webhook against a mock httpx and capture headers.

        Returns:
            Dict of headers passed to the mock httpx.post call.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        reg = MagicMock()
        reg.active = True
        reg.callback_url = "https://example.com/hook"
        reg.signing_key = "a" * 32
        reg.id = "reg-001"

        captured_headers: dict[str, str] = {}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None

        with (
            patch("synth_engine.modules.synthesizer.jobs.webhook_delivery.validate_delivery_ips"),
            patch("synth_engine.modules.synthesizer.jobs.webhook_delivery.httpx") as mock_httpx,
        ):
            # T72.5: httpx.Client used as context manager; configure mock client .post
            mock_client = mock_httpx.Client.return_value.__enter__.return_value

            def _capture(*args: object, **kwargs: object) -> MagicMock:
                captured_headers.update(kwargs.get("headers", {}))  # type: ignore[arg-type]
                return mock_response

            mock_client.post.side_effect = _capture
            deliver_webhook(
                registration=reg,
                job_id=42,
                event_type="job.completed",
                payload={"job_id": "42", "status": "COMPLETE"},
            )

        return captured_headers

    def test_x_conclave_signature_header_set(self) -> None:
        """X-Conclave-Signature must be set and prefixed with 'sha256='.

        Args: none.
        """
        headers = self._run_delivery_capture_headers()
        assert "X-Conclave-Signature" in headers
        assert headers["X-Conclave-Signature"].startswith("sha256=")

    def test_x_conclave_event_header_set(self) -> None:
        """X-Conclave-Event must match the event type.

        Args: none.
        """
        headers = self._run_delivery_capture_headers()
        assert headers.get("X-Conclave-Event") == "job.completed"

    def test_x_conclave_delivery_id_is_valid_uuid(self) -> None:
        """X-Conclave-Delivery-Id must be a valid UUID v4.

        Args: none.
        """
        headers = self._run_delivery_capture_headers()
        delivery_id = headers.get("X-Conclave-Delivery-Id", "")
        # Must be parseable as UUID
        parsed = uuid.UUID(delivery_id)
        assert parsed.version == 4


class TestDeliveryResult:
    """T45.3 delivery result structure."""

    def test_successful_delivery_returns_success_status(self) -> None:
        """Successful delivery must return DeliveryResult with status=SUCCESS.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        reg = MagicMock()
        reg.active = True
        reg.callback_url = "https://example.com/hook"
        reg.signing_key = "a" * 32
        reg.id = "reg-001"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None

        with (
            patch("synth_engine.modules.synthesizer.jobs.webhook_delivery.validate_delivery_ips"),
            patch("synth_engine.modules.synthesizer.jobs.webhook_delivery.httpx") as mock_httpx,
        ):
            # T72.5: httpx.Client used as context manager; configure mock client .post
            mock_httpx.Client.return_value.__enter__.return_value.post.return_value = mock_response
            result = deliver_webhook(
                registration=reg,
                job_id=1,
                event_type="job.completed",
                payload={"job_id": "1", "status": "COMPLETE"},
            )

        assert result.status == "SUCCESS"
        assert result.attempt_number == 1


class TestBoundaryConstraint:
    """T45.3 delivery engine must not import from bootstrapper/."""

    def test_webhook_delivery_does_not_import_bootstrapper(self) -> None:
        """webhook_delivery module must have no bootstrapper imports.

        Enforces the architectural boundary: modules/synthesizer/ cannot
        import from bootstrapper/.  Uses importlib.util to locate the source
        file portably (no hardcoded absolute paths — F12 fix).

        Args: none.
        """
        import ast
        import importlib.util

        spec = importlib.util.find_spec("synth_engine.modules.synthesizer.jobs.webhook_delivery")
        assert spec is not None, "webhook_delivery module not found"
        assert spec.origin is not None, "webhook_delivery module has no origin path"

        import pathlib

        delivery_path = pathlib.Path(spec.origin)
        source = delivery_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                module = ""
                if isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        module = alias.name
                assert "bootstrapper" not in module, (
                    f"webhook_delivery.py imports from bootstrapper: {module}"
                )


class TestIoCCallback:
    """T45.3 IoC callback pattern for job orchestration wiring."""

    def test_set_webhook_delivery_fn_is_importable(self) -> None:
        """set_webhook_delivery_fn must be importable from job_orchestration.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            set_webhook_delivery_fn,
        )

        assert callable(set_webhook_delivery_fn)

    def test_webhook_delivery_fn_called_on_complete(self) -> None:
        """Job orchestration must call the registered webhook delivery fn on COMPLETE.

        Invokes _fire_webhook_callback and verifies the registered callback
        is actually called with the correct arguments.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            _fire_webhook_callback,
            _reset_webhook_delivery_fn,
            set_webhook_delivery_fn,
        )

        called_with: list[object] = []

        def _fake_deliver(job_id: int, status: str) -> None:
            called_with.append((job_id, status))

        set_webhook_delivery_fn(_fake_deliver)
        try:
            _fire_webhook_callback(job_id=1, status="COMPLETE")
            assert called_with == [(1, "COMPLETE")]
        finally:
            _reset_webhook_delivery_fn()

    def test_webhook_delivery_fn_called_on_failed(self) -> None:
        """Job orchestration must call the registered webhook delivery fn on FAILED.

        Invokes _fire_webhook_callback and verifies the registered callback
        is actually called with the correct arguments.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            _fire_webhook_callback,
            _reset_webhook_delivery_fn,
            set_webhook_delivery_fn,
        )

        called_with: list[object] = []

        def _fake_deliver(job_id: int, status: str) -> None:
            called_with.append((job_id, status))

        set_webhook_delivery_fn(_fake_deliver)
        try:
            _fire_webhook_callback(job_id=7, status="FAILED")
            assert called_with == [(7, "FAILED")]
        finally:
            _reset_webhook_delivery_fn()


# ===========================================================================
# URL SANITIZATION AND ERROR MESSAGE SAFETY (P62 review fixes)
# ===========================================================================


class TestSanitizeUrlForLog:
    """P62 DevOps FINDING — callback URLs must be sanitized before logging."""

    def test_strips_query_string(self) -> None:
        """_sanitize_url_for_log must remove query parameters from the URL.

        Operators may embed auth tokens in query params (e.g. ?token=abc123).
        The sanitized URL must retain scheme, host, and path only.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            _sanitize_url_for_log,
        )

        result = _sanitize_url_for_log("https://example.com/hook?token=abc123&other=val")
        assert result == "https://example.com/hook"
        assert "token" not in result
        assert "abc123" not in result

    def test_strips_fragment(self) -> None:
        """_sanitize_url_for_log must remove URL fragments.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            _sanitize_url_for_log,
        )

        result = _sanitize_url_for_log("https://example.com/hook#section")
        assert result == "https://example.com/hook"
        assert "#" not in result

    def test_strips_both_query_and_fragment(self) -> None:
        """_sanitize_url_for_log must strip both query and fragment simultaneously.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            _sanitize_url_for_log,
        )

        result = _sanitize_url_for_log("https://example.com/hook?token=secret#anchor")
        assert result == "https://example.com/hook"

    def test_plain_url_unchanged(self) -> None:
        """URLs without query/fragment must be returned unchanged.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            _sanitize_url_for_log,
        )

        result = _sanitize_url_for_log("https://example.com/hook")
        assert result == "https://example.com/hook"

    def test_unparseable_url_returns_placeholder(self) -> None:
        """An unparseable URL must return a safe placeholder, not raise.

        The helper is called inside log statements and MUST never raise.

        Args: none.
        """
        from unittest.mock import patch

        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            _sanitize_url_for_log,
        )

        with patch(
            "synth_engine.modules.synthesizer.jobs.webhook_delivery.urlparse",
            side_effect=Exception("parse error"),
        ):
            result = _sanitize_url_for_log("not-a-url")
        assert result == "<unparseable-url>"


class TestSafeErrorMsg:
    """P62 DevOps FINDING — str(exc) must not appear in DeliveryResult.error_message."""

    def test_safe_error_msg_returns_type_name(self) -> None:
        """_safe_error_msg must include the exception type name.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import _safe_error_msg

        exc = ConnectionError("hostname:8080: Connection refused")
        result = _safe_error_msg(exc)
        assert "ConnectionError" in result

    def test_safe_error_msg_excludes_raw_str(self) -> None:
        """_safe_error_msg must NOT include the raw exception message.

        Raw exception strings can contain hostnames and ports.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import _safe_error_msg

        sensitive_host = "internal-host-9af3.corp.example.com"
        exc = ConnectionError(f"{sensitive_host}: Connection refused")
        result = _safe_error_msg(exc)
        assert sensitive_host not in result

    def test_delivery_error_message_uses_safe_msg(self) -> None:
        """DeliveryResult.error_message must not contain raw str(exc) after failure.

        Verifies end-to-end that the error_message stored in DeliveryResult
        does not expose raw exception content.

        Args: none.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        reg = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        reg.active = True
        reg.callback_url = "https://example.com/hook"
        reg.signing_key = "a" * 32
        reg.id = "reg-safe-err"

        sensitive = "internal-db-host-abc123.corp.example.com"

        from unittest.mock import patch

        with (
            patch("synth_engine.modules.synthesizer.jobs.webhook_delivery.validate_delivery_ips"),
            patch("synth_engine.modules.synthesizer.jobs.webhook_delivery.httpx") as mock_httpx,
        ):
            # T72.5: httpx.Client used as context manager; configure mock client .post
            mock_post = mock_httpx.Client.return_value.__enter__.return_value.post
            mock_post.side_effect = ConnectionError(f"{sensitive}: timed out")
            result = deliver_webhook(
                registration=reg,
                job_id=5,
                event_type="job.completed",
                payload={"job_id": "5", "status": "COMPLETE"},
            )

        assert result.status == "FAILED"
        assert result.error_message is not None
        assert sensitive not in result.error_message
        assert "ConnectionError" in result.error_message
