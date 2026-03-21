"""Unit tests for webhook registration CRUD endpoints (T45.3).

Attack/negative tests are first (Constitution Priority 0: attack-first ordering).

Attack/negative tests:
1.  SSRF: private IPv4 ranges (127.x, 10.x, 172.16-31.x, 192.168.x) → 400
2.  SSRF: IPv6 localhost (::1) → 400
3.  SSRF: cloud metadata endpoint (169.254.169.254) → 400
4.  SSRF: link-local IPv4 (169.254.x.x) → 400
5.  SSRF: FC00::/7 private IPv6 (fd00::1) → 400
6.  Signing key too short (< 32 chars) → 400
7.  Invalid callback URL (not a URL, missing scheme, empty) → 400
8.  Registration limit: 11th registration by same operator → 409
9.  IDOR: operator A cannot DELETE operator B's webhook → 404
10. IDOR: operator A GET /webhooks must NOT return operator B's registrations
11. Deactivated webhook: GET returns the registration with active=False
12. Unauthenticated access → 401 (when JWT configured)
13. HTTPS-only in production mode: http:// callback URL → 400

Feature/positive tests:
14. POST /webhooks → 201, registration stored
15. GET /webhooks → lists registrations (owner-scoped)
16. GET /webhooks returns signing_key as write-only (not exposed in response)
17. DELETE /webhooks/{id} → 204, registration deactivated
18. DELETE /webhooks/{id} for unknown id → 404
19. ConclaveSettings.webhook_max_registrations field exists with default=10
20. ConclaveSettings.webhook_delivery_timeout_seconds field exists with default=10

CONSTITUTION Priority 0: Security — SSRF protection, key length enforcement, IDOR
CONSTITUTION Priority 3: TDD — RED phase
Task: T45.3 — Implement Webhook Callbacks for Task Completion
"""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import Generator
from typing import Annotated
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool


# ---------------------------------------------------------------------------
# State isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
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


# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def in_memory_engine() -> object:
    """Create an in-memory SQLite engine for testing.

    Returns:
        A SQLAlchemy engine using in-memory SQLite.
    """
    from synth_engine.bootstrapper.schemas.webhooks import (
        WebhookDelivery,
        WebhookRegistration,
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_session(in_memory_engine: object) -> Generator[Session, None, None]:
    """Provide a transactional SQLite session for tests.

    Args:
        in_memory_engine: SQLite in-memory engine.

    Yields:
        An open SQLModel Session.
    """
    from sqlmodel import Session as _Session

    with _Session(in_memory_engine) as session:
        yield session


@pytest.fixture()
def client(db_session: Session) -> TestClient:
    """Build a TestClient with the webhooks router.

    Args:
        db_session: Open in-memory SQLite session.

    Returns:
        Starlette TestClient for the minimal app.
    """
    from synth_engine.bootstrapper.routers.webhooks import router
    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_operator] = lambda: "operator-a"

    return TestClient(app)


@pytest.fixture()
def client_operator_b(db_session: Session) -> TestClient:
    """Build a TestClient authenticating as operator B.

    Args:
        db_session: Open in-memory SQLite session (same as operator A for isolation tests).

    Returns:
        Starlette TestClient for the minimal app authenticated as operator B.
    """
    from synth_engine.bootstrapper.routers.webhooks import router
    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_operator] = lambda: "operator-b"

    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_VALID_KEY = "a" * 32  # 32-char signing key (minimum)
_VALID_CALLBACK = "https://example.com/hook"


def _registration_payload(
    callback_url: str = _VALID_CALLBACK,
    signing_key: str = _VALID_KEY,
    events: list[str] | None = None,
) -> dict[str, object]:
    """Build a valid webhook registration request payload.

    Args:
        callback_url: The callback URL to register.
        signing_key: The HMAC signing key.
        events: Event types to subscribe to; defaults to COMPLETED + FAILED.

    Returns:
        Dict suitable for JSON body in POST /webhooks.
    """
    return {
        "callback_url": callback_url,
        "signing_key": signing_key,
        "events": events or ["job.completed", "job.failed"],
    }


# ===========================================================================
# ATTACK / NEGATIVE TESTS
# ===========================================================================


