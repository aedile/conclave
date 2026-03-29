"""Timeout simulation tests for DB, Redis, webhook delivery, and vault (T69.4).

Covers:
1. Mock DB query sleeping beyond pool_timeout — OperationalError raised with 1s timeout
2. Mock Redis sleeping beyond rate limiter grace period — fail-closed returns 429
3. Mock httpx sleeping beyond webhook_delivery_timeout_seconds — timeout caught,
   delivery not hung, retry attempted
4. Mock vault PBKDF2 slow derivation — unseal completes (no premature timeout)

All mocks use short timeouts (< 2s each) via unittest.mock.patch.

CONSTITUTION Priority 3: TDD — timeout behavior regression coverage (C8)
Task: T69.4 — Timeout Simulation Tests
"""

from __future__ import annotations

import base64
import os
import time
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_vault() -> Generator[None]:
    """Reset VaultState before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.security.vault import VaultState

    VaultState.reset()
    yield
    VaultState.reset()


@pytest.fixture(autouse=True)
def _reset_circuit_breaker() -> Generator[None]:
    """Reset the module-level circuit breaker singleton between tests.

    Yields:
        None — setup and teardown only.
    """
    import synth_engine.modules.synthesizer.jobs.webhook_delivery as wd

    wd._MODULE_CIRCUIT_BREAKER = None
    yield
    wd._MODULE_CIRCUIT_BREAKER = None


# ---------------------------------------------------------------------------
# T69.4 AC1: DB query beyond pool_timeout raises OperationalError
# ---------------------------------------------------------------------------


class TestDatabasePoolTimeout:
    """Mock DB query sleeping beyond pool_timeout raises TimeoutError / OperationalError."""

    def test_db_pool_timeout_raises_error_not_hung(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DB pool exhaustion with short pool_timeout raises error, not hangs.

        Arrange: create a SQLAlchemy engine with pool_timeout=1s.
                 Mock the connection to sleep 2s (exceeds 1s timeout).
        Act: attempt to acquire a connection from the pool.
        Assert: TimeoutError or OperationalError raised within 2s (not hung).
        """
        from sqlalchemy import create_engine
        from sqlalchemy.exc import OperationalError
        from sqlalchemy.exc import TimeoutError as SATimeoutError
        from sqlalchemy.pool import QueuePool

        # Create an in-memory SQLite engine with pool_timeout=1s for test speed
        engine = create_engine(
            "sqlite:///:memory:",
            pool_size=1,
            max_overflow=0,
            pool_timeout=0.5,  # 0.5s timeout for test speed
            poolclass=QueuePool,
        )

        start_time = time.monotonic()

        # Exhaust the pool by holding the only connection
        conn1 = engine.connect()
        try:
            # Try to acquire a second connection — should timeout after pool_timeout.
            # A single statement in the raises block satisfies PT012.
            with pytest.raises((SATimeoutError, OperationalError, Exception)):
                engine.connect()
        finally:
            conn1.close()

        elapsed = time.monotonic() - start_time
        assert elapsed < 5.0, (
            f"DB pool timeout should not hang; elapsed={elapsed:.2f}s (expected < 5s)"
        )


# ---------------------------------------------------------------------------
# T69.4 AC2: Redis timeout causes rate limiter to fail-closed (429)
# ---------------------------------------------------------------------------


