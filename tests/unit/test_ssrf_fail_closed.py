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
        assert validate_callback_url.__name__ == "validate_callback_url"

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
        assert validate_callback_url.__name__ == "validate_callback_url"

    def test_webhook_registration_uses_strict_true(self) -> None:
        """Webhook registration endpoint calls validate_callback_url with strict=True.

        Behavioral test: calls the actual POST /webhooks/ endpoint handler and
        asserts that validate_callback_url is invoked with strict=True.

        The bootstrapper registration endpoint must enforce fail-closed behavior
        by passing strict=True explicitly so that DNS failures reject the URL.

        Args: none (no parameters).
        """
        from collections.abc import Generator
        from unittest.mock import MagicMock, patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlmodel import Session

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.dependencies.tenant import TenantContext, get_current_user
        from synth_engine.bootstrapper.routers.webhooks import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")

        mock_session = MagicMock(spec=Session)
        # Simulate empty registration list (no limit exceeded)
        mock_session.exec.return_value.all.return_value = []
        mock_session.commit = MagicMock()

        def mock_add(obj: object) -> None:
            # After add+commit, populate required fields for response serialization
            if hasattr(obj, "callback_url"):
                obj.id = "test-id-123"  # type: ignore[union-attr]
                obj.events = '["job.completed"]'  # type: ignore[union-attr]
                obj.active = True  # type: ignore[union-attr]
                obj.owner_id = "op-test"  # type: ignore[union-attr]

        mock_session.add = mock_add
        mock_session.refresh = MagicMock()

        def override_db_session() -> Generator[Session]:
            yield mock_session

        app.dependency_overrides[get_current_user] = lambda: TenantContext(
            org_id="00000000-0000-0000-0000-000000000000",
            user_id="op-test",
            role="admin",
        )
        app.dependency_overrides[get_db_session] = override_db_session

        callback_url = "https://hooks.example.com/webhook"
        signing_key = "a" * 32  # 32-char minimum

        with (
            patch(
                "synth_engine.bootstrapper.routers.webhooks.get_settings",
            ) as mock_wh_settings,
            patch(
                "synth_engine.bootstrapper.routers.webhooks.validate_callback_url"
            ) as mock_validate,
            patch(
                "synth_engine.bootstrapper.routers.webhooks.resolve_and_pin_ips",
                return_value=["93.184.216.34"],
            ),
        ):
            mock_wh_settings.return_value.is_production.return_value = False
            mock_wh_settings.return_value.webhook_max_registrations = 10

            client = TestClient(app, raise_server_exceptions=True)
            response = client.post(
                "/api/v1/webhooks/",
                json={
                    "callback_url": callback_url,
                    "signing_key": signing_key,
                    "events": ["job.completed"],
                },
            )

        # Assert validate_callback_url was called with strict=True
        mock_validate.assert_called_once_with(callback_url, strict=True)
        assert response.status_code == 201, (
            f"Expected 201 Created, got {response.status_code}: {response.text!r}"
        )

    def test_webhook_delivery_uses_validate_delivery_ips(self) -> None:
        """Webhook delivery calls validate_delivery_ips with the hostname (T69.1).

        Behavioral test: calls deliver_webhook() directly and asserts that
        validate_delivery_ips is invoked with the hostname extracted from the
        callback URL (fail-closed DNS re-validation at delivery time).

        T69.1 replaced validate_callback_url(strict=False) with validate_delivery_ips
        for fail-closed DNS rebinding protection.

        Args: none (no parameters).
        """
        from unittest.mock import MagicMock, patch

        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        mock_registration = MagicMock()
        mock_registration.active = True
        mock_registration.id = "reg-uuid-001"
        mock_registration.callback_url = "https://delivery.example.com/hook"
        mock_registration.signing_key = "b" * 32
        mock_registration.events = ["job.completed"]

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.validate_delivery_ips"
            ) as mock_validate,
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.httpx.Client"
            ) as mock_client_cls,
        ):
            mock_validate.return_value = None  # SSRF check passes
            # T72.5: httpx.Client context manager; configure mock client .post
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.raise_for_status = MagicMock()
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__enter__.return_value = mock_client

            deliver_webhook(
                registration=mock_registration,
                job_id=42,
                event_type="job.completed",
                payload={"job_id": "42", "status": "COMPLETE"},
                timeout_seconds=5,
            )

        # validate_delivery_ips must be called with the hostname (T69.1 — fail-closed)
        mock_validate.assert_called_once_with("delivery.example.com", pinned_ips=None)
        assert mock_validate.call_count == 1
