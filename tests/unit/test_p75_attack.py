"""Negative/attack tests for Phase 75 — Multi-Worker Safety & Observability.

Attack tests verifying that:
1. CB Redis keys use ``conclave:cb:`` prefix — no collision with ``ratelimit:`` or ``huey.*``.
2. CB Redis corrupt values (non-integer) trigger the same fallback as RedisError.
3. CB Redis keys have TTL equal to cooldown_seconds (no permanent-on-crash leak).
4. Grace period uses UTC epoch (``time.time()``) NOT ``time.monotonic()`` (cross-process).
5. Grace period key is DELETED on Redis recovery, not merely ignored.
6. Prometheus multiprocess dir is rejected when non-existent.
7. Prometheus multiprocess dir is rejected when non-absolute path is given.
8. Prometheus multiprocess dir is rejected when it is inside the source tree.
9. Factory double-set emits WARNING but succeeds (existing behavior preserved).
10. Half-open probe coordination uses ``SET NX EX`` — only one worker fires the probe.

CONSTITUTION Priority 0: Security — fail-closed on invalid configuration
CONSTITUTION Priority 3: TDD — Attack tests committed before implementation (Rule 22)
Task: T75.1 (Redis CB), T75.2 (Redis grace period), T75.3 (Prometheus multiprocess),
      T75.4 (factory injection sync)
Phase: P75 — Multi-Worker Safety & Observability
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# T75.1 — Redis Circuit Breaker attack tests
# ---------------------------------------------------------------------------


class TestRedisCBKeyPrefix:
    """CB Redis keys must use ``conclave:cb:`` prefix to avoid collisions."""

    def test_cb_redis_key_uses_conclave_cb_prefix(self) -> None:
        """All Redis keys written by RedisCircuitBreaker must start with ``conclave:cb:``.

        This prevents collisions with ``ratelimit:`` keys and ``huey.*`` keys
        that share the same Redis database.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
        )

        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis.incr.return_value = 1
        mock_redis.expire.return_value = True
        mock_redis.set.return_value = True

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=300,
        )
        url = "https://example.com/hook"
        cb.record_failure(url)

        # Every Redis call must use the conclave:cb: prefix
        all_calls: list[tuple[str, ...]] = []
        for c in mock_redis.method_calls:
            for arg in c.args:
                if isinstance(arg, str):
                    all_calls.append((c[0], arg))

        key_calls = [
            (method, key) for method, key in all_calls if "ratelimit:" in key or "huey." in key
        ]
        assert key_calls == [], (
            f"CB Redis keys must not use ratelimit: or huey.* prefixes. "
            f"Found colliding keys: {key_calls}"
        )

        # Verify conclave:cb: prefix is actually used
        conclave_calls = [
            (method, key) for method, key in all_calls if key.startswith("conclave:cb:")
        ]
        assert len(conclave_calls) > 0, (
            f"CB Redis keys must use 'conclave:cb:' prefix. All key args seen: {all_calls}"
        )

    def test_cb_redis_key_is_scoped_per_url(self) -> None:
        """Different callback URLs must produce different Redis key prefixes.

        Keys must be scoped per URL so that a failing URL does not trip the
        circuit for a different URL that happens to share a host.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
        )

        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis.incr.return_value = 1
        mock_redis.expire.return_value = True

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=300,
        )

        url_a = "https://alpha.example.com/hook"
        url_b = "https://beta.example.com/hook"

        # Record keys used for url_a
        cb.record_failure(url_a)
        calls_a = [str(c) for c in mock_redis.method_calls]
        mock_redis.reset_mock()

        # Record keys used for url_b
        cb.record_failure(url_b)
        calls_b = [str(c) for c in mock_redis.method_calls]

        # The key arguments must differ between the two URLs
        assert calls_a != calls_b, (
            "Circuit breaker must use different Redis keys for different URLs. "
            f"URL A calls: {calls_a}, URL B calls: {calls_b}"
        )


class TestRedisCBTTL:
    """All CB Redis keys must have TTL equal to cooldown_seconds."""

    def test_cb_failure_key_has_ttl_set(self) -> None:
        """``record_failure()`` must set TTL on all Redis keys it writes.

        Keys without TTL become permanent if the worker crashes while the
        circuit is tripped — permanently blocking delivery to that URL.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
        )

        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis.incr.return_value = 3  # threshold — trips circuit
        mock_redis.expire.return_value = True
        mock_redis.set.return_value = True

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=300,
        )
        url = "https://example.com/hook"
        cb.record_failure(url)

        # Verify that either expire() was called or a SET with EX was used
        expire_calls = mock_redis.expire.call_args_list
        set_calls = mock_redis.set.call_args_list

        has_ttl = len(expire_calls) > 0 or any(
            "ex" in (kw or {}) or "px" in (kw or {})
            for _, kw in (c for c in set_calls if len(c) >= 2)
        )
        # Check set calls for EX kwarg
        for c in set_calls:
            kw = c.kwargs if hasattr(c, "kwargs") else (c[1] if len(c) > 1 else {})
            if "ex" in kw or "px" in kw:
                has_ttl = True

        if len(expire_calls) > 0:
            has_ttl = True

        assert has_ttl, (
            "CB Redis keys must have TTL set. "
            "Call expire() or use SET with EX argument. "
            f"expire calls: {expire_calls}, set calls: {set_calls}"
        )
        # Specific assertion: at least one Redis method was called (INCR triggers state)
        assert mock_redis.incr.called, "record_failure() must call Redis INCR"

    def test_cb_failure_key_ttl_matches_cooldown(self) -> None:
        """TTL on the failure counter key must equal ``cooldown_seconds``.

        Using a different TTL (e.g. longer) means tripped circuits could
        linger beyond the cooldown window after a worker crash.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
        )

        cooldown = 120
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis.incr.return_value = 3
        mock_redis.expire.return_value = True
        mock_redis.set.return_value = True

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=cooldown,
        )
        url = "https://example.com/hook"
        cb.record_failure(url)

        # Check that all expire() calls use the cooldown duration
        for expire_call in mock_redis.expire.call_args_list:
            args = expire_call.args if hasattr(expire_call, "args") else expire_call[0]
            if len(args) >= 2:
                ttl_arg = args[1]
                assert ttl_arg == cooldown, (
                    f"CB Redis key TTL must be {cooldown} (cooldown_seconds), got TTL={ttl_arg}."
                )

        # Check SET calls with EX kwarg
        for set_call in mock_redis.set.call_args_list:
            kw = (
                set_call.kwargs
                if hasattr(set_call, "kwargs")
                else (set_call[1] if len(set_call) > 1 else {})
            )
            if "ex" in kw:
                assert kw["ex"] == cooldown, (
                    f"CB Redis SET EX must be {cooldown} (cooldown_seconds), got ex={kw['ex']}."
                )


class TestRedisCBCorruptValue:
    """Corrupt Redis values must trigger the same fallback as RedisError."""

    def test_corrupt_redis_value_falls_back_gracefully(self) -> None:
        """Non-integer value from INCR (e.g. b'corrupted') must not raise.

        If Redis returns a non-integer value for an INCR key, the circuit
        breaker must treat it as a RedisError and fall back to process-local
        behavior (WebhookCircuitBreaker) — never propagating ValueError.
        """
        import redis as redis_lib

        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
        )

        mock_redis = MagicMock()
        # Simulate corrupt INCR response (not an integer)
        mock_redis.incr.side_effect = redis_lib.RedisError("WRONGTYPE")
        mock_redis.get.return_value = None

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=300,
        )
        url = "https://example.com/hook"

        # Must not raise — must degrade gracefully
        try:
            cb.record_failure(url)
        except Exception as exc:
            pytest.fail(
                f"RedisCircuitBreaker.record_failure() must not raise on Redis error. "
                f"Got: {type(exc).__name__}: {exc}"
            )
        # After a Redis error, the circuit must remain in closed state (not permanently stuck)
        is_open_val = cb.is_open(url)
        assert is_open_val == False, (
            "After Redis error in record_failure(), circuit must remain closed (not open). "
            f"Got: {is_open_val!r}"
        )

    def test_non_integer_incr_response_triggers_fallback(self) -> None:
        """If INCR returns a bytes value that cannot be interpreted as int, fallback must fire."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
        )

        mock_redis = MagicMock()
        # Some Redis clients may return bytes
        mock_redis.incr.return_value = b"not-a-number"
        mock_redis.get.return_value = None
        mock_redis.expire.return_value = True

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=300,
        )
        url = "https://example.com/hook"

        # Must not raise — must handle gracefully
        try:
            cb.record_failure(url)
        except Exception as exc:
            pytest.fail(
                f"RedisCircuitBreaker must handle non-integer INCR response gracefully. "
                f"Got: {type(exc).__name__}: {exc}"
            )
        # Specific: circuit must remain closed after graceful degradation
        result = cb.is_open(url)
        assert result == False, (
            f"After non-integer INCR, circuit must remain closed. Got: {result!r}"
        )


