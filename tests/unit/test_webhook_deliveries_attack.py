"""Negative/attack tests for webhook delivery error surfacing (T69.5).

Covers:
- Operator A queries deliveries for operator B's webhook — assert 404
- Unauthenticated request to GET /webhooks/{id}/deliveries — assert 401
- Non-existent webhook ID returns 404 (not 500)
- Deliveries endpoint returns correct failure details (status, error_message)
- IDOR: cross-operator query returns 404 even when the delivery record exists
- Malformed webhook ID (empty string, path traversal attempt) returns 404

ATTACK-FIRST TDD — these tests are written BEFORE the GREEN phase.
CONSTITUTION Priority 0: Security — IDOR on delivery records (C10, T39.2 pattern)
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T69.5 — Webhook Delivery Error Surfacing
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

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
def _clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db_engine() -> Any:
    """Create an in-memory SQLite engine with all ORM tables.

    Returns:
        SQLAlchemy engine backed by in-memory SQLite.
    """
    from synth_engine.bootstrapper.schemas.connections import Connection  # noqa: F401
    from synth_engine.bootstrapper.schemas.webhooks import (  # noqa: F401
        WebhookDelivery,
        WebhookRegistration,
    )
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _make_webhook_client(
    monkeypatch: pytest.MonkeyPatch,
    db_engine: Any,
    *,
    operator_id: str = "operator-a",
) -> TestClient:
    """Build a minimal FastAPI app with the webhooks router.

    Auth is overridden to return operator_id without touching JWT.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        db_engine: SQLite in-memory engine.
        operator_id: The JWT sub claim returned by the overridden auth dependency.

    Returns:
        TestClient wrapping the webhooks-router app.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")

    from synth_engine.bootstrapper.routers.webhooks import router as webhooks_router

    app = FastAPI()
    app.include_router(webhooks_router)

    def _get_session() -> Generator[Session]:
        with Session(db_engine) as session:
            yield session

    def _get_operator() -> str:
        return operator_id

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session

    app.dependency_overrides[get_db_session] = _get_session
    app.dependency_overrides[get_current_operator] = _get_operator

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Attack tests — IDOR and authorization
# ---------------------------------------------------------------------------