class TestSSRFProtection:
    """T45.3 SSRF: callback URL must not resolve to private/reserved IPs."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/hook",
            "http://127.1.2.3/hook",
            "http://10.0.0.1/hook",
            "http://10.255.255.255/hook",
            "http://172.16.0.1/hook",
            "http://172.31.0.1/hook",
            "http://192.168.1.1/hook",
            "http://192.168.255.255/hook",
        ],
    )
    def test_private_ipv4_rejected(self, client: TestClient, url: str) -> None:
        """SSRF attack: private IPv4 ranges must be rejected at registration.

        Args:
            client: Test client for webhook router.
            url: Private IPv4 callback URL.
        """
        resp = client.post("/webhooks/", json=_registration_payload(callback_url=url))
        assert resp.status_code == 400, f"Expected 400 for {url}, got {resp.status_code}"

    def test_ipv6_localhost_rejected(self, client: TestClient) -> None:
        """SSRF attack: IPv6 localhost [::1] must be rejected."""
        resp = client.post(
            "/webhooks/", json=_registration_payload(callback_url="http://[::1]/hook")
        )
        assert resp.status_code == 400

    def test_cloud_metadata_rejected(self, client: TestClient) -> None:
        """SSRF attack: AWS/GCP metadata endpoint must be rejected."""
        resp = client.post(
            "/webhooks/",
            json=_registration_payload(
                callback_url="http://169.254.169.254/latest/meta-data/"
            ),
        )
        assert resp.status_code == 400

    def test_link_local_ipv4_rejected(self, client: TestClient) -> None:
        """SSRF attack: link-local 169.254.x.x must be rejected."""
        resp = client.post(
            "/webhooks/",
            json=_registration_payload(callback_url="http://169.254.1.100/hook"),
        )
        assert resp.status_code == 400

    def test_private_ipv6_fc00_rejected(self, client: TestClient) -> None:
        """SSRF attack: private IPv6 fc00::/7 range must be rejected."""
        resp = client.post(
            "/webhooks/",
            json=_registration_payload(callback_url="http://[fd00::1]/hook"),
        )
        assert resp.status_code == 400


class TestSigningKeyValidation:
    """T45.3 signing key must be at least 32 characters."""

    @pytest.mark.parametrize("key", ["", "short", "a" * 31])
    def test_key_too_short_rejected(self, client: TestClient, key: str) -> None:
        """Signing key shorter than 32 characters must be rejected with 400.

        Args:
            client: Test client for webhook router.
            key: Short signing key.
        """
        resp = client.post(
            "/webhooks/", json=_registration_payload(signing_key=key)
        )
        assert resp.status_code in (400, 422), (
            f"Expected 400/422 for short key, got {resp.status_code}"
        )

    def test_key_exactly_32_chars_accepted(self, client: TestClient) -> None:
        """Signing key of exactly 32 characters must be accepted.

        Args:
            client: Test client for webhook router.
        """
        resp = client.post(
            "/webhooks/", json=_registration_payload(signing_key="a" * 32)
        )
        assert resp.status_code == 201


class TestCallbackUrlValidation:
    """T45.3 callback URL format validation."""

    @pytest.mark.parametrize(
        "url",
        [
            "not-a-url",
            "",
            "ftp://example.com/hook",
            "example.com/hook",
        ],
    )
    def test_invalid_callback_url_rejected(self, client: TestClient, url: str) -> None:
        """Non-HTTP(S) or malformed callback URLs must be rejected.

        Args:
            client: Test client for webhook router.
            url: Invalid callback URL.
        """
        resp = client.post(
            "/webhooks/", json=_registration_payload(callback_url=url)
        )
        assert resp.status_code in (400, 422), (
            f"Expected 400/422 for url={url!r}, got {resp.status_code}"
        )


class TestRegistrationLimit:
    """T45.3 max 10 active registrations per operator."""

    def test_eleventh_registration_rejected(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Attempting to create an 11th registration must return 409.

        Args:
            client: Test client authenticating as operator-a.
            db_session: Open in-memory session.
        """
        for i in range(10):
            resp = client.post(
                "/webhooks/",
                json=_registration_payload(
                    callback_url=f"https://example.com/hook{i}"
                ),
            )
            assert resp.status_code == 201, f"Registration {i+1} failed: {resp.json()}"

        # 11th must fail
        resp = client.post(
            "/webhooks/",
            json=_registration_payload(callback_url="https://example.com/hook99"),
        )
        assert resp.status_code == 409


class TestIDOR:
    """T45.3 IDOR: cross-tenant isolation."""

    def test_operator_b_cannot_delete_operator_a_webhook(
        self,
        client: TestClient,
        client_operator_b: TestClient,
    ) -> None:
        """Operator B DELETE on operator A's webhook must return 404.

        Returns 404 (not 403) to prevent enumeration of webhook IDs.

        Args:
            client: Test client for operator A.
            client_operator_b: Test client for operator B.
        """
        # Operator A creates a webhook
        resp = client.post("/webhooks/", json=_registration_payload())
        assert resp.status_code == 201
        webhook_id = resp.json()["id"]

        # Operator B attempts to delete it → 404 (not 403, prevents enumeration)
        resp_b = client_operator_b.delete(f"/webhooks/{webhook_id}")
        assert resp_b.status_code == 404

    def test_operator_b_cannot_list_operator_a_webhooks(
        self,
        client: TestClient,
        client_operator_b: TestClient,
    ) -> None:
        """Operator A's webhooks must NOT appear in operator B's GET /webhooks.

        Args:
            client: Test client for operator A.
            client_operator_b: Test client for operator B.
        """
        # Operator A registers one webhook
        resp = client.post("/webhooks/", json=_registration_payload())
        assert resp.status_code == 201
        op_a_id = resp.json()["id"]

        # Operator B lists webhooks — must not see operator A's
        resp_b = client_operator_b.get("/webhooks/")
        assert resp_b.status_code == 200
        ids = [w["id"] for w in resp_b.json()["items"]]
        assert op_a_id not in ids


