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


# ---------------------------------------------------------------------------
# F21: RS256 full callback flow integration test
# ---------------------------------------------------------------------------


class TestRS256CallbackFlow:
    """Integration test for the full RS256 callback → token exchange → JWT verification flow.

    Uses a real RSA key pair generated in the test. Serves the JWKS and token
    endpoint via pytest-httpserver (a real local HTTP server — not a mock).
    Exercises the complete _verify_id_token() path with a real RS256-signed
    ID token.

    F21 — round-2 review finding: no integration test existed for the full
    RS256 callback flow.
    """

    def test_rs256_callback_issues_jwt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        httpserver: HTTPServer,
    ) -> None:
        """Full authorize → callback → JWT flow with real RS256 key pair.

        AC: The callback endpoint must:
        1. Accept a real RS256-signed ID token (not HS256).
        2. Verify the signature using the public key from a live JWKS endpoint
           served by pytest-httpserver.
        3. Return a compact JWT access token on success.

        This test generates a real RSA-2048 key pair, signs an OIDC ID token,
        serves the corresponding JWKS via pytest-httpserver, and exercises the
        full _verify_id_token() code path.
        """
        import math
        import time
        import uuid as _uuid
        from base64 import urlsafe_b64encode as _b64

        import jwt as pyjwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        # --- Generate a real RSA-2048 key pair ---
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        public_key = private_key.public_key()

        # --- Build JWKS from public key ---
        pub_numbers = public_key.public_numbers()

        def _int_to_b64(n: int) -> str:
            byte_length = math.ceil(n.bit_length() / 8)
            return _b64(n.to_bytes(byte_length, "big")).rstrip(b"=").decode()

        kid = "test-rsa-key-1"
        jwk = {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": kid,
            "n": _int_to_b64(pub_numbers.n),
            "e": _int_to_b64(pub_numbers.e),
        }
        jwks = {"keys": [jwk]}

        issuer_url = httpserver.url_for("").rstrip("/")

        # --- Sign a real OIDC ID token with the private key ---
        now = int(time.time())
        id_token_claims = {
            "iss": issuer_url,
            "sub": "user@example.com",
            "aud": "test-client",
            "iat": now,
            "exp": now + 300,
            "email": "user@example.com",
        }
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        id_token = pyjwt.encode(
            id_token_claims,
            private_pem,
            algorithm="RS256",
            headers={"kid": kid},
        )

        # --- Build the mock token endpoint response ---
        token_response = {
            "access_token": "idp-access-token",
            "token_type": "bearer",
            "id_token": id_token,
        }

        # --- Configure pytest-httpserver endpoints ---
        discovery_doc = {
            "issuer": issuer_url,
            "authorization_endpoint": f"{issuer_url}/authorize",
            "token_endpoint": f"{issuer_url}/token",
            "jwks_uri": f"{issuer_url}/.well-known/jwks.json",
        }
        httpserver.expect_request("/.well-known/openid-configuration").respond_with_json(
            discovery_doc
        )
        httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
        httpserver.expect_request("/token", method="POST").respond_with_json(token_response)

        # --- Configure environment ---
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

        with patch("synth_engine.bootstrapper.dependencies.oidc.validate_oidc_issuer_url"):
            initialize_oidc_provider(
                issuer_url=issuer_url,
                client_id="test-client",
            )

        # --- Redis mock: in-memory store for PKCE state ---
        redis_store: dict[str, bytes] = {}

        mock_redis = MagicMock()

        def _setex(key: str, ttl: int, value: str | bytes) -> None:
            redis_store[key] = value if isinstance(value, bytes) else value.encode()

        def _getdel(key: str) -> bytes | None:
            return redis_store.pop(key, None)

        mock_redis.setex.side_effect = _setex
        mock_redis.getdel.side_effect = _getdel
        mock_redis.smembers.return_value = set()
        mock_redis.eval.return_value = 1

        # --- DB mock: provision a new user ---
        mock_user = MagicMock()
        mock_user.id = _uuid.UUID(_USER_UUID)
        mock_user.email = "user@example.com"
        mock_user.role = "operator"
        mock_db_session = MagicMock()
        mock_db_session.exec.return_value.first.return_value = None  # no existing user

        def _mock_refresh(u: Any) -> None:
            u.id = _uuid.UUID(_USER_UUID)
            u.email = "user@example.com"
            u.role = "operator"

        mock_db_session.refresh.side_effect = _mock_refresh

        verifier, challenge = _make_pkce_pair()

        client = TestClient(app, raise_server_exceptions=False)

        with (
            patch(
                "synth_engine.bootstrapper.routers.auth_oidc.get_redis_client",
                return_value=mock_redis,
            ),
            patch("synth_engine.bootstrapper.routers.auth_oidc.get_audit_logger") as mock_al,
            patch("sqlmodel.Session") as mock_session_cls,
            patch("synth_engine.shared.db.get_engine"),
        ):
            mock_al.return_value = MagicMock()
            mock_session_cls.return_value.__enter__ = lambda s, *a: mock_db_session
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

            # Step 1: authorize
            auth_resp = client.get(
                f"/auth/oidc/authorize?code_challenge={challenge}&code_challenge_method=S256"
            )
            assert auth_resp.status_code == 200, (
                f"Expected 200 from authorize, got {auth_resp.status_code}: {auth_resp.text}"
            )

            # Step 2: extract state from authorize response
            auth_body = auth_resp.json()
            state = auth_body["state"]
            assert state, "Authorize must return a non-empty state"

            # Step 3: callback — pass the state and code_verifier
            callback_resp = client.get(
                f"/auth/oidc/callback?code=test-auth-code&state={state}&code_verifier={verifier}"
            )

        get_settings.cache_clear()

        assert callback_resp.status_code == 200, (
            f"Expected 200 from callback (RS256), got {callback_resp.status_code}: "
            f"{callback_resp.text}"
        )
        body = callback_resp.json()
        assert "access_token" in body, (
            f"Callback must return 'access_token', got keys: {list(body.keys())}"
        )
        assert body["token_type"] == "bearer", (
            f"token_type must be 'bearer', got {body['token_type']!r}"
        )
        assert body["expires_in"] == 3600, f"expires_in must be 3600, got {body['expires_in']!r}"

        # Verify the returned JWT is a valid compact JWT (3 dot-separated segments)
        token_parts = body["access_token"].split(".")
        assert len(token_parts) == 3, (
            f"access_token must be a compact JWT (3 parts), got {len(token_parts)} parts"
        )

    def test_rs256_callback_rejects_tampered_id_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
        httpserver: HTTPServer,
    ) -> None:
        """Callback with a tampered RS256 ID token returns 401.

        AC: A tampered signature on the ID token must be rejected by
        _verify_id_token(). This validates that signature verification is
        actually enforced, not just present.
        """
        import math
        import time
        from base64 import urlsafe_b64encode as _b64

        import jwt as pyjwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        # --- Two separate key pairs: one signs, other is in JWKS (mismatch) ---
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwks_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwks_pub_numbers = jwks_key.public_key().public_numbers()

        def _int_to_b64(n: int) -> str:
            byte_length = math.ceil(n.bit_length() / 8)
            return _b64(n.to_bytes(byte_length, "big")).rstrip(b"=").decode()

        kid = "test-rsa-key-2"
        jwk = {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": kid,
            "n": _int_to_b64(jwks_pub_numbers.n),
            "e": _int_to_b64(jwks_pub_numbers.e),
        }
        jwks = {"keys": [jwk]}

        issuer_url = httpserver.url_for("").rstrip("/")

        now = int(time.time())
        id_token_claims = {
            "iss": issuer_url,
            "sub": "user@example.com",
            "aud": "test-client",
            "iat": now,
            "exp": now + 300,
            "email": "user@example.com",
        }
        # Sign with the WRONG key (not in JWKS)
        signing_pem = signing_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        id_token = pyjwt.encode(
            id_token_claims, signing_pem, algorithm="RS256", headers={"kid": kid}
        )

        token_response = {"access_token": "idp-at", "token_type": "bearer", "id_token": id_token}

        discovery_doc = {
            "issuer": issuer_url,
            "authorization_endpoint": f"{issuer_url}/authorize",
            "token_endpoint": f"{issuer_url}/token",
            "jwks_uri": f"{issuer_url}/.well-known/jwks.json",
        }
        httpserver.expect_request("/.well-known/openid-configuration").respond_with_json(
            discovery_doc
        )
        httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
        httpserver.expect_request("/token", method="POST").respond_with_json(token_response)

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

        from synth_engine.bootstrapper.dependencies.oidc import initialize_oidc_provider
        from synth_engine.bootstrapper.routers.auth_oidc import router as oidc_router

        app = FastAPI()
        app.include_router(oidc_router)

        with patch("synth_engine.bootstrapper.dependencies.oidc.validate_oidc_issuer_url"):
            initialize_oidc_provider(issuer_url=issuer_url, client_id="test-client")

        redis_store: dict[str, bytes] = {}
        mock_redis = MagicMock()

        def _setex(key: str, ttl: int, value: str | bytes) -> None:
            redis_store[key] = value if isinstance(value, bytes) else value.encode()

        def _getdel(key: str) -> bytes | None:
            return redis_store.pop(key, None)

        mock_redis.setex.side_effect = _setex
        mock_redis.getdel.side_effect = _getdel
        mock_redis.smembers.return_value = set()

        verifier, challenge = _make_pkce_pair()
        client = TestClient(app, raise_server_exceptions=False)

        with patch(
            "synth_engine.bootstrapper.routers.auth_oidc.get_redis_client",
            return_value=mock_redis,
        ):
            auth_resp = client.get(
                f"/auth/oidc/authorize?code_challenge={challenge}&code_challenge_method=S256"
            )
            assert auth_resp.status_code == 200, (
                f"Expected 200 from authorize, got {auth_resp.status_code}"
            )
            state = auth_resp.json()["state"]

            callback_resp = client.get(
                f"/auth/oidc/callback?code=test-code&state={state}&code_verifier={verifier}"
            )

        get_settings.cache_clear()

        assert callback_resp.status_code == 401, (
            f"Expected 401 for tampered RS256 signature, got {callback_resp.status_code}: "
            f"{callback_resp.text}"
        )
        body = callback_resp.json()
        problem = body.get("detail", body)
        assert problem.get("status") == 401, (
            f"Expected RFC 7807 status=401 in problem body, got {problem!r}"
        )
