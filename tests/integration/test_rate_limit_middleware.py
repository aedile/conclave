"""Integration tests for rate limiting middleware (T39.3).

These tests exercise the full FastAPI HTTP stack with the rate limit
middleware registered and wired via setup_middleware(). All middleware
layers are active (vault, license, auth, and rate limit).

Tests cover:
- AC1: Middleware active on all endpoints in the full stack.
- AC2: Exceeding rate limit returns 429 RFC 7807 with Retry-After.
- AC3: /unseal limited to 5/min per IP (validated with low test limit).
- AC4: Authenticated endpoints limited per operator.
- AC5: Rate limit values read from ConclaveSettings.
- AC6: Different operators have independent limits.

CONSTITUTION Priority 0: Security — brute-force protection
CONSTITUTION Priority 3: TDD
Task: T39.3 — Add Rate Limiting Middleware
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
# Patch targets
# ---------------------------------------------------------------------------

_VAULT_PATCH = "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed"
_LICENSE_PATCH = "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed"

_TEST_SECRET = (
    "integration-test-jwt-secret-key-long-enough-for-hs256-32chars+"  # pragma: allowlist secret
)
_TEST_PASSPHRASE = "test-rate-limit-pass"

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

    Returns:
        bcrypt hash string of _TEST_PASSPHRASE.
    """
    return _bcrypt.hashpw(_TEST_PASSPHRASE.encode(), _bcrypt.gensalt()).decode()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_rate_limit_test_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    credentials_hash: str,
    unseal_limit: int = 5,
    auth_limit: int = 10,
    general_limit: int = 60,
    download_limit: int = 10,
) -> Any:
    """Build a fully-wired test app with configurable rate limits.

    Injects rate limit settings via environment variables so that the
    middleware reads them from ConclaveSettings rather than hardcoded values.

    Args:
        monkeypatch: pytest monkeypatch fixture for env var injection.
        credentials_hash: bcrypt hash of the operator passphrase.
        unseal_limit: Per-IP limit on /unseal per minute.
        auth_limit: Per-IP limit on /auth/token per minute.
        general_limit: Per-operator limit on all other endpoints per minute.
        download_limit: Per-operator limit on /jobs/{id}/download per minute.

    Returns:
        A FastAPI application instance ready for AsyncClient testing.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", credentials_hash)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)
    monkeypatch.setenv("RATE_LIMIT_UNSEAL_PER_MINUTE", str(unseal_limit))
    monkeypatch.setenv("RATE_LIMIT_AUTH_PER_MINUTE", str(auth_limit))
    monkeypatch.setenv("RATE_LIMIT_GENERAL_PER_MINUTE", str(general_limit))
    monkeypatch.setenv("RATE_LIMIT_DOWNLOAD_PER_MINUTE", str(download_limit))

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.main import create_app

    return create_app()


def _make_valid_token(sub: str = "test-operator") -> str:
    """Create a valid JWT token for integration tests.

    Args:
        sub: Subject claim (operator identifier).

    Returns:
        Compact JWT string with 1-hour expiry.
    """
    now = int(time.time())
    return pyjwt.encode(
        {"sub": sub, "iat": now, "exp": now + 3600, "scope": ["read", "write"]},
        _TEST_SECRET,
        algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# AC1: Rate limiting active on all endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_middleware_active_in_full_stack(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """Rate limiting middleware must be active in the full application stack.

    Arrange: build the full app with low limits so 429 is reachable.
    Act: exceed the /unseal limit.
    Assert: 429 returned (proves rate limiting is active, not bypassed).
    """
    app = _make_rate_limit_test_app(
        monkeypatch,
        credentials_hash=test_passphrase_hash,
        unseal_limit=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Forwarded-For": "192.0.2.1"}
        # First request should succeed
        r1 = await client.post("/unseal", json={"passphrase": "anything"}, headers=headers)
        assert r1.status_code != 429, f"First request must not be rate limited; got {r1.status_code}"
        # Second request should be rate limited
        r2 = await client.post("/unseal", json={"passphrase": "anything"}, headers=headers)

    assert r2.status_code == 429, (
        f"Second /unseal from same IP must be rate limited (429); got {r2.status_code}"
    )


# ---------------------------------------------------------------------------
# AC2: 429 response is RFC 7807 with Retry-After in full stack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_response_rfc7807_and_retry_after_in_full_stack(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """429 response must conform to RFC 7807 and include Retry-After header.

    Full-stack test: rate limit triggers before vault/license/auth gates.

    Arrange: build the app with unseal_limit=1.
    Act: make two /unseal requests from the same IP.
    Assert: second response is 429 with RFC 7807 body and Retry-After header.
    """
    app = _make_rate_limit_test_app(
        monkeypatch,
        credentials_hash=test_passphrase_hash,
        unseal_limit=1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Forwarded-For": "192.0.2.2"}
        await client.post("/unseal", json={"passphrase": "anything"}, headers=headers)
        response = await client.post("/unseal", json={"passphrase": "anything"}, headers=headers)

    assert response.status_code == 429
    body = response.json()
    assert body.get("status") == 429
    assert "title" in body
    assert "detail" in body
    assert "retry-after" in response.headers, (
        f"429 must include Retry-After; headers: {dict(response.headers)}"
    )


# ---------------------------------------------------------------------------
# AC3: /unseal is OUTERMOST — fires before vault/auth gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_fires_before_vault_gate_on_unseal(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """/unseal rate limit fires BEFORE the vault gate (outermost layer).

    If rate limiting is outermost, a rate-limited /unseal request must return
    429, not 423 (Locked by vault) or 401 (Unauthorized). This proves the
    middleware ordering is correct.

    Arrange: vault is sealed; unseal_limit=1.
    Act: exceed the /unseal limit while vault is sealed.
    Assert: second request returns 429, not 423.
    """
    app = _make_rate_limit_test_app(
        monkeypatch,
        credentials_hash=test_passphrase_hash,
        unseal_limit=1,
    )

    # Vault is sealed — SealGate would return 423. But rate limit fires first.
    with patch(_VAULT_PATCH, return_value=True):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-Forwarded-For": "192.0.2.3"}
            await client.post("/unseal", json={"passphrase": "x"}, headers=headers)
            response = await client.post("/unseal", json={"passphrase": "x"}, headers=headers)

    assert response.status_code == 429, (
        f"Rate limit (429) must fire before vault gate (423); got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# AC4: Authenticated endpoints limited per operator (full stack)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticated_endpoint_rate_limited_per_operator(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """Authenticated endpoint /jobs must be rate limited per operator.

    Arrange: build with general_limit=1; valid JWT.
    Act: two GET /jobs requests with the same token.
    Assert: second request returns 429.
    """
    app = _make_rate_limit_test_app(
        monkeypatch,
        credentials_hash=test_passphrase_hash,
        general_limit=1,
    )
    token = _make_valid_token("op-alpha")

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {token}"}
            r1 = await client.get("/jobs", headers=headers)
            r2 = await client.get("/jobs", headers=headers)

    assert r1.status_code != 429, f"First request must not be rate limited; got {r1.status_code}"
    assert r2.status_code == 429, (
        f"Second request must be rate limited (429); got {r2.status_code}"
    )


# ---------------------------------------------------------------------------
# AC6: Different operators have independent limits (full stack)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_operators_independent_limits_full_stack(
    monkeypatch: pytest.MonkeyPatch,
    test_passphrase_hash: str,
) -> None:
    """Different operators must have independent rate limit buckets.

    Arrange: build with general_limit=1; two operator tokens.
    Act: exhaust operator A; make a request as operator B.
    Assert: operator A gets 429; operator B gets 200.
    """
    app = _make_rate_limit_test_app(
        monkeypatch,
        credentials_hash=test_passphrase_hash,
        general_limit=1,
    )
    token_a = _make_valid_token("op-alpha")
    token_b = _make_valid_token("op-beta")

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Exhaust operator A
            await client.get("/jobs", headers={"Authorization": f"Bearer {token_a}"})
            r_a2 = await client.get("/jobs", headers={"Authorization": f"Bearer {token_a}"})
            # Operator B should still be allowed
            r_b1 = await client.get("/jobs", headers={"Authorization": f"Bearer {token_b}"})

    assert r_a2.status_code == 429, "Operator A must be rate limited after exceeding"
    assert r_b1.status_code != 429, "Operator B must NOT be affected by operator A's limit"


# ---------------------------------------------------------------------------
# AC5: Settings-driven configuration via env vars
# ---------------------------------------------------------------------------


def test_rate_limit_settings_read_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rate limit settings must be read from environment variables via ConclaveSettings.

    Arrange: set rate limit env vars to non-default values.
    Assert: ConclaveSettings fields reflect those values.
    """
    monkeypatch.setenv("RATE_LIMIT_UNSEAL_PER_MINUTE", "3")
    monkeypatch.setenv("RATE_LIMIT_AUTH_PER_MINUTE", "7")
    monkeypatch.setenv("RATE_LIMIT_GENERAL_PER_MINUTE", "30")
    monkeypatch.setenv("RATE_LIMIT_DOWNLOAD_PER_MINUTE", "5")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.rate_limit_unseal_per_minute == 3
    assert settings.rate_limit_auth_per_minute == 7
    assert settings.rate_limit_general_per_minute == 30
    assert settings.rate_limit_download_per_minute == 5
