"""Unit tests for bootstrapper startup configuration validation (ADV-077).

Tests verify that ``validate_config()`` enforces required environment variables
at startup, providing a fail-fast mechanism that prevents the application from
starting in a misconfigured state.

Contract:
- ``validate_config()`` checks for ``DATABASE_URL`` and ``AUDIT_KEY`` in all modes.
- In production mode (``ENV=production`` or ``CONCLAVE_ENV=production``), also
  requires ``ARTIFACT_SIGNING_KEY`` and ``MASKING_SALT``.
- Raises ``SystemExit`` with a clear error message listing ALL missing vars when
  any required vars are absent.
- Returns ``None`` successfully when all required vars are present.

CONSTITUTION Priority 0: Security — fail-fast on missing security-critical config
CONSTITUTION Priority 3: TDD
Task: P9-T9.1 — Advisory Drain + Startup Validation (ADV-077)
Task: P19-T19.2 — Security Hardening: MASKING_SALT production enforcement
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helper: base env vars that satisfy all non-production requirements
# ---------------------------------------------------------------------------

_BASE_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/db",
    "AUDIT_KEY": "deadbeefdeadbeefdeadbeefdeadbeef",
}

_PROD_ENV = {
    **_BASE_ENV,
    "ARTIFACT_SIGNING_KEY": "cafecafecafecafecafecafecafecafe",
    "MASKING_SALT": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
}


# ---------------------------------------------------------------------------
# Tests: missing base required vars raise SystemExit
# ---------------------------------------------------------------------------


def test_missing_database_url_raises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """validate_config() raises SystemExit when DATABASE_URL is missing.

    DATABASE_URL is required in all deployment modes (development and production).
    Missing it must cause an immediate SystemExit with a descriptive message.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "DATABASE_URL" in str(exc_info.value)


def test_missing_audit_key_raises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """validate_config() raises SystemExit when AUDIT_KEY is missing.

    AUDIT_KEY is required in all deployment modes.  Missing it must cause an
    immediate SystemExit with a descriptive message.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    monkeypatch.delenv("AUDIT_KEY", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "AUDIT_KEY" in str(exc_info.value)


def test_missing_multiple_vars_lists_all_in_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() lists ALL missing vars in the SystemExit message.

    The error must not fail fast on the first missing var and omit others.
    A single clear message listing everything that's wrong is required.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUDIT_KEY", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    error_message = str(exc_info.value)
    assert "DATABASE_URL" in error_message, "SystemExit message must list DATABASE_URL as missing"
    assert "AUDIT_KEY" in error_message, "SystemExit message must list AUDIT_KEY as missing"


# ---------------------------------------------------------------------------
# Tests: production mode additionally requires ARTIFACT_SIGNING_KEY
# ---------------------------------------------------------------------------


def test_production_mode_without_artifact_signing_key_raises_system_exit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() raises SystemExit in production when ARTIFACT_SIGNING_KEY is absent.

    Production mode is detected via ENV=production.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("MASKING_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "ARTIFACT_SIGNING_KEY" in str(exc_info.value)


def test_production_mode_without_artifact_signing_key_raises_system_exit_conclave_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() raises SystemExit in production when ARTIFACT_SIGNING_KEY is absent.

    Production mode is also detected via CONCLAVE_ENV=production.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("MASKING_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "ARTIFACT_SIGNING_KEY" in str(exc_info.value)


def test_non_production_mode_without_artifact_signing_key_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() passes in development mode without ARTIFACT_SIGNING_KEY.

    ARTIFACT_SIGNING_KEY is only required when ENV=production or
    CONCLAVE_ENV=production.  Development deployments must be able to start
    without it (unsigned artifacts are permitted in development).
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)

    # Must not raise — development mode does not require ARTIFACT_SIGNING_KEY or MASKING_SALT
    result = validate_config()
    assert result is None


def test_all_vars_present_non_production_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() passes when all required vars are set (no production mode)."""
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)

    result = validate_config()
    assert result is None


