"""Negative/attack tests for production-mode-default hardening (T50.3).

These attack tests are written FIRST per Rule 22 (Attack-First TDD).

Attack surface:
- No CONCLAVE_ENV set, no JWT_SECRET_KEY → must fail (production enforced by default)
- No CONCLAVE_ENV, no JWT_SECRET_KEY, no MASKING_SALT → startup fails (not dev by default)
- CONCLAVE_ENV=production, missing JWT_SECRET_KEY → startup fails
- /security/shred accessible without auth → endpoint must require authentication
- /security/keys/rotate accessible without auth → endpoint must require authentication
- Operator relying on old dev-default: no CONCLAVE_ENV set → now gets startup failure
- Old dev-mode fallback: CONCLAVE_ENV unset → NOT dev mode any more

CONSTITUTION Priority 0: Security — secure-by-default, auth cannot be silently bypassed
CONSTITUTION Priority 3: TDD — attack tests FIRST per Rule 22
Task: T50.3 — Default to Production Mode
Advisory: ADV-P47-04 — Remove security routes from AUTH_EXEMPT_PATHS
"""

from __future__ import annotations

import logging

import pytest

pytestmark = pytest.mark.unit

_VALID_BCRYPT_HASH = "$2b$12$" + "a" * 53  # 60 chars total — valid structural format


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _set_all_production_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set ALL production-required env vars so ConclaveSettings() succeeds in production mode.

    After T63.1, the model_validator enforces all required fields at construction
    time when conclave_env='production'. Tests that remove CONCLAVE_ENV (so it
    defaults to 'production') must also provide all required fields to avoid
    ValidationError.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
    """
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")  # pragma: allowlist secret
    monkeypatch.setenv(
        "ARTIFACT_SIGNING_KEY",
        "cafecafecafecafecafecafecafecafe",  # pragma: allowlist secret
    )
    monkeypatch.setenv(
        "MASKING_SALT",
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",  # pragma: allowlist secret
    )
    monkeypatch.setenv("JWT_SECRET_KEY", "c" * 64)  # pragma: allowlist secret
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", _VALID_BCRYPT_HASH)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)


def _minimal_prod_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set minimum required vars for production mode (excluding auth-specific ones).

    Used to isolate which variable triggers the failure under test.
    """
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")  # pragma: allowlist secret
    monkeypatch.setenv(
        "ARTIFACT_SIGNING_KEY",
        "cafecafecafecafecafecafecafecafe",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    monkeypatch.setenv(
        "MASKING_SALT",
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",  # pragma: allowlist secret
    )
    # Remove both env var alternatives so default kicks in
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)


# ---------------------------------------------------------------------------
# Attack: unset CONCLAVE_ENV defaults to production, not dev
# ---------------------------------------------------------------------------


def test_unset_conclave_env_is_not_development(monkeypatch: pytest.MonkeyPatch) -> None:
    """When CONCLAVE_ENV is unset, the system must NOT default to dev mode.

    The old default of '' (empty string) caused is_production() to return False,
    silently booting in dev mode with auth disabled. The new default must be
    'production' so that a bare deployment with no env config fails fast.
    """
    from synth_engine.shared.settings import ConclaveSettings

    # T63.1: provide all production-required fields so the validator does not raise.
    # The key assertion is that conclave_env defaults to 'production' (not '').
    _set_all_production_required(monkeypatch)

    s = ConclaveSettings()
    assert s.is_production() is True, (
        "When CONCLAVE_ENV is unset, is_production() must return True — "
        "the system defaults to production mode, not development mode"
    )
    assert s.is_production()


