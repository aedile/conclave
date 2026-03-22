"""Negative/attack tests for config validation hardening (T47.4, T47.5, ADV-P46-03).

These attack tests are written FIRST per Rule 22 (Attack-First TDD).

Attack surface:
- JWT_SECRET_KEY absent/empty/whitespace-only in production → silent auth bypass
- OPERATOR_CREDENTIALS_HASH absent/empty in production → token issuance always fails silently
- OPERATOR_CREDENTIALS_HASH with invalid bcrypt format → credential oracle attack surface
- Both missing simultaneously → collect-all error pattern
- Hash value leaking into error messages → hash oracle attack vector
- mTLS cert file exists but unreadable (mode 000) → TOCTOU race window
- mTLS cert path is a directory → mis-configuration silent failure

CONSTITUTION Priority 0: Security — fail-fast on missing auth-critical config
CONSTITUTION Priority 3: TDD — attack tests FIRST per Rule 22
Task: T47.4 — Add JWT_SECRET_KEY to production-required validation
Task: T47.5 — Add OPERATOR_CREDENTIALS_HASH to production-required validation
Task: ADV-P46-03 — Fix cert readability check (existence + open())
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_PROD_BASE_ENV: dict[str, str] = {
    "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/db",
    "AUDIT_KEY": "deadbeefdeadbeefdeadbeefdeadbeef",
    "ENV": "production",
    "ARTIFACT_SIGNING_KEY": "cafecafecafecafecafecafecafecafe",
    "MASKING_SALT": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
}

_VALID_BCRYPT_HASH = "$2b$12$" + "a" * 53  # 60 chars total — valid structural format

_DEV_KEYS_TO_DELETE = (
    "CONCLAVE_ENV",
    "ARTIFACT_SIGNING_KEYS",
    "ARTIFACT_SIGNING_KEY_ACTIVE",
    "MTLS_ENABLED",
    "JWT_SECRET_KEY",
    "OPERATOR_CREDENTIALS_HASH",
)


def _set_prod_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set all production-required env vars to valid values."""
    for key, val in _PROD_BASE_ENV.items():
        monkeypatch.setenv(key, val)
    for key in _DEV_KEYS_TO_DELETE:
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Attack tests: JWT_SECRET_KEY (T47.4)
# ---------------------------------------------------------------------------


