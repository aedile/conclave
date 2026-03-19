"""Unit tests for the centralized ConclaveSettings Pydantic model (T36.1).

Tests verify that:
- ``ConclaveSettings`` validates all environment variable fields.
- ``get_settings()`` returns a cached singleton.
- ``VAULT_SEAL_SALT`` is NOT read at boot (deferred to vault unseal time).
- Production mode enforces required fields via Pydantic validators.
- The model correctly parses each env var category.

CONSTITUTION Priority 0: Security — centralized config prevents silent misconfiguration
CONSTITUTION Priority 3: TDD — RED/GREEN/REFACTOR
Task: T36.1 — Centralize Configuration Into Pydantic Settings Model
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Tests: ConclaveSettings field parsing
# ---------------------------------------------------------------------------


def test_settings_parses_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.database_url is populated from DATABASE_URL env var."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.database_url == "postgresql+asyncpg://user:pass@localhost/db"


def test_settings_parses_audit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.audit_key is populated from AUDIT_KEY env var."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "bb" * 32)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.audit_key == "bb" * 32


def test_settings_parses_conclave_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.conclave_env is populated from CONCLAVE_ENV env var."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "cc" * 32)
    monkeypatch.setenv("CONCLAVE_ENV", "production")

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.conclave_env == "production"


def test_settings_defaults_conclave_env_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.conclave_env defaults to empty string when not set."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "dd" * 32)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.conclave_env == ""


def test_settings_parses_force_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.force_cpu is True when FORCE_CPU=true."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "ee" * 32)
    monkeypatch.setenv("FORCE_CPU", "true")

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.force_cpu is True


def test_settings_force_cpu_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.force_cpu defaults to False when FORCE_CPU not set."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "ff" * 32)
    monkeypatch.delenv("FORCE_CPU", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.force_cpu is False


def test_settings_parses_otel_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.otel_exporter_otlp_endpoint is populated from env var."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.otel_exporter_otlp_endpoint == "http://jaeger:4317"


def test_settings_otel_endpoint_defaults_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.otel_exporter_otlp_endpoint defaults to None when not set."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.otel_exporter_otlp_endpoint is None


def test_settings_parses_huey_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.huey_backend is populated from HUEY_BACKEND env var."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("HUEY_BACKEND", "memory")

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.huey_backend == "memory"


def test_settings_parses_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.redis_url is populated from REDIS_URL env var."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("REDIS_URL", "redis://myredis:6379/1")

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.redis_url == "redis://myredis:6379/1"


def test_settings_parses_masking_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.masking_salt is populated from MASKING_SALT env var."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MASKING_SALT", "my-secret-salt")

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.masking_salt == "my-secret-salt"


def test_settings_parses_artifact_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.artifact_signing_key is populated from ARTIFACT_SIGNING_KEY."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "ab" * 32)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.artifact_signing_key == "ab" * 32


# ---------------------------------------------------------------------------
# Tests: DATABASE_URL missing or empty
# ---------------------------------------------------------------------------


def test_settings_missing_database_url_defaults_to_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings.database_url defaults to empty string when DATABASE_URL is absent.

    The model itself does not raise on missing DATABASE_URL — runtime validation
    is handled by config_validation.validate_config() at startup.  This keeps
    the model lightweight and testable in environments where DATABASE_URL is
    not set (e.g., unit tests for non-DB code).
    """
    from synth_engine.shared.settings import ConclaveSettings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)

    s = ConclaveSettings()
    assert s.database_url == ""


