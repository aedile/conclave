"""Negative/attack tests for CONCLAVE_ prefixed env var aliases (T63.2).

Attack tests written FIRST per Rule 22 (Attack-First TDD).

Attack surface:
- CONCLAVE_DATABASE_URL accepted as alias for DATABASE_URL
- CONCLAVE_AUDIT_KEY accepted as alias for AUDIT_KEY
- CONCLAVE_MASKING_SALT accepted as alias for MASKING_SALT
- CONCLAVE_JWT_SECRET_KEY accepted as alias for JWT_SECRET_KEY
- When both old and new name are set simultaneously, behaviour must be deterministic
- _warn_unrecognized_conclave_env_vars must NOT warn for recognised alias names
- Old names must continue to work (backward-compatibility attack: deployers should not
  be silently broken by this change)
- An alias value must pass the production validator (alias and non-alias are equivalent)

CONSTITUTION Priority 0: Security — aliases must not bypass production validation
CONSTITUTION Priority 3: TDD — attack tests FIRST per Rule 22
Task: T63.2 — Unify Environment Variable Naming (CONCLAVE_ prefixed aliases)
"""

from __future__ import annotations

import logging

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

#: Structurally valid bcrypt hash for tests (60 chars, starts with $2b$).
_VALID_BCRYPT_HASH: str = "$2b$12$" + "a" * 53  # pragma: allowlist secret


def _set_full_production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set all production-required env vars to valid values.

    Provides the minimal production configuration so tests can verify
    alias behaviour without triggering unrelated production-required
    field validation errors.

    Args:
        monkeypatch: pytest monkeypatch fixture for environment manipulation.
    """
    monkeypatch.setenv(
        "CONCLAVE_DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db"
    )  # pragma: allowlist secret
    monkeypatch.setenv("CONCLAVE_AUDIT_KEY", "aa" * 32)  # pragma: allowlist secret
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "bb" * 32)  # pragma: allowlist secret
    monkeypatch.setenv("CONCLAVE_MASKING_SALT", "cc" * 16)  # pragma: allowlist secret
    monkeypatch.setenv(  # pragma: allowlist secret
        "CONCLAVE_JWT_SECRET_KEY", "supersecretjwtkey-for-production-tests"
    )
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", _VALID_BCRYPT_HASH)
    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUDIT_KEY", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ENV", raising=False)


# ---------------------------------------------------------------------------
# ATTACK: Old names must remain fully functional (backward-compatibility)
# ---------------------------------------------------------------------------


def test_old_database_url_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL (without CONCLAVE_ prefix) must continue to work.

    Operators who have not migrated to the new CONCLAVE_DATABASE_URL alias
    must not be broken.  Removing the old name without deprecation would be
    a breaking change for all existing deployments.

    Arrange: set DATABASE_URL (old name), not CONCLAVE_DATABASE_URL.
    Act: construct ConclaveSettings in development mode.
    Assert: database_url matches the value set via DATABASE_URL.
    """
    from synth_engine.shared.settings import ConclaveSettings

    monkeypatch.setenv("DATABASE_URL", "sqlite:///old-name.db")
    monkeypatch.delenv("CONCLAVE_DATABASE_URL", raising=False)
    monkeypatch.setenv("CONCLAVE_ENV", "development")

    s = ConclaveSettings()
    assert s.database_url == "sqlite:///old-name.db", (
        f"Old DATABASE_URL must still be accepted; got: {s.database_url!r}"
    )


def test_old_audit_key_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUDIT_KEY (without CONCLAVE_ prefix) must continue to work.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    from synth_engine.shared.settings import ConclaveSettings

    monkeypatch.setenv("AUDIT_KEY", "bb" * 32)  # pragma: allowlist secret
    monkeypatch.delenv("CONCLAVE_AUDIT_KEY", raising=False)
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")

    s = ConclaveSettings()
    assert s.audit_key.get_secret_value() == "bb" * 32, (
        "Old AUDIT_KEY must still be accepted"
    )


