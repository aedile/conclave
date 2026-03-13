"""Unit tests for the Redis-backed idempotency middleware.

Tests cover duplicate detection, pass-through behaviour for new keys,
correct handling of requests without idempotency keys, async Redis,
atomic SET NX EX, key length cap, Redis-down degradation, and
key release on handler exception (including delete-failure path).

CONSTITUTION Priority 3: TDD RED/GREEN Phase
Task: P2-T2.1 — Module Bootstrapper, OTEL, Idempotency, Orphan Task Reaper
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient


def _build_app(redis_mock: MagicMock, ttl: int = 300) -> FastAPI:
    """Build a minimal FastAPI app wired with IdempotencyMiddleware.

    Args:
        redis_mock: An AsyncMock substituting a real aioredis client.
        ttl: Time-to-live in seconds for idempotency keys.

    Returns:
        A FastAPI application with idempotency middleware attached and
        simple POST /items, GET /items, and PATCH /items/{item_id} routes.
    """
    from synth_engine.shared.middleware.idempotency import IdempotencyMiddleware

    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware, redis_client=redis_mock, ttl_seconds=ttl)

    @app.post("/items")
    async def create_item() -> JSONResponse:
        return JSONResponse({"created": True}, status_code=201)

    @app.get("/items")
    async def list_items() -> JSONResponse:
        return JSONResponse({"items": []}, status_code=200)

    @app.patch("/items/{item_id}")
    async def update_item(item_id: str) -> JSONResponse:
        return JSONResponse({"updated": item_id}, status_code=200)

    return app


def _build_error_app(redis_mock: MagicMock, ttl: int = 300) -> FastAPI:
    """Build a minimal FastAPI app whose POST route raises an exception.

    Used to verify that the idempotency key is released (deleted) when the
    downstream handler raises, allowing the caller to retry.

    Args:
        redis_mock: An AsyncMock substituting a real aioredis client.
        ttl: Time-to-live in seconds for idempotency keys.

    Returns:
        A FastAPI application where POST /items always raises RuntimeError.
    """
    from synth_engine.shared.middleware.idempotency import IdempotencyMiddleware

    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware, redis_client=redis_mock, ttl_seconds=ttl)

    @app.post("/items")
    async def create_item_fail() -> JSONResponse:
        raise RuntimeError("handler crashed")

    return app


@pytest.mark.asyncio
async def test_duplicate_request_returns_409() -> None:
    """A repeated idempotency key on POST returns HTTP 409 with detail body.

    When set() returns None (key already exists), the middleware must
    short-circuit and return 409 Conflict with the key echoed in the body.
    """
    redis_mock = AsyncMock()
    # First call: key is new (set returns True). Second call: key exists (set returns None).
    redis_mock.set.side_effect = [True, None]

    app = _build_app(redis_mock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Idempotency-Key": "test-key-abc"}
        await client.post("/items", headers=headers)
        response = await client.post("/items", headers=headers)

    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "Duplicate request"
    assert body["idempotency_key"] == "test-key-abc"


@pytest.mark.asyncio
async def test_first_request_passes_through() -> None:
    """A new idempotency key on POST passes through to the route handler.

    When Redis set() returns True (key was freshly set), the middleware must
    forward the request and return the handler's response.
    """
    redis_mock = AsyncMock()
    redis_mock.set.return_value = True  # Key is new

    app = _build_app(redis_mock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/items", headers={"X-Idempotency-Key": "brand-new-key"})

    assert response.status_code == 201
    assert response.json() == {"created": True}
    redis_mock.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_header_on_post_passes_through() -> None:
    """A POST without X-Idempotency-Key header passes through unchanged.

    Not all endpoints require idempotency; the middleware must not block
    requests that omit the header.
    """
    redis_mock = AsyncMock()

    app = _build_app(redis_mock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/items")

    assert response.status_code == 201
    redis_mock.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_request_ignores_idempotency() -> None:
    """GET requests are never subject to idempotency checks.

    GET is a safe, idempotent HTTP method; storing keys for it would
    be incorrect behaviour. The middleware must ignore GET requests entirely.
    """
    redis_mock = AsyncMock()

    app = _build_app(redis_mock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/items", headers={"X-Idempotency-Key": "some-key"})

    assert response.status_code == 200
    redis_mock.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_request_enforces_idempotency() -> None:
    """PATCH with a duplicate key returns 409.

    PATCH is a mutating method; the middleware must enforce idempotency
    for PATCH in the same way it does for POST.
    """
    redis_mock = AsyncMock()
    redis_mock.set.side_effect = [True, None]

    app = _build_app(redis_mock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Idempotency-Key": "patch-key-xyz"}
        await client.patch("/items/1", headers=headers)
        response = await client.patch("/items/1", headers=headers)

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_redis_key_uses_atomic_set_nx_ex() -> None:
    """The middleware uses a single atomic SET NX EX call (no TOCTOU).

    Namespacing keys prevents collisions with other Redis consumers sharing
    the same instance. The single atomic call eliminates the check-then-act
    race condition.
    """
    redis_mock = AsyncMock()
    redis_mock.set.return_value = True  # Key is new

    app = _build_app(redis_mock, ttl=120)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/items", headers={"X-Idempotency-Key": "my-unique-key"})

    redis_mock.set.assert_awaited_once_with("idempotency:my-unique-key", "1", nx=True, ex=120)


@pytest.mark.asyncio
async def test_key_too_long_returns_400() -> None:
    """An idempotency key exceeding 128 characters is rejected with HTTP 400.

    Oversized keys could be used to bloat Redis memory; the middleware
    must validate key length and reject before touching Redis.
    """
    redis_mock = AsyncMock()

    app = _build_app(redis_mock)
    long_key = "x" * 129  # One character over the 128-char cap
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/items", headers={"X-Idempotency-Key": long_key})

    assert response.status_code == 400
    assert response.json()["detail"] == "Idempotency key too long (max 128 characters)"
    redis_mock.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_key_exactly_128_chars_is_accepted() -> None:
    """An idempotency key of exactly 128 characters is not rejected.

    The length cap is exclusive of the boundary (> 128 triggers 400).
    """
    redis_mock = AsyncMock()
    redis_mock.set.return_value = True

    app = _build_app(redis_mock)
    boundary_key = "a" * 128
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/items", headers={"X-Idempotency-Key": boundary_key})

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_redis_down_passes_through() -> None:
    """When Redis raises RedisError, the request is passed through (degraded mode).

    The middleware must not block requests when Redis is unavailable;
    it logs a warning and forwards to the downstream handler.
    """
    import redis.exceptions

    redis_mock = AsyncMock()
    redis_mock.set.side_effect = redis.exceptions.RedisError("connection refused")

    app = _build_app(redis_mock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/items", headers={"X-Idempotency-Key": "some-key"})

    # Must pass through to handler — not block the request
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_key_released_when_handler_raises() -> None:
    """The idempotency key is deleted from Redis when the downstream handler raises.

    The middleware claims the slot atomically (SET NX), but if the handler
    crashes it must delete the key so the caller can retry with the same
    idempotency key.
    """
    redis_mock = AsyncMock()
    redis_mock.set.return_value = True  # Key is new, slot claimed

    app = _build_error_app(redis_mock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="handler crashed"):
            await client.post("/items", headers={"X-Idempotency-Key": "retry-key"})

    # The slot was claimed (set was called), then deleted to allow retry
    redis_mock.set.assert_awaited_once()
    redis_mock.delete.assert_awaited_once_with("idempotency:retry-key")


@pytest.mark.asyncio
async def test_key_release_failure_after_handler_exception_does_not_hide_error() -> None:
    """When both handler and delete() raise, the original exception propagates.

    If the downstream handler raises AND Redis delete() also raises, the
    middleware must not suppress the original exception — it logs a warning
    about the delete failure and re-raises the original error.
    """
    import redis.exceptions

    redis_mock = AsyncMock()
    redis_mock.set.return_value = True  # Key is new, slot claimed
    redis_mock.delete.side_effect = redis.exceptions.RedisError("delete failed")

    app = _build_error_app(redis_mock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="handler crashed"):
            await client.post("/items", headers={"X-Idempotency-Key": "double-fail-key"})

    redis_mock.delete.assert_awaited_once()
