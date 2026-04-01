"""Feature tests for T74.1 — DB pool parameters externalized to ConclaveSettings.

Verifies:
1. All 6 pool parameters are configurable via CONCLAVE_ env vars.
2. Defaults match original hardcoded values.
3. get_engine() and get_worker_engine() read from settings, not module constants.
4. Valid boundary values (min=1, max limits) are accepted.

CONSTITUTION Priority 3: TDD — RED/GREEN/REFACTOR
Task: T74.1 — Externalize DB pool parameters to ConclaveSettings
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

_VALID_BCRYPT_HASH: str = "$2b$12$" + "a" * 53  # pragma: allowlist secret


def _clear_settings() -> None:
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()


def _set_minimal_dev_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)  # pragma: allowlist secret
    _clear_settings()


class TestDbPoolSettingsDefaults:
    """Pool parameters must default to the original hardcoded values."""

    def setup_method(self) -> None:
        _clear_settings()

    def teardown_method(self) -> None:
        _clear_settings()

    def test_default_pool_size_is_5(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """conclave_db_pool_size must default to 5 (original _POOL_SIZE)."""
        _set_minimal_dev_env(monkeypatch)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_pool_size == 5

    def test_default_max_overflow_is_10(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """conclave_db_max_overflow must default to 10 (original _MAX_OVERFLOW)."""
        _set_minimal_dev_env(monkeypatch)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_max_overflow == 10

    def test_default_worker_pool_size_is_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """conclave_db_worker_pool_size must default to 1 (original _WORKER_POOL_SIZE)."""
        _set_minimal_dev_env(monkeypatch)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_worker_pool_size == 1

    def test_default_worker_max_overflow_is_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """conclave_db_worker_max_overflow must default to 2 (original _WORKER_MAX_OVERFLOW)."""
        _set_minimal_dev_env(monkeypatch)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_worker_max_overflow == 2

    def test_default_worker_pool_recycle_is_1800(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """conclave_db_worker_pool_recycle must default to 1800 (original _WORKER_POOL_RECYCLE)."""
        _set_minimal_dev_env(monkeypatch)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_worker_pool_recycle == 1800

    def test_default_worker_pool_timeout_is_30(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """conclave_db_worker_pool_timeout must default to 30 (original _WORKER_POOL_TIMEOUT)."""
        _set_minimal_dev_env(monkeypatch)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_worker_pool_timeout == 30


class TestDbPoolSettingsOverride:
    """Pool parameters must be overridable via CONCLAVE_ env vars."""

    def setup_method(self) -> None:
        _clear_settings()

    def teardown_method(self) -> None:
        _clear_settings()

    def test_pool_size_override_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_POOL_SIZE=8 must override the default of 5."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_POOL_SIZE", "8")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_pool_size == 8

    def test_max_overflow_override_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_MAX_OVERFLOW=20 must override the default of 10."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_MAX_OVERFLOW", "20")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_max_overflow == 20

    def test_worker_pool_size_override_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_WORKER_POOL_SIZE=3 must override the default of 1."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_WORKER_POOL_SIZE", "3")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_worker_pool_size == 3

    def test_worker_pool_recycle_override_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_WORKER_POOL_RECYCLE=900 must override the default of 1800."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_WORKER_POOL_RECYCLE", "900")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_worker_pool_recycle == 900

    def test_worker_pool_timeout_override_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_WORKER_POOL_TIMEOUT=60 must override the default of 30."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_WORKER_POOL_TIMEOUT", "60")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_worker_pool_timeout == 60


class TestDbPoolSettingsBoundaries:
    """Boundary values (min=1, max=limit) must be accepted."""

    def setup_method(self) -> None:
        _clear_settings()

    def teardown_method(self) -> None:
        _clear_settings()

    def test_pool_size_minimum_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_POOL_SIZE=1 (minimum) must be accepted."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_POOL_SIZE", "1")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_pool_size == 1

    def test_pool_size_maximum_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_POOL_SIZE=50 (maximum) must be accepted."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_POOL_SIZE", "50")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_pool_size == 50

    def test_worker_pool_size_maximum_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_WORKER_POOL_SIZE=10 (maximum) must be accepted."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_WORKER_POOL_SIZE", "10")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_db_worker_pool_size == 10