def test_unset_conclave_env_conclave_env_field_is_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONCLAVE_ENV field must default to 'production', not empty string.

    A bare deployment with no CONCLAVE_ENV must boot in production mode.
    Defaulting to '' was the vulnerability: it allowed unauthenticated access.
    """
    from synth_engine.shared.settings import ConclaveSettings

    # T63.1: provide all production-required fields so the validator does not raise.
    # The key assertion is that conclave_env defaults to 'production' (not '').
    _set_all_production_required(monkeypatch)

    s = ConclaveSettings()
    assert s.conclave_env == "production", (
        "conclave_env must default to 'production' — "
        "empty string default was a security vulnerability (T50.3)"
    )


# ---------------------------------------------------------------------------
# Attack: no env vars set + no JWT_SECRET_KEY → startup must fail
# ---------------------------------------------------------------------------


def test_no_conclave_env_no_jwt_secret_startup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset CONCLAVE_ENV + no JWT_SECRET_KEY → startup must fail.

    This is the exact attack scenario T50.3 addresses: a fresh deployment with
    no .env file boots silently in dev mode (old behaviour) with auth disabled.
    New behaviour: the system defaults to production, which requires JWT_SECRET_KEY.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    _minimal_prod_base(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "")
    # Both CONCLAVE_ENV and ENV are unset — defaults to production

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    error_message = str(exc_info.value)
    # Either JWT or credentials hash error proves production enforcement is active
    assert "JWT_SECRET_KEY" in error_message or "OPERATOR_CREDENTIALS_HASH" in error_message, (
        "Startup must fail with a config error when JWT auth vars are absent — "
        f"got: {error_message}"
    )


def test_no_env_vars_at_all_startup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absolutely bare deployment (no env vars) must fail at startup.

    Old behaviour: defaults to dev mode → auth bypassed silently.
    New behaviour: defaults to production → fails fast on missing secrets.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    # Clear everything that the conftest autouse fixture sets
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_KEY", "")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)

    with pytest.raises(SystemExit):
        validate_config()


# ---------------------------------------------------------------------------
# Attack: CONCLAVE_ENV=production, no JWT_SECRET_KEY → startup must fail
# ---------------------------------------------------------------------------


def test_production_mode_no_jwt_secret_startup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit CONCLAVE_ENV=production + empty JWT_SECRET_KEY → SystemExit.

    This is an existing behaviour test — verifying production enforcement
    through the explicit CONCLAVE_ENV path is unchanged by T50.3.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    _minimal_prod_base(monkeypatch)
    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "$2b$12$" + "a" * 53)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "JWT_SECRET_KEY" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Attack: /security/shred must NOT be in AUTH_EXEMPT_PATHS (ADV-P47-04)
# ---------------------------------------------------------------------------


def test_security_shred_not_in_auth_exempt_paths() -> None:
    """/security/shred must NOT be in AUTH_EXEMPT_PATHS.

    ADV-P47-04: /security/shred and /security/keys/rotate are security-critical
    endpoints that bypass authentication when present in AUTH_EXEMPT_PATHS.
    An attacker with network access could call /security/shred without credentials,
    destroying all encryption keys — a data-destruction attack vector.
    """
    from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

    assert "/security/shred" not in AUTH_EXEMPT_PATHS, (
        "/security/shred must NOT be in AUTH_EXEMPT_PATHS — "
        "this endpoint destroys encryption keys and must require authentication"
    )


def test_security_keys_rotate_not_in_auth_exempt_paths() -> None:
    """/security/keys/rotate must NOT be in AUTH_EXEMPT_PATHS.

    ADV-P47-04: An unauthenticated key rotation is a privilege escalation attack.
    Rotations must be performed only by authenticated operators.
    """
    from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

    assert "/security/keys/rotate" not in AUTH_EXEMPT_PATHS, (
        "/security/keys/rotate must NOT be in AUTH_EXEMPT_PATHS — "
        "key rotation is a security operation that must require authentication"
    )


def test_security_shred_not_in_common_infra_exempt_paths() -> None:
    """/security/shred must NOT be in COMMON_INFRA_EXEMPT_PATHS.

    Removing it from COMMON_INFRA_EXEMPT_PATHS propagates to all consumers:
    vault.py, licensing.py, and auth.py all compose from this base set.
    """
    from synth_engine.bootstrapper.dependencies._exempt_paths import COMMON_INFRA_EXEMPT_PATHS

    assert "/security/shred" not in COMMON_INFRA_EXEMPT_PATHS, (
        "/security/shred must NOT be in COMMON_INFRA_EXEMPT_PATHS"
    )


def test_security_keys_rotate_not_in_common_infra_exempt_paths() -> None:
    """/security/keys/rotate must NOT be in COMMON_INFRA_EXEMPT_PATHS."""
    from synth_engine.bootstrapper.dependencies._exempt_paths import COMMON_INFRA_EXEMPT_PATHS

    assert "/security/keys/rotate" not in COMMON_INFRA_EXEMPT_PATHS, (
        "/security/keys/rotate must NOT be in COMMON_INFRA_EXEMPT_PATHS"
    )


# ---------------------------------------------------------------------------
# Attack: upgrade path — operator relying on dev-default now gets failure
# ---------------------------------------------------------------------------


def test_operator_relying_on_old_dev_default_now_gets_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator who never set CONCLAVE_ENV, relying on old dev-default, now gets failure.

    This is the breaking-change attack mitigation: an operator who deployed
    without a CONCLAVE_ENV and relied on it defaulting to dev mode (auth disabled)
    now receives a clear startup failure rather than silently running unsecured.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    # Simulate an operator who configured the minimum they thought was needed
    # for development — no CONCLAVE_ENV, base vars only
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")  # pragma: allowlist secret
    # They never set CONCLAVE_ENV (old default: '' → dev mode, auth skipped)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    # They never set JWT_SECRET_KEY because they thought they were in dev mode
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "")
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)
    # In production mode, ARTIFACT_SIGNING_KEY and MASKING_SALT are also required
    monkeypatch.setenv(
        "ARTIFACT_SIGNING_KEY",
        "cafecafecafecafecafecafecafecafe",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    monkeypatch.setenv(
        "MASKING_SALT",
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",  # pragma: allowlist secret
    )  # pragma: allowlist secret

    with pytest.raises(SystemExit), caplog_context():
        validate_config()


# Utility to avoid importing caplog at module level in this attack-test context
def caplog_context() -> None:  # type: ignore[return]
    """No-op context manager — SystemExit raised before any caplog needed."""
    import contextlib

    return contextlib.nullcontext()


def test_old_dev_default_upgrade_path_requires_explicit_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev mode now requires explicit CONCLAVE_ENV=development.

    After T50.3, dev mode is not the default. Developers must explicitly opt in.
    This test verifies that setting CONCLAVE_ENV=development does allow dev boot.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")  # pragma: allowlist secret
    monkeypatch.setenv("CONCLAVE_ENV", "development")  # explicit dev mode
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)

    # Must NOT raise — explicit development mode is valid
    result = validate_config()
    assert result is None, (
        "validate_config() must succeed when CONCLAVE_ENV=development is explicitly set"
    )
    assert str(result) == "None"


# ---------------------------------------------------------------------------
# Attack: dev mode warning must be emitted — silence is dangerous
# ---------------------------------------------------------------------------


def test_development_mode_emits_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dev mode must emit a WARNING so operators know auth is disabled.

    A silent dev-mode boot is dangerous in containerized environments where
    the port may be inadvertently exposed. The WARNING gives operators a
    visible signal that authentication is disabled.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")  # pragma: allowlist secret
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)

    with caplog.at_level(logging.WARNING, logger="synth_engine.bootstrapper.config_validation"):
        validate_config()

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("development" in msg.lower() for msg in warning_messages), (
        "A WARNING must be emitted when running in development mode. "
        f"Got warnings: {warning_messages}"
    )
    assert any(
        "authentication" in msg.lower() or "auth" in msg.lower() for msg in warning_messages
    ), (
        "The development-mode warning must mention that authentication is disabled. "
        f"Got warnings: {warning_messages}"
    )