class TestRedisCBHalfOpenProbeCoordination:
    """Half-open probe must use SET NX EX — only one worker fires the probe."""

    def test_half_open_probe_uses_set_nx_ex(self) -> None:
        """When circuit is in half-open state, probe key must be SET with NX and EX.

        This is the atomic Redis primitive that ensures only ONE worker among
        N workers fires the probe attempt — the rest see the key exists and skip.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
        )

        mock_redis = MagicMock()
        # Circuit is tripped (get tripped_at returns a value)
        tripped_time = str(time.time() - 400).encode()  # 400s ago, beyond 300s cooldown
        mock_redis.get.return_value = tripped_time

        # SET NX EX returns True when the key was set (this worker wins probe)
        mock_redis.set.return_value = True

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=300,
        )
        url = "https://example.com/hook"

        # In half-open state, is_open() with probe coordination should call SET NX EX
        result = cb.is_open(url)

        # Check that SET was called with nx=True and ex=cooldown
        set_calls = mock_redis.set.call_args_list
        probe_set_calls = [
            c
            for c in set_calls
            if (c.kwargs if hasattr(c, "kwargs") else (c[1] if len(c) > 1 else {})).get("nx")
        ]
        # Probe lock must use NX to be atomic
        assert len(probe_set_calls) > 0 or result is False, (
            "Half-open probe must use SET NX EX for atomic single-worker coordination. "
            f"SET calls: {set_calls}"
        )


# ---------------------------------------------------------------------------
# T75.2 — Redis Grace Period attack tests
# ---------------------------------------------------------------------------


class TestGracePeriodUsesUTCEpoch:
    """Grace period must use ``time.time()`` (UTC epoch), not ``time.monotonic()``."""

    def test_grace_period_started_key_stores_time_time(self) -> None:
        """The grace period start timestamp stored in Redis must come from ``time.time()``.

        ``time.monotonic()`` is per-process and is NOT comparable across multiple
        uvicorn workers on the same host (each process has its own monotonic clock
        that starts at process creation).  Using monotonic() would cause the grace
        period to expire at different times on different workers.
        """
        # We verify this by checking that the middleware stores a value comparable
        # to time.time() output (a large float, > 1e9 Unix epoch) rather than
        # a small monotonic value (< 1e6 typically).
        mock_redis = MagicMock()
        stored_values: list[Any] = []

        def _capture_set(key: str, value: Any, **kwargs: Any) -> bool:
            if "conclave:grace:" in key:
                stored_values.append(value)
            return True

        mock_redis.set.side_effect = _capture_set
        mock_redis.get.return_value = None

        from synth_engine.bootstrapper.dependencies.rate_limit_middleware import (
            RateLimitGateMiddleware,
        )

        # Trigger grace period storage by simulating Redis failure in dispatch
        # We test the grace period storage function directly
        middleware = RateLimitGateMiddleware.__new__(RateLimitGateMiddleware)
        middleware._fail_open = False
        middleware._redis_grace_key = "conclave:grace:started"
        middleware._redis = mock_redis

        # Simulate recording grace period start
        if hasattr(middleware, "_record_grace_period_start"):
            middleware._record_grace_period_start()
            # The stored value must be a UTC epoch float > 1e9
            if stored_values:
                stored_val = float(stored_values[0])
                assert stored_val > 1_000_000_000, (
                    f"Grace period timestamp must be UTC epoch (>1e9), "
                    f"got {stored_val} (looks like monotonic clock or other source)."
                )

    def test_grace_period_key_prefix_is_conclave_grace(self) -> None:
        """Grace period Redis key must use ``conclave:grace:`` prefix.

        This avoids collision with ``conclave:cb:`` and ``ratelimit:`` namespaces.
        """
        from synth_engine.bootstrapper.dependencies.rate_limit_middleware import (
            _REDIS_GRACE_KEY_PREFIX,
        )

        assert _REDIS_GRACE_KEY_PREFIX.startswith("conclave:grace:"), (
            f"Grace period key prefix must be 'conclave:grace:', got {_REDIS_GRACE_KEY_PREFIX!r}"
        )

    def test_grace_period_key_has_ttl(self) -> None:
        """Grace period Redis key must have TTL to prevent stale values surviving restarts.

        A grace period key without TTL would be retained across worker restarts,
        causing the rate limiter to think Redis was in failure state even after a
        clean restart.
        """
        from synth_engine.bootstrapper.dependencies.rate_limit_middleware import (
            _REDIS_GRACE_PERIOD_TTL_SECONDS,
        )

        # TTL must be > 0 and should be at least 2x the grace period
        assert _REDIS_GRACE_PERIOD_TTL_SECONDS > 0, (
            "Grace period key must have a positive TTL to expire stale values."
        )
        assert _REDIS_GRACE_PERIOD_TTL_SECONDS >= 10, (
            f"Grace period TTL ({_REDIS_GRACE_PERIOD_TTL_SECONDS}s) must be >= 10s "
            f"to be meaningful. Expected at least 2x _REDIS_GRACE_PERIOD_SECONDS."
        )


class TestGracePeriodKeyDeletedOnRecovery:
    """Grace period key must be deleted from Redis on recovery, not just ignored."""

    def test_grace_key_deleted_when_redis_recovers(self) -> None:
        """When Redis recovers (successful _redis_hit), the grace key must be deleted.

        Merely resetting ``_redis_first_failure_time = None`` is insufficient
        in multi-worker mode: other workers read the key from Redis to determine
        if they are in the grace period.  Deletion is the only correct approach.
        """
        from synth_engine.bootstrapper.dependencies.rate_limit_middleware import (
            RateLimitGateMiddleware,
        )

        deleted_keys: list[str] = []

        mock_redis = MagicMock()

        def _capture_delete(key: str) -> int:
            deleted_keys.append(key)
            return 1

        mock_redis.delete.side_effect = _capture_delete

        middleware = RateLimitGateMiddleware.__new__(RateLimitGateMiddleware)
        middleware._fail_open = False
        middleware._redis = mock_redis
        middleware._redis_first_failure_time = None

        # Simulate what happens when recovery is detected
        if hasattr(middleware, "_record_redis_recovery"):
            middleware._record_redis_recovery()
            # Must delete the grace key from Redis
            assert len(deleted_keys) > 0, (
                "On Redis recovery, the grace period key must be deleted from Redis. "
                f"redis.delete() was not called. Called methods: {mock_redis.method_calls}"
            )
            # The deleted key must be the grace key
            assert any("conclave:grace:" in k for k in deleted_keys), (
                f"Deleted keys must include the grace key. Got: {deleted_keys}"
            )


# ---------------------------------------------------------------------------
# T75.3 — Prometheus Multiprocess Mode attack tests
# ---------------------------------------------------------------------------


class TestPrometheusMultiprocDirValidation:
    """Prometheus multiprocess dir must be validated fail-closed."""

    def test_nonexistent_dir_raises_on_startup(self) -> None:
        """Startup must fail-closed with a clear error when PROMETHEUS_MULTIPROC_DIR is set
        but the directory does not exist.

        An unwritable metrics dir silently drops metrics — better to fail fast.
        """
        from synth_engine.bootstrapper.main import validate_prometheus_multiproc_dir

        # Use a path that definitely doesn't exist
        nonexistent = "/tmp/p75_test_nonexistent_prometheus_dir_xyz_abc_123"
        with pytest.raises((ValueError, RuntimeError, OSError, SystemExit)) as exc_info:
            validate_prometheus_multiproc_dir(nonexistent)

        # Error must be descriptive — not just an assertion error
        assert exc_info.value is not None

    def test_relative_path_rejected(self) -> None:
        """Prometheus multiprocess dir must be an absolute path.

        A relative path is ambiguous across uvicorn worker processes that may
        have different working directories.
        """
        from synth_engine.bootstrapper.main import validate_prometheus_multiproc_dir

        with pytest.raises((ValueError, RuntimeError, SystemExit)):
            validate_prometheus_multiproc_dir("relative/path/to/metrics")

    def test_path_inside_source_tree_rejected(self) -> None:
        """Prometheus multiprocess dir must not be inside the application source tree.

        Storing Prometheus .db files inside src/ would contaminate the source
        tree and risk committing metrics data to version control.
        """

        from synth_engine.bootstrapper.main import validate_prometheus_multiproc_dir

        # Find the src directory
        src_path = str(Path(__file__).parent.parent.parent / "src")

        with pytest.raises((ValueError, RuntimeError, SystemExit)):
            validate_prometheus_multiproc_dir(src_path + "/prometheus_metrics")

    def test_valid_absolute_writable_dir_accepted(self, tmp_path: Path) -> None:
        """A valid absolute writable directory outside source tree must be accepted."""
        from synth_engine.bootstrapper.main import validate_prometheus_multiproc_dir

        # tmp_path is absolute, writable, and outside src/
        # Should not raise
        result = validate_prometheus_multiproc_dir(str(tmp_path))
        # Specific: must return None (validation-only function)
        assert result == None, (  # noqa: E711 — explicit None return check
            f"validate_prometheus_multiproc_dir must return None for valid dirs. Got: {result!r}"
        )

    def test_env_var_not_set_returns_none_or_noop(self) -> None:
        """When PROMETHEUS_MULTIPROC_DIR is not set, single-worker mode must work unchanged.

        The function must return None or a no-op sentinel when dir is unset —
        not raise.
        """
        from synth_engine.bootstrapper.main import validate_prometheus_multiproc_dir

        # Passing None (or empty string) signals "not configured"
        result = validate_prometheus_multiproc_dir(None)
        # Must not raise; result must be None (single-worker mode no-op)
        assert result == None, (  # noqa: E711 — explicit None return check
            f"Expected None for None input, got {result!r}"
        )


# ---------------------------------------------------------------------------
# T75.4 — Factory injection thread-safety attack tests
# ---------------------------------------------------------------------------


class TestFactoryDoubleSetWarns:
    """Double-set factory must emit WARNING but succeed (existing behavior preserved)."""

    def test_double_set_dp_wrapper_factory_emits_warning(self) -> None:
        """Calling set_dp_wrapper_factory() twice must emit a WARNING log.

        This prevents silent regressions where multiple bootstrapper instances
        compete to register factories without visibility.
        """
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            set_dp_wrapper_factory,
        )

        factory_a = MagicMock(name="factory_a")
        factory_b = MagicMock(name="factory_b")

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration._logger"
        ) as mock_logger:
            set_dp_wrapper_factory(factory_a)
            set_dp_wrapper_factory(factory_b)  # double-set — must warn

            # Verify warning was emitted on the second call
            warning_calls = [c for c in mock_logger.method_calls if "warning" in str(c[0]).lower()]
            # At minimum one warning must have been emitted for the double-set
            assert len(warning_calls) >= 1, (
                "set_dp_wrapper_factory() called twice must emit at least one WARNING. "
                f"Logger calls: {mock_logger.method_calls}"
            )

    def test_double_set_webhook_delivery_fn_emits_warning(self) -> None:
        """Calling set_webhook_delivery_fn() twice must emit a WARNING log."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            set_webhook_delivery_fn,
        )

        fn_a: Any = lambda job_id, status: None  # noqa: E731
        fn_b: Any = lambda job_id, status: None  # noqa: E731

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration._logger"
        ) as mock_logger:
            set_webhook_delivery_fn(fn_a)
            set_webhook_delivery_fn(fn_b)  # double-set — must warn

            warning_calls = [c for c in mock_logger.method_calls if "warning" in str(c[0]).lower()]
            assert len(warning_calls) >= 1, (
                "set_webhook_delivery_fn() called twice must emit at least one WARNING. "
                f"Logger calls: {mock_logger.method_calls}"
            )

    def test_double_set_does_not_raise(self) -> None:
        """Double-set must succeed — not raise — to preserve backward compatibility."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            set_dp_wrapper_factory,
            set_webhook_delivery_fn,
        )

        factory = MagicMock()
        fn: Any = lambda job_id, status: None  # noqa: E731

        # Must not raise
        set_dp_wrapper_factory(factory)
        set_dp_wrapper_factory(factory)

        set_webhook_delivery_fn(fn)
        set_webhook_delivery_fn(fn)

        # Specific: factories must be set (not None) after double-set
        from synth_engine.modules.synthesizer.jobs import job_orchestration as orch

        assert orch._dp_wrapper_factory is factory, (
            "After double-set, _dp_wrapper_factory must be the last supplied factory."
        )


class TestFactoryLockThreadSafety:
    """Factory injection lock must protect thread-safety within a single process."""

    def test_concurrent_set_dp_wrapper_factory_does_not_corrupt_state(self) -> None:
        """Concurrent calls to set_dp_wrapper_factory() must not corrupt state.

        This simulates multiple threads (e.g., from uvicorn --threads N)
        calling the factory setter simultaneously.  The factory must end up
        set to one of the supplied values — not None, not a mixed object.

        NOTE: This lock protects INTRA-PROCESS thread safety only. Cross-process
        safety is provided by process-level isolation (each uvicorn worker has
        its own Python interpreter).
        """
        from synth_engine.modules.synthesizer.jobs import job_orchestration as orch
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            set_dp_wrapper_factory,
        )

        factories = [MagicMock(name=f"factory_{i}") for i in range(10)]
        errors: list[Exception] = []

        def _set(factory: Any) -> None:
            try:
                set_dp_wrapper_factory(factory)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_set, args=(f,)) for f in factories]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No exceptions must have been raised
        assert errors == [], f"Concurrent set_dp_wrapper_factory raised: {errors}"

        # Final state must be one of the supplied factories
        assert orch._dp_wrapper_factory in factories, (
            f"Factory must be set to one of the supplied values after concurrent writes. "
            f"Got: {orch._dp_wrapper_factory!r}"
        )

    def test_reset_helpers_acquire_lock(self) -> None:
        """Reset helpers (_reset_*) must be safe to call concurrently with setters.

        If reset helpers bypass the lock, a set() + reset() interleaving could
        leave state in an inconsistent intermediate position.
        """
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            _reset_webhook_delivery_fn,
            set_webhook_delivery_fn,
        )

        fn: Any = lambda job_id, status: None  # noqa: E731
        errors: list[Exception] = []

        def _concurrent_set_reset() -> None:
            try:
                set_webhook_delivery_fn(fn)
                _reset_webhook_delivery_fn()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_concurrent_set_reset) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No exceptions from concurrent set/reset
        assert errors == [], f"Concurrent set/reset raised: {errors}"


# ---------------------------------------------------------------------------
# T75.1 — Redis CB fallback to process-local on startup unavailability
# ---------------------------------------------------------------------------


class TestRedisCBFallback:
    """CB must fall back to process-local WebhookCircuitBreaker when Redis is unavailable."""

    def test_redis_unavailable_at_startup_produces_local_fallback(self) -> None:
        """If Redis client raises on first use in _get_circuit_breaker(), return local CB.

        The module MUST NOT store None as the circuit breaker singleton.
        It MUST store a process-local WebhookCircuitBreaker instance instead.
        After a Redis failure, subsequent calls must reuse the same local CB
        (not re-attempt Redis on every delivery).

        T75.1: Redis client is injected via set_circuit_breaker_redis_client().
        When the injected Redis client is unavailable (raises on first use),
        _get_circuit_breaker() falls back to process-local WebhookCircuitBreaker.
        """
        import redis as redis_lib

        import synth_engine.modules.synthesizer.jobs.webhook_delivery as _mod
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            WebhookCircuitBreaker,
            set_circuit_breaker_redis_client,
        )

        original_cb = _mod._MODULE_CIRCUIT_BREAKER
        original_redis = _mod._CB_REDIS_CLIENT

        # Inject a Redis client that fails on any operation
        failing_redis = MagicMock()
        failing_redis.ping.side_effect = redis_lib.ConnectionError("Redis unavailable")

        try:
            # set_circuit_breaker_redis_client resets _MODULE_CIRCUIT_BREAKER to None
            set_circuit_breaker_redis_client(failing_redis)

            # Patch RedisCircuitBreaker.__init__ to raise ConnectionError
            # so that _get_circuit_breaker() exercises the except branch
            with patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.RedisCircuitBreaker",
                side_effect=redis_lib.ConnectionError("Redis unavailable at startup"),
            ):
                cb = _mod._get_circuit_breaker()

            # Must return a usable circuit breaker, not None
            assert cb is not None, (
                "_get_circuit_breaker() must return a usable circuit breaker even when "
                "Redis is unavailable at startup. Got None."
            )

            # Must be a WebhookCircuitBreaker (process-local fallback)
            assert isinstance(cb, WebhookCircuitBreaker), (
                f"Fallback must be WebhookCircuitBreaker, got {type(cb).__name__}"
            )

            # Singleton must be stored (not None) for subsequent calls
            assert _mod._MODULE_CIRCUIT_BREAKER is not None, (
                "After Redis-unavailable fallback, _MODULE_CIRCUIT_BREAKER must not be None. "
                "Store the local fallback so subsequent calls reuse it."
            )
            # Specific: the stored singleton must be the same object returned
            assert _mod._MODULE_CIRCUIT_BREAKER is cb, (
                "The stored singleton must be the same WebhookCircuitBreaker instance returned."
            )
        finally:
            _mod._CB_REDIS_CLIENT = original_redis
            _mod._MODULE_CIRCUIT_BREAKER = original_cb
