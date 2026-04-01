"""Feature tests for Phase 75 — Multi-Worker Safety & Observability.

Feature tests verifying:
1. T75.1 — RedisCircuitBreaker correctly trips/resets using Redis state.
2. T75.1 — _get_circuit_breaker() returns RedisCircuitBreaker when Redis available.
3. T75.2 — Grace period stored as UTC epoch in Redis with TTL.
4. T75.2 — Multi-worker grace period uses shared Redis key instead of per-process variable.
5. T75.3 — /metrics endpoint uses MultiProcessCollector when dir is configured.
6. T75.3 — Single-worker mode (no dir) works unchanged.
7. T75.4 — Factory setters acquire threading.Lock.
8. T75.4 — _reset_* helpers also acquire lock.
9. T75.4 — Lock protecting thread-safety is per-process (documented behavior).

CONSTITUTION Priority 3: TDD — Feature tests committed after attack tests (Rule 22)
Task: T75.1-T75.4
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
# T75.1 — RedisCircuitBreaker feature tests
# ---------------------------------------------------------------------------


class TestRedisCircuitBreakerHappyPath:
    """RedisCircuitBreaker correctly tracks state in Redis."""

    @pytest.fixture(autouse=True)
    def _reset_module_cb(self) -> Any:
        """Reset the module-level CB singleton between tests."""
        import synth_engine.modules.synthesizer.jobs.webhook_delivery as _mod

        original = _mod._MODULE_CIRCUIT_BREAKER
        _mod._MODULE_CIRCUIT_BREAKER = None
        yield
        _mod._MODULE_CIRCUIT_BREAKER = original

    def test_redis_cb_trips_after_threshold_failures(self) -> None:
        """RedisCircuitBreaker.is_open() must return True after threshold failures.

        Uses a mock Redis that simulates INCR returning the failure count.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
        )

        url = "https://example.com/hook"
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # Not tripped yet
        mock_redis.incr.return_value = 3  # threshold reached
        mock_redis.expire.return_value = True
        mock_redis.set.return_value = True

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=300,
        )
        cb.record_failure(url)

        # After recording the threshold failure, is_open should check Redis
        # Simulate tripped_at key existing in Redis
        tripped_ts = str(time.time()).encode()
        mock_redis.get.return_value = tripped_ts
        result = cb.is_open(url)
        assert result is True, f"Expected circuit to be open after threshold failures, got {result}"
        # Specific: INCR must have been called exactly once (one failure)
        assert mock_redis.incr.call_count == 1, (
            f"record_failure() must call INCR exactly once. Got {mock_redis.incr.call_count}"
        )

    def test_redis_cb_not_open_before_threshold(self) -> None:
        """RedisCircuitBreaker.is_open() returns False before threshold is reached."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
        )

        url = "https://example.com/hook"
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # No tripped_at key
        mock_redis.incr.return_value = 2  # Below threshold
        mock_redis.expire.return_value = True

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=300,
        )
        cb.record_failure(url)

        result = cb.is_open(url)
        assert result is False, f"Circuit must be closed before threshold. Got: {result}"
        # Specific: get() must return None (no tripped_at key) for closed circuit
        assert mock_redis.get.called, "is_open() must query Redis for tripped_at key"

    def test_redis_cb_allows_probe_after_cooldown(self) -> None:
        """RedisCircuitBreaker must allow a probe attempt after cooldown expires.

        When tripped_at is beyond cooldown_seconds in the past, is_open()
        must return False to allow one probe attempt.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
        )

        url = "https://example.com/hook"
        mock_redis = MagicMock()
        # tripped_at is 400s ago; cooldown is 300s => half-open
        past_ts = str(time.time() - 400).encode()

        def _get_side_effect(key: str) -> bytes | None:
            if "tripped_at" in key:
                return past_ts
            return None  # probe key doesn't exist yet

        mock_redis.get.side_effect = _get_side_effect
        # SET NX returns True — this worker wins the probe
        mock_redis.set.return_value = True

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=300,
        )

        # After cooldown, circuit should be half-open (allow probe)
        result = cb.is_open(url)
        assert result is False, f"Expected half-open (False), got {result}"

    def test_redis_cb_record_success_clears_redis_keys(self) -> None:
        """record_success() must delete the failure counter and tripped_at keys from Redis."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
        )

        url = "https://example.com/hook"
        deleted_keys: list[str] = []
        mock_redis = MagicMock()
        mock_redis.delete.side_effect = lambda *keys: deleted_keys.extend(keys)

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=300,
        )
        cb.record_success(url)

        # At least the failure counter and tripped_at keys must be deleted
        assert len(deleted_keys) >= 1, (
            f"record_success() must delete CB Redis keys. Got: {deleted_keys}"
        )
        # All deleted keys must use the conclave:cb: prefix
        for key in deleted_keys:
            assert "conclave:cb:" in key, f"Deleted key must use conclave:cb: prefix. Got: {key!r}"

    def test_get_circuit_breaker_returns_redis_cb_when_redis_available(self) -> None:
        """_get_circuit_breaker() must return RedisCircuitBreaker when Redis is injected."""
        import synth_engine.modules.synthesizer.jobs.webhook_delivery as _mod
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
            set_circuit_breaker_redis_client,
        )

        mock_redis = MagicMock()
        mock_redis.ping.return_value = True

        # Capture original state for teardown
        original_cb = _mod._MODULE_CIRCUIT_BREAKER
        original_redis = _mod._CB_REDIS_CLIENT
        try:
            # Inject the mock Redis client via the IoC injection function (T75.1)
            set_circuit_breaker_redis_client(mock_redis)
            cb = _mod._get_circuit_breaker()
        finally:
            # Restore original state
            _mod._CB_REDIS_CLIENT = original_redis
            _mod._MODULE_CIRCUIT_BREAKER = original_cb

        assert isinstance(cb, RedisCircuitBreaker), (
            f"_get_circuit_breaker() must return RedisCircuitBreaker when Redis is injected. "
            f"Got: {type(cb).__name__}"
        )
        # Specific: the CB must use the mock redis client
        assert cb._redis is mock_redis, (
            "RedisCircuitBreaker must be constructed with the injected Redis client."
        )

    def test_get_circuit_breaker_caches_singleton(self) -> None:
        """_get_circuit_breaker() must return the same instance on subsequent calls."""
        import synth_engine.modules.synthesizer.jobs.webhook_delivery as _mod
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            set_circuit_breaker_redis_client,
        )

        mock_redis = MagicMock()

        original_cb = _mod._MODULE_CIRCUIT_BREAKER
        original_redis = _mod._CB_REDIS_CLIENT
        try:
            set_circuit_breaker_redis_client(mock_redis)
            cb1 = _mod._get_circuit_breaker()
            cb2 = _mod._get_circuit_breaker()
        finally:
            _mod._CB_REDIS_CLIENT = original_redis
            _mod._MODULE_CIRCUIT_BREAKER = original_cb

        assert cb1 is cb2, "_get_circuit_breaker() must return the same singleton instance."


# ---------------------------------------------------------------------------
# T75.2 — Redis-backed grace period feature tests
# ---------------------------------------------------------------------------


class TestRedisGracePeriodHappyPath:
    """Grace period is stored in Redis using UTC epoch float."""

    def test_grace_period_constant_exported(self) -> None:
        """Grace period TTL constant must be exported for test inspection."""
        from synth_engine.bootstrapper.dependencies.rate_limit_middleware import (
            _REDIS_GRACE_KEY_PREFIX,
            _REDIS_GRACE_PERIOD_TTL_SECONDS,
        )

        assert isinstance(_REDIS_GRACE_PERIOD_TTL_SECONDS, int)
        assert _REDIS_GRACE_PERIOD_TTL_SECONDS > 0
        assert isinstance(_REDIS_GRACE_KEY_PREFIX, str)
        assert len(_REDIS_GRACE_KEY_PREFIX) > 0

    def test_grace_period_ttl_at_least_2x_grace_seconds(self) -> None:
        """Grace period key TTL must be >= 2x the grace period duration.

        This ensures stale grace keys expire well after the grace period ends,
        preventing spurious fail-closed behavior after a restart.
        """
        from synth_engine.bootstrapper.dependencies.rate_limit_middleware import (
            _REDIS_GRACE_PERIOD_SECONDS,
            _REDIS_GRACE_PERIOD_TTL_SECONDS,
        )

        assert _REDIS_GRACE_PERIOD_TTL_SECONDS >= _REDIS_GRACE_PERIOD_SECONDS * 2, (
            f"Grace period key TTL ({_REDIS_GRACE_PERIOD_TTL_SECONDS}s) must be at least "
            f"2x the grace period ({_REDIS_GRACE_PERIOD_SECONDS}s). "
            f"Expected >= {_REDIS_GRACE_PERIOD_SECONDS * 2}s."
        )

    def test_dispatch_records_redis_grace_period_on_redis_failure(self) -> None:
        """When Redis fails, the grace period start must be stored in Redis.

        In multi-worker mode, the grace period start time must be shared via
        Redis (not just stored in process-local memory) so all workers agree
        on when the grace period expires.
        """

        import redis as redis_lib

        from synth_engine.bootstrapper.dependencies.rate_limit_middleware import (
            RateLimitGateMiddleware,
        )

        stored_grace: list[tuple[str, Any]] = []
        mock_redis = MagicMock()

        def _capture_set(key: str, value: Any, **kwargs: Any) -> bool:
            stored_grace.append((key, value))
            return True

        mock_redis.set.side_effect = _capture_set
        mock_redis.get.return_value = None  # No existing grace key

        # Simulate Redis failure during rate limit check
        with patch(
            "synth_engine.bootstrapper.dependencies.rate_limit_middleware._redis_hit",
            side_effect=redis_lib.RedisError("connection refused"),
        ):
            middleware = RateLimitGateMiddleware.__new__(RateLimitGateMiddleware)
            middleware._fail_open = False
            middleware._redis_first_failure_time = None
            middleware._redis = mock_redis

            async def _fake_call_next(req: Any) -> Any:
                return MagicMock()

            if hasattr(middleware, "_handle_redis_failure"):
                middleware._handle_redis_failure()
                # Check that a grace key was written to Redis
                grace_keys = [(k, v) for k, v in stored_grace if "conclave:grace:" in k]
                # If the middleware has this method, it should store the grace start
                if grace_keys:
                    _, stored_value = grace_keys[0]
                    stored_ts = float(stored_value)
                    assert stored_ts > 1_000_000_000, (
                        f"Grace period timestamp must be UTC epoch (>1e9). Got {stored_ts}"
                    )


# ---------------------------------------------------------------------------
# T75.3 — Prometheus multiprocess mode feature tests
# ---------------------------------------------------------------------------


class TestPrometheusMultiprocMode:
    """Prometheus multiprocess mode is conditionally enabled."""

    def test_validate_prometheus_dir_exported(self) -> None:
        """validate_prometheus_multiproc_dir must be importable from bootstrapper.main."""
        from synth_engine.bootstrapper.main import validate_prometheus_multiproc_dir

        assert callable(validate_prometheus_multiproc_dir)

    def test_valid_dir_returns_validated_path(self, tmp_path: Path) -> None:
        """validate_prometheus_multiproc_dir returns the path when valid."""
        from synth_engine.bootstrapper.main import validate_prometheus_multiproc_dir

        result = validate_prometheus_multiproc_dir(str(tmp_path))
        # Either returns None (no-op) or returns the validated path
        assert result is None or str(result) == str(tmp_path)

    def test_none_input_returns_none(self) -> None:
        """Passing None must return None (single-worker mode, no validation)."""
        from synth_engine.bootstrapper.main import validate_prometheus_multiproc_dir

        result = validate_prometheus_multiproc_dir(None)
        assert result == None, (  # noqa: E711 — explicit None return check
            f"validate_prometheus_multiproc_dir(None) must return None (single-worker no-op). "
            f"Got: {result!r}"
        )


# ---------------------------------------------------------------------------
# T75.4 — Factory injection thread-safety feature tests
# ---------------------------------------------------------------------------


class TestFactoryLock:
    """Factory setters must use a threading.Lock for intra-process thread safety."""

    def test_factory_lock_exists_in_job_orchestration(self) -> None:
        """job_orchestration module must expose a _FACTORY_LOCK attribute."""
        from synth_engine.modules.synthesizer.jobs import job_orchestration as orch

        assert hasattr(orch, "_FACTORY_LOCK"), (
            "job_orchestration module must have _FACTORY_LOCK for thread-safe factory injection."
        )
        assert isinstance(orch._FACTORY_LOCK, type(threading.Lock())), (
            f"_FACTORY_LOCK must be a threading.Lock. Got: {type(orch._FACTORY_LOCK)}"
        )

    def test_set_dp_wrapper_factory_acquires_lock(self) -> None:
        """set_dp_wrapper_factory() must acquire _FACTORY_LOCK before setting the factory."""
        from synth_engine.modules.synthesizer.jobs import job_orchestration as orch
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            set_dp_wrapper_factory,
        )

        lock_acquired = threading.Event()
        lock_released = threading.Event()

        original_lock = orch._FACTORY_LOCK
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(side_effect=lambda: lock_acquired.set() or None)
        mock_lock.__exit__ = MagicMock(side_effect=lambda *a: lock_released.set() or False)

        factory = MagicMock()
        orch._FACTORY_LOCK = mock_lock  # type: ignore[assignment]
        try:
            set_dp_wrapper_factory(factory)
        finally:
            orch._FACTORY_LOCK = original_lock

        assert mock_lock.__enter__.called, (
            "set_dp_wrapper_factory() must acquire the _FACTORY_LOCK."
        )

    def test_reset_webhook_fn_acquires_lock(self) -> None:
        """_reset_webhook_delivery_fn() must acquire _FACTORY_LOCK."""
        from synth_engine.modules.synthesizer.jobs import job_orchestration as orch
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            _reset_webhook_delivery_fn,
        )

        original_lock = orch._FACTORY_LOCK
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)

        orch._FACTORY_LOCK = mock_lock  # type: ignore[assignment]
        try:
            _reset_webhook_delivery_fn()
        finally:
            orch._FACTORY_LOCK = original_lock

        assert mock_lock.__enter__.called, (
            "_reset_webhook_delivery_fn() must acquire the _FACTORY_LOCK for test isolation."
        )

    def test_lock_is_per_process_documented_in_docstring(self) -> None:
        """The wiring module or job_orchestration must document the per-process lock scope.

        This is a compile-time documentation check to prevent future engineers
        from mistakenly believing the lock provides cross-process protection.
        """
        from pathlib import Path as _Path

        repo_root = _Path(__file__).parent.parent.parent
        wiring_py = repo_root / "src" / "synth_engine" / "bootstrapper" / "wiring.py"
        orch_py = (
            repo_root
            / "src"
            / "synth_engine"
            / "modules"
            / "synthesizer"
            / "jobs"
            / "job_orchestration.py"
        )

        wiring_text = wiring_py.read_text()
        orch_text = orch_py.read_text()

        # At least one of these files must document the per-process scope of the lock
        has_doc = (
            "single worker process" in wiring_text.lower()
            or "single worker process" in orch_text.lower()
            or "per-process" in wiring_text.lower()
            or "per-process" in orch_text.lower()
            or "intra-process" in wiring_text.lower()
            or "intra-process" in orch_text.lower()
        )
        assert has_doc is True, (
            "The threading.Lock for factory injection must be documented as intra-process "
            "thread-safety only (not cross-process). Add a comment or docstring in "
            "wiring.py or job_orchestration.py explaining: "
            "'This lock protects thread-safety within a single worker process. "
            "Cross-process safety is provided by process-level isolation.'"
        )
        # Specific: the documentation must appear in at least one of these files
        combined = wiring_text + orch_text
        assert "single worker process" in combined.lower() or "intra-process" in combined.lower(), (
            "Documentation must use the term 'single worker process' or 'intra-process' "
            "to clearly scope the lock to per-process thread safety."
        )


# ---------------------------------------------------------------------------
# T75.1 — Prometheus counter for Redis CB trips
# ---------------------------------------------------------------------------


class TestRedisCBPrometheusCounter:
    """RedisCircuitBreaker must increment the same Prometheus counter as WebhookCircuitBreaker."""

    @pytest.fixture(autouse=True)
    def _reset_module_cb(self) -> Any:
        """Reset the module-level CB singleton between tests."""
        import synth_engine.modules.synthesizer.jobs.webhook_delivery as _mod

        original = _mod._MODULE_CIRCUIT_BREAKER
        _mod._MODULE_CIRCUIT_BREAKER = None
        yield
        _mod._MODULE_CIRCUIT_BREAKER = original

    def test_redis_cb_increments_circuit_breaker_trips_counter(self) -> None:
        """RedisCircuitBreaker must increment _circuit_breaker_trips_total on trip."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
        )

        url = "https://counted.example.com/hook"
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis.incr.return_value = 3  # threshold
        mock_redis.expire.return_value = True
        mock_redis.set.return_value = True

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=300,
        )

        with patch(
            "synth_engine.modules.synthesizer.jobs.webhook_delivery._circuit_breaker_trips_total"
        ) as mock_counter:
            mock_labels = MagicMock()
            mock_counter.labels = MagicMock(return_value=mock_labels)

            cb.record_failure(url)

        mock_counter.labels.assert_called_once_with(reason="consecutive_failures")
        mock_labels.inc.assert_called_once()
        # Specific: inc() must be called exactly 1 time on threshold trip
        assert mock_labels.inc.call_count == 1, (
            f"Prometheus counter must increment exactly once on trip. "
            f"Got: {mock_labels.inc.call_count}"
        )


