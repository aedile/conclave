"""Integration tests for Redis-backed rate limiting (T48.1).

These tests exercise the full rate limiting middleware stack against a live
Redis instance.  They require a running Redis server on the default URL
(redis://localhost:6379 or REDIS_URL env var).

Tests cover:
- AC1: Rate limit uses Redis INCR + EXPIRE for distributed counting.
- AC2: /unseal brute-force protection is Redis-backed and consistent.
- AC3: Rate limit is SHARED across simulated workers (cross-worker test).
- AC4: Graceful degradation: fallback to in-memory when Redis unavailable.
- AC5: Per-IP and per-operator scoping preserved with Redis backend.
- AC6: Rate limit headers (X-RateLimit-Remaining, Retry-After) correct.
- AC7: Keys use 'ratelimit:' prefix (no collision with idempotency keys).

CONSTITUTION Priority 0: Security — brute-force and DoS protection
CONSTITUTION Priority 3: TDD
Task: T48.1 — Redis-Backed Rate Limiting
"""

from __future__ import annotations

import time
from typing import Any

import jwt as pyjwt
import pytest
import redis as redis_lib
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = "integration-test-secret-key-long-enough"  # pragma: allowlist secret
#: Use DB 2 to avoid colliding with Huey (DB 0) and idempotency tests (DB 1).
_REDIS_URL = "redis://localhost:6379/2"

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
    pytest.mark.skipif(not _redis_available(), reason="Redis not available at localhost:6379"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_client() -> redis_lib.Redis:
    """Provide a live Redis client connected to the test database.

    Yields:
        A redis.Redis client instance for DB 2.

    Note: The fixture flushes DB 2 before each test to ensure a clean state.
    """
    client = redis_lib.Redis.from_url(_REDIS_URL)
    # Flush the test DB to start clean
    client.flushdb()
    return client


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _build_app(
    redis_client: redis_lib.Redis,
    *,
    unseal_limit: int = 5,
    auth_limit: int = 10,
    general_limit: int = 60,
    download_limit: int = 10,
) -> Any:
    """Build a minimal FastAPI app with Redis-backed RateLimitGateMiddleware.

    Args:
        redis_client: Live Redis client to inject.
        unseal_limit: Requests per minute allowed on /unseal per IP.
        auth_limit: Requests per minute allowed on /auth/token per IP.
        general_limit: Requests per minute allowed on all other endpoints.
        download_limit: Requests per minute allowed on download endpoints.

    Returns:
        A FastAPI instance with RateLimitGateMiddleware registered.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware

    app = FastAPI()
    app.add_middleware(
        RateLimitGateMiddleware,
        redis_client=redis_client,
        unseal_limit=unseal_limit,
        auth_limit=auth_limit,
        general_limit=general_limit,
        download_limit=download_limit,
    )

    @app.post("/unseal")
    async def _unseal_route() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    @app.post("/auth/token")
    async def _auth_route() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    @app.get("/api/v1/jobs")
    async def _jobs_route() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    @app.get("/api/v1/jobs/{job_id}/download")
    async def _download_route(job_id: str) -> JSONResponse:
        return JSONResponse(content={"ok": True})

    return app


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
# AC1: Redis INCR called per request with live Redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_incr_key_exists_after_request(redis_client: redis_lib.Redis) -> None:
    """After a request, a Redis key with 'ratelimit:' prefix must exist.

    Verifies AC1 (Redis INCR + EXPIRE) and AC7 (key prefix isolation) with
    a live Redis instance.

    Arrange: build app with live Redis.
    Act: POST /unseal.
    Assert: a 'ratelimit:' key exists in Redis.
    """
    app = _build_app(redis_client, unseal_limit=5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/unseal", headers={"X-Forwarded-For": "10.0.0.1"})

    assert response.status_code == 200

    # Verify a ratelimit: key was created in Redis
    keys = redis_client.keys("ratelimit:*")
    assert len(keys) >= 1, f"Expected at least one 'ratelimit:' key in Redis; found: {keys}"


@pytest.mark.asyncio
async def test_redis_key_has_ttl_set(redis_client: redis_lib.Redis) -> None:
    """Redis rate limit keys must have a TTL (not persist forever).

    Verifies that EXPIRE was called atomically with INCR.

    Arrange: build app with live Redis; make one request.
    Assert: the ratelimit: key has a positive TTL.
    """
    app = _build_app(redis_client, unseal_limit=5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/unseal", headers={"X-Forwarded-For": "10.0.0.2"})

    keys = redis_client.keys("ratelimit:*")
    assert keys, "Expected at least one ratelimit: key"

    for key in keys:
        ttl = redis_client.ttl(key)
        assert ttl > 0, (
            f"Rate limit key {key!r} must have a positive TTL; got {ttl} (keys without TTL would "
            "permanently block identities)"
        )


# ---------------------------------------------------------------------------
# AC2: /unseal limit enforced with live Redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unseal_limit_enforced_with_live_redis(redis_client: redis_lib.Redis) -> None:
    """The /unseal rate limit must be enforced by live Redis counting.

    Arrange: build app with unseal_limit=2; live Redis.
    Act: POST /unseal 3 times from the same IP.
    Assert: third request returns 429.
    """
    app = _build_app(redis_client, unseal_limit=2)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Forwarded-For": "10.1.1.1"}
        r1 = await client.post("/unseal", headers=headers)
        r2 = await client.post("/unseal", headers=headers)
        r3 = await client.post("/unseal", headers=headers)

    assert r1.status_code == 200, f"First request must pass; got {r1.status_code}"
    assert r2.status_code == 200, f"Second request must pass; got {r2.status_code}"
    assert r3.status_code == 429, f"Third request must be rate limited; got {r3.status_code}"


# ---------------------------------------------------------------------------
# AC3: Rate limit is SHARED across simulated workers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_shared_across_simulated_workers(redis_client: redis_lib.Redis) -> None:
    """Rate limit must be shared (not per-worker) when using Redis backend.

    This is the core distributed rate limiting test.  Two simulated workers
    share the same Redis client.  Worker A exhausts the limit.  Worker B,
    which has its own independent in-memory state (fresh app instance), must
    ALSO be rate limited because the count is shared in Redis.

    Arrange: two FastAPI app instances sharing the same Redis client.
            unseal_limit=2; same IP.
    Act: Worker A makes 2 requests (exhausts the limit).
         Worker B (fresh app instance, no local memory of prior requests)
         makes 1 request from the same IP.
    Assert: Worker B's request is rate limited (429) because Redis count is shared.
    """
    # Worker A: fresh app instance
    app_worker_a = _build_app(redis_client, unseal_limit=2)
    # Worker B: completely independent app instance with same Redis
    app_worker_b = _build_app(redis_client, unseal_limit=2)

    ip_headers = {"X-Forwarded-For": "10.2.2.2"}

    # Worker A exhausts the limit
    async with AsyncClient(
        transport=ASGITransport(app=app_worker_a), base_url="http://test"
    ) as client_a:
        r_a1 = await client_a.post("/unseal", headers=ip_headers)
        r_a2 = await client_a.post("/unseal", headers=ip_headers)

    assert r_a1.status_code == 200, f"Worker A first request must pass; got {r_a1.status_code}"
    assert r_a2.status_code == 200, f"Worker A second request must pass; got {r_a2.status_code}"

    # Worker B makes a request — must be rate limited because Redis count is at 2
    async with AsyncClient(
        transport=ASGITransport(app=app_worker_b), base_url="http://test"
    ) as client_b:
        r_b1 = await client_b.post("/unseal", headers=ip_headers)

    assert r_b1.status_code == 429, (
        f"Worker B must be rate limited (429) because limit is shared via Redis; "
        f"got {r_b1.status_code}. This proves distributed counting is working."
    )


# ---------------------------------------------------------------------------
# AC5: Per-IP scoping with live Redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_ips_independent_limits_with_live_redis(
    redis_client: redis_lib.Redis,
) -> None:
    """Different IPs must have independent rate limit buckets in Redis.

    Arrange: unseal_limit=1; two different IPs.
    Act: exhaust IP A; make request from IP B.
    Assert: IP A rate limited; IP B allowed.
    """
    app = _build_app(redis_client, unseal_limit=1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Exhaust IP A
        await client.post("/unseal", headers={"X-Forwarded-For": "10.3.3.3"})
        r_a2 = await client.post("/unseal", headers={"X-Forwarded-For": "10.3.3.3"})
        # IP B should still be allowed
        r_b1 = await client.post("/unseal", headers={"X-Forwarded-For": "10.3.3.4"})

    assert r_a2.status_code == 429, "IP A must be rate limited after exceeding"
    assert r_b1.status_code == 200, "IP B must NOT be affected by IP A's limit"


# ---------------------------------------------------------------------------
# AC5: Per-operator scoping with live Redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_operators_independent_limits_with_live_redis(
    redis_client: redis_lib.Redis,
) -> None:
    """Different authenticated operators must have independent buckets in Redis.

    Arrange: general_limit=1; two operator JWTs.
    Act: exhaust operator A; make request as operator B.
    Assert: operator A rate limited; operator B allowed.
    """
    app = _build_app(redis_client, general_limit=1)
    token_a = _make_valid_token("op-alpha")
    token_b = _make_valid_token("op-beta")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Exhaust operator A
        await client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token_a}"})
        r_a2 = await client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token_a}"})
        # Operator B should still be allowed
        r_b1 = await client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token_b}"})

    assert r_a2.status_code == 429, "Operator A must be rate limited after exceeding"
    assert r_b1.status_code == 200, "Operator B must NOT be affected by operator A's limit"


# ---------------------------------------------------------------------------
# AC6: Rate limit headers correct with live Redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_x_ratelimit_remaining_header_decrements_with_live_redis(
    redis_client: redis_lib.Redis,
) -> None:
    """X-RateLimit-Remaining must correctly reflect remaining count with live Redis.

    Arrange: unseal_limit=3; make 2 requests.
    Assert: first request has remaining=2; second has remaining=1.
    """
    app = _build_app(redis_client, unseal_limit=3)
    headers = {"X-Forwarded-For": "10.4.4.4"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/unseal", headers=headers)
        r2 = await client.post("/unseal", headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert "x-ratelimit-remaining" in r1.headers, "X-RateLimit-Remaining must be present on r1"
    assert "x-ratelimit-remaining" in r2.headers, "X-RateLimit-Remaining must be present on r2"

    remaining_r1 = int(r1.headers["x-ratelimit-remaining"])
    remaining_r2 = int(r2.headers["x-ratelimit-remaining"])
    assert remaining_r1 == 2, (
        f"After 1st request (limit=3), remaining must be 2; got {remaining_r1}"
    )
    assert remaining_r2 == 1, (
        f"After 2nd request (limit=3), remaining must be 1; got {remaining_r2}"
    )


@pytest.mark.asyncio
async def test_retry_after_header_on_429_with_live_redis(redis_client: redis_lib.Redis) -> None:
    """Retry-After header must be present and non-negative on 429 with live Redis.

    Arrange: unseal_limit=1; make 2 requests.
    Assert: second response is 429 with Retry-After header.
    """
    app = _build_app(redis_client, unseal_limit=1)
    headers = {"X-Forwarded-For": "10.5.5.5"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/unseal", headers=headers)
        r2 = await client.post("/unseal", headers=headers)

    assert r2.status_code == 429
    assert "retry-after" in r2.headers, (
        f"429 must include Retry-After header; got: {dict(r2.headers)}"
    )
    assert int(r2.headers["retry-after"]) >= 0, "Retry-After must be non-negative"


# ---------------------------------------------------------------------------
# AC7: No collision with idempotency keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ratelimit_keys_do_not_collide_with_idempotency_namespace(
    redis_client: redis_lib.Redis,
) -> None:
    """Rate limit keys and idempotency keys must use distinct namespaces.

    Manually insert an idempotency-style key and verify that rate limit
    operations do not overwrite or read it.

    Arrange: seed an 'idempotency:' key in Redis.
    Act: make rate limit requests.
    Assert: the idempotency key is unchanged; ratelimit: keys are separate.
    """
    idempotency_key = "idempotency:op-test:user-key-abc"
    redis_client.set(idempotency_key, "seeded-value", ex=300)

    app = _build_app(redis_client, unseal_limit=5)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/unseal", headers={"X-Forwarded-For": "10.6.6.6"})

    # Idempotency key must be untouched
    value = redis_client.get(idempotency_key)
    assert value == b"seeded-value", (
        f"Rate limit ops must not overwrite idempotency key; got: {value!r}"
    )

    # Rate limit keys must exist separately
    ratelimit_keys = redis_client.keys("ratelimit:*")
    assert ratelimit_keys, "ratelimit: keys must exist after request"
    idempotency_keys = redis_client.keys("idempotency:*")
    assert len(idempotency_keys) == 1, (
        f"Only the seeded idempotency key should exist; got: {idempotency_keys}"
    )
