"""Unit tests for bootstrapper startup configuration validation (ADV-077).

Tests verify that ``validate_config()`` enforces required environment variables
at startup, providing a fail-fast mechanism that prevents the application from
starting in a misconfigured state.

Contract:
- ``validate_config()`` checks for ``DATABASE_URL`` and ``AUDIT_KEY`` in all modes.
- In production mode (``ENV=production`` or ``CONCLAVE_ENV=production``), also
  requires ``ARTIFACT_SIGNING_KEY``.
- Raises ``SystemExit`` with a clear error message listing ALL missing vars when
  any required vars are absent.
- Returns ``None`` successfully when all required vars are present.

CONSTITUTION Priority 0: Security — fail-fast on missing security-critical config
CONSTITUTION Priority 3: TDD
Task: P9-T9.1 — Advisory Drain + Startup Validation (ADV-077)
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

    # Must not raise — development mode does not require ARTIFACT_SIGNING_KEY
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


pytestmark = pytest.mark.unit
