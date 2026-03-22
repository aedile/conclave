"""Negative/attack tests for Redis-backed rate limiting (T48.1).

These tests verify that the Redis-backed implementation is hardened against:
- Redis key namespace collision (ratelimit: prefix isolation)
- X-Forwarded-For spoofing (trusted proxy logic preserved)
- Graceful degradation when Redis is unavailable mid-request
- Atomic INCR+EXPIRE (no keys without TTL)
- Redis connection pool reuse (no separate connection created)

CONSTITUTION Priority 0: Security — brute-force and DoS protection
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T48.1 — Redis-Backed Rate Limiting
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

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
        redis_client: Injected Redis client (or None for default).
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

    @app.get("/jobs")
    async def _jobs_route() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    return app


# ---------------------------------------------------------------------------
# ATTACK: Redis key namespace collision
# ---------------------------------------------------------------------------


def test_redis_key_uses_ratelimit_prefix() -> None:
    """Rate limit Redis keys MUST use the 'ratelimit:' prefix.

    This prevents collision with idempotency middleware keys which use
    the 'idempotency:' prefix, and Huey keys which use the 'huey.' prefix.

    Arrange: mock Redis client; build middleware.
    Act: inspect the key written by the Redis INCR call.
    Assert: every rate limit key starts with 'ratelimit:'.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware

    mock_redis = MagicMock(spec=redis_lib.Redis)
    # Simulate Redis pipeline: pipeline().incr().expire().execute() returning [1, True]
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.return_value = [1, True]
    mock_redis.pipeline.return_value = mock_pipeline

    middleware = RateLimitGateMiddleware(
        app=MagicMock(),
        redis_client=mock_redis,
        unseal_limit=5,
        auth_limit=10,
        general_limit=60,
        download_limit=10,
    )

    # Trigger a Redis INCR via _redis_hit for an /unseal request
    middleware._redis_hit("5/minute", "ip:10.0.0.1")

    # Verify the pipeline's incr call used a key starting with 'ratelimit:'
    assert mock_pipeline.incr.called, "Redis pipeline INCR must be called"
    key_arg = mock_pipeline.incr.call_args[0][0]
    assert key_arg.startswith("ratelimit:"), (
        f"Rate limit Redis key must start with 'ratelimit:'; got: {key_arg!r}"
    )


def test_redis_key_does_not_collide_with_idempotency_prefix() -> None:
    """Rate limit key prefix 'ratelimit:' must not overlap with 'idempotency:'.

    Ensures the two middleware namespaces are distinct.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware

    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.return_value = [1, True]
    mock_redis.pipeline.return_value = mock_pipeline

    middleware = RateLimitGateMiddleware(
        app=MagicMock(),
        redis_client=mock_redis,
        unseal_limit=5,
        auth_limit=10,
        general_limit=60,
        download_limit=10,
    )
    middleware._redis_hit("5/minute", "op:operator-123")

    key_arg = mock_pipeline.incr.call_args[0][0]
    assert not key_arg.startswith("idempotency:"), (
        f"Rate limit key must NOT start with 'idempotency:'; got: {key_arg!r}"
    )


# ---------------------------------------------------------------------------
# ATTACK: X-Forwarded-For spoofing — trusted proxy logic preserved
# ---------------------------------------------------------------------------


def test_xff_spoofing_uses_leftmost_ip_not_arbitrary_entry() -> None:
    """X-Forwarded-For rate limit key uses leftmost IP (real client), not rightmost.

    A spoofed X-Forwarded-For header with multiple IPs must still key on
    the leftmost IP (the real client in a standard proxy chain), not a
    spoofed entry injected by the client at another position.

    The leftmost-IP trust model is consistent with the existing implementation
    and must not change with the Redis backend upgrade.
    """
    from unittest.mock import MagicMock

    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_client_ip

    # Attacker tries to inject a trusted internal IP in the middle of the chain
    request = MagicMock()
    request.headers = {"X-Forwarded-For": "203.0.113.99, 10.0.0.1, 192.168.1.1"}
    request.client = MagicMock()
    request.client.host = "172.16.0.1"

    ip = _extract_client_ip(request)
    # Must return the LEFTMOST IP — the real client's address
    assert ip == "203.0.113.99", (
        f"IP extraction must use leftmost XFF entry (real client); got: {ip!r}"
    )


def test_xff_spoofing_empty_header_falls_back_to_client_host() -> None:
    """An empty X-Forwarded-For header must fall back to request.client.host.

    Prevents spoofing via an empty XFF header to get a different key bucket.
    """
    from unittest.mock import MagicMock

    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_client_ip

    request = MagicMock()
    request.headers = {"X-Forwarded-For": ""}
    request.client = MagicMock()
    request.client.host = "198.51.100.5"

    ip = _extract_client_ip(request)
    # Empty XFF should not be trusted; fallback to client.host
    assert ip == "198.51.100.5", f"Empty XFF must fall back to client.host; got: {ip!r}"


# ---------------------------------------------------------------------------
# ATTACK: Graceful degradation — Redis unavailable mid-request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graceful_degradation_redis_down_allows_request() -> None:
    """When Redis is unavailable, the request MUST be allowed (fail open) with WARNING log.

    Graceful degradation spec:
    - Redis connection failure during rate limit check must not block the request.
    - A WARNING must be logged.
    - The request passes through to the next handler.

    This ensures availability is preserved during Redis outages.
    """
    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.side_effect = redis_lib.ConnectionError("Redis down")
    mock_redis.pipeline.return_value = mock_pipeline

    app = _build_redis_app(redis_client=mock_redis, unseal_limit=2)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/unseal", headers={"X-Forwarded-For": "10.1.1.1"})

    assert response.status_code == 200, (
        f"Redis down must fail open (allow request); got {response.status_code}"
    )


@pytest.mark.asyncio
async def test_graceful_degradation_logs_warning_on_redis_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When Redis is unavailable, a WARNING must be emitted to the rate_limit logger.

    Arrange: Redis pipeline raises ConnectionError.
    Act: make a request.
    Assert: WARNING log entry is present in the rate_limit logger.
    """
    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.side_effect = redis_lib.ConnectionError("Redis unavailable")
    mock_redis.pipeline.return_value = mock_pipeline

    app = _build_redis_app(redis_client=mock_redis, unseal_limit=2)

    rate_limit_logger = "synth_engine.bootstrapper.dependencies.rate_limit"
    with caplog.at_level(logging.WARNING, logger=rate_limit_logger):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/unseal", headers={"X-Forwarded-For": "10.1.1.2"})

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    logged_messages = [r.message for r in warning_records]
    has_redis_warning = any(
        "redis" in msg.lower() or "fallback" in msg.lower() for msg in logged_messages
    )
    assert has_redis_warning, (
        f"WARNING about Redis degradation must be logged; got: {logged_messages}"
    )


