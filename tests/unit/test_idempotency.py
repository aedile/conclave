"""Unit tests for the Redis-backed idempotency middleware.

Tests cover duplicate detection, pass-through behaviour for new keys,
and correct handling of requests without idempotency keys.

CONSTITUTION Priority 3: TDD RED Phase
Task: P2-T2.1 — Module Bootstrapper, OTEL, Idempotency, Orphan Task Reaper
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient


def _build_app(redis_mock: MagicMock, ttl: int = 300) -> FastAPI:
    """Build a minimal FastAPI app wired with IdempotencyMiddleware.

    Args:
        redis_mock: A MagicMock substituting a real Redis client.
        ttl: Time-to-live in seconds for idempotency keys.

    Returns:
        A FastAPI application with idempotency middleware attached and
        a simple POST /items route for testing.
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


@pytest.mark.asyncio
async def test_duplicate_request_returns_409() -> None:
    """A repeated idempotency key on POST returns HTTP 409 with detail body.

    When a key has already been stored in Redis (exists=True), the middleware
    must short-circuit the request and return a 409 Conflict response with
    the idempotency key echoed in the body.
    """
    redis_mock = MagicMock()
    # First call: key does not exist. Second call: key exists.
    redis_mock.exists.side_effect = [0, 1]

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

    When Redis reports the key does not exist, the middleware must store
    it and forward the request, returning the handler's response.
    """
    redis_mock = MagicMock()
    redis_mock.exists.return_value = 0  # Key not seen before

    app = _build_app(redis_mock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/items", headers={"X-Idempotency-Key": "brand-new-key"})

    assert response.status_code == 201
    assert response.json() == {"created": True}
    redis_mock.setex.assert_called_once()


@pytest.mark.asyncio
async def test_missing_header_on_post_passes_through() -> None:
    """A POST without X-Idempotency-Key header passes through unchanged.

    Not all endpoints require idempotency; the middleware must not block
    requests that omit the header.
    """
    redis_mock = MagicMock()

    app = _build_app(redis_mock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/items")

    assert response.status_code == 201
    redis_mock.exists.assert_not_called()
    redis_mock.setex.assert_not_called()


@pytest.mark.asyncio
async def test_get_request_ignores_idempotency() -> None:
    """GET requests are never subject to idempotency checks.

    GET is a safe, idempotent HTTP method; storing keys for it would
    be incorrect behaviour. The middleware must ignore GET requests entirely.
    """
    redis_mock = MagicMock()
    redis_mock.exists.return_value = 1  # Would trigger 409 if checked

    app = _build_app(redis_mock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/items", headers={"X-Idempotency-Key": "some-key"})

    assert response.status_code == 200
    redis_mock.exists.assert_not_called()


@pytest.mark.asyncio
async def test_patch_request_enforces_idempotency() -> None:
    """PATCH with a duplicate key returns 409.

    PATCH is a mutating method; the middleware must enforce idempotency
    for PATCH in the same way it does for POST.
    """
    redis_mock = MagicMock()
    redis_mock.exists.side_effect = [0, 1]

    app = _build_app(redis_mock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Idempotency-Key": "patch-key-xyz"}
        await client.patch("/items/1", headers=headers)
        response = await client.patch("/items/1", headers=headers)

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_redis_key_format_uses_prefix() -> None:
    """The Redis key is stored with the idempotency: prefix.

    Namespacing keys prevents collisions with other Redis consumers
    sharing the same instance.
    """
    redis_mock = MagicMock()
    redis_mock.exists.return_value = 0

    app = _build_app(redis_mock, ttl=120)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/items", headers={"X-Idempotency-Key": "my-unique-key"})

    redis_mock.setex.assert_called_once_with("idempotency:my-unique-key", 120, "1")