def test_settings_missing_audit_key_defaults_to_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings.audit_key defaults to empty string when AUDIT_KEY is absent.

    The model itself does not raise on missing AUDIT_KEY — runtime validation
    is handled by config_validation.validate_config() at startup (all modes)
    and by _load_audit_key() when the audit logger is first created.
    """
    from synth_engine.shared.settings import ConclaveSettings

    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.delenv("AUDIT_KEY", raising=False)

    s = ConclaveSettings()
    assert s.audit_key == ""


# ---------------------------------------------------------------------------
# Tests: VAULT_SEAL_SALT is NOT read at boot (deferred)
# ---------------------------------------------------------------------------


def test_vault_seal_salt_not_on_settings_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """VAULT_SEAL_SALT must NOT be a field on ConclaveSettings.

    The vault salt is intentionally deferred — it is read only at unseal time
    (inside VaultState.unseal()), not at application boot.  Including it on
    the settings model would force it to be present at startup, breaking the
    vault's deferred security model.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.delenv("VAULT_SEAL_SALT", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    # Must construct successfully without VAULT_SEAL_SALT in environment
    s = ConclaveSettings()
    assert not hasattr(s, "vault_seal_salt"), (
        "VAULT_SEAL_SALT must NOT be a field on ConclaveSettings — "
        "it is read lazily at vault unseal time, not at boot"
    )


def test_settings_construction_succeeds_without_vault_seal_salt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings constructs successfully when VAULT_SEAL_SALT is absent.

    This verifies the deferred-vault-reads constraint from the task spec:
    the settings model must not block startup because the vault salt is absent.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.delenv("VAULT_SEAL_SALT", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    # Must not raise
    s = ConclaveSettings()
    assert s is not None


# ---------------------------------------------------------------------------
# Tests: get_settings() singleton / lru_cache
# ---------------------------------------------------------------------------


def test_get_settings_returns_same_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_settings() returns the same cached instance on repeated calls.

    The @lru_cache on get_settings() must ensure that exactly one
    ConclaveSettings instance is created per process lifecycle.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)

    # Clear any existing cache by reimporting
    import synth_engine.shared.settings as settings_module

    settings_module.get_settings.cache_clear()

    from synth_engine.shared.settings import get_settings

    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2, "get_settings() must return the same cached instance"


def test_get_settings_cache_can_be_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_settings().cache_clear() allows a fresh instance to be constructed.

    Tests and operator tooling that need fresh settings (e.g. after a monkeypatch)
    must be able to clear the cache.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    s1 = get_settings()

    monkeypatch.setenv("DATABASE_URL", "sqlite:///other.db")
    get_settings.cache_clear()
    s2 = get_settings()

    assert s1 is not s2
    assert s2.database_url == "sqlite:///other.db"


# ---------------------------------------------------------------------------
# Tests: is_production() helper
# ---------------------------------------------------------------------------


def test_is_production_via_conclave_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.is_production() returns True when CONCLAVE_ENV=production."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.delenv("ENV", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.is_production() is True


def test_is_production_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.is_production() returns True when ENV=production."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.is_production() is True


def test_is_production_false_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.is_production() returns False in development mode."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.is_production() is False


def test_is_production_false_when_neither_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.is_production() returns False when neither ENV nor CONCLAVE_ENV set."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.is_production() is False


# ---------------------------------------------------------------------------
# Tests: conclave_ssl_required field
# ---------------------------------------------------------------------------


def test_settings_ssl_required_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.conclave_ssl_required defaults to True."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.delenv("CONCLAVE_SSL_REQUIRED", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.conclave_ssl_required is True


def test_settings_ssl_required_parses_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.conclave_ssl_required is False when CONCLAVE_SSL_REQUIRED=false."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("CONCLAVE_SSL_REQUIRED", "false")

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.conclave_ssl_required is False


# ---------------------------------------------------------------------------
# Tests: huey_immediate field
# ---------------------------------------------------------------------------


def test_settings_huey_immediate_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.huey_immediate defaults to False."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.delenv("HUEY_IMMEDIATE", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.huey_immediate is False


def test_settings_huey_immediate_parses_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.huey_immediate is True when HUEY_IMMEDIATE=true."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("HUEY_IMMEDIATE", "true")

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.huey_immediate is True
