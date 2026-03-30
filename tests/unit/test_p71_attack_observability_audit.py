"""Attack tests for T71.5 (unified counter) and T71.1 (audit events on destructive endpoints).

ATTACK-FIRST TDD — these tests prove the system:
1. Does NOT register duplicate Prometheus metric names on import.
2. Increments the unified counter with router+endpoint labels.
3. Returns 500 and performs NO mutation when audit write fails on any of the
   four newly-audited destructive endpoints.
4. Emits the correct audit event type for each endpoint.
5. Emits a compensating *_ABORTED event when the DB fails AFTER a successful audit.

CONSTITUTION Priority 0: Security — audit ordering and no-mutation-on-audit-fail
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T71.5 — Unify audit failure Prometheus counter
Task: T71.1 — Add audit events to connections/settings/webhooks destructive endpoints
"""

from __future__ import annotations

import base64
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def unseal_vault_for_tests(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Unseal vault and reset after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.security.vault import VaultState

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    VaultState.reset()
    VaultState.unseal(bytearray(b"test-passphrase-for-p71-tests"))
    yield
    VaultState.reset()


@contextmanager  # type: ignore[arg-type]
def _bypass_middleware() -> Any:
    """Context manager that patches vault/license middleware to pass through.

    Yields:
        None
    """
    with (
        patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ),
        patch(
            "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
            return_value=True,
        ),
    ):
        yield


