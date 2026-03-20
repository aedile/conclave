"""Integration tests for JWT authentication middleware and /auth/token endpoint.

These tests exercise the full FastAPI HTTP stack end-to-end using
httpx.AsyncClient with ASGITransport. Each test drives a complete
HTTP flow through the real middleware chain.

Tests cover:
- Unauthenticated request to protected endpoint → 401
- Expired token → 401
- Valid token → request passes through to route handler
- /auth/token endpoint with valid credentials → 200 + token
- /auth/token endpoint with invalid credentials → 401
- Exempt paths (like /unseal, /health) pass without token
- Algorithm confusion rejection

CONSTITUTION Priority 0: Security — JWT pinned algorithm, no alg:none
CONSTITUTION Priority 3: TDD
Task: T39.1 — Add Authentication Middleware (JWT Bearer Token)
"""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import bcrypt as _bcrypt
import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Patch targets — module paths for state singletons
# ---------------------------------------------------------------------------

_VAULT_PATCH = "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed"
_LICENSE_PATCH = "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed"

#: A secret long enough for HS256 (PyJWT requires ≥256-bit for HS256 in strict mode).
_TEST_SECRET = (
    "integration-test-jwt-secret-key-long-enough-for-hs256-32chars+"  # pragma: allowlist secret
)

#: Test passphrase — well under 72-byte bcrypt limit.
_TEST_PASSPHRASE = "test-pass-ok"


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


@pytest.fixture(scope="session")
def test_passphrase_hash() -> str:
    """Compute bcrypt hash of the test passphrase once per session.

    Uses the ``bcrypt`` library directly (not passlib) since passlib's
    bcrypt backend is incompatible with bcrypt 5.0.0 on Python 3.14.

    Returns:
        bcrypt hash string of _TEST_PASSPHRASE.
    """
    return _bcrypt.hashpw(_TEST_PASSPHRASE.encode(), _bcrypt.gensalt()).decode()


# ---------------------------------------------------------------------------
# App factory helper
# ---------------------------------------------------------------------------


