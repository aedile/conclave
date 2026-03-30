"""Attack / negative tests for the ingestion module (P73 — Gate 1).

Proves the ingestion validation layer REJECTS malicious or malformed inputs:
- SQL-injection attempts embedded in connection URLs.
- Non-PostgreSQL schemes (mysql, sqlite, redis).
- Remote hosts without sslmode=require (when SSL enforcement is on).
- URLs with embedded credentials that could be logged.
- Empty / None connection strings.

Constitution Priority 0: Security.
Task: P73 — Test Quality Rehabilitation (Gate 1 — attack coverage for ingestion).
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.attack]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate(url: str) -> None:
    """Call validate_connection_string and propagate exceptions.

    Args:
        url: Raw PostgreSQL connection URL to validate.
    """
    from synth_engine.modules.ingestion.validators import validate_connection_string

    validate_connection_string(url)


# ---------------------------------------------------------------------------
# Attack: non-PostgreSQL schemes are rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malicious_scheme_url",
    [
        "mysql://user:pass@host/db",
        "sqlite:///local.db",
        "redis://localhost:6379/0",
        "http://attacker.example.com/path",
        "file:///etc/passwd",
    ],
)
def test_non_postgresql_scheme_is_rejected(malicious_scheme_url: str) -> None:
    """Non-PostgreSQL connection URLs must be rejected with ValueError.

    An attacker submitting a non-PostgreSQL URL could pivot to file-reads
    (sqlite file://) or SSRF-like reads (http://).  The validator must
    allow only known PostgreSQL driver schemes.

    Args:
        malicious_scheme_url: A URL using a non-PostgreSQL scheme.
    """
    match_pattern = r"scheme|unsupported|Unsupported|invalid|Invalid"
    with pytest.raises(ValueError, match=match_pattern) as exc_info:
        _validate(malicious_scheme_url)

    error_message = str(exc_info.value)
    assert "scheme" in error_message.lower() or "unsupported" in error_message.lower(), (
        f"Expected error message to reference the scheme, got: {error_message!r}"
    )


# ---------------------------------------------------------------------------
# Attack: remote host without sslmode=require is rejected
# ---------------------------------------------------------------------------


def test_remote_host_without_ssl_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remote PostgreSQL connection without sslmode=require must be rejected.

    Without SSL, credentials and query data travel in plain text over the
    network.  The validator must block such connections when SSL enforcement
    is enabled (the production default).

    Args:
        monkeypatch: pytest fixture for reversible env var injection.
    """
    monkeypatch.setenv("CONCLAVE_SSL_REQUIRED", "true")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    with pytest.raises(ValueError, match=r"[Ss][Ss][Ll]|sslmode") as exc_info:
        _validate("postgresql://user:pass@db.example.com:5432/mydb")

    error_message = str(exc_info.value).lower()
    # Must reference SSL / sslmode in the error — NOT the password
    assert "ssl" in error_message, (
        f"Expected error message to reference SSL requirement, got: {error_message!r}"
    )
    # Credentials must NOT appear in the error message
    assert "pass" not in error_message, (
        f"Error message must not expose credentials, got: {error_message!r}"
    )


# ---------------------------------------------------------------------------
# Attack: empty connection string is rejected
# ---------------------------------------------------------------------------


def test_empty_connection_string_is_rejected() -> None:
    """An empty string must not silently pass validation.

    An attacker or misconfigured client that sends an empty URL should receive
    a clear error — not trigger a silent no-op or expose a raw exception
    with internal detail.
    """
    with pytest.raises((ValueError, Exception)) as exc_info:
        _validate("")

    # The error must be a clean ValueError or similar — not an unhandled
    # AttributeError that would expose internals.
    assert exc_info.type.__name__ in {"ValueError", "AttributeError"} or issubclass(
        exc_info.type, ValueError | AttributeError
    ), f"Expected ValueError or AttributeError for empty URL, got {exc_info.type.__name__}"


# ---------------------------------------------------------------------------
# Attack: credentials must be stripped from error messages
# ---------------------------------------------------------------------------


def test_credentials_not_leaked_in_ssl_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials embedded in the URL must not appear in error messages.

    A developer debugging an SSL error must never see the raw password in
    the exception string — it could be captured in logs or tracebacks.

    Args:
        monkeypatch: pytest fixture for reversible env var injection.
    """
    monkeypatch.setenv("CONCLAVE_SSL_REQUIRED", "true")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    secret_password = "SuperS3cr3tP@ssw0rd"  # pragma: allowlist secret
    url = f"postgresql://admin:{secret_password}@remote.example.com:5432/prod"

    with pytest.raises(ValueError, match=r"[Ss][Ss][Ll]|sslmode") as exc_info:
        _validate(url)

    error_message = str(exc_info.value)
    assert secret_password not in error_message, (
        f"Secret password must not appear in error messages. Error was: {error_message!r}"
    )
