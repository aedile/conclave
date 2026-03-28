"""Feature tests for production-mode-default hardening (T50.3).

These tests are written AFTER attack tests per Rule 22 (Attack-First TDD).
They verify happy-path and backward-compatibility behaviours.

CONSTITUTION Priority 3: TDD — feature tests after attack tests
Task: T50.3 — Default to Production Mode
Advisory: ADV-P47-04 — Remove security routes from AUTH_EXEMPT_PATHS
Fix: T63.1 — Provide all production-required fields in tests that construct
             ConclaveSettings in production mode.  After T63.1, the
             model_validator enforces artifact_signing_key, masking_salt,
             jwt_secret_key, and operator_credentials_hash at construction
             time rather than deferring to validate_config().
"""

from __future__ import annotations

import logging

import pytest

pytestmark = pytest.mark.unit

_VALID_BCRYPT_HASH = "$2b$12$" + "a" * 53  # 60 chars total — valid structural format

# ---------------------------------------------------------------------------
# Helper: inject all production-required fields
# ---------------------------------------------------------------------------
# After T63.1, Pydantic model_validators enforce production-required fields at
# construction time.  Any test that constructs ConclaveSettings in production
# mode (conclave_env='production') must supply these four secrets or the
# validator raises ValidationError before the test can make its assertion.
#
# Tests that only care about conclave_env / is_production() use this helper to
# satisfy the validator without obscuring the test intent.
# ---------------------------------------------------------------------------

_PRODUCTION_REQUIRED_FIELDS: dict[str, str] = {
    "DATABASE_URL": "sqlite:///test.db",
    "AUDIT_KEY": "a" * 64,  # 32 bytes hex-encoded
    "ARTIFACT_SIGNING_KEY": "b" * 64,
    "MASKING_SALT": "test-salt-value",
    "JWT_SECRET_KEY": "c" * 64,
    "OPERATOR_CREDENTIALS_HASH": _VALID_BCRYPT_HASH,
}


def _set_production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject all production-required env vars so the model_validator passes.

    Call this before constructing ``ConclaveSettings()`` whenever the test
    forces production mode (i.e. deletes CONCLAVE_ENV or sets it to
    'production').  Remove ARTIFACT_SIGNING_KEYS and
    ARTIFACT_SIGNING_KEY_ACTIVE to avoid triggering the multi-key validator.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
    """
    for key, value in _PRODUCTION_REQUIRED_FIELDS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)


# ---------------------------------------------------------------------------
# Feature: conclave_env defaults to 'production'
# ---------------------------------------------------------------------------


def test_settings_defaults_conclave_env_to_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings.conclave_env defaults to 'production' when CONCLAVE_ENV not set.

    This is the core T50.3 change: the default shifts from '' to 'production'.
    An unset CONCLAVE_ENV must now trigger production mode, not development mode.
    """
    from synth_engine.shared.settings import ConclaveSettings

    _set_production_env(monkeypatch)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)

    s = ConclaveSettings()
    assert s.conclave_env == "production", (
        "conclave_env must default to 'production' (T50.3 secure-by-default)"
    )


def test_unset_conclave_env_is_production_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_production() returns True when CONCLAVE_ENV is not set.

    Unset CONCLAVE_ENV now means production mode — the secure default.
    """
    from synth_engine.shared.settings import ConclaveSettings

    _set_production_env(monkeypatch)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)

    s = ConclaveSettings()
    assert s.is_production() is True


def test_explicit_development_mode_is_not_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """CONCLAVE_ENV=development explicitly → is_production() returns False.

    Developers must opt in to dev mode explicitly. This verifies the opt-in works.
    """
    from synth_engine.shared.settings import ConclaveSettings

    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.delenv("ENV", raising=False)

    s = ConclaveSettings()
    assert s.is_production() is False


def test_explicit_production_mode_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """CONCLAVE_ENV=production still returns is_production() == True.

    Backward compatibility: existing deployments that explicitly set
    CONCLAVE_ENV=production continue to work.
    """
    from synth_engine.shared.settings import ConclaveSettings

    _set_production_env(monkeypatch)
    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.delenv("ENV", raising=False)

    s = ConclaveSettings()
    assert s.is_production() is True


def test_legacy_env_production_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy ENV=production still triggers production mode.

    The legacy ENV field still works for backward compatibility.
    """
    from synth_engine.shared.settings import ConclaveSettings

    _set_production_env(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)

    s = ConclaveSettings()
    assert s.is_production() is True


