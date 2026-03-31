"""Negative/attack tests for exception specificity hardening (P72).

Verifies that:
1. Unexpected exceptions (RuntimeError, TypeError, AttributeError, etc.)
   are NOT swallowed by the narrowed (ValueError, OSError) catches in routers.
   They must propagate so FastAPI's default 500 handler fires, which is the
   correct behavior — swallowing arbitrary exceptions hides programming errors.

2. The narrowed catches still handle ValueError and OSError correctly:
   - Return 500 with the correct RFC 7807 body
   - Increment AUDIT_WRITE_FAILURE_TOTAL counter

3. Lifecycle shutdown catches only the exceptions the called functions can raise.

4. Retention cleanup catches only OSError / SQLAlchemyError, not arbitrary exceptions.

5. The httpx.Client context manager in webhook_delivery maintains correct
   session-per-invocation behavior and does not leak connections.

ATTACK-FIRST TDD per Rule 22.
CONSTITUTION Priority 0: Security — unexpected exception swallowing hides bugs.
CONSTITUTION Priority 3: TDD — attack tests committed before feature tests.
Task: P72 — Exception Specificity & Router Safety Hardening
"""

from __future__ import annotations

import base64
import os
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_audit_logger() -> Generator[None]:
    """Reset audit logger singleton after each test.

    Yields:
        None — setup/teardown only.
    """
    yield
    from synth_engine.shared.security.audit import reset_audit_logger

    reset_audit_logger()


@pytest.fixture(autouse=True)
def _set_audit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a valid AUDIT_KEY for tests that instantiate the audit logger.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("AUDIT_KEY", os.urandom(32).hex())
    monkeypatch.setenv("CONCLAVE_ENV", "development")