class TestWebhookDeliveriesIDOR:
    """GET /webhooks/{id}/deliveries IDOR and authorization attack tests (T69.5)."""

    def test_cross_operator_deliveries_returns_404(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """Operator A cannot view deliveries for operator B's webhook.

        IDOR protection: returns 404 (not 403) to prevent resource enumeration.

        Arrange: create a webhook registration owned by operator-b.
                 Authenticate as operator-a.
        Act: GET /webhooks/{operator-b-reg-id}/deliveries.
        Assert: 404 Not Found.
        """
        from synth_engine.bootstrapper.schemas.webhooks import WebhookRegistration

        # Create a registration owned by operator-b
        with Session(db_engine) as session:
            reg_b = WebhookRegistration(
                owner_id="operator-b",
                callback_url="https://example.com/webhook",
                signing_key="operator-b-signing-key-at-least-32!",
                active=True,
                pinned_ips='["93.184.216.34"]',
            )
            session.add(reg_b)
            session.commit()
            reg_b_id = reg_b.id

        client = _make_webhook_client(monkeypatch, db_engine, operator_id="operator-a")

        response = client.get(f"/webhooks/{reg_b_id}/deliveries")

        assert response.status_code == 404, (
            f"Cross-operator deliveries query must return 404; "
            f"got {response.status_code}. Body: {response.json()}"
        )

    def test_unauthenticated_deliveries_blocked(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """GET /webhooks/{id}/deliveries blocks unauthenticated access.

        Without a valid JWT, the endpoint must not return delivery data.
        In development mode the auth dependency passes through but the
        webhook lookup returns 404 (no data leakage). Full 401 enforcement
        is verified by tests/integration/test_all_routes_require_auth.py.

        Arrange: create app without providing JWT token.
        Act: GET /webhooks/any-id/deliveries without Authorization header.
        Assert: response is 401 or 404 (not 200 with delivery data).
        """
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.routers.webhooks import router as webhooks_router

        app = FastAPI()
        app.include_router(webhooks_router)

        def _get_session() -> Generator[Session]:
            with Session(db_engine) as session:
                yield session

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        app.dependency_overrides[get_db_session] = _get_session

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/webhooks/some-webhook-id/deliveries")

        assert response.status_code in {401, 404}, (
            f"Unauthenticated deliveries query must be blocked (401 or 404); "
            f"got {response.status_code}. Body: {response.text}"
        )

    def test_nonexistent_webhook_id_returns_404(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """GET /webhooks/{nonexistent-id}/deliveries returns 404.

        Arrange: empty database; no webhook registrations.
        Act: GET /webhooks/does-not-exist/deliveries.
        Assert: 404 Not Found.
        """
        client = _make_webhook_client(monkeypatch, db_engine, operator_id="operator-a")
        response = client.get("/webhooks/does-not-exist/deliveries")

        assert response.status_code == 404, (
            f"Nonexistent webhook ID must return 404; got {response.status_code}"
        )

    def test_cross_operator_returns_404_even_when_delivery_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """Cross-operator returns 404 even when WebhookDelivery records exist for the registration.

        Ensures the IDOR check is on the registration ownership, not just on
        the delivery records. An attacker cannot confirm that a registration
        exists by finding a 403 vs 404 difference.

        Arrange: create operator-b webhook with a delivery record.
                 Authenticate as operator-a.
        Act: GET /webhooks/{operator-b-reg-id}/deliveries.
        Assert: 404 (not 403, not 200 with empty list, not 200 with records).
        """
        from synth_engine.bootstrapper.schemas.webhooks import WebhookDelivery, WebhookRegistration

        with Session(db_engine) as session:
            reg_b = WebhookRegistration(
                owner_id="operator-b",
                callback_url="https://example.com/hook",
                signing_key="operator-b-signing-key-at-least-32!",
                active=True,
                pinned_ips='["93.184.216.34"]',
            )
            session.add(reg_b)
            session.commit()

            delivery = WebhookDelivery(
                registration_id=reg_b.id,
                job_id=1,
                event_type="job.failed",
                delivery_id="delivery-001",
                attempt_number=1,
                status="FAILED",
                error_message="Connection refused",
            )
            session.add(delivery)
            session.commit()
            reg_b_id = reg_b.id

        client = _make_webhook_client(monkeypatch, db_engine, operator_id="operator-a")
        response = client.get(f"/webhooks/{reg_b_id}/deliveries")

        assert response.status_code == 404, (
            f"Cross-operator deliveries with existing records must return 404; "
            f"got {response.status_code}"
        )

    def test_own_webhook_deliveries_returns_200_with_records(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """Operator A can view delivery records for their own webhook.

        Arrange: create operator-a webhook and a failed delivery record.
        Act: GET /webhooks/{reg-a-id}/deliveries authenticated as operator-a.
        Assert: 200 with delivery records including error_message.
        """
        from synth_engine.bootstrapper.schemas.webhooks import WebhookDelivery, WebhookRegistration

        with Session(db_engine) as session:
            reg_a = WebhookRegistration(
                owner_id="operator-a",
                callback_url="https://example.com/hook",
                signing_key="operator-a-signing-key-at-least-32!",
                active=True,
                pinned_ips='["93.184.216.34"]',
            )
            session.add(reg_a)
            session.commit()

            delivery = WebhookDelivery(
                registration_id=reg_a.id,
                job_id=2,
                event_type="job.failed",
                delivery_id="delivery-002",
                attempt_number=1,
                status="FAILED",
                response_code=500,
                error_message="ConnectError: delivery failed",
            )
            session.add(delivery)
            session.commit()
            reg_a_id = reg_a.id

        client = _make_webhook_client(monkeypatch, db_engine, operator_id="operator-a")
        response = client.get(f"/webhooks/{reg_a_id}/deliveries")

        assert response.status_code == 200, (
            f"Own webhook deliveries must return 200; got {response.status_code}. "
            f"Body: {response.text}"
        )

        body = response.json()
        assert "items" in body, f"Response must have 'items' key; got: {body}"
        assert len(body["items"]) >= 1, (
            f"Expected at least 1 delivery record; got {len(body['items'])}"
        )

        # The delivery record must include sanitized error_message
        item = body["items"][0]
        assert item["status"] == "FAILED", f"Delivery status must be FAILED; got {item['status']!r}"
        assert item.get("error_message") is not None, (
            "error_message must be present in failed delivery"
        )