def _make_auth_test_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    secret: str = _TEST_SECRET,
    credentials_hash: str,
) -> Any:
    """Build a fully-wired FastAPI test app with JWT auth configured.

    Patches VaultState and LicenseState so middleware passes. Sets JWT
    environment variables for a deterministic test configuration.

    Args:
        monkeypatch: pytest monkeypatch fixture for env var injection.
        secret: JWT secret key to set in the environment.
        credentials_hash: bcrypt hash of the operator passphrase.

    Returns:
        A FastAPI application instance ready for AsyncClient testing.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", secret)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", credentials_hash)
    # Required settings for create_app() startup
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.main import create_app

    return create_app()


def _make_valid_token(secret: str = _TEST_SECRET) -> str:
    """Create a valid test JWT token.

    Args:
        secret: HMAC secret to sign the token with.

    Returns:
        Compact JWT string with valid claims and 1-hour expiry.
    """
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": "test-operator",
            "iat": now,
            "exp": now + 3600,
            "scope": ["read", "write"],
        },
        secret,
        algorithm="HS256",
    )


def _make_expired_token(secret: str = _TEST_SECRET) -> str:
    """Create an expired JWT token for rejection testing.

    Args:
        secret: HMAC secret to sign the token with.

    Returns:
        Compact JWT string with exp set 10 seconds in the past.
    """
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": "test-operator",
            "iat": now - 3610,
            "exp": now - 10,
            "scope": ["read"],
        },
        secret,
        algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# AC1 — Protected endpoints require JWT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_request_to_protected_endpoint_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """A request to /jobs without a token returns 401.

    Arrange: build the app with JWT configured; patch vault/license as open.
    Act: GET /jobs with no Authorization header.
    Assert: HTTP 401 with RFC 7807 body.
    """
    app = _make_auth_test_app(monkeypatch, credentials_hash=test_passphrase_hash)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/jobs")

    assert response.status_code == 401
    body = response.json()
    assert "status" in body
    assert body["status"] == 401


@pytest.mark.asyncio
async def test_expired_token_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """A request with an expired JWT token must return 401.

    Arrange: build the app; create an expired token.
    Act: GET /jobs with expired Authorization: Bearer token.
    Assert: HTTP 401.
    """
    app = _make_auth_test_app(monkeypatch, credentials_hash=test_passphrase_hash)
    expired_token = _make_expired_token()

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/jobs",
                headers={"Authorization": f"Bearer {expired_token}"},
            )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_valid_token_reaches_route_handler(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """A request with a valid JWT must reach the route handler (not be blocked).

    Arrange: build the app; create a valid token.
    Act: GET /jobs with valid Authorization: Bearer token.
    Assert: NOT 401 — route handler responds (any other status is acceptable).
    """
    app = _make_auth_test_app(monkeypatch, credentials_hash=test_passphrase_hash)
    valid_token = _make_valid_token()

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/jobs",
                headers={"Authorization": f"Bearer {valid_token}"},
            )

    # Auth gate must NOT return 401 — route handler responds (any other status is ok)
    assert response.status_code != 401, (
        f"Valid token should not receive 401; got {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_unseal_endpoint_exempt_from_auth(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """/unseal endpoint must be accessible without a JWT token.

    Arrange: build the app with JWT configured.
    Act: POST /unseal with no Authorization header.
    Assert: NOT 401 — vault/seal logic handles the response, but auth gate passes.
    """
    app = _make_auth_test_app(monkeypatch, credentials_hash=test_passphrase_hash)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/unseal",
            json={"passphrase": "anything"},
        )

    # Must not be 401 — auth gate must pass /unseal through
    assert response.status_code != 401, f"/unseal must be auth-exempt; got 401: {response.text}"


@pytest.mark.asyncio
async def test_health_endpoint_exempt_from_auth(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """/health endpoint must be accessible without a JWT token.

    Arrange: build the app with JWT configured.
    Act: GET /health with no Authorization header.
    Assert: HTTP 200 (health endpoint passes through without auth).
    """
    app = _make_auth_test_app(monkeypatch, credentials_hash=test_passphrase_hash)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200, (
        f"/health must be auth-exempt and return 200; got {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_algorithm_confusion_attack_rejected(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """Algorithm confusion attack (RS256 token against HS256-pinned decoder) is rejected.

    Security: An attacker who constructs a token with a different algorithm
    header must be rejected with 401, not allowed through.

    Arrange: build the app with HS256; craft a token with RS256 header.
    Act: GET /jobs with the forged token.
    Assert: HTTP 401.
    """
    import base64
    import json

    app = _make_auth_test_app(monkeypatch, credentials_hash=test_passphrase_hash)

    now = int(time.time())
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        .rstrip(b"=")
        .decode()
    )
    payload_b64 = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sub": "attacker",
                    "exp": now + 3600,
                    "iat": now,
                    "scope": ["admin"],
                }
            ).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    forged_token = f"{header}.{payload_b64}.fakesignature"

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/jobs",
                headers={"Authorization": f"Bearer {forged_token}"},
            )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# AC5 — /auth/token endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_token_endpoint_returns_200_with_valid_credentials(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """POST /auth/token with valid credentials returns 200 and a JWT token.

    Arrange: build the app with credentials hash configured.
    Act: POST /auth/token with username and correct passphrase.
    Assert: HTTP 200 with a token field.
    """
    app = _make_auth_test_app(monkeypatch, credentials_hash=test_passphrase_hash)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/token",
                json={"username": "operator", "passphrase": _TEST_PASSPHRASE},
            )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body, f"Response must contain 'access_token'; got: {body}"
    assert "token_type" in body
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_auth_token_endpoint_returns_401_with_invalid_credentials(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """POST /auth/token with wrong passphrase returns 401.

    Arrange: build the app with a known credentials hash.
    Act: POST /auth/token with wrong passphrase.
    Assert: HTTP 401.
    """
    app = _make_auth_test_app(monkeypatch, credentials_hash=test_passphrase_hash)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/token",
                json={"username": "operator", "passphrase": "wrong-passphrase"},
            )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_token_endpoint_returns_401_when_no_credentials_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /auth/token returns 401 when OPERATOR_CREDENTIALS_HASH is empty.

    Security: if no credentials are configured, no token should be issued.

    Arrange: build the app with empty credentials hash.
    Act: POST /auth/token with any credentials.
    Assert: HTTP 401.
    """
    app = _make_auth_test_app(monkeypatch, credentials_hash="")

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/token",
                json={"username": "operator", "passphrase": "any-passphrase"},
            )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_issued_token_can_authenticate_subsequent_request(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """Token issued by /auth/token can be used to authenticate subsequent requests.

    This is the critical end-to-end flow: obtain a token, then use it.

    Arrange: build the app with credentials configured.
    Act: (1) POST /auth/token → get token; (2) GET /jobs with token.
    Assert: step (2) returns non-401.
    """
    app = _make_auth_test_app(monkeypatch, credentials_hash=test_passphrase_hash)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Step 1: obtain token
            token_response = await client.post(
                "/auth/token",
                json={"username": "operator", "passphrase": _TEST_PASSPHRASE},
            )
            assert token_response.status_code == 200, (
                f"Token issuance failed: {token_response.text}"
            )
            access_token = token_response.json()["access_token"]

            # Step 2: use token on a protected endpoint
            jobs_response = await client.get(
                "/jobs",
                headers={"Authorization": f"Bearer {access_token}"},
            )

    assert jobs_response.status_code != 401, (
        f"Issued token failed auth: {jobs_response.status_code}: {jobs_response.text}"
    )


@pytest.mark.asyncio
async def test_401_response_is_rfc7807_format(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """401 Unauthorized response body must conform to RFC 7807 Problem Details.

    Arrange: build the app with JWT configured.
    Act: GET /jobs with no token.
    Assert: response body has status, title, detail fields.
    """
    app = _make_auth_test_app(monkeypatch, credentials_hash=test_passphrase_hash)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/jobs")

    assert response.status_code == 401
    body = response.json()
    assert "status" in body, f"RFC 7807 requires 'status' field; got: {body}"
    assert "title" in body, f"RFC 7807 requires 'title' field; got: {body}"
    assert "detail" in body, f"RFC 7807 requires 'detail' field; got: {body}"
    assert body["status"] == 401