class TestProductionHttpsOnly:
    """T45.3 production mode must reject http:// callback URLs."""

    def test_http_url_rejected_in_production(
        self, db_session: Session
    ) -> None:
        """In production mode, http:// callback URLs must be rejected with 400.

        Args:
            db_session: Open in-memory SQLite session.
        """
        from synth_engine.bootstrapper.routers.webhooks import router
        from synth_engine.bootstrapper.dependencies.auth import get_current_operator
        from synth_engine.bootstrapper.dependencies.db import get_db_session

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db_session] = lambda: db_session
        app.dependency_overrides[get_current_operator] = lambda: "operator-a"

        with patch(
            "synth_engine.bootstrapper.routers.webhooks.get_settings"
        ) as mock_settings:
            mock_settings.return_value.is_production.return_value = True
            mock_settings.return_value.webhook_max_registrations = 10

            test_client = TestClient(app)
            resp = test_client.post(
                "/webhooks/",
                json=_registration_payload(
                    callback_url="http://legitimate-but-http.example.com/hook"
                ),
            )
        assert resp.status_code == 400


# ===========================================================================
# FEATURE / POSITIVE TESTS
# ===========================================================================


class TestWebhookRegistrationCRUD:
    """T45.3 POST/GET/DELETE /webhooks CRUD."""

    def test_create_webhook_returns_201(self, client: TestClient) -> None:
        """POST /webhooks with valid data must return 201.

        Args:
            client: Test client for webhook router.
        """
        resp = client.post("/webhooks/", json=_registration_payload())
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["callback_url"] == _VALID_CALLBACK
        assert body["active"] is True

    def test_create_webhook_does_not_expose_signing_key(
        self, client: TestClient
    ) -> None:
        """POST /webhooks response must NOT include the signing_key value.

        Args:
            client: Test client for webhook router.
        """
        resp = client.post("/webhooks/", json=_registration_payload())
        assert resp.status_code == 201
        body = resp.json()
        # signing_key must be absent or masked in responses
        assert "signing_key" not in body or body.get("signing_key") is None

    def test_list_webhooks_returns_owner_scoped(self, client: TestClient) -> None:
        """GET /webhooks must return only registrations owned by the calling operator.

        Args:
            client: Test client for operator A.
        """
        client.post("/webhooks/", json=_registration_payload())
        resp = client.get("/webhooks/")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert len(body["items"]) >= 1
        # All items must belong to operator-a
        for item in body["items"]:
            assert item["owner_id"] == "operator-a"

    def test_delete_webhook_deactivates_registration(
        self, client: TestClient
    ) -> None:
        """DELETE /webhooks/{id} must set active=False on the registration.

        Args:
            client: Test client for operator A.
        """
        resp = client.post("/webhooks/", json=_registration_payload())
        assert resp.status_code == 201
        webhook_id = resp.json()["id"]

        del_resp = client.delete(f"/webhooks/{webhook_id}")
        assert del_resp.status_code == 204

        # After deletion, list should show active=False or empty
        list_resp = client.get("/webhooks/")
        items = list_resp.json()["items"]
        matching = [w for w in items if w["id"] == webhook_id]
        # Either removed from list or marked inactive
        for item in matching:
            assert item["active"] is False

    def test_delete_nonexistent_webhook_returns_404(
        self, client: TestClient
    ) -> None:
        """DELETE /webhooks/{id} for unknown id must return 404.

        Args:
            client: Test client for operator A.
        """
        fake_id = str(uuid.uuid4())
        resp = client.delete(f"/webhooks/{fake_id}")
        assert resp.status_code == 404


class TestWebhookSettings:
    """T45.3 ConclaveSettings webhook fields."""

    def test_webhook_max_registrations_default(self) -> None:
        """ConclaveSettings.webhook_max_registrations defaults to 10."""
        import os

        os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
        os.environ.setdefault("AUDIT_KEY", "a" * 64)

        from synth_engine.shared.settings import ConclaveSettings

        settings = ConclaveSettings(database_url="sqlite:///:memory:", audit_key="a" * 64)
        assert settings.webhook_max_registrations == 10

    def test_webhook_delivery_timeout_default(self) -> None:
        """ConclaveSettings.webhook_delivery_timeout_seconds defaults to 10."""
        from synth_engine.shared.settings import ConclaveSettings

        settings = ConclaveSettings(database_url="sqlite:///:memory:", audit_key="a" * 64)
        assert settings.webhook_delivery_timeout_seconds == 10