@pytest.mark.asyncio
async def test_graceful_degradation_does_not_log_raw_ip(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Degradation warning log must NOT contain the raw client IP address.

    CONSTITUTION Priority 0: Do not emit PII (client IPs) in log output.
    """
    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.side_effect = redis_lib.ConnectionError("Redis down")
    mock_redis.pipeline.return_value = mock_pipeline

    raw_ip = "203.0.113.77"
    app = _build_redis_app(redis_client=mock_redis, unseal_limit=2)

    rate_limit_logger = "synth_engine.bootstrapper.dependencies.rate_limit"
    with caplog.at_level(logging.WARNING, logger=rate_limit_logger):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/unseal", headers={"X-Forwarded-For": raw_ip})

    assert raw_ip not in caplog.text, (
        f"Raw IP '{raw_ip}' must NOT appear in warning log (CONSTITUTION P0); "
        f"got: {caplog.text!r}"
    )


# ---------------------------------------------------------------------------
# ATTACK: Atomic INCR+EXPIRE — no keys without TTL
# ---------------------------------------------------------------------------


def test_redis_hit_uses_pipeline_for_atomic_incr_expire() -> None:
    """INCR and EXPIRE must be issued atomically via a pipeline.

    A non-atomic implementation (INCR then EXPIRE as separate commands) risks
    creating a key without TTL if the process crashes between the two commands,
    permanently blocking an IP until manual Redis intervention.

    Assert: both incr() and expire() are called on the SAME pipeline object,
    confirming they are batched in a single round-trip.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware

    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.return_value = [1, True]
    mock_redis.pipeline.return_value = mock_pipeline

    middleware = RateLimitGateMiddleware(
        app=MagicMock(),
        redis_client=mock_redis,
        unseal_limit=5,
        auth_limit=10,
        general_limit=60,
        download_limit=10,
    )
    middleware._redis_hit("5/minute", "ip:10.0.0.2")

    assert mock_pipeline.incr.called, "Pipeline INCR must be called"
    assert mock_pipeline.expire.called, "Pipeline EXPIRE must be called"
    assert mock_pipeline.execute.called, "Pipeline execute() must flush both commands atomically"


def test_redis_hit_expire_uses_correct_ttl_for_per_minute_limit() -> None:
    """EXPIRE TTL must be set to 60 seconds for a per-minute rate limit.

    This ensures the window resets after exactly one minute and keys
    do not persist indefinitely.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware

    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.return_value = [1, True]
    mock_redis.pipeline.return_value = mock_pipeline

    middleware = RateLimitGateMiddleware(
        app=MagicMock(),
        redis_client=mock_redis,
        unseal_limit=5,
        auth_limit=10,
        general_limit=60,
        download_limit=10,
    )
    middleware._redis_hit("5/minute", "ip:10.0.0.3")

    # expire(key, seconds) — seconds must be 60 for a per-minute window
    expire_args = mock_pipeline.expire.call_args[0]
    ttl = expire_args[1]
    assert ttl == 60, f"EXPIRE TTL must be 60s for per-minute limit; got: {ttl}"


# ---------------------------------------------------------------------------
# ATTACK: Redis connection pool reuse
# ---------------------------------------------------------------------------


def test_ratelimit_middleware_uses_injected_redis_client() -> None:
    """RateLimitGateMiddleware must use the injected Redis client, not create its own.

    This verifies the connection pool reuse requirement: the middleware must
    accept a redis_client constructor parameter and use it, rather than calling
    get_redis_client() internally (which would create a competing connection pool).
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware

    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.return_value = [3, True]
    mock_redis.pipeline.return_value = mock_pipeline

    middleware = RateLimitGateMiddleware(
        app=MagicMock(),
        redis_client=mock_redis,
        unseal_limit=5,
        auth_limit=10,
        general_limit=60,
        download_limit=10,
    )

    # The injected client must be stored and used
    assert middleware._redis is mock_redis, (
        "Middleware must store and use the injected redis_client, not create its own"
    )


def test_ratelimit_middleware_no_new_redis_import_called_when_client_injected() -> None:
    """When a redis_client is injected, get_redis_client() must NOT be called.

    Prevents the middleware from creating a second connection pool that bypasses
    the shared client from bootstrapper/dependencies/redis.py.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware

    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.return_value = [1, True]
    mock_redis.pipeline.return_value = mock_pipeline

    get_redis_patch = "synth_engine.bootstrapper.dependencies.rate_limit.get_redis_client"
    with patch(get_redis_patch) as mock_get_client:
        RateLimitGateMiddleware(
            app=MagicMock(),
            redis_client=mock_redis,
            unseal_limit=5,
            auth_limit=10,
            general_limit=60,
            download_limit=10,
        )
        # get_redis_client should NOT be called when client is explicitly injected
        mock_get_client.assert_not_called()


# ---------------------------------------------------------------------------
# ATTACK: Rate limit exceeded response does not leak Redis internals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_response_does_not_expose_redis_key_details() -> None:
    """A 429 response body must not expose Redis key internals or raw identifiers.

    CONSTITUTION Priority 0: no internal infrastructure details in error responses.
    """
    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    # Return count=999 (way over any limit)
    mock_pipeline.execute.return_value = [999, True]
    mock_redis.pipeline.return_value = mock_pipeline

    app = _build_redis_app(redis_client=mock_redis, unseal_limit=5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/unseal", headers={"X-Forwarded-For": "10.5.5.5"})

    assert response.status_code == 429
    body_text = response.text
    assert "ratelimit:" not in body_text, "Redis key prefix must NOT appear in 429 response body"
    assert "redis" not in body_text.lower(), "Redis internals must NOT appear in 429 response body"


# ---------------------------------------------------------------------------
# ATTACK: In-memory fallback preserves request count for the current request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_fallback_still_enforces_limits() -> None:
    """When Redis is unavailable, the in-memory fallback must still enforce limits.

    Graceful degradation must not disable rate limiting entirely — it must
    fall back to in-memory counting so per-worker limits still apply.

    Arrange: Redis always raises ConnectionError; in-memory fallback has limit=2.
    Act: make 3 requests from the same IP.
    Assert: third request is rate limited (429) by the in-memory fallback.
    """
    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.side_effect = redis_lib.ConnectionError("Redis down")
    mock_redis.pipeline.return_value = mock_pipeline

    app = _build_redis_app(redis_client=mock_redis, unseal_limit=2)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Forwarded-For": "10.9.9.9"}
        r1 = await client.post("/unseal", headers=headers)
        r2 = await client.post("/unseal", headers=headers)
        r3 = await client.post("/unseal", headers=headers)

    assert r1.status_code == 200, f"First request must pass; got {r1.status_code}"
    assert r2.status_code == 200, f"Second request must pass; got {r2.status_code}"
    assert r3.status_code == 429, (
        f"Third request must be rate limited by in-memory fallback; got {r3.status_code}"
    )