def test_legacy_env_development_with_conclave_env_production_is_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENV=development does NOT override CONCLAVE_ENV=production default.

    When CONCLAVE_ENV defaults to 'production' but ENV=development is set,
    is_production() must still return True because CONCLAVE_ENV wins.
    """
    from synth_engine.shared.settings import ConclaveSettings

    _set_production_env(monkeypatch)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)  # defaults to 'production'

    s = ConclaveSettings()
    # conclave_env defaults to 'production' → is_production() True
    # env='development' → ENV check is False
    # OR logic: True OR False = True
    assert s.is_production() is True


# ---------------------------------------------------------------------------
# Feature: validate_config() with all required production vars succeeds
# ---------------------------------------------------------------------------


def test_production_all_vars_present_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """validate_config() passes when all production vars are set.

    Full production configuration must boot successfully.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")  # pragma: allowlist secret
    # No CONCLAVE_ENV set — defaults to production
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv(
        "ARTIFACT_SIGNING_KEY",
        "cafecafecafecafecafecafecafecafe",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    monkeypatch.setenv(
        "MASKING_SALT",
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",  # pragma: allowlist secret
    )
    monkeypatch.setenv(
        "JWT_SECRET_KEY", "supersecretkey-for-production"
    )  # pragma: allowlist secret
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", _VALID_BCRYPT_HASH)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)

    result = validate_config()
    assert result is None


# ---------------------------------------------------------------------------
# Feature: dev mode startup warning content
# ---------------------------------------------------------------------------


def test_dev_mode_warning_contains_production_guidance(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dev-mode startup warning must guide operators toward production configuration.

    The warning should mention 'CONCLAVE_ENV=production' so operators know
    exactly what to set to enable production mode.
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
        result = validate_config()

    assert result is None, "Dev mode must not raise SystemExit"

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("CONCLAVE_ENV=production" in msg for msg in warning_messages), (
        "Dev-mode warning must mention 'CONCLAVE_ENV=production' to guide operators. "
        f"Got: {warning_messages}"
    )


def test_dev_mode_via_env_also_emits_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dev mode via ENV=development also emits the startup warning.

    The warning must fire for both dev-mode signals: ENV=development and
    CONCLAVE_ENV=development.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")  # pragma: allowlist secret
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("CONCLAVE_ENV", "development")  # override the default
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)

    with caplog.at_level(logging.WARNING, logger="synth_engine.bootstrapper.config_validation"):
        result = validate_config()

    assert result is None
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("development" in msg.lower() for msg in warning_messages), (
        f"Expected dev-mode WARNING, got: {warning_messages}"
    )


def test_production_mode_does_not_emit_dev_mode_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Production mode must NOT emit the dev-mode warning.

    The dev-mode warning is a production-safety signal only. Production boots
    must not be polluted with false alarms.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")  # pragma: allowlist secret
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv(
        "ARTIFACT_SIGNING_KEY",
        "cafecafecafecafecafecafecafecafe",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    monkeypatch.setenv(
        "MASKING_SALT",
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    monkeypatch.setenv(
        "JWT_SECRET_KEY", "supersecretkey-for-production"
    )  # pragma: allowlist secret
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", _VALID_BCRYPT_HASH)
    monkeypatch.setenv("CONCLAVE_TLS_CERT_PATH", "/etc/ssl/conclave/conclave.crt")
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)

    with caplog.at_level(logging.WARNING, logger="synth_engine.bootstrapper.config_validation"):
        result = validate_config()

    assert result is None
    dev_warnings = [
        r.message
        for r in caplog.records
        if r.levelno == logging.WARNING and "development" in r.message.lower()
    ]
    assert not dev_warnings, (
        f"Production mode must NOT emit a dev-mode warning. Got: {dev_warnings}"
    )


# ---------------------------------------------------------------------------
# Feature: exempt paths count after removing security routes (ADV-P47-04)
# ---------------------------------------------------------------------------


def test_common_infra_exempt_paths_has_exactly_seven_paths() -> None:
    """COMMON_INFRA_EXEMPT_PATHS must contain exactly 7 paths.

    Count: 11 (T48.3) → 9 (P50) → 10 (T55.1) → 7 (T66.2 removed /docs,/redoc,/openapi.json).
    """
    from synth_engine.bootstrapper.dependencies._exempt_paths import COMMON_INFRA_EXEMPT_PATHS

    assert len(COMMON_INFRA_EXEMPT_PATHS) == 7, (
        f"Expected 7 paths in COMMON_INFRA_EXEMPT_PATHS. "
        f"Got {len(COMMON_INFRA_EXEMPT_PATHS)}: {sorted(COMMON_INFRA_EXEMPT_PATHS)}"
    )


def test_auth_exempt_paths_has_exactly_eight_paths() -> None:
    """AUTH_EXEMPT_PATHS must have exactly 8 paths (7 common + /auth/token).

    Count: 12 (T48.3) → 10 (P50) → 11 (T55.1) → 8 (T66.2 removed /docs,/redoc,/openapi.json).
    """
    from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

    assert len(AUTH_EXEMPT_PATHS) == 8, (
        f"Expected 8 paths in AUTH_EXEMPT_PATHS. "
        f"Got {len(AUTH_EXEMPT_PATHS)}: {sorted(AUTH_EXEMPT_PATHS)}"
    )


def test_common_infra_exempt_paths_contains_expected_seven_paths() -> None:
    """COMMON_INFRA_EXEMPT_PATHS must contain exactly the 7 expected paths.

    T55.1 added /health/vault; T66.2 removed /docs, /redoc, /openapi.json (ADV-P62-01).
    """
    from synth_engine.bootstrapper.dependencies._exempt_paths import COMMON_INFRA_EXEMPT_PATHS

    expected = frozenset(
        {
            "/unseal",
            "/health",
            "/ready",
            "/health/vault",
            "/metrics",
            "/license/challenge",
            "/license/activate",
        }
    )
    assert COMMON_INFRA_EXEMPT_PATHS == expected, (
        f"COMMON_INFRA_EXEMPT_PATHS mismatch.\n"
        f"Expected: {sorted(expected)}\n"
        f"Got: {sorted(COMMON_INFRA_EXEMPT_PATHS)}"
    )


def test_auth_exempt_paths_still_contains_auth_token() -> None:
    """/auth/token must remain in AUTH_EXEMPT_PATHS — operators need to log in."""
    from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

    assert "/auth/token" in AUTH_EXEMPT_PATHS


def test_vault_exempt_paths_contains_security_shred() -> None:
    """/security/shred MUST be in vault EXEMPT_PATHS — emergency shred requires sealed-state access.

    P50 review fix: restored /security/shred to SEAL_EXEMPT_PATHS (vault/license layer)
    while keeping it out of AUTH_EXEMPT_PATHS (still requires JWT auth).
    """
    from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

    assert "/security/shred" in EXEMPT_PATHS


def test_vault_exempt_paths_does_not_contain_keys_rotate() -> None:
    """/security/keys/rotate must NOT be in vault EXEMPT_PATHS.

    Rotation requires an unsealed vault.
    """
    from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

    assert "/security/keys/rotate" not in EXEMPT_PATHS


def test_license_exempt_paths_contains_security_shred() -> None:
    """/security/shred MUST be in LICENSE_EXEMPT_PATHS — emergency shred without license.

    P50 review fix: SEAL_EXEMPT_PATHS feeds the license gate too.
    """
    from synth_engine.bootstrapper.dependencies.licensing import LICENSE_EXEMPT_PATHS

    assert "/security/shred" in LICENSE_EXEMPT_PATHS


def test_license_exempt_paths_does_not_contain_security_keys_rotate() -> None:
    """/security/keys/rotate must not be in LICENSE_EXEMPT_PATHS after ADV-P47-04 fix."""
    from synth_engine.bootstrapper.dependencies.licensing import LICENSE_EXEMPT_PATHS

    assert "/security/keys/rotate" not in LICENSE_EXEMPT_PATHS


# ---------------------------------------------------------------------------
# Feature: test environment uses development mode (migration guard)
# ---------------------------------------------------------------------------


def test_test_suite_runs_in_development_mode_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The test suite must run in development mode via conftest-injected CONCLAVE_ENV.

    After T50.3, the conftest must inject CONCLAVE_ENV=development as a test-safe
    default to prevent production-mode enforcement from breaking tests that do not
    explicitly set CONCLAVE_ENV.

    This test verifies that without any explicit env manipulation, the test
    environment is in development mode.
    """
    from synth_engine.shared.settings import get_settings

    # Don't monkeypatch anything — rely on conftest defaults
    settings = get_settings()
    # The conftest must inject CONCLAVE_ENV=development to make tests work
    assert settings.is_production() is False, (
        "Test environment must be in development mode (conftest must inject "
        "CONCLAVE_ENV=development). Currently is_production()=True, which would "
        "break tests that don't configure all production vars."
    )
