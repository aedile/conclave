"""Concurrent load tests for masking, vault, and rate limiter (T69.3).

Covers:
1. 10 threads masking the same (value, salt) 100 times each — all 1000 results identical
   (validates T68.1 thread-local Faker fix)
2. 10 threads attempting concurrent vault unseal — exactly one succeeds, others
   raise VaultAlreadyUnsealedError
3. 50 concurrent requests to a rate-limited endpoint — rate_limit succeed, rest 429
4. Database connection pool exhaustion — _POOL_SIZE + _MAX_OVERFLOW concurrent
   queries succeed, additional queries wait or fail gracefully

CONSTITUTION Priority 3: TDD — test coverage for concurrency regression (C8)
Task: T69.3 — Concurrent Load Tests
"""

from __future__ import annotations

import base64
import os
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from unittest.mock import MagicMock

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


# ---------------------------------------------------------------------------
# T69.3 AC1: Masking determinism under thread contention
# ---------------------------------------------------------------------------


class TestMaskingDeterminismUnderThreadContention:
    """Masking determinism with 10 threads masking the same (value, salt) 100x each."""

    def test_10_threads_masking_same_value_salt_1000_results_identical(self) -> None:
        """10 threads mask the same (value, salt) 100 times each; all 1000 results identical.

        Validates T68.1: thread-local Faker seeding ensures that concurrent
        calls with the same inputs always produce the same output.

        Arrange: 10 threads, each calling mask_value('alice', 'users.email') 100 times.
        Act: collect all 1000 results via ThreadPoolExecutor.
        Assert: all 1000 results are identical (deterministic masking).
        """
        from faker import Faker

        from synth_engine.modules.masking.deterministic import mask_value

        def _mask_fn(faker: Faker) -> str:
            return faker.email()

        def _do_mask(_: int) -> str:
            return mask_value("alice@test.com", "users.email", _mask_fn)

        results: list[str] = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_do_mask, i) for i in range(1000)]
            for fut in as_completed(futures):
                results.append(fut.result())

        unique_results = set(results)
        assert len(unique_results) == 1, (
            f"All 1000 masking results must be identical; "
            f"got {len(unique_results)} unique values: {unique_results}"
        )
        assert len(results) == 1000, f"Expected exactly 1000 results; got {len(results)}"

    def test_masking_different_values_produces_different_outputs(self) -> None:
        """Sanity check: different values produce different masked outputs.

        Ensures the hash-seeding is not constant (i.e., the determinism is
        input-dependent, not just producing a fixed output).

        Arrange: 2 threads masking 'alice' and 'bob' with same salt.
        Act: collect results.
        Assert: results for 'alice' != results for 'bob'.
        """
        from faker import Faker

        from synth_engine.modules.masking.deterministic import mask_value

        def _mask_fn(faker: Faker) -> str:
            return faker.email()

        result_alice = mask_value("alice@test.com", "users.email", _mask_fn)
        result_bob = mask_value("bob@test.com", "users.email", _mask_fn)

        assert result_alice != result_bob, (
            "Different input values must produce different masked outputs; "
            "same output indicates seed is constant (bug)"
        )


# ---------------------------------------------------------------------------
# T69.3 AC2: Vault unseal race condition
# ---------------------------------------------------------------------------


