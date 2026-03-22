"""Feature tests for config validation hardening (T47.4, T47.5, ADV-P46-03).

These tests are written AFTER attack tests per Rule 22 (Attack-First TDD).
They verify the happy-path and development-mode behaviours.

CONSTITUTION Priority 3: TDD — feature tests after attack tests
Task: T47.4 — Add JWT_SECRET_KEY to production-required validation
Task: T47.5 — Add OPERATOR_CREDENTIALS_HASH to production-required validation
Task: ADV-P46-03 — Fix cert readability check (existence + open())
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_VALID_BCRYPT_HASH = "$2b$12$" + "a" * 53  # 60 chars total — valid structural format

_PROD_KEYS_TO_DELETE = (
    "CONCLAVE_ENV",
    "ARTIFACT_SIGNING_KEYS",
    "ARTIFACT_SIGNING_KEY_ACTIVE",
    "MTLS_ENABLED",
)


def _set_prod_full_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set all production vars — including new auth ones — to valid values."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv(
        "ARTIFACT_SIGNING_KEY",
        "cafecafecafecafecafecafecafecafe",  # pragma: allowlist secret
    )
    monkeypatch.setenv(
        "MASKING_SALT",
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",  # pragma: allowlist secret
    )
    monkeypatch.setenv("JWT_SECRET_KEY", "supersecretkey-for-production")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", _VALID_BCRYPT_HASH)
    for key in _PROD_KEYS_TO_DELETE:
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Feature tests: JWT_SECRET_KEY happy path
# ---------------------------------------------------------------------------


def test_valid_jwt_secret_production_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid JWT_SECRET_KEY in production → validate_config() returns None without error.

    When all production-required variables are present and well-formed,
    validate_config() must complete without raising.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    _set_prod_full_valid(monkeypatch)

    result = validate_config()
    assert result is None


def test_empty_jwt_secret_dev_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Empty JWT_SECRET_KEY in development → WARNING logged, no SystemExit.

    Development environments legitimately start without a JWT key for local
    testing.  The validator must warn but not block startup.
    The warning must be omitted in production (that's a fatal error instead).
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)

    with caplog.at_level(logging.WARNING, logger="synth_engine.bootstrapper.config_validation"):
        result = validate_config()

    assert result is None, "Must not raise SystemExit in development mode"
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("JWT_SECRET_KEY" in msg for msg in warning_messages), (
        f"Expected WARNING containing 'JWT_SECRET_KEY', got: {warning_messages}"
    )


# ---------------------------------------------------------------------------
# Feature tests: OPERATOR_CREDENTIALS_HASH happy path
# ---------------------------------------------------------------------------


def test_valid_operator_hash_production_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid bcrypt OPERATOR_CREDENTIALS_HASH in production → validate_config() succeeds.

    A structurally valid bcrypt hash (starts with '$2b$', length >= 59 chars)
    must pass both presence and format checks.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    _set_prod_full_valid(monkeypatch)

    result = validate_config()
    assert result is None


def test_empty_operator_hash_dev_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Empty OPERATOR_CREDENTIALS_HASH in development → WARNING logged, no SystemExit.

    Development environments may start without an operator hash configured.
    The validator must warn but not block startup.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)

    with caplog.at_level(logging.WARNING, logger="synth_engine.bootstrapper.config_validation"):
        result = validate_config()

    assert result is None, "Must not raise SystemExit in development mode"
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("OPERATOR_CREDENTIALS_HASH" in msg for msg in warning_messages), (
        f"Expected WARNING containing 'OPERATOR_CREDENTIALS_HASH', got: {warning_messages}"
    )


def test_invalid_hash_dev_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invalid OPERATOR_CREDENTIALS_HASH format in development → WARNING logged, no SystemExit.

    An invalid hash format in development must produce a warning so developers
    notice the misconfiguration before deploying to production.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "not-a-bcrypt-hash")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("MASKING_SALT", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)

    with caplog.at_level(logging.WARNING, logger="synth_engine.bootstrapper.config_validation"):
        result = validate_config()

    assert result is None, "Must not raise SystemExit in development mode"
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("OPERATOR_CREDENTIALS_HASH" in msg for msg in warning_messages), (
        f"Expected WARNING containing 'OPERATOR_CREDENTIALS_HASH', got: {warning_messages}"
    )


# ---------------------------------------------------------------------------
# Feature tests: mTLS cert readability (ADV-P46-03)
# ---------------------------------------------------------------------------


def test_mtls_cert_readable_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All mTLS cert files exist and are readable → validate_config() returns None.

    The existence + readability check must not erroneously flag valid certs.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    ca = tmp_path / "ca.crt"
    client_cert = tmp_path / "app.crt"
    client_key = tmp_path / "app.key"
    ca.write_bytes(b"dummy-ca")
    client_cert.write_bytes(b"dummy-cert")
    client_key.write_bytes(b"dummy-key")

    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", str(ca))
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", str(client_cert))
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", str(client_key))
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)

    result = validate_config()
    assert result is None


# ---------------------------------------------------------------------------
# Feature tests: cross-concern — other config errors still collected
# ---------------------------------------------------------------------------


def test_jwt_and_hash_valid_with_other_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT_SECRET_KEY and OPERATOR_CREDENTIALS_HASH valid but other vars missing.

    Other errors must still be collected alongside the auth var checks.

    Validating the new auth vars must integrate cleanly with the existing
    collect-all pattern.  Other errors must still appear in the SystemExit
    message alongside (or independently of) auth var errors.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    # DATABASE_URL is missing — this alone should cause SystemExit
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv(
        "ARTIFACT_SIGNING_KEY",
        "cafecafecafecafecafecafecafecafe",  # pragma: allowlist secret
    )
    monkeypatch.setenv(
        "MASKING_SALT",
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",  # pragma: allowlist secret
    )
    monkeypatch.setenv("JWT_SECRET_KEY", "supersecretkey-for-production")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", _VALID_BCRYPT_HASH)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)
    monkeypatch.delenv("MTLS_ENABLED", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "DATABASE_URL" in str(exc_info.value), "DATABASE_URL error must still be collected"
