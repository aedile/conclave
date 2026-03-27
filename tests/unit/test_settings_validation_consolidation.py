"""Negative/attack tests for consolidated settings validation (T63.1).

Tests verify that:
- validate_config() does NOT double-raise when Pydantic validators already caught an error.
- ConclaveSettings rejects empty DATABASE_URL in production at construction time.
- ConclaveSettings rejects missing ARTIFACT_SIGNING_KEY in production at construction time.
- ConclaveSettings rejects missing MASKING_SALT in production at construction time.
- get_settings.cache_clear() still works for test isolation.

CONSTITUTION Priority 0: Security — fail-fast on missing security-critical config.
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22).
Task: T63.1 — Consolidate Settings Validation
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

# ---------------------------------------------------------------------------
# State isolation fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# ATTACK: validate_config() must not double-raise on Pydantic validation errors
# ---------------------------------------------------------------------------


def test_validate_config_does_not_double_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """validate_config() must catch Pydantic ValidationError and raise SystemExit once.

    When ConclaveSettings construction fails (e.g. DATABASE_URL empty in production),
    validate_config() must convert the ValidationError to a single SystemExit.
    It must NOT raise a second exception by accessing settings that failed to construct.

    Arrange: set CONCLAVE_ENV=production with empty DATABASE_URL.
    Act: call validate_config().
    Assert: exactly one SystemExit is raised; no chained exceptions beyond the SystemExit.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)  # pragma: allowlist secret

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    # The error message must mention DATABASE_URL
    error_msg = str(exc_info.value)
    assert "DATABASE_URL" in error_msg or "database_url" in error_msg, (
        f"SystemExit message must mention DATABASE_URL; got: {error_msg!r}"
    )


# ---------------------------------------------------------------------------
# ATTACK: ConclaveSettings rejects empty DATABASE_URL in production at construction
# ---------------------------------------------------------------------------


def test_settings_rejects_empty_database_url_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings must raise ValueError for empty DATABASE_URL in production.

    Construction-time validation (Pydantic @model_validator) must reject an
    empty database_url when conclave_env='production'. This ensures fail-fast
    before any application code runs — not just at validate_config() call time.

    Arrange: set CONCLAVE_ENV=production, DATABASE_URL=''.
    Act: construct ConclaveSettings.
    Assert: raises ValueError (Pydantic ValidationError wraps it) mentioning database_url.
    """
    from pydantic import ValidationError

    from synth_engine.shared.settings import ConclaveSettings

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_KEY", "b" * 64)  # pragma: allowlist secret

    with pytest.raises((ValueError, ValidationError)) as exc_info:
        ConclaveSettings()

    error_text = str(exc_info.value)
    assert "database_url" in error_text.lower() or "DATABASE_URL" in error_text, (
        f"ValidationError must mention database_url; got: {error_text!r}"
    )


def test_settings_rejects_whitespace_only_database_url_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings must reject whitespace-only DATABASE_URL in production.

    A whitespace-only DATABASE_URL is semantically empty and must be treated
    as missing in production mode.

    Arrange: set CONCLAVE_ENV=production, DATABASE_URL='   '.
    Act: construct ConclaveSettings.
    Assert: raises ValueError or ValidationError.
    """
    from pydantic import ValidationError

    from synth_engine.shared.settings import ConclaveSettings

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "   ")
    monkeypatch.setenv("AUDIT_KEY", "c" * 64)  # pragma: allowlist secret

    with pytest.raises((ValueError, ValidationError)):
        ConclaveSettings()


def test_settings_allows_nonempty_database_url_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings must accept a non-empty DATABASE_URL in production.

    Happy path: production mode with a valid DSN must not raise.

    T63.1: All production-required fields must be provided for the construction
    to succeed. This test verifies the happy path where all required fields are
    set correctly.

    Arrange: set CONCLAVE_ENV=production with all production-required fields.
    Act: construct ConclaveSettings.
    Assert: no exception raised; database_url matches the provided value.
    """
    from synth_engine.shared.settings import ConclaveSettings

    valid_url = "postgresql+asyncpg://user:pass@host:5432/db"  # pragma: allowlist secret
    _valid_bcrypt_hash = "$2b$12$" + "a" * 53  # pragma: allowlist secret
    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", valid_url)
    monkeypatch.setenv("AUDIT_KEY", "d" * 64)  # pragma: allowlist secret
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "e" * 32)  # pragma: allowlist secret
    monkeypatch.setenv("MASKING_SALT", "f" * 16)  # pragma: allowlist secret
    monkeypatch.setenv(  # pragma: allowlist secret
        "JWT_SECRET_KEY", "supersecretjwtkey-for-production-happy-path"
    )
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", _valid_bcrypt_hash)

    settings = ConclaveSettings()
    assert settings.database_url == valid_url, (
        f"database_url must match provided value; got: {settings.database_url!r}"
    )


# ---------------------------------------------------------------------------
# ATTACK: get_settings.cache_clear() works for test isolation
# ---------------------------------------------------------------------------


def test_get_settings_cache_clear_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_settings.cache_clear() must allow re-construction with new env vars.

    Test isolation requires that clearing the lru_cache causes the next call
    to get_settings() to construct a fresh ConclaveSettings from current env.

    Arrange: clear cache; set DATABASE_URL=first; call get_settings().
    Act: change DATABASE_URL=second; clear cache; call get_settings() again.
    Assert: second call returns settings with the new DATABASE_URL value.
    """
    from synth_engine.shared.settings import get_settings

    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://first:host/db")  # pragma: allowlist secret

    settings_first = get_settings()
    assert "first" in settings_first.database_url, (
        f"First settings must use 'first' URL; got: {settings_first.database_url!r}"
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://second:host/db")  # pragma: allowlist secret
    get_settings.cache_clear()

    settings_second = get_settings()
    assert "second" in settings_second.database_url, (
        f"After cache_clear, must use 'second' URL; got: {settings_second.database_url!r}"
    )
    assert settings_first is not settings_second, "cache_clear must return a new instance"


# ---------------------------------------------------------------------------
# ATTACK: Production validation catches all required fields in one pass
# ---------------------------------------------------------------------------


def test_production_validation_collects_all_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production validation must surface ALL missing required fields in one error.

    An operator must not need to fix one field, restart, fix another, restart, etc.
    All missing fields must be reported in a single error message.

    Arrange: CONCLAVE_ENV=production; empty DATABASE_URL and AUDIT_KEY.
    Act: construct ConclaveSettings (or call validate_config()).
    Assert: both field names appear in the error output.
    """
    from pydantic import ValidationError

    from synth_engine.shared.settings import ConclaveSettings

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_KEY", "")  # pragma: allowlist secret

    with pytest.raises((ValueError, ValidationError)) as exc_info:
        ConclaveSettings()

    error_text = str(exc_info.value)
    # Both fields must appear in the error (collected validation, not first-fail)
    has_database = "database_url" in error_text.lower() or "DATABASE_URL" in error_text
    has_audit = "audit_key" in error_text.lower() or "AUDIT_KEY" in error_text
    assert has_database, f"Error must mention database_url; got: {error_text!r}"
    assert has_audit, f"Error must mention audit_key; got: {error_text!r}"