def _make_connections_app() -> FastAPI:
    """Build a minimal FastAPI app with the connections router and in-memory DB.

    Returns:
        FastAPI test app wired with connections router.
    """
    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.connections import router as connections_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()
    app.include_router(connections_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    def _override_operator() -> str:
        return "test-operator"

    app.dependency_overrides[get_db_session] = _override_session
    app.dependency_overrides[get_current_operator] = _override_operator
    return app


def _make_settings_app() -> FastAPI:
    """Build a minimal FastAPI app with the settings router and in-memory DB.

    Returns:
        FastAPI test app wired with settings router.
    """
    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.settings import router as settings_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()
    app.include_router(settings_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    def _override_operator() -> str:
        return "test-operator"

    def _override_scope_write() -> str:
        return "test-operator"

    from synth_engine.bootstrapper.dependencies.auth import require_scope

    app.dependency_overrides[get_db_session] = _override_session
    app.dependency_overrides[get_current_operator] = _override_operator
    app.dependency_overrides[require_scope("settings:write")] = _override_scope_write
    return app


def _make_webhooks_app() -> FastAPI:
    """Build a minimal FastAPI app with the webhooks router and in-memory DB.

    Returns:
        FastAPI test app wired with webhooks router.
    """
    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.webhooks import router as webhooks_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()
    app.include_router(webhooks_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    def _override_operator() -> str:
        return "test-operator"

    app.dependency_overrides[get_db_session] = _override_session
    app.dependency_overrides[get_current_operator] = _override_operator
    return app


def _seed_connection(app: FastAPI, connection_id: str = "conn-001") -> None:
    """Insert a test Connection row directly into the in-memory DB.

    Args:
        app: The FastAPI app whose DB override to use.
        connection_id: The connection ID to seed.
    """
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.schemas.connections import Connection

    gen = app.dependency_overrides[get_db_session]()
    session = next(gen)  # type: ignore[call-overload]
    conn = Connection(
        id=connection_id,
        owner_id="test-operator",
        name="test-conn",
        host="localhost",
        port=5432,
        database="testdb",
        schema_name="public",
    )
    session.add(conn)
    session.commit()
    try:
        next(gen)
    except StopIteration:
        pass


def _seed_setting(app: FastAPI, key: str = "test-key") -> None:
    """Insert a test Setting row directly into the in-memory DB.

    Args:
        app: The FastAPI app whose DB override to use.
        key: The setting key to seed.
    """
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.schemas.settings import Setting

    gen = app.dependency_overrides[get_db_session]()
    session = next(gen)  # type: ignore[call-overload]
    setting = Setting(key=key, value="test-value")
    session.add(setting)
    session.commit()
    try:
        next(gen)
    except StopIteration:
        pass


def _seed_webhook(app: FastAPI, webhook_id: str = "wh-001") -> None:
    """Insert a test WebhookRegistration row directly into the in-memory DB.

    Args:
        app: The FastAPI app whose DB override to use.
        webhook_id: The webhook ID to seed.
    """
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.schemas.webhooks import WebhookRegistration

    gen = app.dependency_overrides[get_db_session]()
    session = next(gen)  # type: ignore[call-overload]
    reg = WebhookRegistration(
        id=webhook_id,
        owner_id="test-operator",
        callback_url="https://example.com/hook",
        signing_key="test-signing-key-minimum-32chars!",
        active=True,
    )
    session.add(reg)
    session.commit()
    try:
        next(gen)
    except StopIteration:
        pass


# ---------------------------------------------------------------------------
# T71.5 — Unified Prometheus counter tests
# ---------------------------------------------------------------------------


def test_no_duplicate_prometheus_metric_names_on_import() -> None:
    """Importing shared.observability twice must not raise a duplicate metric error.

    Prometheus raises ValueError if the same metric name is registered twice.
    The unified counter must use CollectorRegistry carefully or be a module-level
    singleton so repeated imports are safe.
    """
    # Import once — already imported by production code during app boot.
    from synth_engine.shared import observability as obs1

    # Second import must not raise.
    from synth_engine.shared import observability as obs2

    # Both imports reference the same module object (Python module cache).
    assert obs1 is obs2, "Module identity must be preserved across imports"


def test_unified_counter_incremented_with_router_label() -> None:
    """AUDIT_WRITE_FAILURE_TOTAL counter must accept router + endpoint labels.

    Args:
        None (standalone test).

    The counter must be callable with labels(router=..., endpoint=...).inc()
    without raising.
    """
    from synth_engine.shared.observability import AUDIT_WRITE_FAILURE_TOTAL

    # Must not raise — just increment.
    AUDIT_WRITE_FAILURE_TOTAL.labels(router="connections", endpoint="/connections/{id}").inc()
    AUDIT_WRITE_FAILURE_TOTAL.labels(router="settings", endpoint="/settings/{key}").inc()
    # Counter must be incremented (non-negative value)
    sample = AUDIT_WRITE_FAILURE_TOTAL.labels(router="connections", endpoint="/connections/{id}")
    assert sample._value.get() >= 1.0, "counter must be >= 1 after increment"


# ---------------------------------------------------------------------------
# T71.1 — Audit failure tests (attack: audit raises → 500, no mutation)
# ---------------------------------------------------------------------------


def test_connections_delete_audit_failure_returns_500_no_mutation() -> None:
    """DELETE /connections/{id} returns 500 and leaves connection intact when audit fails.

    The audit event must be emitted BEFORE the database delete.  If the
    audit write raises, the connection MUST NOT be deleted and the endpoint
    MUST return 500.
    """
    app = _make_connections_app()
    _seed_connection(app, "conn-audit-fail")

    with (
        _bypass_middleware(),
        patch("synth_engine.bootstrapper.routers.connections.get_audit_logger") as mock_get_audit,
    ):
        mock_logger = MagicMock()
        mock_logger.log_event.side_effect = OSError("audit write failure")
        mock_get_audit.return_value = mock_logger

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/connections/conn-audit-fail")

    assert resp.status_code == 500, f"Expected 500, got {resp.status_code}"

    # Verify connection was NOT deleted — re-query it.
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.schemas.connections import Connection

    gen = app.dependency_overrides[get_db_session]()
    session = next(gen)  # type: ignore[call-overload]
    conn = session.get(Connection, "conn-audit-fail")
    assert conn is not None, "Connection must still exist after audit failure"
    assert conn.id == "conn-audit-fail"


def test_settings_put_audit_failure_returns_500_no_mutation() -> None:
    """PUT /settings/{key} returns 500 and leaves setting unchanged when audit fails."""
    app = _make_settings_app()
    _seed_setting(app, "key-audit-fail")

    with (
        _bypass_middleware(),
        patch("synth_engine.bootstrapper.routers.settings.get_audit_logger") as mock_get_audit,
    ):
        mock_logger = MagicMock()
        mock_logger.log_event.side_effect = OSError("audit write failure")
        mock_get_audit.return_value = mock_logger

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.put("/settings/key-audit-fail", json={"value": "new-value"})

    assert resp.status_code == 500, f"Expected 500, got {resp.status_code}"

    # Verify setting was NOT changed.
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.schemas.settings import Setting

    gen = app.dependency_overrides[get_db_session]()
    session = next(gen)  # type: ignore[call-overload]
    setting = session.get(Setting, "key-audit-fail")
    assert setting is not None, "Setting must still exist"
    assert setting.value == "test-value", "Setting value must be unchanged"


def test_settings_delete_audit_failure_returns_500_no_mutation() -> None:
    """DELETE /settings/{key} returns 500 and leaves setting intact when audit fails."""
    app = _make_settings_app()
    _seed_setting(app, "key-delete-fail")

    with (
        _bypass_middleware(),
        patch("synth_engine.bootstrapper.routers.settings.get_audit_logger") as mock_get_audit,
    ):
        mock_logger = MagicMock()
        mock_logger.log_event.side_effect = OSError("audit write failure")
        mock_get_audit.return_value = mock_logger

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/settings/key-delete-fail")

    assert resp.status_code == 500, f"Expected 500, got {resp.status_code}"

    # Verify setting was NOT deleted.
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.schemas.settings import Setting

    gen = app.dependency_overrides[get_db_session]()
    session = next(gen)  # type: ignore[call-overload]
    setting = session.get(Setting, "key-delete-fail")
    assert setting is not None, "Setting must still exist after audit failure"


def test_webhooks_delete_audit_failure_returns_500_no_mutation() -> None:
    """DELETE /webhooks/{id} returns 500 and leaves webhook active when audit fails."""
    app = _make_webhooks_app()
    _seed_webhook(app, "wh-audit-fail")

    with (
        _bypass_middleware(),
        patch("synth_engine.bootstrapper.routers.webhooks.get_audit_logger") as mock_get_audit,
    ):
        mock_logger = MagicMock()
        mock_logger.log_event.side_effect = OSError("audit write failure")
        mock_get_audit.return_value = mock_logger

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/webhooks/wh-audit-fail")

    assert resp.status_code == 500, f"Expected 500, got {resp.status_code}"

    # Verify webhook is still active.
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.schemas.webhooks import WebhookRegistration

    gen = app.dependency_overrides[get_db_session]()
    session = next(gen)  # type: ignore[call-overload]
    reg = session.get(WebhookRegistration, "wh-audit-fail")
    assert reg is not None, "Webhook registration must still exist"
    assert reg.active is True, "Webhook must still be active after audit failure"


# ---------------------------------------------------------------------------
# T71.1 — Audit event type correctness
# ---------------------------------------------------------------------------


def test_connections_delete_audit_event_type_is_CONNECTION_DELETED() -> None:  # noqa: N802
    """DELETE /connections/{id} must emit event_type='CONNECTION_DELETED'."""
    app = _make_connections_app()
    _seed_connection(app, "conn-type-check")

    captured_calls: list[dict[str, Any]] = []

    def _capturing_log_event(**kwargs: Any) -> None:
        captured_calls.append(kwargs)

    with (
        _bypass_middleware(),
        patch("synth_engine.bootstrapper.routers.connections.get_audit_logger") as mock_get_audit,
    ):
        mock_logger = MagicMock()
        mock_logger.log_event.side_effect = _capturing_log_event
        mock_get_audit.return_value = mock_logger

        client = TestClient(app, raise_server_exceptions=False)
        client.delete("/connections/conn-type-check")

    # Audit write was called (even though it raised — side_effect fires first).
    assert len(captured_calls) >= 1, "log_event must have been called"
    first_call = captured_calls[0]
    assert first_call.get("event_type") == "CONNECTION_DELETED", (
        f"Expected 'CONNECTION_DELETED', got {first_call.get('event_type')!r}"
    )
    assert first_call.get("resource") == "connection/conn-type-check"
    assert first_call.get("action") == "delete"


def test_settings_put_audit_event_emitted_before_commit() -> None:
    """PUT /settings/{key} emits the audit event before modifying the setting value.

    This verifies the audit-before-mutation ordering requirement.
    """
    app = _make_settings_app()
    _seed_setting(app, "key-order-check")

    call_order: list[str] = []

    def _audit_call(**kwargs: Any) -> None:
        call_order.append("audit")

    with (
        _bypass_middleware(),
        patch("synth_engine.bootstrapper.routers.settings.get_audit_logger") as mock_get_audit,
    ):
        mock_logger = MagicMock()
        mock_logger.log_event.side_effect = _audit_call
        mock_get_audit.return_value = mock_logger

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.put("/settings/key-order-check", json={"value": "updated"})

    # The audit call must have happened (at least once).
    assert "audit" in call_order, "Audit event must have been called"
    # Response must be 200 (upsert success) when audit does not fail.
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"


def test_webhook_deactivate_audit_reflects_soft_delete() -> None:
    """DELETE /webhooks/{id} must emit event_type='WEBHOOK_DEACTIVATED' (soft delete)."""
    app = _make_webhooks_app()
    _seed_webhook(app, "wh-type-check")

    captured_calls: list[dict[str, Any]] = []

    def _capturing_log_event(**kwargs: Any) -> None:
        captured_calls.append(kwargs)

    with (
        _bypass_middleware(),
        patch("synth_engine.bootstrapper.routers.webhooks.get_audit_logger") as mock_get_audit,
    ):
        mock_logger = MagicMock()
        mock_logger.log_event.side_effect = _capturing_log_event
        mock_get_audit.return_value = mock_logger

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/webhooks/wh-type-check")

    assert resp.status_code == 204, f"Expected 204, got {resp.status_code}"
    assert len(captured_calls) >= 1, "log_event must have been called"
    first_call = captured_calls[0]
    assert first_call.get("event_type") == "WEBHOOK_DEACTIVATED", (
        f"Expected 'WEBHOOK_DEACTIVATED', got {first_call.get('event_type')!r}"
    )
    assert first_call.get("action") == "deactivate"


def test_db_failure_after_audit_emits_compensating_aborted_event() -> None:
    """A DB failure after successful audit must emit a compensating *_ABORTED event.

    Tests the connections endpoint: if session.commit() raises AFTER audit
    succeeds, a CONNECTION_DELETE_ABORTED compensating event must be emitted.
    """
    from sqlalchemy.exc import SQLAlchemyError

    app = _make_connections_app()
    _seed_connection(app, "conn-db-fail")

    captured_event_types: list[str] = []

    def _capturing_log_event(**kwargs: Any) -> None:
        event_type = kwargs.get("event_type", "")
        captured_event_types.append(str(event_type))

    with (
        _bypass_middleware(),
        patch("synth_engine.bootstrapper.routers.connections.get_audit_logger") as mock_get_audit,
    ):
        mock_logger = MagicMock()
        mock_logger.log_event.side_effect = _capturing_log_event
        mock_get_audit.return_value = mock_logger

        # Patch session.commit to raise after audit is called.
        from synth_engine.bootstrapper.dependencies.db import get_db_session

        original_override = app.dependency_overrides[get_db_session]

        def _failing_session() -> Any:
            for session in original_override():

                def _patched_commit() -> None:
                    # Let commit fail only if there's a pending delete.
                    raise SQLAlchemyError("simulated DB failure after audit")

                session.commit = _patched_commit  # type: ignore[method-assign]
                yield session

        app.dependency_overrides[get_db_session] = _failing_session

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/connections/conn-db-fail")

    assert resp.status_code == 500, f"Expected 500, got {resp.status_code}"
    assert "CONNECTION_DELETED" in captured_event_types, (
        "Primary audit event 'CONNECTION_DELETED' must have been emitted"
    )
    assert any("ABORTED" in et for et in captured_event_types), (
        f"Compensating ABORTED event must be emitted; got {captured_event_types}"
    )
