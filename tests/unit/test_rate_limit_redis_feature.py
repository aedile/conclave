"""Feature tests for Redis-backed rate limiting (T48.1).

Tests verify the Redis-backed implementation provides:
- AC1: Rate limit uses Redis INCR + EXPIRE for distributed counting
- AC3: /unseal brute-force protection is Redis-backed
- AC4: Per-IP and per-operator scoping preserved
- AC5: Rate limit headers (X-RateLimit-Remaining, Retry-After) correct
- AC6: Graceful degradation falls back to in-memory when Redis unavailable
- AC7: Existing rate limit behavior fully preserved

CONSTITUTION Priority 0: Security — brute-force and DoS protection
CONSTITUTION Priority 3: TDD
Task: T48.1 — Redis-Backed Rate Limiting
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import jwt as pyjwt
import pytest
import redis as redis_lib
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_redis_app(
    *,
    redis_client: Any = None,
    unseal_limit: int = 5,
    auth_limit: int = 10,
    general_limit: int = 60,
    download_limit: int = 10,
) -> Any:
    """Build a minimal FastAPI app with Redis-backed RateLimitGateMiddleware.

    Args:
        redis_client: Injected Redis client (or None to use default get_redis_client).
        unseal_limit: Requests per minute allowed on /unseal per IP.
        auth_limit: Requests per minute allowed on /auth/token per IP.
        general_limit: Requests per minute allowed on all other endpoints.
        download_limit: Requests per minute allowed on download endpoints.

    Returns:
        A FastAPI instance with RateLimitGateMiddleware registered.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware

    app = FastAPI()
    kwargs: dict[str, Any] = {
        "unseal_limit": unseal_limit,
        "auth_limit": auth_limit,
        "general_limit": general_limit,
        "download_limit": download_limit,
    }
    if redis_client is not None:
        kwargs["redis_client"] = redis_client
    app.add_middleware(RateLimitGateMiddleware, **kwargs)

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


def _make_mock_redis(*, count: int = 1) -> tuple[MagicMock, MagicMock]:
    """Build a mock Redis client that returns count on pipeline INCR.

    Args:
        count: The value returned by INCR (simulates Nth request in window).

    Returns:
        Tuple of (mock_redis, mock_pipeline).
    """
    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.return_value = [count, True]
    mock_redis.pipeline.return_value = mock_pipeline
    return mock_redis, mock_pipeline


