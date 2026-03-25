"""Attack and feature tests for SSRF fail-closed behavior (T55.4).

Attack-first ordering per Constitution Priority 0 and Rule 22.

Attack/negative tests (committed first):
1.  DNS failure at registration → rejection (fail-closed, strict=True default)
2.  Resolves to internal IP after DNS → rejection
3.  DNS failure at delivery → allowed (fail-open, strict=False)
4.  strict parameter defaults to True (fail-closed by default)

Feature/positive tests:
5.  strict=True raises ValueError on DNS error (explicit)
6.  strict=False passes on DNS error (explicit)
7.  Webhook registration code path calls validate_callback_url with strict=True
8.  Webhook delivery code path calls validate_callback_url with strict=False

CONSTITUTION Priority 0: Security — SSRF prevention (fail-closed at registration)
CONSTITUTION Priority 3: TDD — attack-first RED phase
Task: T55.4 — SSRF Registration Fail-Closed for Phase 55
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Attack / negative tests (ATTACK RED — these must be committed first)
# ---------------------------------------------------------------------------


class TestSSRFAttackFailClosed:
    """Attack tests: SSRF fail-closed behavior at registration time."""

    def test_ssrf_registration_rejects_unresolvable_hostname(self) -> None:
        """DNS failure at registration must cause rejection (fail-closed).

        An attacker could register a URL pointing to an internal host whose
        DNS entry is not yet visible to the engine — but they control the DNS
        record and will point it to an internal target after registration.
        With strict=True (the default), DNS failure is treated as a security
        risk and the URL is rejected.

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        with patch(
            "synth_engine.shared.ssrf.socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            with pytest.raises(ValueError, match="private|reserved|forbidden|DNS|unresolvable"):
                validate_callback_url("https://attacker-controlled.internal/hook")

    def test_ssrf_registration_rejects_internal_ip_after_dns_resolution(self) -> None:
        """DNS resolution to 10.x must be rejected at registration time.

        An attacker registers a public-looking hostname that actually resolves
        to an internal RFC-1918 address. strict=True (default) must block this.

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        fake_addr = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 80))]
        with patch("synth_engine.shared.ssrf.socket.getaddrinfo", return_value=fake_addr):
            with pytest.raises(ValueError, match="private|reserved|forbidden"):
                validate_callback_url("https://looks-public.example.com/hook")

    def test_ssrf_delivery_allows_dns_failure_fallback(self) -> None:
        """DNS failure at delivery time must be allowed (fail-open, strict=False).

        The delivery engine passes strict=False so that transient DNS failures
        do not abort delivery attempts — the HTTP call itself will fail if the
        host is truly unreachable.

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        with patch(
            "synth_engine.shared.ssrf.socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            # Must NOT raise with strict=False — existing fail-open behavior preserved
            validate_callback_url(
                "https://nonexistent.example.com/hook",
                strict=False,
            )

    def test_ssrf_strict_default_is_true(self) -> None:
        """validate_callback_url() must default to strict=True (fail-closed).

        Calling without an explicit strict argument must behave identically to
        strict=True.  This test guards against someone changing the default.

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        with patch(
            "synth_engine.shared.ssrf.socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            # Calling with NO strict argument: must raise (same as strict=True)
            with pytest.raises(ValueError, match="private|reserved|forbidden|DNS|unresolvable"):
                validate_callback_url("https://unresolvable.example.com/hook")


# ---------------------------------------------------------------------------
# Feature / positive tests
# ---------------------------------------------------------------------------


class TestSSRFFailClosedFeature:
    """Feature tests: explicit strict parameter behavior."""

    def test_validate_callback_url_strict_true_raises_on_dns_error(self) -> None:
        """Explicit strict=True raises ValueError when DNS resolution fails.

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        with patch(
            "synth_engine.shared.ssrf.socket.getaddrinfo",
            side_effect=socket.gaierror("NXDOMAIN"),
        ):
            with pytest.raises(ValueError, match="private|reserved|forbidden|DNS|unresolvable"):
                validate_callback_url("https://example.com/hook", strict=True)

    def test_validate_callback_url_strict_false_passes_on_dns_error(self) -> None:
        """Explicit strict=False does not raise when DNS resolution fails.

        Args: none (no parameters).
        """
        from synth_engine.shared.ssrf import validate_callback_url

        with patch(
            "synth_engine.shared.ssrf.socket.getaddrinfo",
            side_effect=socket.gaierror("NXDOMAIN"),
        ):
            # Must not raise — fail-open preserved for delivery path
            validate_callback_url("https://example.com/hook", strict=False)

    def test_webhook_registration_uses_strict_true(self) -> None:
        """Webhook registration module calls validate_callback_url with strict=True.

        The bootstrapper registration endpoint must enforce fail-closed behavior
        by passing strict=True explicitly so that DNS failures reject the URL.
        (P55 arch review: _ssrf_validate_registration wrapper inlined — this test
        now verifies the inlined call at the module import level.)

        Args: none (no parameters).
        """
        import inspect

        import synth_engine.bootstrapper.routers.webhooks as webhooks_module

        source = inspect.getsource(webhooks_module)
        # Verify the inlined call uses strict=True
        assert "validate_callback_url(body.callback_url, strict=True)" in source, (
            "webhooks.py must call validate_callback_url with strict=True at registration"
        )

    def test_webhook_delivery_uses_strict_false(self) -> None:
        """Webhook delivery module calls validate_callback_url with strict=False.

        The delivery loop re-validates for DNS-rebinding protection but uses
        strict=False so that transient DNS failures do not abort delivery.
        (P55 arch review: _ssrf_validate_delivery wrapper inlined — this test
        now verifies the inlined call at the module import level.)

        Args: none (no parameters).
        """
        import inspect

        import synth_engine.modules.synthesizer.webhook_delivery as delivery_module

        source = inspect.getsource(delivery_module)
        # Verify the inlined call uses strict=False
        assert "validate_callback_url(registration.callback_url, strict=False)" in source, (
            "webhook_delivery.py must call validate_callback_url with strict=False at delivery"
        )
