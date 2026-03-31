"""Negative / attack tests for Phase 74 — Configuration Externalization (P74).

These tests must fail RED before implementation and pass GREEN after.

Covers:
- DB pool parameters: zero / negative / oversized values rejected at settings load.
- Rate limit window: zero / negative rejected at settings load.
- SecretStr fields not exposed in repr after settings extraction.
- RateLimitSettings sub-model added to settings_models.py includes window field.

CONSTITUTION Priority 0: Security — fail-fast on invalid pool/window config.
Rule 22 — Attack tests before feature tests (mandatory).
Task: T74.1 — Externalize DB pool parameters to ConclaveSettings
Task: T74.2 — Wire rate limit window to ConclaveSettings
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_BCRYPT_HASH: str = "$2b$12$" + "a" * 53  # pragma: allowlist secret


def _set_minimal_dev_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set minimal env vars for development-mode settings construction."""
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)  # pragma: allowlist secret
    # Clear settings singleton so changes are picked up.
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# T74.1: DB pool parameter validation — attack tests
# ---------------------------------------------------------------------------


class TestDbPoolParameterValidation:
    """Pool size zero/negative/oversized values must be rejected at settings load.

    Security rationale: pool_size=0 causes pool exhaustion (all connections
    blocked immediately); negative values are semantically invalid and may
    trigger driver bugs; oversized values exceed PgBouncer limits and can
    exhaust PostgreSQL server connections.
    """

    def setup_method(self) -> None:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

    def teardown_method(self) -> None:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

    def test_db_pool_size_zero_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_POOL_SIZE=0 must be rejected at settings load."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_POOL_SIZE", "0")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_db_pool_size_negative_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_POOL_SIZE=-1 must be rejected at settings load."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_POOL_SIZE", "-1")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_db_pool_size_over_limit_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_POOL_SIZE=51 must be rejected (gt=0, le=50)."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_POOL_SIZE", "51")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_db_max_overflow_zero_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_MAX_OVERFLOW=0 must be rejected (gt=0)."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_MAX_OVERFLOW", "0")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_db_worker_pool_size_zero_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_WORKER_POOL_SIZE=0 must be rejected (gt=0)."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_WORKER_POOL_SIZE", "0")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_db_worker_pool_size_over_limit_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONCLAVE_DB_WORKER_POOL_SIZE=11 must be rejected (gt=0, le=10)."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_WORKER_POOL_SIZE", "11")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_db_worker_max_overflow_zero_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_WORKER_MAX_OVERFLOW=0 must be rejected (gt=0)."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_WORKER_MAX_OVERFLOW", "0")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_db_worker_pool_recycle_zero_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_WORKER_POOL_RECYCLE=0 must be rejected (gt=0)."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_WORKER_POOL_RECYCLE", "0")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_db_worker_pool_timeout_zero_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_DB_WORKER_POOL_TIMEOUT=0 must be rejected (gt=0)."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_DB_WORKER_POOL_TIMEOUT", "0")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()


# ---------------------------------------------------------------------------
# T74.2: Rate limit window validation — attack tests
# ---------------------------------------------------------------------------


class TestRateLimitWindowValidation:
    """rate_limit_window_seconds zero/negative must be rejected at settings load.

    Security rationale: window=0 would make the Redis key expire immediately,
    defeating rate limiting entirely (bypass attack).  Negative values are
    semantically invalid.  Upper bound le=3600 prevents accidental 24-hour
    windows that disable effective rate limiting.
    """

    def setup_method(self) -> None:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

    def teardown_method(self) -> None:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

    def test_rate_limit_window_zero_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_RATE_LIMIT_WINDOW_SECONDS=0 must be rejected (gt=0)."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_RATE_LIMIT_WINDOW_SECONDS", "0")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_rate_limit_window_negative_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_RATE_LIMIT_WINDOW_SECONDS=-1 must be rejected (gt=0)."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_RATE_LIMIT_WINDOW_SECONDS", "-1")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_rate_limit_window_over_limit_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCLAVE_RATE_LIMIT_WINDOW_SECONDS=3601 must be rejected (le=3600)."""
        _set_minimal_dev_env(monkeypatch)
        monkeypatch.setenv("CONCLAVE_RATE_LIMIT_WINDOW_SECONDS", "3601")
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()


# ---------------------------------------------------------------------------
# Security: SecretStr fields must not appear in repr after extraction
# ---------------------------------------------------------------------------


class TestSecretStrNotExposedInRepr:
    """SecretStr fields must not leak raw values in repr() after T74 extraction.

    This test is a regression guard: T74.3 decomposes settings.py. Any
    accidental str conversion of a SecretStr field would expose key material
    in logs or error messages.
    """

    def setup_method(self) -> None:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

    def teardown_method(self) -> None:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

    def test_audit_key_not_in_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """repr(settings) must not contain raw audit_key value."""
        _set_minimal_dev_env(monkeypatch)
        raw_key = "deadbeef" * 8  # pragma: allowlist secret
        monkeypatch.setenv("AUDIT_KEY", raw_key)  # pragma: allowlist secret

        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        settings_repr = repr(s)
        assert raw_key not in settings_repr, (
            f"audit_key raw value must not appear in repr(settings): {settings_repr[:200]}"
        )

    def test_jwt_secret_key_not_in_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """repr(settings) must not contain raw jwt_secret_key value."""
        _set_minimal_dev_env(monkeypatch)
        raw_key = "my-super-secret-jwt-key-value-xyz"  # pragma: allowlist secret
        monkeypatch.setenv("JWT_SECRET_KEY", raw_key)  # pragma: allowlist secret

        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        settings_repr = repr(s)
        assert raw_key not in settings_repr, (
            "jwt_secret_key raw value must not appear in repr(settings)"
        )

    def test_masking_salt_not_in_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """repr(settings) must not contain raw masking_salt value."""
        _set_minimal_dev_env(monkeypatch)
        raw_salt = "unique-masking-salt-never-expose"  # pragma: allowlist secret
        monkeypatch.setenv("MASKING_SALT", raw_salt)  # pragma: allowlist secret

        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        settings_repr = repr(s)
        assert raw_salt not in settings_repr, (
            "masking_salt raw value must not appear in repr(settings)"
        )


# ---------------------------------------------------------------------------
# T74.3 regression: validate_production_required_fields still works after
# settings.py decomposition
# ---------------------------------------------------------------------------


class TestProductionValidationSurvivesDecomposition:
    """Production mode required-field validation must survive T74.3 decomposition."""

    def setup_method(self) -> None:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

    def teardown_method(self) -> None:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

    def test_production_mode_requires_audit_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing AUDIT_KEY in production mode must raise ValueError/ValidationError."""
        monkeypatch.setenv("CONCLAVE_ENV", "production")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.delenv("AUDIT_KEY", raising=False)
        monkeypatch.delenv("CONCLAVE_AUDIT_KEY", raising=False)

        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_production_mode_requires_database_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing DATABASE_URL in production mode must raise ValueError/ValidationError."""
        monkeypatch.setenv("CONCLAVE_ENV", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("CONCLAVE_DATABASE_URL", raising=False)

        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()
