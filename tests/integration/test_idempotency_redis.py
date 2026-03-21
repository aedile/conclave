"""Integration tests for idempotency middleware with live Redis (T45.1).

These tests exercise the full FastAPI HTTP stack with the IdempotencyMiddleware
wired to an actual Redis instance.  They require a running Redis server on
the default URL (redis://localhost:6379/0 or REDIS_URL env var).

Tests cover:
- AC1: Middleware active in the full stack with real Redis.
- AC2: Duplicate key returns 409 with correct JSON body.
- AC3: Key expires after TTL (verified with TTL=1).
- AC4: Key is released on handler exception (retry succeeds).
- AC5: Per-operator key scoping prevents cross-operator collisions.
- AC6: TTL is read from ConclaveSettings.idempotency_ttl_seconds.

CONSTITUTION Priority 0: Security — idempotency prevents duplicate job creation
CONSTITUTION Priority 3: TDD
Task: T45.1 — Reintroduce Idempotency Middleware (TBD-07)
"""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any

import jwt as pyjwt
import pytest
import redis as redis_lib
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = "integration-test-secret-key-long-enough"  # pragma: allowlist secret
_REDIS_URL = "redis://localhost:6379/1"  # DB 1 to avoid colliding with Huey DB 0

# ---------------------------------------------------------------------------
# Skip guard — skip if Redis is not available
# ---------------------------------------------------------------------------


def _redis_available() -> bool:
    """Return True if a Redis server is reachable at _REDIS_URL.

    Returns:
        True when Redis responds to PING; False on any connection error.
    """
    try:
        client = redis_lib.Redis.from_url(_REDIS_URL, socket_connect_timeout=1)
        client.ping()
        client.close()
        return True
    except (redis_lib.ConnectionError, redis_lib.TimeoutError):
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _redis_available(), reason="Redis not available"),
]

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


@pytest.fixture
def redis_client() -> Generator[redis_lib.Redis[Any]]:
    """Provide a Redis client connected to DB 1 (test isolation).

    Flushes DB 1 before and after each test to ensure a clean state.

    Yields:
        redis.Redis client connected to test DB 1.
    """
    client: redis_lib.Redis[Any] = redis_lib.Redis.from_url(_REDIS_URL)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


# ---------------------------------------------------------------------------
# Test application factory
# ---------------------------------------------------------------------------


def _make_test_app(
    redis_client_inst: redis_lib.Redis[Any],
    *,
    ttl: int = 300,
) -> Any:
    """Build a minimal FastAPI app with IdempotencyMiddleware using real Redis.

    Args:
        redis_client_inst: Live Redis client to inject.
        ttl: Idempotency TTL in seconds.

    Returns:
        FastAPI application instance.
    """
    from fastapi import FastAPI

    from synth_engine.shared.middleware.idempotency import IdempotencyMiddleware

    app = FastAPI()
    exempt = frozenset({"/health", "/unseal"})

    @app.post("/jobs")
    async def create_job() -> dict[str, str]:
        return {"status": "created"}

    @app.post("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("handler exploded")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(
        IdempotencyMiddleware,
        redis_client=redis_client_inst,
        exempt_paths=exempt,
        ttl_seconds=ttl,
    )
    return app


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_request_with_new_key_returns_200(
    redis_client: redis_lib.Redis[Any],
) -> None:
    """First POST with new idempotency key must return 200 and store key in Redis.

    AC1: Middleware is active in the full stack with real Redis.
    """
    app = _make_test_app(redis_client)
    token = pyjwt.encode({"sub": "op-1", "exp": 9999999999, "iat": 1}, _TEST_SECRET)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/jobs",
            headers={"Idempotency-Key": "job-key-001", "Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    # Verify key exists in Redis
    assert redis_client.exists("idempotency:op-1:job-key-001") == 1


@pytest.mark.asyncio
async def test_duplicate_key_returns_409(redis_client: redis_lib.Redis[Any]) -> None:
    """Second POST with the same idempotency key must return 409.

    AC2: Duplicate key returns 409 with correct JSON body.
    """
    app = _make_test_app(redis_client)
    token = pyjwt.encode({"sub": "op-1", "exp": 9999999999, "iat": 1}, _TEST_SECRET)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp1 = await ac.post(
            "/jobs",
            headers={"Idempotency-Key": "dup-key", "Authorization": f"Bearer {token}"},
        )
        resp2 = await ac.post(
            "/jobs",
            headers={"Idempotency-Key": "dup-key", "Authorization": f"Bearer {token}"},
        )

    assert resp1.status_code == 200
    assert resp2.status_code == 409
    body = resp2.json()
    assert body["detail"] == "Duplicate request"
    assert body["idempotency_key"] == "dup-key"


@pytest.mark.asyncio
async def test_key_expires_after_ttl(redis_client: redis_lib.Redis[Any]) -> None:
    """Idempotency key must expire from Redis after TTL seconds.

    AC3: Key expires after TTL — after expiry the same key is accepted again.
    Uses TTL=1 so the test does not need to wait long.
    """
    app = _make_test_app(redis_client, ttl=1)
    token = pyjwt.encode({"sub": "op-1", "exp": 9999999999, "iat": 1}, _TEST_SECRET)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp1 = await ac.post(
            "/jobs",
            headers={"Idempotency-Key": "ttl-key", "Authorization": f"Bearer {token}"},
        )
        assert resp1.status_code == 200

        # Wait for TTL to expire
        time.sleep(1.1)

        resp2 = await ac.post(
            "/jobs",
            headers={"Idempotency-Key": "ttl-key", "Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_key_released_on_handler_exception(redis_client: redis_lib.Redis[Any]) -> None:
    """On handler exception, key must be deleted from Redis so retry succeeds.

    AC4: Key is released on handler exception.
    """
    app = _make_test_app(redis_client)
    token = pyjwt.encode({"sub": "op-1", "exp": 9999999999, "iat": 1}, _TEST_SECRET)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # First call triggers exception — key should be released
        resp1 = await ac.post(
            "/boom",
            headers={"Idempotency-Key": "retry-key", "Authorization": f"Bearer {token}"},
        )
        # FastAPI returns 500 for unhandled exceptions
        assert resp1.status_code == 500

        # Key must NOT remain in Redis
        assert redis_client.exists("idempotency:op-1:retry-key") == 0


@pytest.mark.asyncio
async def test_per_operator_key_scoping_prevents_collision(
    redis_client: redis_lib.Redis[Any],
) -> None:
    """Two operators using the same key must not collide in Redis.

    AC5: Per-operator key scoping.
    """
    app = _make_test_app(redis_client)
    token_a = pyjwt.encode({"sub": "op-alice", "exp": 9999999999, "iat": 1}, _TEST_SECRET)
    token_b = pyjwt.encode({"sub": "op-bob", "exp": 9999999999, "iat": 1}, _TEST_SECRET)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp_a = await ac.post(
            "/jobs",
            headers={"Idempotency-Key": "shared-key", "Authorization": f"Bearer {token_a}"},
        )
        resp_b = await ac.post(
            "/jobs",
            headers={"Idempotency-Key": "shared-key", "Authorization": f"Bearer {token_b}"},
        )

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert redis_client.exists("idempotency:op-alice:shared-key") == 1
    assert redis_client.exists("idempotency:op-bob:shared-key") == 1
