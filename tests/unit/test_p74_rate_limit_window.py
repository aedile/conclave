"""Feature tests for T74.2 — Rate limit window externalized to ConclaveSettings.

Verifies:
1. rate_limit_window_seconds added to ConclaveSettings with default 60.
2. CONCLAVE_RATE_LIMIT_WINDOW_SECONDS env var overrides the default.
3. rate_limit_backend._redis_hit uses the configured window (not hardcoded 60).
4. Valid boundaries (1, 3600) are accepted.

CONSTITUTION Priority 3: TDD — RED/GREEN/REFACTOR
Task: T74.2 — Wire rate limit window to ConclaveSettings
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _clear_settings() -> None:
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()


def _set_minimal_dev_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)  # pragma: allowlist secret
    _clear_settings()


class TestRateLimitWindowSettingsDefault:
    """rate_limit_window_seconds must default to 60."""

    def setup_method(self) -> None:
        _clear_settings()

    def teardown_method(self) -> None:
        _clear_settings()

    def test_default_window_is_60(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """conclave_rate_limit_window_seconds must default to 60."""
        _set_minimal_dev_env(monkeypatch)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_rate_limit_window_seconds == 60

    def test_window_override_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_RATE_LIMIT_WINDOW_SECONDS=120 must override the default."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_RATE_LIMIT_WINDOW_SECONDS", "120")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_rate_limit_window_seconds == 120

    def test_window_minimum_boundary_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_RATE_LIMIT_WINDOW_SECONDS=1 (minimum) must be accepted."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_RATE_LIMIT_WINDOW_SECONDS", "1")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_rate_limit_window_seconds == 1

    def test_window_maximum_boundary_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_RATE_LIMIT_WINDOW_SECONDS=3600 (maximum) must be accepted."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_RATE_LIMIT_WINDOW_SECONDS", "3600")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_rate_limit_window_seconds == 3600


class TestRateLimitBackendUsesConfiguredWindow:
    """rate_limit_backend._redis_hit must use the window from settings, not a constant."""

    def setup_method(self) -> None:
        _clear_settings()

    def teardown_method(self) -> None:
        _clear_settings()

    def test_redis_hit_uses_configured_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_redis_hit must pass the configured window to the Redis EXPIRE command.

        When CONCLAVE_RATE_LIMIT_WINDOW_SECONDS=120, the Redis key must embed
        '120' in its name and EXPIRE must be called with 120.
        """
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_RATE_LIMIT_WINDOW_SECONDS", "120")
        _clear_settings()

        expire_calls: list[tuple[str, int]] = []

        class _MockPipeline:
            def __init__(self) -> None:
                self._incr_result = 1

            def incr(self, key: str) -> None:
                pass

            def expire(self, key: str, seconds: int) -> None:
                expire_calls.append((key, seconds))

            def execute(self) -> list[int]:
                return [self._incr_result]

            def __enter__(self) -> _MockPipeline:
                return self

            def __exit__(self, *args: object) -> None:
                pass

        class _MockRedis:
            def pipeline(self) -> _MockPipeline:
                return _MockPipeline()

        from synth_engine.bootstrapper.dependencies.rate_limit_backend import _redis_hit

        mock_redis = _MockRedis()
        count, allowed = _redis_hit(
            mock_redis,  # type: ignore[arg-type]
            "5/minute",
            "ip:10.0.0.1",
        )

        # The expire call must use the configured window (120), not the hardcoded 60.
        assert len(expire_calls) == 1, "pipeline.expire() must be called exactly once"
        key_used, seconds_used = expire_calls[0]
        assert seconds_used == 120, f"EXPIRE must use configured window 120, but got {seconds_used}"
        # The Redis key must embed the window value for observability.
        assert "120" in key_used, (
            f"Redis key must embed window '120' for observability, got key: {key_used}"
        )

    def test_redis_hit_key_format_uses_default_60(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With default window (60), Redis key must embed '60'."""
        _set_minimal_dev_env(monkeypatch)
        _clear_settings()

        expire_calls: list[tuple[str, int]] = []

        class _MockPipeline:
            def incr(self, key: str) -> None:
                pass

            def expire(self, key: str, seconds: int) -> None:
                expire_calls.append((key, seconds))

            def execute(self) -> list[int]:
                return [1]

            def __enter__(self) -> _MockPipeline:
                return self

            def __exit__(self, *args: object) -> None:
                pass

        class _MockRedis:
            def pipeline(self) -> _MockPipeline:
                return _MockPipeline()

        from synth_engine.bootstrapper.dependencies.rate_limit_backend import _redis_hit

        _redis_hit(
            _MockRedis(),  # type: ignore[arg-type]
            "10/minute",
            "op:some-operator",
        )

        assert len(expire_calls) == 1
        key_used, seconds_used = expire_calls[0]
        assert seconds_used == 60, f"Default window must be 60, got {seconds_used}"
        assert "60" in key_used, f"Key must embed '60', got: {key_used}"