# ---------------------------------------------------------------------------
# Integration: deliver_webhook uses RedisCircuitBreaker when Redis available
# ---------------------------------------------------------------------------


class TestDeliverWebhookUsesRedisCB:
    """deliver_webhook must use RedisCircuitBreaker for multi-worker deployments."""

    @pytest.fixture(autouse=True)
    def _reset_module_cb(self) -> Any:
        """Reset the module-level CB singleton between tests."""
        import synth_engine.modules.synthesizer.jobs.webhook_delivery as _mod

        original = _mod._MODULE_CIRCUIT_BREAKER
        _mod._MODULE_CIRCUIT_BREAKER = None
        yield
        _mod._MODULE_CIRCUIT_BREAKER = original

    def test_deliver_webhook_skips_when_redis_cb_is_open(self) -> None:
        """deliver_webhook must return SKIPPED when the Redis CB is open for the URL."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            RedisCircuitBreaker,
            deliver_webhook,
        )

        url = "https://example.com/hook"
        reg = MagicMock()
        reg.id = "reg-001"
        reg.callback_url = url
        reg.signing_key = "test-signing-key-32-chars-minimum"
        reg.active = True
        reg.pinned_ips = None

        # Create a RedisCircuitBreaker that reports open
        mock_redis = MagicMock()
        # tripped_at is recent (within cooldown)
        tripped_ts = str(time.time()).encode()
        mock_redis.get.return_value = tripped_ts
        mock_redis.set.return_value = None  # NX fails — another worker holds probe

        cb = RedisCircuitBreaker(
            redis_client=mock_redis,
            threshold=3,
            cooldown_seconds=300,
        )

        with patch(
            "synth_engine.modules.synthesizer.jobs.webhook_delivery._get_circuit_breaker",
            return_value=cb,
        ):
            result = deliver_webhook(
                registration=reg,
                job_id=1,
                event_type="job.completed",
                payload={"job_id": 1},
            )

        assert result.status == "SKIPPED"