def test_all_vars_present_production_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() passes when all required vars are set in production mode."""
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "cafecafecafecafecafecafecafecafe")
    monkeypatch.setenv("MASKING_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)

    result = validate_config()
    assert result is None


# ---------------------------------------------------------------------------
# Tests: empty-string env vars are treated as missing
# ---------------------------------------------------------------------------


def test_empty_string_database_url_raises_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() raises SystemExit when DATABASE_URL is set to an empty string.

    An empty-string value is semantically equivalent to absent: a connection
    string of "" cannot form a valid database URL.  The validator must reject
    it with a SystemExit that names DATABASE_URL in the message.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "DATABASE_URL" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tests: production mode requires MASKING_SALT (AC2 — T19.2)
# ---------------------------------------------------------------------------


def test_production_mode_without_masking_salt_raises_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() raises SystemExit in production when MASKING_SALT is absent.

    MASKING_SALT is required in production to prevent deterministic masking from
    using a known hardcoded development salt, which would make masked values
    reversible.  Production startup without MASKING_SALT must cause an immediate
    SystemExit with a descriptive message naming MASKING_SALT.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "cafecafecafecafecafecafecafecafe")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "MASKING_SALT" in str(exc_info.value)


def test_production_mode_without_masking_salt_via_conclave_env_raises_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() raises SystemExit via CONCLAVE_ENV=production when MASKING_SALT absent.

    Both ENV and CONCLAVE_ENV are valid production mode indicators.  This test
    verifies MASKING_SALT enforcement applies when CONCLAVE_ENV=production is used.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "cafecafecafecafecafecafecafecafe")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "MASKING_SALT" in str(exc_info.value)


def test_non_production_mode_without_masking_salt_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() passes in development mode without MASKING_SALT.

    MASKING_SALT is only required in production.  Development deployments may
    use the hardcoded fallback salt without triggering a startup failure.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY", raising=False)

    result = validate_config()
    assert result is None


def test_production_missing_both_signing_key_and_masking_salt_lists_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() lists both ARTIFACT_SIGNING_KEY and MASKING_SALT when both absent.

    The collect-all-then-raise pattern must apply to production-required vars too.
    A single error message must name every missing variable, not just the first one.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    error_message = str(exc_info.value)
    assert "ARTIFACT_SIGNING_KEY" in error_message
    assert "MASKING_SALT" in error_message


# ---------------------------------------------------------------------------
# Tests: docker-compose.yml pgbouncer auth type (AC3 — T19.2)
# ---------------------------------------------------------------------------


def test_docker_compose_pgbouncer_auth_type_is_scram_sha_256() -> None:
    """docker-compose.yml must set PGBOUNCER_AUTH_TYPE to scram-sha-256, not md5.

    PostgreSQL 14+ deprecates md5 auth in favour of scram-sha-256.  ADV-016
    resolution requires changing the docker-compose.yml env var to match the
    SCRAM-SHA-256 format already documented in pgbouncer/userlist.txt.example.
    """
    import pathlib

    compose_path = pathlib.Path(__file__).parent.parent.parent / "docker-compose.yml"
    content = compose_path.read_text()

    assert "PGBOUNCER_AUTH_TYPE: scram-sha-256" in content, (
        "docker-compose.yml must set PGBOUNCER_AUTH_TYPE to scram-sha-256 (ADV-016 resolution)"
    )
    assert "PGBOUNCER_AUTH_TYPE: md5" not in content, (
        "docker-compose.yml must not use md5 auth type — upgrade to scram-sha-256"
    )


# ---------------------------------------------------------------------------
# Tests: OPERATOR_MANUAL.md X-Forwarded-For proxy documentation (AC1 — T19.2)
# ---------------------------------------------------------------------------


def test_operator_manual_documents_trusted_proxy_requirement() -> None:
    """OPERATOR_MANUAL.md must document the X-Forwarded-For trusted proxy requirement.

    The FastAPI app does not validate that X-Forwarded-For headers come from a
    trusted reverse proxy.  Operators must be warned that production deployments
    require a trusted reverse proxy that strips and re-sets X-Forwarded-For to
    prevent IP spoofing.
    """
    import pathlib

    manual_path = pathlib.Path(__file__).parent.parent.parent / "docs" / "OPERATOR_MANUAL.md"
    content = manual_path.read_text()

    assert "X-Forwarded-For" in content, (
        "OPERATOR_MANUAL.md must document X-Forwarded-For header handling"
    )
    assert "trusted" in content.lower() and "proxy" in content.lower(), (
        "OPERATOR_MANUAL.md must warn about the trusted reverse proxy requirement"
    )


pytestmark = pytest.mark.unit
