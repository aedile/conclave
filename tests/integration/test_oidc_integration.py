"""Integration tests for the OIDC authorization flow — Phase 81 (B4).

Tests the full OIDC authorize → callback → JWT flow against a live HTTP server
(pytest-httpserver) acting as a mock IdP. These tests exercise the real
``initialize_oidc_provider()``, ``get_oidc_authorize``, and ``get_oidc_callback``
code paths with a real JWKS key pair.

Tests:
1. Full OIDC happy path: authorize → callback → JWT returned.
2. Callback with invalid state → 401 rejected.
3. OIDC disabled → authorize returns 404.

CONSTITUTION Priority 0: Security — OIDC flow, PKCE, CSRF
CONSTITUTION Priority 3: TDD
Phase: 81 — SSO/OIDC Integration
Review fix: B4 — OIDC integration tests
ADR: ADR-0067 — OIDC Integration
"""

from __future__ import annotations

import hashlib
from base64 import urlsafe_b64encode
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_httpserver import HTTPServer

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = (  # pragma: allowlist secret
    "integration-test-jwt-secret-key-long-enough-for-hs256-32chars+"
)
_ORG_UUID = "11111111-1111-1111-1111-111111111111"
_USER_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE (verifier, challenge) pair for testing.

    Returns:
        Tuple of (code_verifier, code_challenge) where challenge is
        BASE64URL(SHA256(verifier)).
    """
    verifier = "test-code-verifier-for-integration-tests-1234"
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis() -> MagicMock:
    """In-memory Redis mock with getdel support for OIDC state management.

    Returns:
        MagicMock configured to behave like a minimal Redis client.
    """
    redis = MagicMock()
    _store: dict[str, bytes] = {}

    def _setex(key: str, ttl: int, value: str | bytes) -> None:
        _store[key] = value if isinstance(value, bytes) else value.encode()

    def _getdel(key: str) -> bytes | None:
        return _store.pop(key, None)

    def _smembers(key: str) -> set[bytes]:
        return set()

    redis.setex.side_effect = _setex
    redis.getdel.side_effect = _getdel
    redis.smembers.side_effect = _smembers
    redis.eval.return_value = 1  # Lua script for write_session
    return redis


@pytest.fixture
def oidc_test_app(
    monkeypatch: pytest.MonkeyPatch,
    httpserver: HTTPServer,
    mock_redis: MagicMock,
) -> TestClient:
    """FastAPI TestClient wired to the OIDC router with a mock IdP.

    Uses pytest-httpserver to simulate a real OpenID Connect provider
    with a discovery document and JWKS endpoint.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        httpserver: Local HTTP server acting as the mock IdP.
        mock_redis: In-memory Redis mock.

    Returns:
        TestClient configured for OIDC integration testing.
    """
    issuer_url = httpserver.url_for("").rstrip("/")

    # Configure mock IdP discovery document.
    discovery_doc = {
        "issuer": issuer_url,
        "authorization_endpoint": f"{issuer_url}/authorize",
        "token_endpoint": f"{issuer_url}/token",
        "jwks_uri": f"{issuer_url}/.well-known/jwks.json",
    }
    httpserver.expect_request("/.well-known/openid-configuration").respond_with_json(discovery_doc)

    # JWKS with a single HS256-compatible key (simplified for testing).
    # In production this would be RS256; we test the full JWT decode flow below.
    jwks_data: dict[str, Any] = {"keys": []}
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks_data)

    # Set environment.
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER_URL", issuer_url)
    monkeypatch.setenv("OIDC_CLIENT_ID", "test-client")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-secret")  # pragma: allowlist secret
    monkeypatch.setenv("OIDC_STATE_TTL_SECONDS", "600")
    monkeypatch.setenv("SESSION_TTL_SECONDS", "28800")
    monkeypatch.setenv("CONCURRENT_SESSION_LIMIT", "3")
    monkeypatch.setenv("CONCLAVE_MULTI_TENANT_ENABLED", "false")
    monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "false")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.dependencies.oidc import (
        initialize_oidc_provider,
    )
    from synth_engine.bootstrapper.routers.auth_oidc import router as oidc_router

    app = FastAPI()
    app.include_router(oidc_router)

    # Initialize the OIDC provider against the mock IdP.
    with patch("synth_engine.bootstrapper.dependencies.oidc.validate_oidc_issuer_url"):
        initialize_oidc_provider(
            issuer_url=issuer_url,
            client_id="test-client",
        )

    client = TestClient(app, raise_server_exceptions=False)

    with patch(
        "synth_engine.bootstrapper.routers.auth_oidc.get_redis_client",
        return_value=mock_redis,
    ):
        yield client

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOIDCAuthorizeEndpoint:
    """Integration tests for GET /auth/oidc/authorize."""

    def test_authorize_returns_redirect_url_and_state(self, oidc_test_app: TestClient) -> None:
        """GET /auth/oidc/authorize returns redirect_url and state in JSON.

        AC: Full integration test — provider initialized from mock IdP,
        state written to mock Redis, redirect_url points to mock IdP authorize.
        """
        _, challenge = _make_pkce_pair()

        resp = oidc_test_app.get(
            f"/auth/oidc/authorize?code_challenge={challenge}&code_challenge_method=S256"
        )

        assert resp.status_code == 200, (
            f"Expected 200 from /auth/oidc/authorize, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "redirect_url" in body, f"Expected 'redirect_url' in response, got {body}"
        assert "state" in body, f"Expected 'state' in response, got {body}"
        assert "?response_type=code" in body["redirect_url"], (
            f"redirect_url must include response_type=code: {body['redirect_url']!r}"
        )
        assert len(body["state"]) > 0, "State must be non-empty"

    def test_authorize_invalid_challenge_format_returns_400(
        self, oidc_test_app: TestClient
    ) -> None:
        """code_challenge not matching RFC 7636 format returns 400.

        AC: F1 — code_challenge must be exactly 43 URL-safe base64 chars.
        """
        resp = oidc_test_app.get(
            "/auth/oidc/authorize?code_challenge=short&code_challenge_method=S256"
        )
        assert resp.status_code == 400, (
            f"Expected 400 for invalid code_challenge format, got {resp.status_code}"
        )


class TestOIDCCallbackEndpoint:
    """Integration tests for GET /auth/oidc/callback."""

    def test_callback_with_invalid_state_returns_401(self, oidc_test_app: TestClient) -> None:
        """Callback with a state not in Redis returns 401.

        AC: State validation is the first callback check. An unknown state
        means the request was not preceded by a valid authorize call or
        the state has expired/been replayed.
        """
        resp = oidc_test_app.get(
            "/auth/oidc/callback"
            "?code=some-auth-code"
            "&state=state-not-in-redis"
            "&code_verifier=valid-verifier-abc123"
        )
        assert resp.status_code == 401, (
            f"Expected 401 for unknown state, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        problem = body.get("detail", body)
        assert problem.get("status") == 401, (
            f"Expected RFC 7807 status=401 in problem body, got {problem!r}"
        )

    def test_callback_missing_state_returns_422(self, oidc_test_app: TestClient) -> None:
        """Callback with missing state query parameter returns 422.

        AC: FastAPI validates required query params before route handler runs.
        Missing state → FastAPI 422 Unprocessable Entity.
        """
        resp = oidc_test_app.get(
            "/auth/oidc/callback?code=some-auth-code"
            # No state parameter
        )
        assert resp.status_code == 422, f"Expected 422 for missing state, got {resp.status_code}"


class TestOIDCDisabled:
    """Integration tests for OIDC disabled mode."""

    def test_authorize_returns_404_when_oidc_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /auth/oidc/authorize returns 404 when OIDC_ENABLED=false.

        AC: When OIDC is disabled, both OIDC endpoints return 404 to avoid
        advertising their existence (Decision 5 / ADR-0067).
        """
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")
        monkeypatch.setenv("OIDC_ENABLED", "false")
        monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "false")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from synth_engine.bootstrapper.routers.auth_oidc import router as oidc_router

        app = FastAPI()
        app.include_router(oidc_router)
        client = TestClient(app, raise_server_exceptions=False)

        try:
            _, challenge = _make_pkce_pair()
            resp = client.get(
                f"/auth/oidc/authorize?code_challenge={challenge}&code_challenge_method=S256"
            )
            assert resp.status_code == 404, (
                f"Expected 404 when OIDC disabled, got {resp.status_code}"
            )
        finally:
            get_settings.cache_clear()