def test_empty_jwt_secret_production_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty JWT_SECRET_KEY in production silently disables auth — must raise SystemExit.

    An empty jwt_secret_key means every JWT validation call will fail at runtime,
    effectively disabling all authenticated routes.  Fail-fast at boot is the
    correct defence.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    _set_prod_base(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", _VALID_BCRYPT_HASH)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "JWT_SECRET_KEY" in str(exc_info.value)


def test_whitespace_jwt_secret_production_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace-only JWT_SECRET_KEY in production must be treated as empty → SystemExit.

    A key consisting solely of spaces or tabs has negligible entropy.  Stripping
    before the truthiness check ensures operators cannot accidentally provision a
    whitespace key without being caught at startup.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    _set_prod_base(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", "   ")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", _VALID_BCRYPT_HASH)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "JWT_SECRET_KEY" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Attack tests: OPERATOR_CREDENTIALS_HASH (T47.5)
# ---------------------------------------------------------------------------


def test_empty_operator_hash_production_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty OPERATOR_CREDENTIALS_HASH in production → auth token issuance always fails.

    Without a hash, POST /auth/token will always reject credentials.  Leaving
    production running without a hash configured is a latent misconfiguration that
    must be surfaced at boot, not discovered at login time.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    _set_prod_base(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", "supersecretkey-for-production")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "")

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "OPERATOR_CREDENTIALS_HASH" in str(exc_info.value)


def test_invalid_hash_format_production_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-bcrypt OPERATOR_CREDENTIALS_HASH in production → format check raises SystemExit.

    A hash that doesn't start with '$2b$' or is too short is structurally invalid.
    Accepting it silently would mean the auth layer compares a passphrase against
    an incompatible hash format, guaranteeing auth failure.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    _set_prod_base(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", "supersecretkey-for-production")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "not-a-bcrypt-hash")

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "OPERATOR_CREDENTIALS_HASH" in str(exc_info.value)


def test_hash_too_short_production_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hash with '$2b$' prefix but too short to be valid must fail format check.

    A structurally truncated bcrypt hash could cause bcrypt.checkpw to raise
    a ValueError at auth time.  Detecting this at startup prevents a denial-of-
    auth scenario.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    _set_prod_base(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", "supersecretkey-for-production")
    # Starts with $2b$ but is only 20 chars — too short for a real bcrypt hash
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "$2b$12$tooshort")

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "OPERATOR_CREDENTIALS_HASH" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Attack test: collect-all — both missing simultaneously
# ---------------------------------------------------------------------------


def test_both_missing_production_collects_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both JWT_SECRET_KEY and OPERATOR_CREDENTIALS_HASH absent → single SystemExit listing both.

    The collect-all pattern must apply to auth-critical config.  An operator
    must see ALL missing variables in one error, not fix one at a time.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    _set_prod_base(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "")

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    error_message = str(exc_info.value)
    assert "JWT_SECRET_KEY" in error_message, "Must name JWT_SECRET_KEY in error"
    assert "OPERATOR_CREDENTIALS_HASH" in error_message, "Must name OPERATOR_CREDENTIALS_HASH in error"


# ---------------------------------------------------------------------------
# Attack test: hash oracle prevention
# ---------------------------------------------------------------------------


def test_hash_value_not_in_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Error message must name the variable, never include the actual hash value.

    Leaking a bcrypt hash into a log or error message creates a hash oracle:
    an attacker with access to application logs could use the hash offline.
    The error must reference the variable name only.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    _set_prod_base(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", "supersecretkey-for-production")

    # Use a recognisably unique invalid hash so we can test it is NOT in the output
    bad_hash = "not-a-real-bcrypt-hash-but-unique-sentinel-xyzzy"
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", bad_hash)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    error_message = str(exc_info.value)
    assert "OPERATOR_CREDENTIALS_HASH" in error_message, "Must name the variable"
    assert bad_hash not in error_message, (
        "Hash value MUST NOT appear in error message — hash oracle prevention"
    )


# ---------------------------------------------------------------------------
# Attack tests: mTLS cert readability (ADV-P46-03)
# ---------------------------------------------------------------------------


def test_mtls_cert_unreadable_appends_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cert file exists but mode 000 (unreadable) → error about readability, not just existence.

    The prior check only called Path.exists().  A file that exists but has mode 000
    would pass the existence check but fail at TLS handshake time.  The fix must
    attempt an open() and report permission errors at startup.

    This test is skipped when running as root (root can read any file).
    """
    if os.getuid() == 0:  # type: ignore[attr-defined]
        pytest.skip("Cannot test unreadable files as root")

    from synth_engine.bootstrapper.config_validation import validate_config

    # Create cert files — two readable, one not
    ca = tmp_path / "ca.crt"
    client_cert = tmp_path / "app.crt"
    client_key = tmp_path / "app.key"
    ca.write_bytes(b"dummy-ca")
    client_cert.write_bytes(b"dummy-cert")
    client_key.write_bytes(b"dummy-key")

    # Remove all permissions on the CA cert
    ca.chmod(0o000)

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

    try:
        with pytest.raises(SystemExit) as exc_info:
            validate_config()

        error_message = str(exc_info.value)
        assert "MTLS_CA_CERT_PATH" in error_message, (
            "Error must name the unreadable cert path variable"
        )
        # Must mention readability/permission, not just existence
        assert any(
            word in error_message.lower()
            for word in ("read", "permission", "access")
        ), f"Error must mention readability issue, got: {error_message}"
    finally:
        # Restore permissions so tmp_path cleanup can remove the file
        ca.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_mtls_cert_is_directory_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mTLS cert path pointing to a directory → open() raises IsADirectoryError → error appended.

    A directory cannot be used as a TLS certificate.  The readability check must
    catch this case and append an appropriate error.
    """
    from synth_engine.bootstrapper.config_validation import validate_config

    ca_dir = tmp_path / "ca_dir"
    ca_dir.mkdir()
    client_cert = tmp_path / "app.crt"
    client_key = tmp_path / "app.key"
    client_cert.write_bytes(b"dummy-cert")
    client_key.write_bytes(b"dummy-key")

    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", str(ca_dir))
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", str(client_cert))
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", str(client_key))
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "MTLS_CA_CERT_PATH" in str(exc_info.value)
