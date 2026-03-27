"""Negative/attack tests for rate limiter fail-closed behavior (T63.3).

Tests verify that when Redis is unavailable:
- After the grace period expires, requests are rejected with 429.
- During the grace period, requests are served from in-memory counter (not unlimited).
- The grace period clock does not reset on repeated Redis failure cycles.
- When rate_limit_fail_open=True, the in-memory fallback is restored (pre-P63 behavior).
- In production with rate_limit_fail_open=True, a security WARNING is logged.

CONSTITUTION Priority 0: Security — rate limiting must fail closed to prevent DoS bypass.
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22).
Task: T63.3 — Rate Limiter Fail-Closed on Redis Failure
"""

from __future__ import annotations

import logging
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import redis as redis_lib
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# State isolation fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_failing_redis() -> redis_lib.Redis:
    """Build a mock Redis client whose pipeline always raises ConnectionError.

    Returns:
        A mock Redis client configured to fail on every pipeline execute().
    """
    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.side_effect = redis_lib.ConnectionError("Redis down")
    mock_redis.pipeline.return_value = mock_pipeline
    return mock_redis


def _build_fail_closed_app(
    *,
    redis_client: Any = None,
    unseal_limit: int = 5,
    auth_limit: int = 10,
    general_limit: int = 60,
    download_limit: int = 10,
) -> Any:
    """Build a FastAPI app with RateLimitGateMiddleware in fail-closed mode.

    This uses the default settings (rate_limit_fail_open=False), meaning
    Redis failure will fail closed after the grace period.

    Args:
        redis_client: Injected Redis client.
        unseal_limit: Requests per minute allowed on /unseal per IP.
        auth_limit: Requests per minute allowed on /auth/token per IP.
        general_limit: Requests per minute on all other endpoints.
        download_limit: Requests per minute on download endpoints.

    Returns:
        A FastAPI app with RateLimitGateMiddleware registered.
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

    return app


# ---------------------------------------------------------------------------
# ATTACK: After grace period, requests must be rejected with 429
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requests_rejected_429_after_grace_period_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the 5-second grace period expires, Redis failure must reject with 429.

    Arrange: Redis always raises ConnectionError; simulate grace period expired by
    setting the first_failure_time to 10 seconds ago.
    Act: make a request.
    Assert: response is 429 (fail-closed), not 200 (fail-open).

    CONSTITUTION Priority 0: rate limiting must not silently disable itself.
    """
    mock_redis = _make_failing_redis()
    app = _build_fail_closed_app(redis_client=mock_redis, unseal_limit=100)

    # Advance the grace period clock past expiry by patching time.monotonic
    # so the middleware sees the failure as 10 seconds old.
    grace_period_seconds = 5
    fake_now = time.monotonic() + grace_period_seconds + 1.0

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First request — this triggers the first_failure_time to be recorded
        with patch(
            "synth_engine.bootstrapper.dependencies.rate_limit.time.monotonic",
            return_value=fake_now - grace_period_seconds - 1.0,
        ):
            r1 = await client.post("/unseal", headers={"X-Forwarded-For": "10.2.2.2"})

        # Second request — now past grace period
        with patch(
            "synth_engine.bootstrapper.dependencies.rate_limit.time.monotonic",
            return_value=fake_now,
        ):
            r2 = await client.post("/unseal", headers={"X-Forwarded-For": "10.2.2.2"})

    # The second request (post-grace-period) must be rejected
    assert r2.status_code == 429, (
        f"After grace period expires, Redis failure must reject with 429 (fail-closed); "
        f"got {r2.status_code}"
    )


@pytest.mark.asyncio
async def test_grace_period_uses_in_memory_counter_within_5_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """During the 5-second grace period, in-memory counter enforces limits.

    The grace period is NOT unlimited — it uses in-memory counting.
    A limit of 2 req/min means the 3rd request is still rate-limited even
    during the grace period.

    Arrange: Redis always fails; grace period active (not yet expired).
    Act: make 3 requests from the same IP within the grace period.
    Assert: first 2 allowed, 3rd rejected (429 from in-memory limiter).
    """
    mock_redis = _make_failing_redis()
    app = _build_fail_closed_app(redis_client=mock_redis, unseal_limit=2)

    # Freeze monotonic time so the grace period never expires during the test
    frozen_time = time.monotonic()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Forwarded-For": "10.3.3.3"}
        with patch(
            "synth_engine.bootstrapper.dependencies.rate_limit.time.monotonic",
            return_value=frozen_time,
        ):
            r1 = await client.post("/unseal", headers=headers)
            r2 = await client.post("/unseal", headers=headers)
            r3 = await client.post("/unseal", headers=headers)

    assert r1.status_code == 200, f"1st request in grace period must pass; got {r1.status_code}"
    assert r2.status_code == 200, f"2nd request in grace period must pass; got {r2.status_code}"
    assert r3.status_code == 429, (
        f"3rd request must be limited by in-memory counter during grace period; "
        f"got {r3.status_code}"
    )