# ---------------------------------------------------------------------------
# ATTACK: New CONCLAVE_ aliases must be accepted (primary feature)
# ---------------------------------------------------------------------------


def test_conclave_database_url_alias_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """CONCLAVE_DATABASE_URL must be accepted as alias for DATABASE_URL.

    Arrange: set CONCLAVE_DATABASE_URL; unset DATABASE_URL; use dev mode.
    Act: construct ConclaveSettings.
    Assert: database_url matches CONCLAVE_DATABASE_URL value.
    """
    from synth_engine.shared.settings import ConclaveSettings

    monkeypatch.setenv("CONCLAVE_DATABASE_URL", "sqlite:///conclave-alias.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)  # pragma: allowlist secret

    s = ConclaveSettings()
    assert s.database_url == "sqlite:///conclave-alias.db", (
        f"CONCLAVE_DATABASE_URL alias must be accepted; got: {s.database_url!r}"
    )


def test_conclave_audit_key_alias_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """CONCLAVE_AUDIT_KEY must be accepted as alias for AUDIT_KEY.

    Arrange: set CONCLAVE_AUDIT_KEY; unset AUDIT_KEY; use dev mode.
    Act: construct ConclaveSettings.
    Assert: audit_key matches CONCLAVE_AUDIT_KEY value.
    """
    from synth_engine.shared.settings import ConclaveSettings

    expected = "cc" * 32  # pragma: allowlist secret
    monkeypatch.setenv("CONCLAVE_AUDIT_KEY", expected)
    monkeypatch.delenv("AUDIT_KEY", raising=False)
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")

    s = ConclaveSettings()
    assert s.audit_key.get_secret_value() == expected, (
        f"CONCLAVE_AUDIT_KEY alias must be accepted; got: {s.audit_key!r}"
    )


def test_conclave_masking_salt_alias_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """CONCLAVE_MASKING_SALT must be accepted as alias for MASKING_SALT.

    Arrange: set CONCLAVE_MASKING_SALT; unset MASKING_SALT; use dev mode.
    Act: construct ConclaveSettings.
    Assert: masking_salt matches CONCLAVE_MASKING_SALT value.
    """
    from synth_engine.shared.settings import ConclaveSettings

    monkeypatch.setenv("CONCLAVE_MASKING_SALT", "my-masking-salt")  # pragma: allowlist secret
    monkeypatch.delenv("MASKING_SALT", raising=False)
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)  # pragma: allowlist secret

    s = ConclaveSettings()
    assert s.masking_salt is not None, (
        "masking_salt must not be None when CONCLAVE_MASKING_SALT is set"
    )
    assert s.masking_salt.get_secret_value() == "my-masking-salt", (
        f"CONCLAVE_MASKING_SALT alias must be accepted; got: {s.masking_salt!r}"
    )


def test_conclave_jwt_secret_key_alias_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """CONCLAVE_JWT_SECRET_KEY must be accepted as alias for JWT_SECRET_KEY.

    Arrange: set CONCLAVE_JWT_SECRET_KEY; unset JWT_SECRET_KEY; use dev mode.
    Act: construct ConclaveSettings.
    Assert: jwt_secret_key matches CONCLAVE_JWT_SECRET_KEY value.
    """
    from synth_engine.shared.settings import ConclaveSettings

    expected = "my-jwt-secret-key-for-alias-test"  # pragma: allowlist secret
    monkeypatch.setenv("CONCLAVE_JWT_SECRET_KEY", expected)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)  # pragma: allowlist secret

    s = ConclaveSettings()
    assert s.jwt_secret_key.get_secret_value() == expected, (
        f"CONCLAVE_JWT_SECRET_KEY alias must be accepted; got: {s.jwt_secret_key!r}"
    )


# ---------------------------------------------------------------------------
# ATTACK: Alias must pass production validation
# ---------------------------------------------------------------------------