# ---------------------------------------------------------------------------
# AC1: Redis INCR + EXPIRE called on every request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_incr_called_on_every_request() -> None:
    """Redis pipeline INCR must be called for every rate-limited request.

    Arrange: mock Redis returning count=1 (within limit).
    Act: make one request to /unseal.
    Assert: pipeline.incr() was called exactly once.
    """
    mock_redis, mock_pipeline = _make_mock_redis(count=1)
    app = _build_redis_app(redis_client=mock_redis, unseal_limit=5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/unseal", headers={"X-Forwarded-For": "10.0.0.1"})

    assert mock_pipeline.incr.called, "Redis INCR must be called on each request"


@pytest.mark.asyncio
async def test_redis_expire_called_on_every_request() -> None:
    """Redis pipeline EXPIRE must be called for every rate-limited request.

    This ensures the TTL is always set (even after restart) so keys never
    accumulate indefinitely.

    Arrange: mock Redis returning count=1.
    Act: make one request to /unseal.
    Assert: pipeline.expire() was called exactly once.
    """
    mock_redis, mock_pipeline = _make_mock_redis(count=1)
    app = _build_redis_app(redis_client=mock_redis, unseal_limit=5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/unseal", headers={"X-Forwarded-For": "10.0.0.1"})

    assert mock_pipeline.expire.called, "Redis EXPIRE must be called on each request"


# ---------------------------------------------------------------------------
# AC3: /unseal brute-force — Redis count drives 429
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_count_exceeding_limit_returns_429() -> None:
    """When Redis INCR count exceeds the limit, the request gets 429.

    Arrange: mock Redis returning count=6 (over the 5/min unseal limit).
    Act: POST /unseal.
    Assert: HTTP 429.
    """
    mock_redis, _ = _make_mock_redis(count=6)
    app = _build_redis_app(redis_client=mock_redis, unseal_limit=5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/unseal", headers={"X-Forwarded-For": "10.2.2.2"})

    assert response.status_code == 429, (
        f"INCR count exceeding limit must return 429; got {response.status_code}"
    )


@pytest.mark.asyncio
async def test_redis_count_at_limit_returns_200() -> None:
    """When Redis INCR count equals the limit, the request is allowed (200).

    The limit is inclusive: count <= limit is allowed.

    Arrange: mock Redis returning count=5 (exactly at 5/min unseal limit).
    Act: POST /unseal.
    Assert: HTTP 200.
    """
    mock_redis, _ = _make_mock_redis(count=5)
    app = _build_redis_app(redis_client=mock_redis, unseal_limit=5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/unseal", headers={"X-Forwarded-For": "10.3.3.3"})

    assert response.status_code == 200, (
        f"INCR count at limit must be allowed (200); got {response.status_code}"
    )


@pytest.mark.asyncio
async def test_redis_count_one_over_limit_returns_429() -> None:
    """When Redis INCR count is exactly one over the limit, the request gets 429.

    Arrange: mock Redis returning count=limit+1.
    Act: POST /unseal.
    Assert: HTTP 429.
    """
    mock_redis, _ = _make_mock_redis(count=3)
    app = _build_redis_app(redis_client=mock_redis, unseal_limit=2)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/unseal", headers={"X-Forwarded-For": "10.4.4.4"})

    assert response.status_code == 429


# ---------------------------------------------------------------------------
# AC4: Per-IP and per-operator scoping with Redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_key_includes_ip_for_unseal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Redis key for /unseal must encode the client IP for per-IP scoping.

    T66.3: With trusted_proxy_count=1, the XFF header is trusted and the
    IP it contains is used as the rate-limit key.  This test sets
    CONCLAVE_TRUSTED_PROXY_COUNT=1 to enable XFF extraction.

    Arrange: mock Redis; build app with trusted_proxy_count=1.
    Act: POST /unseal with a specific X-Forwarded-For.
    Assert: the Redis INCR key contains the IP address.
    """
    from synth_engine.shared.settings import get_settings

    monkeypatch.setenv("CONCLAVE_TRUSTED_PROXY_COUNT", "1")
    get_settings.cache_clear()

    mock_redis, mock_pipeline = _make_mock_redis(count=1)
    app = _build_redis_app(redis_client=mock_redis, unseal_limit=5)

    target_ip = "198.51.100.42"
    # With trusted_proxy_count=1, XFF must have 2 entries: "client, proxy".
    # The trusted proxy appends its own IP; the client IP is at index -2.
    xff_header = f"{target_ip}, 10.0.0.1"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/unseal", headers={"X-Forwarded-For": xff_header})

    key_arg = mock_pipeline.incr.call_args[0][0]
    assert target_ip in key_arg, (
        f"Redis key for /unseal must contain the client IP; key={key_arg!r}, ip={target_ip!r}"
    )

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_redis_key_includes_operator_sub_for_authenticated_endpoint() -> None:
    """The Redis key for authenticated endpoints must encode the operator sub claim.

    Arrange: mock Redis; build app with a JWT carrying sub='op-test'.
    Act: GET /jobs with valid JWT.
    Assert: the Redis INCR key contains the operator sub.
    """
    mock_redis, mock_pipeline = _make_mock_redis(count=1)
    app = _build_redis_app(redis_client=mock_redis, general_limit=60)

    secret = "test-secret-key-long-enough-32ch"  # pragma: allowlist secret
    now = int(time.time())
    token = pyjwt.encode(
        {"sub": "op-test-unique", "iat": now, "exp": now + 3600},
        secret,
        algorithm="HS256",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token}"})

    key_arg = mock_pipeline.incr.call_args[0][0]
    assert "op-test-unique" in key_arg, f"Redis key must contain operator sub; key={key_arg!r}"


# ---------------------------------------------------------------------------
# AC5: Rate limit headers still correct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_x_ratelimit_remaining_header_correct() -> None:
    """X-RateLimit-Remaining header must be included on allowed requests.

    The remaining count is: limit - current_count.

    Arrange: mock Redis returning count=3; limit=5.
    Act: POST /unseal.
    Assert: X-RateLimit-Remaining header is present and equals 2 (5 - 3).
    """
    mock_redis, _ = _make_mock_redis(count=3)
    app = _build_redis_app(redis_client=mock_redis, unseal_limit=5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/unseal", headers={"X-Forwarded-For": "10.5.5.5"})

    assert response.status_code == 200
    assert "x-ratelimit-remaining" in response.headers, (
        f"X-RateLimit-Remaining header must be present; headers: {dict(response.headers)}"
    )
    remaining = int(response.headers["x-ratelimit-remaining"])
    assert remaining == 2, f"Remaining must be limit(5) - count(3) = 2; got {remaining}"


@pytest.mark.asyncio
async def test_retry_after_header_present_on_429() -> None:
    """Retry-After header must be present on 429 responses.

    Arrange: mock Redis returning count exceeding limit.
    Act: POST /unseal.
    Assert: 429 with Retry-After header.
    """
    mock_redis, _ = _make_mock_redis(count=99)
    app = _build_redis_app(redis_client=mock_redis, unseal_limit=5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/unseal", headers={"X-Forwarded-For": "10.6.6.6"})

    assert response.status_code == 429
    assert "retry-after" in response.headers, (
        f"429 must include Retry-After header; headers: {dict(response.headers)}"
    )
    retry_after = int(response.headers["retry-after"])
    assert retry_after >= 0, f"Retry-After must be non-negative; got {retry_after}"


# ---------------------------------------------------------------------------
# AC6: Graceful degradation — in-memory fallback when Redis unavailable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graceful_degradation_request_passes_when_redis_down() -> None:
    """Requests must be allowed when Redis is unavailable (fail open).

    Arrange: Redis raises ConnectionError on every pipeline call.
    Act: make a request.
    Assert: HTTP 200 (not 500 or 429).
    """
    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.side_effect = redis_lib.ConnectionError("Redis down")
    mock_redis.pipeline.return_value = mock_pipeline
    # Also fail direct Redis calls so grace-period key reads/writes fail correctly.
    # Without this, mock_redis.get() returns a MagicMock whose float() == 1.0,
    # making the grace period look like it started at epoch+1s (elapsed >> grace).
    _conn_err = redis_lib.ConnectionError("Redis down")
    mock_redis.get.side_effect = _conn_err
    mock_redis.set.side_effect = _conn_err
    mock_redis.delete.side_effect = _conn_err

    app = _build_redis_app(redis_client=mock_redis, unseal_limit=5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/unseal", headers={"X-Forwarded-For": "10.7.7.7"})

    assert response.status_code == 200, (
        f"Redis down must fail open (200); got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# AC7: RFC 7807 response format preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_response_rfc7807_format_with_redis_backend() -> None:
    """429 response must conform to RFC 7807 Problem Details with Redis backend.

    Arrange: mock Redis returning count=99 (far over limit).
    Act: POST /unseal.
    Assert: RFC 7807 body with type, status, title, detail fields.
    """
    mock_redis, _ = _make_mock_redis(count=99)
    app = _build_redis_app(redis_client=mock_redis, unseal_limit=5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/unseal", headers={"X-Forwarded-For": "10.8.8.8"})

    assert response.status_code == 429
    body = response.json()
    assert "type" in body
    assert "status" in body
    assert "title" in body
    assert "detail" in body
    assert body["status"] == 429


# ---------------------------------------------------------------------------
# Feature: _redis_hit function contract
# ---------------------------------------------------------------------------


def test_redis_hit_returns_count_and_allowed_tuple() -> None:
    """_redis_hit must return (count, allowed) tuple.

    Args:
        count: The value from Redis INCR.
        allowed: True when count <= limit, False when count > limit.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit_backend import _redis_hit

    mock_redis, mock_pipeline = _make_mock_redis(count=3)

    count, allowed = _redis_hit(mock_redis, "5/minute", "ip:1.2.3.4")
    assert count == 3
    assert allowed == True


def test_redis_hit_returns_not_allowed_when_count_exceeds_limit() -> None:
    """_redis_hit must return allowed=False when INCR count exceeds limit.

    Arrange: Redis returns count=6 (over 5/minute limit).
    Act: call _redis_hit.
    Assert: allowed=False.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit_backend import _redis_hit

    mock_redis, mock_pipeline = _make_mock_redis(count=6)

    count, allowed = _redis_hit(mock_redis, "5/minute", "ip:1.2.3.4")
    assert count == 6
    assert allowed is False