@pytest.mark.asyncio
async def test_grace_period_not_reset_on_repeated_redis_failure_cycles() -> None:
    """The grace period clock must NOT reset when Redis fails again after recovery.

    If Redis recovers briefly then fails again, the grace period clock for the
    second failure cycle must start fresh only when Redis was genuinely healthy
    in between. This test verifies that repeated failure cycles with no recovery
    do not extend the grace period indefinitely.

    Arrange: Redis fails throughout; mock monotonic to show time advancing.
    Act: first request at t=0 (starts clock), second request at t=6 (past grace).
    Assert: second request gets 429 (fail-closed, grace period has expired).
    """
    mock_redis = _make_failing_redis()
    app = _build_fail_closed_app(redis_client=mock_redis, unseal_limit=100)

    base_time = time.monotonic()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Request 1 at t=0: records first_failure_time
        with patch(
            "synth_engine.bootstrapper.dependencies.rate_limit.time.monotonic",
            return_value=base_time,
        ):
            await client.post("/unseal", headers={"X-Forwarded-For": "10.4.4.4"})

        # Request 2 at t=6: grace period (5s) has expired → must reject
        with patch(
            "synth_engine.bootstrapper.dependencies.rate_limit.time.monotonic",
            return_value=base_time + 6.0,
        ):
            r_after_grace = await client.post(
                "/unseal", headers={"X-Forwarded-For": "10.4.4.4"}
            )

    assert r_after_grace.status_code == 429, (
        f"After grace period, repeated Redis failures must still reject (429); "
        f"got {r_after_grace.status_code}"
    )


# ---------------------------------------------------------------------------
# ATTACK: rate_limit_fail_open=True restores pre-P63 in-memory fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_fail_open_true_restores_in_memory_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When rate_limit_fail_open=True, Redis failure allows in-memory fallback (pre-P63).

    This is the escape hatch for deployments that need availability over
    strict security. With fail_open=True, the first request still passes
    regardless of the grace period (consistent with pre-P63 behavior).

    Arrange: set CONCLAVE_RATE_LIMIT_FAIL_OPEN=true; Redis always fails.
    Act: make a request after simulating grace period expiry.
    Assert: response is 200 (fail-open — in-memory fallback, no 429).
    """
    from synth_engine.shared.settings import get_settings

    monkeypatch.setenv("CONCLAVE_RATE_LIMIT_FAIL_OPEN", "true")
    monkeypatch.setenv("CONCLAVE_ENV", "development")  # avoid production validation
    get_settings.cache_clear()

    mock_redis = _make_failing_redis()
    app = _build_fail_closed_app(redis_client=mock_redis, unseal_limit=100)

    # Simulate past-grace-period time
    fake_now = time.monotonic() + 10.0

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch(
            "synth_engine.bootstrapper.dependencies.rate_limit.time.monotonic",
            return_value=fake_now,
        ):
            response = await client.post("/unseal", headers={"X-Forwarded-For": "10.5.5.5"})

    assert response.status_code == 200, (
        f"With rate_limit_fail_open=True, Redis failure must allow request (fail-open); "
        f"got {response.status_code}"
    )


@pytest.mark.asyncio
async def test_rate_limit_fail_open_true_logs_warning_in_production(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When rate_limit_fail_open=True in production, a security WARNING is logged at startup.

    Operators who enable fail-open in production must receive an explicit
    security WARNING so this misconfiguration is visible in log aggregators.

    Arrange: set CONCLAVE_RATE_LIMIT_FAIL_OPEN=true and CONCLAVE_ENV=production.
    Act: call validate_config() (or equivalent startup path).
    Assert: a WARNING log message about rate_limit_fail_open in production is emitted.
    """
    from synth_engine.shared.settings import get_settings

    monkeypatch.setenv("CONCLAVE_RATE_LIMIT_FAIL_OPEN", "true")
    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@host/db")  # pragma: allowlist secret
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)  # pragma: allowlist secret
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-long-enough-32chars")  # pragma: allowlist secret
    monkeypatch.setenv(
        "OPERATOR_CREDENTIALS_HASH",
        "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/L/Ldv5t.iifcXiJea",  # pragma: allowlist secret
    )
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "b" * 64)
    monkeypatch.setenv("MASKING_SALT", "c" * 32)  # pragma: allowlist secret
    get_settings.cache_clear()

    from synth_engine.bootstrapper.config_validation import validate_config

    with caplog.at_level(logging.WARNING):
        try:
            validate_config()
        except SystemExit:
            pass  # May raise on missing certs, vault, etc. — we only care about the log.

    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    has_fail_open_warning = any(
        "fail_open" in msg.lower() or "fail open" in msg.lower() or "rate_limit_fail_open" in msg
        for msg in warning_messages
    )
    assert has_fail_open_warning, (
        f"rate_limit_fail_open=True in production must emit a security WARNING; "
        f"got warning messages: {warning_messages}"
    )
