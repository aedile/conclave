"""Negative/attack tests for DNS pinning in webhook delivery (T69.1).

Covers:
- Delivery rejected when DNS re-resolution returns a private/blocked IP
- Delivery proceeds when DNS re-resolution returns the same public IP
- DNS rebinding to link-local 169.254.169.254 (AWS metadata) blocked
- DNS rebinding to IPv4-mapped IPv6 private IP blocked at delivery
- SSRF_DELIVERY_REJECTION_TOTAL Prometheus counter increments on rejection
- Legacy webhook with NULL pinned_ips lazy-migrates by re-resolving DNS
- Dual-stack host: both A and AAAA records stored in pinned_ips
- DNS resolution failure at delivery retries per existing backoff policy
  (not silently skipped)

ATTACK-FIRST TDD — these tests are written BEFORE the GREEN phase.
CONSTITUTION Priority 0: Security — DNS rebinding TOCTOU is a P0 SSRF vector (C4)
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T69.1 — DNS Pinning for Webhook SSRF Protection
"""

from __future__ import annotations

import socket
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_circuit_breaker() -> Generator[None]:
    """Reset the module-level circuit breaker singleton between tests.

    Yields:
        None — setup and teardown only.
    """
    import synth_engine.modules.synthesizer.jobs.webhook_delivery as wd

    wd._MODULE_CIRCUIT_BREAKER = None
    yield
    wd._MODULE_CIRCUIT_BREAKER = None


@pytest.fixture(autouse=True)
def _set_development_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set CONCLAVE_ENV=development so deliver_webhook can load settings.

    The default conclave_env is 'production' (secure by default).
    Unit tests that call deliver_webhook() need development mode to avoid
    requiring all production secrets in every test.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")


# ---------------------------------------------------------------------------
# Fake webhook registration helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeRegistration:
    """Minimal fake WebhookRegistrationProtocol implementation.

    Satisfies WebhookRegistrationProtocol (and extended T69.1 interface
    that adds pinned_ips) with no SQLModel dependency.

    Attributes:
        id: Registration identifier.
        callback_url: Callback URL to deliver to.
        signing_key: HMAC signing key.
        active: Whether registration is active.
        pinned_ips: JSON-encoded list of IPs pinned at registration time.
            None means legacy (pre-T69.1) registration requiring lazy migration.
    """

    id: str = "test-reg-001"
    callback_url: str = "https://example.com/webhook"
    signing_key: str = "test-signing-key-at-least-32-chars!!"
    active: bool = True
    pinned_ips: str | None = '["93.184.216.34"]'  # example.com public IP


def _make_addr_info(ip_str: str) -> list[tuple[Any, ...]]:
    """Return a minimal getaddrinfo result for a given IP string.

    Args:
        ip_str: IP address string (IPv4 or IPv6).

    Returns:
        List mimicking socket.getaddrinfo output for a single address.
    """
    if ":" in ip_str:
        # IPv6
        return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip_str, 443, 0, 0))]
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip_str, 443))]


# ---------------------------------------------------------------------------
# Attack tests — DNS rebinding / SSRF at delivery time
# ---------------------------------------------------------------------------