def test_conclave_jwt_secret_key_empty_alias_fails_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty CONCLAVE_JWT_SECRET_KEY must fail production validation.

    The alias must be treated identically to the old name: an empty value
    in production mode must raise just as if JWT_SECRET_KEY was empty.
    This is the critical security invariant: aliases must not create a
    bypass path around production validation.

    Arrange: set CONCLAVE_JWT_SECRET_KEY=''; all other production fields valid.
    Act: construct ConclaveSettings with CONCLAVE_ENV=production.
    Assert: raises ValueError/ValidationError mentioning jwt_secret_key.
    """
    from pydantic import ValidationError

    from synth_engine.shared.settings import ConclaveSettings

    _set_full_production_env(monkeypatch)
    monkeypatch.setenv("CONCLAVE_JWT_SECRET_KEY", "")  # empty — must fail in production

    with pytest.raises((ValueError, ValidationError)) as exc_info:
        ConclaveSettings()

    error_text = str(exc_info.value)
    assert "jwt_secret_key" in error_text.lower() or "JWT_SECRET_KEY" in error_text, (
        f"Validation error must mention jwt_secret_key; got: {error_text!r}"
    )


def test_conclave_audit_key_empty_alias_fails_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty CONCLAVE_AUDIT_KEY must fail production validation.

    Arrange: set CONCLAVE_AUDIT_KEY=''; all other production fields valid.
    Act: construct ConclaveSettings with CONCLAVE_ENV=production.
    Assert: raises ValueError/ValidationError mentioning audit_key.
    """
    from pydantic import ValidationError

    from synth_engine.shared.settings import ConclaveSettings

    _set_full_production_env(monkeypatch)
    monkeypatch.setenv("CONCLAVE_AUDIT_KEY", "")  # empty — must fail in production

    with pytest.raises((ValueError, ValidationError)) as exc_info:
        ConclaveSettings()

    error_text = str(exc_info.value)
    assert "audit_key" in error_text.lower() or "AUDIT_KEY" in error_text, (
        f"Validation error must mention audit_key; got: {error_text!r}"
    )


# ---------------------------------------------------------------------------
# ATTACK: _warn_unrecognized_conclave_env_vars must NOT warn for known aliases
# ---------------------------------------------------------------------------


def test_known_aliases_do_not_trigger_unrecognized_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Known CONCLAVE_ aliases must not trigger the unrecognized-variable WARNING.

    CONCLAVE_DATABASE_URL, CONCLAVE_AUDIT_KEY, CONCLAVE_MASKING_SALT, and
    CONCLAVE_JWT_SECRET_KEY are now recognized fields.  Setting them must not
    trigger the typo-detection WARNING emitted by _warn_unrecognized_conclave_env_vars.

    Arrange: set all four new aliases; use development mode.
    Act: construct ConclaveSettings; capture log.
    Assert: no unrecognized-CONCLAVE warning is emitted for any of the four aliases.
    """
    from synth_engine.shared.settings import ConclaveSettings

    monkeypatch.setenv("CONCLAVE_DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("CONCLAVE_AUDIT_KEY", "aa" * 32)  # pragma: allowlist secret
    monkeypatch.setenv("CONCLAVE_MASKING_SALT", "my-salt")  # pragma: allowlist secret
    monkeypatch.setenv("CONCLAVE_JWT_SECRET_KEY", "my-jwt-key")  # pragma: allowlist secret
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUDIT_KEY", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with caplog.at_level(logging.WARNING, logger="synth_engine.shared.settings"):
        ConclaveSettings()

    warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    alias_warnings = [
        m
        for m in warning_messages
        if any(
            alias in m
            for alias in (
                "CONCLAVE_DATABASE_URL",
                "CONCLAVE_AUDIT_KEY",
                "CONCLAVE_MASKING_SALT",
                "CONCLAVE_JWT_SECRET_KEY",
            )
        )
        and ("unrecognized" in m.lower() or "Unrecognized" in m)
    ]
    assert not alias_warnings, (
        f"Known aliases must not trigger unrecognized-variable warning; "
        f"got: {alias_warnings}"
    )