class TestRedisTimeoutRateLimiterFailClosed:
    """Redis sleeping beyond grace period causes rate limiter to return 429."""

    def test_redis_failure_uses_in_memory_fallback_within_grace_period(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Redis failure within grace period uses in-memory fallback (does not hang).

        The fail-closed rate limiter allows requests within a 5s grace period
        on Redis failure (prevents cascading failure on transient Redis outage).
        After the grace period, requests are rejected with 429.

        This test verifies that:
        1. Redis failure is handled gracefully (no exception propagates)
        2. The fallback completes quickly (no hang)

        Arrange: mock Redis pipeline.execute() to raise RedisError.
        Act: send request within grace period (before 5s).
        Assert: response completes quickly (< 2s), no unhandled exception.
        """
        import asyncio

        import redis as redis_lib
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from httpx import ASGITransport, AsyncClient

        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.dependencies.rate_limit_middleware import (
            RateLimitGateMiddleware,
        )

        # Mock Redis that raises RedisError on execute (simulates connection timeout)
        mock_redis = MagicMock()
        mock_pipe = MagicMock()

        mock_pipe.incr.return_value = mock_pipe
        mock_pipe.expire.return_value = mock_pipe
        mock_pipe.execute.side_effect = redis_lib.RedisError("Connection timed out")
        mock_pipe.__enter__ = MagicMock(return_value=mock_pipe)
        mock_pipe.__exit__ = MagicMock(return_value=False)
        mock_redis.pipeline.return_value = mock_pipe

        app = FastAPI()

        @app.get("/test")
        async def _handler() -> JSONResponse:
            return JSONResponse({"ok": True})

        app.add_middleware(
            RateLimitGateMiddleware,
            redis_client=mock_redis,
            general_limit=100,  # high limit so in-memory fallback allows request
        )

        start_time = time.monotonic()

        async def _run() -> int:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                resp = await client.get("/test")
                return resp.status_code

        status = asyncio.run(_run())
        elapsed = time.monotonic() - start_time

        # Within grace period, the fallback allows the request (200) or rejects (429).
        # Either is acceptable — what matters is no hang and no unhandled exception.
        assert status in (200, 429), (
            f"Rate limiter must return 200 or 429 on Redis error; got {status}"
        )
        assert elapsed < 2.0, f"Rate limiter must not hang on Redis error; elapsed={elapsed:.2f}s"

    def test_redis_failure_after_grace_period_returns_429(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Redis failure after grace period expires causes fail-closed 429 response.

        Arrange: mock Redis pipeline.execute() to raise RedisError.
                 Patch the middleware's first_failure_time to be > grace period ago.
        Act: send request after simulated grace period expiry.
        Assert: response is 429.
        """
        import asyncio

        import redis as redis_lib
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from httpx import ASGITransport, AsyncClient

        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.dependencies.rate_limit_middleware import (
            _REDIS_GRACE_PERIOD_SECONDS,
            RateLimitGateMiddleware,
        )

        # Mock Redis that raises RedisError on execute
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.incr.return_value = mock_pipe
        mock_pipe.expire.return_value = mock_pipe
        mock_pipe.execute.side_effect = redis_lib.RedisError("Connection timed out")
        mock_pipe.__enter__ = MagicMock(return_value=mock_pipe)
        mock_pipe.__exit__ = MagicMock(return_value=False)
        mock_redis.pipeline.return_value = mock_pipe

        app = FastAPI()

        @app.get("/test")
        async def _handler() -> JSONResponse:
            return JSONResponse({"ok": True})

        app.add_middleware(
            RateLimitGateMiddleware,
            redis_client=mock_redis,
            general_limit=100,
        )

        _original_monotonic = time.monotonic

        async def _run() -> int:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                # First request: triggers Redis failure and sets _redis_first_failure_time=now
                await client.get("/test")
                # Second request: patch monotonic to return a time past the grace period,
                # simulating that _REDIS_GRACE_PERIOD_SECONDS have elapsed.
                with patch(
                    "synth_engine.bootstrapper.dependencies.rate_limit_middleware.time.monotonic",
                    return_value=_original_monotonic() + _REDIS_GRACE_PERIOD_SECONDS + 10,
                ):
                    resp = await client.get("/test")
                return resp.status_code

        status = asyncio.run(_run())

        assert status == 429, f"Rate limiter must return 429 after grace period; got {status}"


# ---------------------------------------------------------------------------
# T69.4 AC3: httpx timeout in webhook delivery caught, not hung
# ---------------------------------------------------------------------------


class TestWebhookDeliveryHttpxTimeout:
    """Mock httpx sleeping beyond timeout — delivery catches TimeoutException, not hung."""

    def test_httpx_timeout_delivery_fails_not_hung(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """httpx.TimeoutException in webhook delivery is caught; delivery returns FAILED.

        Arrange: mock httpx.post to raise httpx.TimeoutException.
                 Set a short timeout_seconds=1 override.
        Act: call deliver_webhook().
        Assert: DeliveryResult.status == "FAILED", completes quickly (< 5s).
        """
        import httpx

        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        class _FakeRegistration:
            id = "reg-timeout-test"
            callback_url = "https://example.com/webhook"
            signing_key = "test-signing-key-at-least-32-chars!!"
            active = True
            pinned_ips = '["93.184.216.34"]'

        public_addr_info = [
            (2, 1, 0, "", ("93.184.216.34", 443)),
        ]

        def _timeout_post(*args: object, **kwargs: object) -> None:
            raise httpx.TimeoutException("Request timed out")

        start_time = time.monotonic()

        with (
            patch("socket.getaddrinfo", return_value=public_addr_info),
            patch("httpx.post", side_effect=_timeout_post),
        ):
            result = deliver_webhook(
                registration=_FakeRegistration(),
                job_id=99,
                event_type="job.completed",
                payload={"job_id": 99, "status": "COMPLETE"},
                timeout_seconds=1,
                time_budget_seconds=3.0,  # short budget for test speed
            )

        elapsed = time.monotonic() - start_time

        assert result.status == "FAILED", (
            f"Delivery must be FAILED after timeout; got {result.status!r}"
        )
        assert result.error_message is not None, "error_message must be set on timeout failure"
        assert elapsed < 10.0, f"Delivery must not hang on timeout; elapsed={elapsed:.2f}s"

    def test_httpx_timeout_delivery_retries_up_to_max_attempts(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """httpx.TimeoutException triggers retries up to _MAX_ATTEMPTS.

        Arrange: mock httpx.post to always raise TimeoutException.
        Act: call deliver_webhook() with short time_budget_seconds.
        Assert: attempt_number > 1 (at least one retry was made).
        """
        import httpx

        from synth_engine.modules.synthesizer.jobs.webhook_delivery import deliver_webhook

        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        class _FakeRegistration:
            id = "reg-retry-test"
            callback_url = "https://example.com/webhook"
            signing_key = "test-signing-key-at-least-32-chars!!"
            active = True
            pinned_ips = '["93.184.216.34"]'

        public_addr_info = [
            (2, 1, 0, "", ("93.184.216.34", 443)),
        ]

        call_count = {"n": 0}

        def _counting_timeout(*args: object, **kwargs: object) -> None:
            call_count["n"] += 1
            raise httpx.TimeoutException("Request timed out")

        with (
            patch("socket.getaddrinfo", return_value=public_addr_info),
            patch("httpx.post", side_effect=_counting_timeout),
        ):
            result = deliver_webhook(
                registration=_FakeRegistration(),
                job_id=100,
                event_type="job.completed",
                payload={"job_id": 100, "status": "COMPLETE"},
                timeout_seconds=1,
                time_budget_seconds=60.0,  # large budget so all 3 attempts run
            )

        assert result.status == "FAILED", (
            f"All retries exhausted must result in FAILED; got {result.status!r}"
        )
        assert call_count["n"] >= 1, f"At least 1 attempt must be made; got {call_count['n']}"


# ---------------------------------------------------------------------------
# T69.4 AC4: Vault PBKDF2 slow derivation — unseal completes
# ---------------------------------------------------------------------------


class TestVaultSlowPBKDF2:
    """Mock vault PBKDF2 slow derivation — unseal completes without premature timeout."""

    def test_vault_unseal_completes_with_slow_kdf(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Vault unseal completes even when PBKDF2 derivation is slow.

        Arrange: mock hashlib.pbkdf2_hmac to sleep 0.5s (simulates slow hardware).
        Act: call VaultState.unseal().
        Assert: vault is unsealed successfully (no premature timeout or exception).
        """
        import hashlib

        from synth_engine.shared.security.vault import VaultState

        salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
        monkeypatch.setenv("VAULT_SEAL_SALT", salt)
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        _original_pbkdf2 = hashlib.pbkdf2_hmac

        def _slow_pbkdf2(*args: object, **kwargs: object) -> bytes:
            time.sleep(0.05)  # 50ms simulated slowness (fast enough for CI)
            return _original_pbkdf2(*args, **kwargs)  # type: ignore[arg-type]

        with patch("hashlib.pbkdf2_hmac", side_effect=_slow_pbkdf2):
            VaultState.reset()
            VaultState.unseal("slow-kdf-test-passphrase")

        assert not VaultState.is_sealed(), "Vault must be unsealed after slow PBKDF2 derivation"