@pytest.fixture(autouse=True)
def _reset_vault(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Ensure VaultState is reset before and after each test.

    Yields:
        None — setup/teardown only.
    """
    from synth_engine.shared.security.vault import VaultState

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    VaultState.reset()
    yield
    VaultState.reset()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings to prevent cross-test contamination.

    Yields:
        None — setup/teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# T72.1 ATTACK: Unexpected exceptions must NOT be swallowed by router catches
# ---------------------------------------------------------------------------


class TestRouterAuditCatchNotBroad:
    """Verify the router audit-write catches do not swallow unexpected exceptions.

    A RuntimeError from an unexpected source (programming error) must NOT be
    caught by the audit-write guard — only (ValueError, OSError) should be caught.
    This is the key invariant: the narrowed catches preserve transparency for
    unexpected failures.
    """

    def test_privacy_router_unexpected_exception_propagates(self) -> None:
        """RuntimeError from audit.log_event must propagate — not be caught.

        In the current broad-catch implementation, a RuntimeError from
        get_audit_logger() would be caught and return 500.  After the fix,
        only (ValueError, OSError) are caught; RuntimeError propagates.

        This test verifies the narrowed behavior by checking that when
        get_audit_logger() raises RuntimeError, it is NOT caught by the
        audit-write guard (the guard should only catch ValueError, OSError).
        """
        from synth_engine.bootstrapper.routers.privacy import refresh_budget
        from synth_engine.bootstrapper.schemas.privacy import BudgetRefreshRequest

        # Build a mock session with a ledger row
        mock_ledger = MagicMock()
        mock_ledger.id = 1
        mock_ledger.total_allocated_epsilon = "10.0"
        mock_ledger.total_spent_epsilon = "2.0"
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = mock_ledger

        body = BudgetRefreshRequest(justification="test justification long enough")

        # Inject a RuntimeError from get_audit_logger().log_event — not ValueError/OSError
        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("unexpected programming error")

        with patch(
            "synth_engine.bootstrapper.routers.privacy.get_audit_logger",
            return_value=mock_audit,
        ):
            # After narrowing: RuntimeError propagates (not caught by ValueError/OSError guard)
            # This test documents the EXPECTED behavior post-fix.
            # If the catch is still broad (Exception), this call would return JSONResponse(500)
            # instead of raising.
            with pytest.raises(RuntimeError, match="unexpected programming error"):
                refresh_budget(body=body, session=mock_session, current_operator="op1")

    def test_connections_router_unexpected_exception_propagates(self) -> None:
        """RuntimeError from get_audit_logger() must propagate in delete_connection.

        After narrowing to (ValueError, OSError), a RuntimeError from
        get_audit_logger() is NOT swallowed by the audit-write guard.
        """
        from synth_engine.bootstrapper.routers.connections import delete_connection

        mock_conn = MagicMock()
        mock_conn.owner_id = "op1"
        mock_session = MagicMock()
        mock_session.get.return_value = mock_conn

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("unexpected error in audit backend")

        with patch(
            "synth_engine.bootstrapper.routers.connections.get_audit_logger",
            return_value=mock_audit,
        ):
            with pytest.raises(RuntimeError, match="unexpected error in audit backend"):
                delete_connection(
                    connection_id="conn-123",
                    session=mock_session,
                    current_operator="op1",
                )

    def test_jobs_router_unexpected_exception_propagates(self) -> None:
        """RuntimeError from audit.log_event must propagate in shred_job.

        After narrowing, unexpected exceptions from get_audit_logger() are NOT
        caught by the (ValueError, OSError) guard in shred_job.
        """
        from synth_engine.bootstrapper.routers.jobs import shred_job

        mock_job = MagicMock()
        mock_job.owner_id = "op1"
        mock_job.status = "COMPLETE"
        mock_job.table_name = "customers"
        mock_session = MagicMock()
        mock_session.get.return_value = mock_job

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("unexpected DB error")

        with patch(
            "synth_engine.bootstrapper.routers.jobs.get_audit_logger",
            return_value=mock_audit,
        ):
            with pytest.raises(RuntimeError, match="unexpected DB error"):
                shred_job(job_id=1, session=mock_session, current_operator="op1")

    def test_admin_router_unexpected_exception_propagates(self) -> None:
        """RuntimeError from get_audit_logger() must propagate in set_legal_hold.

        After narrowing, unexpected exceptions from get_audit_logger() are NOT
        caught by the (ValueError, OSError) guard in set_legal_hold.
        """
        from synth_engine.bootstrapper.routers.admin import LegalHoldRequest, set_legal_hold

        mock_job = MagicMock()
        mock_job.owner_id = "op1"
        mock_job.legal_hold = False
        mock_session = MagicMock()
        mock_session.get.return_value = mock_job

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("audit backend crashed")

        with patch(
            "synth_engine.bootstrapper.routers.admin.get_audit_logger",
            return_value=mock_audit,
        ):
            with pytest.raises(RuntimeError, match="audit backend crashed"):
                set_legal_hold(
                    job_id=1,
                    body=LegalHoldRequest(enable=True),
                    session=mock_session,
                    current_operator="op1",
                )

    def test_settings_router_unexpected_exception_propagates_upsert(self) -> None:
        """RuntimeError from get_audit_logger() must propagate in upsert_setting.

        After narrowing, unexpected exceptions from get_audit_logger() are NOT
        caught by the (ValueError, OSError) guard in upsert_setting.
        """
        from synth_engine.bootstrapper.routers.settings import (
            SettingUpsertRequest,
            upsert_setting,
        )

        mock_session = MagicMock()
        mock_session.get.return_value = None  # setting does not exist yet

        with patch(
            "synth_engine.bootstrapper.routers.settings.get_audit_logger",
        ) as mock_get_logger:
            mock_audit = MagicMock()
            mock_audit.log_event.side_effect = RuntimeError("file handle closed unexpectedly")
            mock_get_logger.return_value = mock_audit

            with pytest.raises(RuntimeError, match="file handle closed unexpectedly"):
                upsert_setting(
                    key="my-key",
                    body=SettingUpsertRequest(value="my-value"),
                    session=mock_session,
                    current_operator="op1",
                )

    def test_settings_router_unexpected_exception_propagates_delete(self) -> None:
        """RuntimeError from get_audit_logger() must propagate in delete_setting.

        After narrowing, unexpected exceptions from get_audit_logger() are NOT
        caught by the (ValueError, OSError) guard in delete_setting.
        """
        from synth_engine.bootstrapper.routers.settings import delete_setting

        mock_setting = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = mock_setting

        with patch(
            "synth_engine.bootstrapper.routers.settings.get_audit_logger",
        ) as mock_get_logger:
            mock_audit = MagicMock()
            mock_audit.log_event.side_effect = RuntimeError("unexpected handler error")
            mock_get_logger.return_value = mock_audit

            with pytest.raises(RuntimeError, match="unexpected handler error"):
                delete_setting(
                    key="my-key",
                    session=mock_session,
                    current_operator="op1",
                )

    def test_webhooks_router_unexpected_exception_propagates(self) -> None:
        """RuntimeError from get_audit_logger() must propagate in deactivate_webhook.

        After narrowing, unexpected exceptions from get_audit_logger() are NOT
        caught by the (ValueError, OSError) guard in deactivate_webhook.
        """
        from synth_engine.bootstrapper.routers.webhooks import deactivate_webhook
        from synth_engine.bootstrapper.schemas.webhooks import WebhookRegistration

        mock_reg = MagicMock(spec=WebhookRegistration)
        mock_reg.owner_id = "op1"
        mock_reg.active = True
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = mock_reg

        with patch(
            "synth_engine.bootstrapper.routers.webhooks.get_audit_logger",
        ) as mock_get_logger:
            mock_audit = MagicMock()
            mock_audit.log_event.side_effect = RuntimeError("HMAC key corrupted")
            mock_get_logger.return_value = mock_audit

            with pytest.raises(RuntimeError, match="HMAC key corrupted"):
                deactivate_webhook(
                    webhook_id="webhook-123",
                    session=mock_session,
                    current_operator="op1",
                )


# ---------------------------------------------------------------------------
# T72.1 ATTACK: ValueError and OSError are still caught correctly
# ---------------------------------------------------------------------------


class TestRouterAuditCatchHandlesExpectedExceptions:
    """Verify the narrowed catches still handle ValueError and OSError.

    Both ValueError (from sign_v3 oversized payload) and OSError (from logging
    backend failure) must still be caught, return 500, and increment the counter.
    """

    def _make_mock_ledger(self) -> MagicMock:
        """Build a mock ledger row for privacy router tests.

        Returns:
            Mock object with ledger fields set.
        """
        ledger = MagicMock()
        ledger.id = 1
        ledger.total_allocated_epsilon = "10.0"
        ledger.total_spent_epsilon = "2.0"
        return ledger

    def test_privacy_router_value_error_returns_500(self) -> None:
        """ValueError from log_event (sign_v3 oversized details) returns 500.

        The narrowed catch (ValueError, OSError) must still handle ValueError
        from sign_v3 with the correct 500 response and counter increment.
        """
        from fastapi.responses import JSONResponse

        from synth_engine.bootstrapper.routers.privacy import refresh_budget
        from synth_engine.bootstrapper.schemas.privacy import BudgetRefreshRequest

        ledger = self._make_mock_ledger()
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = ledger

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = ValueError("oversized details payload")

        with (
            patch(
                "synth_engine.bootstrapper.routers.privacy.get_audit_logger",
                return_value=mock_audit,
            ),
            patch(
                "synth_engine.bootstrapper.routers.privacy.AUDIT_WRITE_FAILURE_TOTAL"
            ) as mock_counter,
        ):
            mock_labels = MagicMock()
            mock_counter.labels.return_value = mock_labels

            result = refresh_budget(
                body=BudgetRefreshRequest(justification="test justification long enough"),
                session=mock_session,
                current_operator="op1",
            )

        assert isinstance(result, JSONResponse), "Must return JSONResponse on ValueError"
        assert result.status_code == 500, f"Expected 500, got {result.status_code}"
        mock_counter.labels.assert_called_once()
        mock_labels.inc.assert_called_once()

    def test_privacy_router_os_error_returns_500(self) -> None:
        """OSError from log_event (logging backend disk full) returns 500.

        The narrowed catch (ValueError, OSError) must still handle OSError
        from the logging backend with the correct 500 response.
        """
        from fastapi.responses import JSONResponse

        from synth_engine.bootstrapper.routers.privacy import refresh_budget
        from synth_engine.bootstrapper.schemas.privacy import BudgetRefreshRequest

        ledger = self._make_mock_ledger()
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = ledger

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = OSError("disk full")

        with (
            patch(
                "synth_engine.bootstrapper.routers.privacy.get_audit_logger",
                return_value=mock_audit,
            ),
            patch(
                "synth_engine.bootstrapper.routers.privacy.AUDIT_WRITE_FAILURE_TOTAL"
            ) as mock_counter,
        ):
            mock_labels = MagicMock()
            mock_counter.labels.return_value = mock_labels

            result = refresh_budget(
                body=BudgetRefreshRequest(justification="test justification long enough"),
                session=mock_session,
                current_operator="op1",
            )

        assert isinstance(result, JSONResponse), "Must return JSONResponse on OSError"
        assert result.status_code == 500, f"Expected 500, got {result.status_code}"
        mock_counter.labels.assert_called_once()
        mock_labels.inc.assert_called_once()

    def test_connections_router_value_error_returns_500(self) -> None:
        """ValueError from log_event in delete_connection returns 500.

        The narrowed catch (ValueError, OSError) must still handle ValueError.
        """
        from fastapi.responses import JSONResponse

        from synth_engine.bootstrapper.routers.connections import delete_connection

        mock_conn = MagicMock()
        mock_conn.owner_id = "op1"
        mock_session = MagicMock()
        mock_session.get.return_value = mock_conn

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = ValueError("details too large")

        with (
            patch(
                "synth_engine.bootstrapper.routers.connections.get_audit_logger",
                return_value=mock_audit,
            ),
            patch(
                "synth_engine.bootstrapper.routers.connections.AUDIT_WRITE_FAILURE_TOTAL"
            ) as mock_counter,
        ):
            mock_labels = MagicMock()
            mock_counter.labels.return_value = mock_labels

            result = delete_connection(
                connection_id="conn-123",
                session=mock_session,
                current_operator="op1",
            )

        assert isinstance(result, JSONResponse), "Must return JSONResponse on ValueError"
        assert result.status_code == 500, f"Expected 500, got {result.status_code}"

    def test_jobs_router_os_error_returns_500(self) -> None:
        """OSError from log_event in shred_job returns 500.

        The narrowed catch (ValueError, OSError) must still handle OSError.
        """
        from fastapi.responses import JSONResponse

        from synth_engine.bootstrapper.routers.jobs import shred_job

        mock_job = MagicMock()
        mock_job.owner_id = "op1"
        mock_job.status = "COMPLETE"
        mock_job.table_name = "employees"
        mock_session = MagicMock()
        mock_session.get.return_value = mock_job

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = OSError("log handler closed")

        with (
            patch(
                "synth_engine.bootstrapper.routers.jobs.get_audit_logger",
                return_value=mock_audit,
            ),
            patch(
                "synth_engine.bootstrapper.routers.jobs.AUDIT_WRITE_FAILURE_TOTAL"
            ) as mock_counter,
        ):
            mock_labels = MagicMock()
            mock_counter.labels.return_value = mock_labels

            result = shred_job(job_id=1, session=mock_session, current_operator="op1")

        assert isinstance(result, JSONResponse), "Must return JSONResponse on OSError"
        assert result.status_code == 500, f"Expected 500, got {result.status_code}"

    def test_settings_router_value_error_returns_500_on_upsert(self) -> None:
        """ValueError from log_event in upsert_setting returns 500.

        The narrowed catch (ValueError, OSError) must handle ValueError.
        """
        from fastapi.responses import JSONResponse

        from synth_engine.bootstrapper.routers.settings import (
            SettingUpsertRequest,
            upsert_setting,
        )

        mock_session = MagicMock()
        mock_session.get.return_value = None

        with (
            patch(
                "synth_engine.bootstrapper.routers.settings.get_audit_logger",
            ) as mock_get_logger,
            patch(
                "synth_engine.bootstrapper.routers.settings.AUDIT_WRITE_FAILURE_TOTAL"
            ) as mock_counter,
        ):
            mock_audit = MagicMock()
            mock_audit.log_event.side_effect = ValueError("non-serializable float")
            mock_get_logger.return_value = mock_audit
            mock_labels = MagicMock()
            mock_counter.labels.return_value = mock_labels

            result = upsert_setting(
                key="my-key",
                body=SettingUpsertRequest(value="my-value"),
                session=mock_session,
                current_operator="op1",
            )

        assert isinstance(result, JSONResponse), "Must return JSONResponse on ValueError"
        assert result.status_code == 500, f"Expected 500, got {result.status_code}"

    def test_webhooks_router_os_error_returns_500(self) -> None:
        """OSError from log_event in deactivate_webhook returns 500.

        The narrowed catch (ValueError, OSError) must handle OSError.
        """
        from fastapi.responses import JSONResponse

        from synth_engine.bootstrapper.routers.webhooks import deactivate_webhook
        from synth_engine.bootstrapper.schemas.webhooks import WebhookRegistration

        mock_reg = MagicMock(spec=WebhookRegistration)
        mock_reg.owner_id = "op1"
        mock_reg.active = True
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = mock_reg

        with (
            patch(
                "synth_engine.bootstrapper.routers.webhooks.get_audit_logger",
            ) as mock_get_logger,
            patch(
                "synth_engine.bootstrapper.routers.webhooks.AUDIT_WRITE_FAILURE_TOTAL"
            ) as mock_counter,
        ):
            mock_audit = MagicMock()
            mock_audit.log_event.side_effect = OSError("disk full on audit log")
            mock_get_logger.return_value = mock_audit
            mock_labels = MagicMock()
            mock_counter.labels.return_value = mock_labels

            result = deactivate_webhook(
                webhook_id="webhook-456",
                session=mock_session,
                current_operator="op1",
            )

        assert isinstance(result, JSONResponse), "Must return JSONResponse on OSError"
        assert result.status_code == 500, f"Expected 500, got {result.status_code}"


# ---------------------------------------------------------------------------
# T72.2 ATTACK: Lifecycle shutdown — unexpected exceptions from cleanup funcs
# ---------------------------------------------------------------------------


class TestLifecycleShutdownCatchSpecificity:
    """Verify lifecycle shutdown catches are narrow enough.

    dispose_engines() can raise OSError or SQLAlchemyError.
    close_redis_client() can raise redis.RedisError or OSError.
    The audit log_event can raise ValueError or OSError.
    RuntimeError from dispose_engines should NOT be silently swallowed.
    """

    def test_dispose_engines_unexpected_exception_propagates(self) -> None:
        """RuntimeError from dispose_engines() must NOT be swallowed in lifecycle.

        dispose_engines() can raise OSError/SQLAlchemyError, not RuntimeError.
        If it raises RuntimeError, the lifecycle should propagate it (programming error).
        After narrowing to (OSError, SQLAlchemyError), RuntimeError propagates.
        """
        import asyncio

        from synth_engine.bootstrapper.lifecycle import _lifespan

        mock_app = MagicMock()

        async def _run() -> None:
            async with _lifespan(mock_app):
                pass

        with (
            patch("synth_engine.bootstrapper.lifecycle.validate_config"),
            patch("synth_engine.bootstrapper.lifecycle.update_cert_expiry_metrics"),
            patch(
                "synth_engine.bootstrapper.lifecycle.get_audit_logger",
            ) as mock_audit_factory,
            patch(
                "synth_engine.bootstrapper.lifecycle.dispose_engines",
                side_effect=RuntimeError("pool corrupted"),
            ),
            patch(
                "synth_engine.bootstrapper.lifecycle.close_redis_client",
            ) as mock_close_redis,  # noqa: F841
        ):
            mock_audit = MagicMock()
            mock_audit_factory.return_value = mock_audit

            # After narrowing dispose_engines catch to (OSError, SQLAlchemyError):
            # RuntimeError propagates out of the finally block.
            with pytest.raises(RuntimeError, match="pool corrupted"):
                asyncio.run(_run())


# ---------------------------------------------------------------------------
# T72.4 ATTACK: Retention cleanup — unexpected exceptions
# ---------------------------------------------------------------------------


class TestRetentionCleanupCatchSpecificity:
    """Verify retention cleanup narrows its per-job catch to expected exceptions.

    The per-job error isolation loop currently catches Exception broadly.
    After narrowing to (OSError, SQLAlchemyError), a programming error
    (e.g. TypeError) should NOT be swallowed — it propagates and halts the loop.
    This is the correct fail-fast behavior for unexpected errors.
    """

    def test_cleanup_jobs_type_error_propagates(self) -> None:
        """TypeError from session.delete() must propagate — not be swallowed.

        After narrowing the per-job catch to (OSError, SQLAlchemyError),
        a TypeError (programming error) propagates to the caller.
        """
        from sqlalchemy import create_engine as sa_create_engine
        from sqlmodel import SQLModel

        from synth_engine.modules.synthesizer.storage.retention import RetentionCleanup

        engine = sa_create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(engine)

        cleanup = RetentionCleanup(engine=engine, job_retention_days=0)

        with patch(
            "synth_engine.modules.synthesizer.storage.retention.Session",
        ) as mock_session_cls:
            # Build a fake expired job
            mock_job = MagicMock()
            mock_job.id = 1
            mock_job.table_name = "employees"
            mock_job.output_path = None

            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.exec.return_value.all.return_value = [mock_job]

            # Inject a TypeError (programming error) from session.delete()
            mock_session.delete.side_effect = TypeError("unexpected type passed to delete()")
            mock_session_cls.return_value = mock_session

            # After narrowing: TypeError is NOT caught by (OSError, SQLAlchemyError)
            # It propagates to the caller (correct fail-fast for programming errors)
            with pytest.raises(TypeError, match="unexpected type passed to delete"):
                cleanup.cleanup_expired_jobs()


# ---------------------------------------------------------------------------
# T72.5 ATTACK: httpx.Client context manager — connection not leaked on error
# ---------------------------------------------------------------------------


class TestWebhookDeliveryHttpxClient:
    """Verify that the httpx.Client is properly closed even on exception.

    Using httpx.Client as a context manager guarantees the connection is
    closed when an exception occurs mid-delivery. This test verifies that
    after the change, the client is closed properly.
    """

    def test_httpx_client_closed_on_exception(self) -> None:
        """httpx.Client must be closed when an exception occurs during delivery.

        Using httpx.Client as a context manager ensures __exit__ is called
        even when an exception propagates. This test verifies the context
        manager protocol is honoured.
        """
        import httpx

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        mock_registration = MagicMock()
        mock_registration.id = "reg-123"
        mock_registration.active = True
        mock_registration.callback_url = "https://example.com/webhook"
        mock_registration.signing_key = "secret"
        mock_registration.pinned_ips = None

        with (
            patch("httpx.Client", return_value=mock_client),
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery._get_circuit_breaker"
            ) as mock_cb_factory,
        ):
            mock_cb = MagicMock()
            mock_cb.is_open.return_value = False
            mock_cb_factory.return_value = mock_cb

            from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

            result = deliver_webhook(
                registration=mock_registration,
                job_id=1,
                event_type="job.completed",
                payload={"job_id": 1, "status": "COMPLETE"},
            )

        # Delivery should fail (ConnectError) but the client must be closed
        assert result.status == "FAILED", f"Expected FAILED, got {result.status}"
        # The context manager __exit__ was called (client closed)
        mock_client.__exit__.assert_called()


# ---------------------------------------------------------------------------
# T72.6 ATTACK: Privacy session race — stale data not returned
# ---------------------------------------------------------------------------


class TestPrivacySessionRace:
    """Verify the sync session re-read after async mutation is not stale.

    T72.6: The sync session that was used to read the ledger before the async
    mutation must be refreshed after _run_reset_budget() completes.
    A test verifying that session.expire() and session.refresh() are called
    on the ledger object before building the response.
    """

    def test_refresh_budget_refreshes_ledger_after_reset(self) -> None:
        """session.expire and session.refresh must be called after _run_reset_budget.

        This ensures the sync session reflects the committed async mutation
        rather than returning stale pre-reset values.
        """
        from synth_engine.bootstrapper.routers.privacy import refresh_budget
        from synth_engine.bootstrapper.schemas.privacy import BudgetRefreshRequest

        mock_ledger = MagicMock()
        mock_ledger.id = 1
        mock_ledger.total_allocated_epsilon = "10.0"
        mock_ledger.total_spent_epsilon = "0.0"  # post-reset value

        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = mock_ledger

        mock_audit = MagicMock()

        with (
            patch(
                "synth_engine.bootstrapper.routers.privacy.get_audit_logger",
                return_value=mock_audit,
            ),
            patch(
                "synth_engine.bootstrapper.routers.privacy._run_reset_budget",
            ),
        ):
            refresh_budget(
                body=BudgetRefreshRequest(justification="test justification long enough"),
                session=mock_session,
                current_operator="op1",
            )

        # Verify the session refreshed the ledger to get post-reset state
        mock_session.expire.assert_called_once_with(mock_ledger)
        mock_session.refresh.assert_called_once_with(mock_ledger)
        # Specific-value: call counts confirm both were invoked exactly once
        assert mock_session.expire.call_count == 1, "expire must be called once"
        assert mock_session.refresh.call_count == 1, "refresh must be called once"