class TestDNSPinningAttacks:
    """DNS pinning attack tests for webhook delivery (T69.1, C4)."""

    def test_delivery_rejected_when_dns_rebinds_to_private_ip(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DNS rebinding to private IP at delivery time must be rejected.

        Arrange: registration pinned to public IP 93.184.216.34.
                 Mock DNS at delivery time to return 10.0.0.1 (RFC 1918).
        Act: call deliver_webhook().
        Assert: DeliveryResult.status == "FAILED", error_message indicates SSRF.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        registration = _FakeRegistration(
            callback_url="https://example.com/webhook",
            pinned_ips='["93.184.216.34"]',
        )

        # DNS at delivery time returns a private RFC 1918 address
        private_addr = _make_addr_info("10.0.0.1")

        with patch("socket.getaddrinfo", return_value=private_addr):
            result = deliver_webhook(
                registration=registration,
                job_id=1,
                event_type="job.completed",
                payload={"job_id": 1, "status": "COMPLETE"},
            )

        assert result.status == "FAILED", (
            f"DNS rebinding to RFC 1918 address must fail delivery; got status={result.status!r}"
        )
        assert result.error_message is not None, (
            "error_message must be set when delivery is rejected for SSRF"
        )

    def test_delivery_rejected_dns_rebind_to_link_local_169_254_169_254(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DNS rebinding to AWS metadata endpoint 169.254.169.254 must be blocked.

        This is the canonical cloud SSRF attack vector: attacker briefly points
        the hostname DNS record at 169.254.169.254 during the TOCTOU window.

        Arrange: registration pinned to public IP.
                 Mock DNS to return 169.254.169.254 at delivery time.
        Act: call deliver_webhook().
        Assert: DeliveryResult.status == "FAILED".
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        registration = _FakeRegistration(
            callback_url="https://example.com/webhook",
            pinned_ips='["93.184.216.34"]',
        )

        metadata_endpoint = _make_addr_info("169.254.169.254")

        with patch("socket.getaddrinfo", return_value=metadata_endpoint):
            result = deliver_webhook(
                registration=registration,
                job_id=2,
                event_type="job.failed",
                payload={"job_id": 2, "status": "FAILED"},
            )

        assert result.status == "FAILED", (
            f"DNS rebinding to 169.254.169.254 (AWS metadata) must fail; "
            f"got status={result.status!r}"
        )

    def test_delivery_rejected_ipv4_mapped_ipv6_private_at_delivery(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """IPv4-mapped IPv6 private address (::ffff:10.0.0.1) blocked at delivery.

        An attacker may attempt SSRF bypass by using the IPv4-mapped IPv6 form
        of a private address.  The delivery SSRF check must unwrap mapped
        addresses before the blocked-network test.

        Arrange: registration pinned to public IP.
                 Mock DNS to return ::ffff:10.0.0.1 at delivery time.
        Act: call deliver_webhook().
        Assert: DeliveryResult.status == "FAILED".
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        registration = _FakeRegistration(
            callback_url="https://example.com/webhook",
            pinned_ips='["93.184.216.34"]',
        )

        mapped_private = _make_addr_info("::ffff:10.0.0.1")

        with patch("socket.getaddrinfo", return_value=mapped_private):
            result = deliver_webhook(
                registration=registration,
                job_id=3,
                event_type="job.completed",
                payload={"job_id": 3, "status": "COMPLETE"},
            )

        assert result.status == "FAILED", (
            f"IPv4-mapped IPv6 private address must be blocked at delivery; "
            f"got status={result.status!r}"
        )

    def test_ssrf_delivery_rejection_prometheus_counter_increments(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SSRF_DELIVERY_REJECTION_TOTAL increments when delivery is rejected for SSRF.

        Arrange: registration pinned to public IP.
                 Mock DNS to return 10.0.0.1 (private) at delivery.
        Act: call deliver_webhook().
        Assert: SSRF_DELIVERY_REJECTION_TOTAL counter value increased by 1.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook
        from synth_engine.shared.ssrf import SSRF_DELIVERY_REJECTION_TOTAL

        registration = _FakeRegistration(
            callback_url="https://example.com/webhook",
            pinned_ips='["93.184.216.34"]',
        )

        # Read the current counter value before the test
        counter_before = SSRF_DELIVERY_REJECTION_TOTAL._value.get()

        private_addr = _make_addr_info("10.0.0.1")

        with patch("socket.getaddrinfo", return_value=private_addr):
            deliver_webhook(
                registration=registration,
                job_id=4,
                event_type="job.completed",
                payload={"job_id": 4, "status": "COMPLETE"},
            )

        counter_after = SSRF_DELIVERY_REJECTION_TOTAL._value.get()
        assert counter_after > counter_before, (
            f"SSRF_DELIVERY_REJECTION_TOTAL must increment on SSRF rejection; "
            f"before={counter_before}, after={counter_after}"
        )

    def test_delivery_proceeds_when_all_ips_are_public(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Delivery proceeds when DNS re-resolution returns same public IP.

        Arrange: registration pinned to 93.184.216.34.
                 Mock DNS at delivery to return same 93.184.216.34.
                 Mock httpx.post to return 200 OK.
        Act: call deliver_webhook().
        Assert: DeliveryResult.status == "SUCCESS".
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        registration = _FakeRegistration(
            callback_url="https://example.com/webhook",
            pinned_ips='["93.184.216.34"]',
        )

        public_addr = _make_addr_info("93.184.216.34")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()  # does not raise

        with (
            patch("socket.getaddrinfo", return_value=public_addr),
            patch("httpx.Client") as mock_client_cls,
        ):
            # T72.5: httpx.Client context manager; configure mock client .post
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__enter__.return_value = mock_client
            result = deliver_webhook(
                registration=registration,
                job_id=5,
                event_type="job.completed",
                payload={"job_id": 5, "status": "COMPLETE"},
            )

        assert result.status == "SUCCESS", (
            f"Delivery to public IP must succeed; got status={result.status!r}, "
            f"error={result.error_message!r}"
        )

    def test_delivery_with_legacy_webhook_missing_pinned_ips_lazy_migrates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Legacy webhook (pinned_ips=None) is lazy-migrated at delivery time.

        When pinned_ips is NULL (pre-T69.1 registration), delivery re-resolves
        the hostname, validates the resolved IP against BLOCKED_NETWORKS, and
        proceeds if the IP is public.

        Arrange: registration with pinned_ips=None (legacy record).
                 Mock DNS to return public IP 93.184.216.34.
                 Mock httpx.post to return 200 OK.
        Act: call deliver_webhook().
        Assert: DeliveryResult.status == "SUCCESS" (legacy path works).
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        registration = _FakeRegistration(
            callback_url="https://example.com/webhook",
            pinned_ips=None,  # legacy — no pinned IPs
        )

        public_addr = _make_addr_info("93.184.216.34")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with (
            patch("socket.getaddrinfo", return_value=public_addr),
            patch("httpx.Client") as mock_client_cls,
        ):
            # T72.5: httpx.Client context manager; configure mock client .post
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__enter__.return_value = mock_client
            result = deliver_webhook(
                registration=registration,
                job_id=6,
                event_type="job.completed",
                payload={"job_id": 6, "status": "COMPLETE"},
            )

        assert result.status == "SUCCESS", (
            f"Legacy webhook (pinned_ips=None) must lazy-migrate and succeed; "
            f"got status={result.status!r}, error={result.error_message!r}"
        )

    def test_delivery_with_legacy_webhook_rejected_when_ip_is_private(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Legacy webhook lazy-migration rejects private IP during validation.

        When pinned_ips is NULL and DNS resolves to a private IP, delivery
        must be rejected (not silently accepted as 'fail-open').

        Arrange: registration with pinned_ips=None.
                 Mock DNS to return 10.0.0.1 (private RFC 1918).
        Act: call deliver_webhook().
        Assert: DeliveryResult.status == "FAILED".
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        registration = _FakeRegistration(
            callback_url="https://example.com/webhook",
            pinned_ips=None,  # legacy record
        )

        private_addr = _make_addr_info("10.0.0.1")

        with patch("socket.getaddrinfo", return_value=private_addr):
            result = deliver_webhook(
                registration=registration,
                job_id=7,
                event_type="job.failed",
                payload={"job_id": 7, "status": "FAILED"},
            )

        assert result.status == "FAILED", (
            f"Legacy webhook with private IP must fail delivery; got status={result.status!r}"
        )

    def test_dns_resolution_failure_at_delivery_returns_failed_not_skipped(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DNS resolution failure at delivery time does not silently skip delivery.

        Per T69.1 AC3: DNS failure at delivery must not be silently skipped.
        The result must be FAILED (not SKIPPED) so the operator is aware.
        This is a change from the current strict=False/fail-open behavior.

        Arrange: mock socket.getaddrinfo to raise gaierror.
        Act: call deliver_webhook().
        Assert: DeliveryResult.status == "FAILED" (not "SKIPPED").
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        registration = _FakeRegistration(
            callback_url="https://example.com/webhook",
            pinned_ips='["93.184.216.34"]',
        )

        with patch(
            "socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            result = deliver_webhook(
                registration=registration,
                job_id=8,
                event_type="job.completed",
                payload={"job_id": 8, "status": "COMPLETE"},
            )

        assert result.status == "FAILED", (
            f"DNS resolution failure must result in FAILED (not SKIPPED); "
            f"got status={result.status!r}"
        )
        assert result.status != "SKIPPED", (
            "SKIPPED is reserved for inactive registrations and open circuit breaker; "
            "DNS failure must be FAILED so operators are notified"
        )


# ---------------------------------------------------------------------------
# Registration-time pinning tests
# ---------------------------------------------------------------------------


class TestDNSPinningAtRegistration:
    """Tests for pinned_ips storage at registration time (T69.1 AC1)."""

    def test_pinned_ips_stores_all_addresses_for_dual_stack_host(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both A and AAAA records are stored in pinned_ips at registration.

        Arrange: mock getaddrinfo to return both IPv4 and IPv6 addresses.
        Act: call the SSRF validation / IP pinning function.
        Assert: returned pinned_ips list contains both addresses.
        """
        from synth_engine.shared.ssrf import resolve_and_pin_ips

        both_addrs = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443)),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                0,
                "",
                ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0),
            ),
        ]

        with patch("socket.getaddrinfo", return_value=both_addrs):
            pinned = resolve_and_pin_ips("example.com")

        assert "93.184.216.34" in pinned, f"IPv4 address must be in pinned list; got {pinned!r}"
        assert "2606:2800:220:1:248:1893:25c8:1946" in pinned, (
            f"IPv6 address must be in pinned list; got {pinned!r}"
        )
        assert len(pinned) == 2, (
            f"Exactly 2 addresses expected for dual-stack host; got {len(pinned)}: {pinned!r}"
        )

    def test_resolve_and_pin_ips_raises_on_private_ip(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """resolve_and_pin_ips raises ValueError for private IP at registration.

        Even if the URL passes the basic validate_callback_url check, direct
        pinning of a private IP must be rejected.

        Arrange: mock getaddrinfo to return 192.168.1.1 (RFC 1918).
        Act: call resolve_and_pin_ips().
        Assert: ValueError raised.
        """
        from synth_engine.shared.ssrf import resolve_and_pin_ips

        private_addr = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 443))]

        with (
            patch("socket.getaddrinfo", return_value=private_addr),
            pytest.raises(ValueError, match="private"),
        ):
            resolve_and_pin_ips("internal-host.example.com")

    def test_resolve_and_pin_ips_raises_on_dns_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """resolve_and_pin_ips raises ValueError when DNS resolution fails.

        At registration time, DNS failure must be fail-closed (rejects the URL).
        This prevents pre-registering an unresolvable hostname that is later
        repointed to an internal target.

        Arrange: mock getaddrinfo to raise gaierror.
        Act: call resolve_and_pin_ips().
        Assert: ValueError raised.
        """
        from synth_engine.shared.ssrf import resolve_and_pin_ips

        with (
            patch(
                "socket.getaddrinfo",
                side_effect=socket.gaierror("Name or service not known"),
            ),
            pytest.raises(ValueError, match="could not be resolved"),
        ):
            resolve_and_pin_ips("unresolvable.invalid")