class TestVaultUnsealRaceCondition:
    """Concurrent vault unseal: exactly one thread succeeds, others raise the error."""

    def test_10_threads_concurrent_unseal_exactly_one_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """10 threads race to unseal the vault; exactly one succeeds.

        Arrange: vault is sealed. 10 threads all call VaultState.unseal() concurrently.
        Act: collect results (successes and exceptions).
        Assert: exactly 1 success, exactly 9 VaultAlreadyUnsealedError exceptions.
        """
        from synth_engine.shared.security.vault import VaultAlreadyUnsealedError, VaultState

        # Provide VAULT_SEAL_SALT for vault operations
        salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
        monkeypatch.setenv("VAULT_SEAL_SALT", salt)
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        VaultState.reset()

        successes = []
        errors: list[Exception] = []
        lock_for_lists: Any = __import__("threading").Lock()

        def _try_unseal() -> None:
            try:
                VaultState.unseal("concurrent-test-passphrase")
                with lock_for_lists:
                    successes.append(True)
            except VaultAlreadyUnsealedError as exc:
                with lock_for_lists:
                    errors.append(exc)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_try_unseal) for _ in range(10)]
            for fut in as_completed(futures):
                # Re-raise any unexpected exception that escaped the try/except
                fut.result()

        assert len(successes) == 1, f"Exactly 1 unseal must succeed; got {len(successes)} successes"
        assert len(errors) == 9, (
            f"Exactly 9 VaultAlreadyUnsealedError must be raised; got {len(errors)} errors"
        )
        for err in errors:
            assert isinstance(err, VaultAlreadyUnsealedError), (
                f"All errors must be VaultAlreadyUnsealedError; got {type(err)!r}"
            )

    def test_vault_sealed_after_reset_can_be_unsealed_again(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Vault can be unsealed after a reset — confirms reset() works correctly.

        Arrange: unseal vault, then reset.
        Act: unseal again.
        Assert: second unseal succeeds without error.
        """
        from synth_engine.shared.security.vault import VaultState

        salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
        monkeypatch.setenv("VAULT_SEAL_SALT", salt)
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        VaultState.reset()
        VaultState.unseal("first-unseal")

        VaultState.reset()  # reset seals the vault again
        VaultState.unseal("second-unseal")  # must not raise

        assert not VaultState.is_sealed(), "Vault must be unsealed after second unseal"


# ---------------------------------------------------------------------------
# T69.3 AC3: Rate limiter accuracy under burst traffic
# ---------------------------------------------------------------------------


class TestRateLimiterUnderBurstTraffic:
    """50 concurrent requests to rate-limited endpoint — rate_limit succeed, rest 429."""

    def test_50_concurrent_requests_enforces_rate_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """50 concurrent requests: at most `rate_limit` succeed, remainder get 429.

        Uses the RateLimitGateMiddleware with a pipeline-counting mock Redis backend
        (replicates the pattern from test_rate_limiting.py) and asyncio.gather for
        concurrent in-process request simulation.

        Arrange: rate_limit_general_per_minute=10, counting mock Redis pipeline.
        Act: send 50 concurrent requests via asyncio.gather.
        Assert: at most 10 responses are 200; at least 40 are 429.
        """
        import asyncio
        import threading
        from unittest.mock import MagicMock

        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from httpx import ASGITransport, AsyncClient

        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.dependencies.rate_limit_middleware import (
            RateLimitGateMiddleware,
        )

        # Build a pipeline-based counting mock Redis (matches existing test pattern)
        _lock = threading.Lock()
        _counters: dict[str, int] = {}

        mock_redis = MagicMock()

        def _make_pipeline() -> MagicMock:
            _pending_key: list[str] = []

            mock_pipe = MagicMock()

            def _incr(key: str) -> MagicMock:
                _pending_key.clear()
                _pending_key.append(key)
                return mock_pipe

            def _expire(key: str, seconds: int) -> MagicMock:
                return mock_pipe

            def _execute() -> list[Any]:
                key = _pending_key[0] if _pending_key else "__unknown__"
                with _lock:
                    _counters[key] = _counters.get(key, 0) + 1
                    count = _counters[key]
                return [count, True]

            mock_pipe.incr.side_effect = _incr
            mock_pipe.expire.side_effect = _expire
            mock_pipe.execute.side_effect = _execute
            mock_pipe.__enter__ = MagicMock(return_value=mock_pipe)
            mock_pipe.__exit__ = MagicMock(return_value=False)
            return mock_pipe

        mock_redis.pipeline.side_effect = lambda: _make_pipeline()

        app = FastAPI()

        @app.get("/test-rate")
        async def _handler() -> JSONResponse:
            return JSONResponse({"ok": True})

        app.add_middleware(
            RateLimitGateMiddleware,
            redis_client=mock_redis,
            general_limit=10,  # explicitly set to 10 for this test
        )

        async def _run() -> list[int]:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                tasks = [client.get("/test-rate") for _ in range(50)]
                responses = await asyncio.gather(*tasks)
                return [r.status_code for r in responses]

        status_codes = asyncio.run(_run())

        count_200 = status_codes.count(200)
        count_429 = status_codes.count(429)

        assert count_200 <= 10, (
            f"At most 10 requests should succeed with rate_limit=10; got {count_200} successes"
        )
        assert count_429 >= 40, (
            f"At least 40 requests should get 429 with rate_limit=10; got {count_429} rejections"
        )
        assert count_200 + count_429 == 50, (
            f"Total responses must be 50; got {count_200 + count_429}"
        )


# ---------------------------------------------------------------------------
# T69.3 AC4: Database connection pool exhaustion
# ---------------------------------------------------------------------------


class TestDatabaseConnectionPoolExhaustion:
    """DB pool exhaustion: production-like pool constants, 4th connection waits or fails."""

    def test_pool_size_plus_overflow_connections_succeed_fourth_waits_or_raises(
        self,
    ) -> None:
        """3 concurrent connections succeed (pool_size=2 + max_overflow=1); 4th waits or raises.

        Uses production-like constants: pool_size=2, max_overflow=1 to verify
        that the pool does not silently discard connections beyond capacity.

        Arrange: SQLite engine with pool_size=2, max_overflow=1, pool_timeout=0.1.
        Act: acquire 3 connections concurrently (all succeed), then attempt a 4th.
        Assert: first 3 succeed; 4th either completes (if a slot freed) or raises
            sqlalchemy.exc.TimeoutError after pool_timeout.
        """
        import threading

        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import TimeoutError as SATimeoutError
        from sqlalchemy.pool import QueuePool

        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=QueuePool,
            pool_size=2,
            max_overflow=1,
            pool_timeout=0.1,  # very short timeout so the test does not block
            connect_args={"check_same_thread": False},
        )

        barrier = threading.Barrier(3)  # 3 threads start together

        def _hold_connection(hold_seconds: float = 0.3) -> bool:
            """Check out a connection, hold it, then return it.

            Args:
                hold_seconds: Seconds to hold the connection before releasing.

            Returns:
                True if connection acquired, False if pool timeout.
            """
            import time

            try:
                conn = engine.connect()
                conn.execute(text("SELECT 1"))
                time.sleep(hold_seconds)
                conn.close()
                return True
            except SATimeoutError:
                return False

        results: list[bool] = []
        result_lock = threading.Lock()

        def _thread_fn() -> None:
            barrier.wait()  # synchronize all 3 threads to maximize contention
            ok = _hold_connection(hold_seconds=0.3)
            with result_lock:
                results.append(ok)

        threads = [threading.Thread(target=_thread_fn) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        successes_3 = sum(1 for r in results if r)
        assert successes_3 == 3, (
            f"All 3 connections (pool_size=2 + max_overflow=1) must succeed; "
            f"got {successes_3} successes"
        )

        # Now all 3 connections are returned. Attempt a 4th with the pool idle —
        # it must succeed since pool is no longer exhausted.
        conn4 = engine.connect()
        conn4.execute(text("SELECT 1"))
        conn4.close()

        # Verify pool exhaustion: request 4 connections simultaneously with pool_size=2,
        # max_overflow=1, pool_timeout=0.1 — the 4th must raise TimeoutError.
        engine2 = create_engine(
            "sqlite:///:memory:",
            poolclass=QueuePool,
            pool_size=2,
            max_overflow=1,
            pool_timeout=0.1,
            connect_args={"check_same_thread": False},
        )

        held_connections: list[object] = []
        held_lock = threading.Lock()

        def _acquire_and_hold() -> None:
            """Acquire and hold a connection without releasing."""
            conn = engine2.connect()
            conn.execute(text("SELECT 1"))
            with held_lock:
                held_connections.append(conn)

        # Acquire 3 connections (pool_size=2 + max_overflow=1) and hold them
        acquire_threads = [threading.Thread(target=_acquire_and_hold) for _ in range(3)]
        for t in acquire_threads:
            t.start()
        for t in acquire_threads:
            t.join(timeout=5.0)

        assert len(held_connections) == 3, (
            f"All 3 connections must be acquired; got {len(held_connections)}"
        )

        # Now pool is exhausted — 4th connection must time out
        with pytest.raises(SATimeoutError):
            engine2.connect()

        # Cleanup
        for conn in held_connections:
            conn.close()  # type: ignore[union-attr]
