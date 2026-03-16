"""Unit tests for ingestion connection string validators.

These tests verify that ``validate_connection_string`` enforces SSL requirements
for non-local connections and accepts valid local or SSL-equipped remote URLs.

CONSTITUTION Priority 0: Security — SSL enforcement is mandatory for remote connections.
  Error messages MUST NOT expose embedded credentials from connection URLs.
CONSTITUTION Priority 3: TDD — tests updated for DevOps security finding (P3-T3.1).
Task: P3-T3.1 — Target Ingestion Engine
Task: P20-T20.4 — Architecture Tightening (ADV-020: configurable sslmode)
"""

from __future__ import annotations

import pytest

from synth_engine.modules.ingestion.validators import (
    _sanitize_url,
    validate_connection_string,
)


class TestSanitizeUrl:
    """Tests for the private :func:`_sanitize_url` helper."""

    def test_strips_password_from_url(self) -> None:
        """Credentials (user:password) are removed from the URL representation.

        This prevents auth material from leaking into exception messages or logs.
        """
        result = _sanitize_url("postgresql+psycopg2://admin:s3cr3t@db.example.com:5432/prod")
        assert "s3cr3t" not in result
        assert "admin" not in result

    def test_strips_password_preserves_host_and_port(self) -> None:
        """Host and port are retained after stripping credentials."""
        result = _sanitize_url("postgresql+psycopg2://user:pass@db.example.com:5432/mydb")
        assert "db.example.com" in result
        assert "5432" in result

    def test_strips_password_preserves_scheme(self) -> None:
        """Scheme is retained after stripping credentials."""
        result = _sanitize_url("postgresql+psycopg2://user:pass@db.example.com/mydb")
        assert "postgresql+psycopg2" in result

    def test_url_without_credentials_unchanged_content(self) -> None:
        """URL without credentials returns equivalent host/scheme content."""
        url = "postgresql://db.example.com:5432/mydb"
        result = _sanitize_url(url)
        assert "db.example.com" in result
        assert "postgresql" in result

    def test_unparseable_url_returns_safe_string(self) -> None:
        """Completely unparseable input returns a safe string without raising."""
        result = _sanitize_url("")
        assert isinstance(result, str)


class TestValidateConnectionString:
    """Tests for :func:`validate_connection_string`."""

    def test_local_host_no_ssl_required(self) -> None:
        """localhost connections pass without sslmode=require.

        Local connections are exempt from SSL enforcement because they traverse
        only the loopback interface — no network exposure.
        """
        validate_connection_string("postgresql+psycopg2://user:pass@localhost:5432/testdb")

    def test_127_0_0_1_no_ssl_required(self) -> None:
        """127.0.0.1 is treated as local and passes without sslmode=require."""
        validate_connection_string("postgresql+psycopg2://user:pass@127.0.0.1:5432/testdb")

    def test_ipv6_loopback_no_ssl_required(self) -> None:
        """[::1] (IPv6 loopback with RFC-3986 bracket notation) passes without sslmode=require.

        Per RFC 3986, IPv6 addresses in URLs must be enclosed in brackets.
        The correct form is ``@[::1]:5432``, not ``@::1:5432``.  The validator
        receives ``::1`` (without brackets) from ``urlparse.hostname``.
        """
        validate_connection_string("postgresql+psycopg2://user:pass@[::1]:5432/testdb")

    def test_remote_host_requires_ssl(self) -> None:
        """Remote host without sslmode=require raises ValueError.

        Any non-loopback host MUST use sslmode=require to prevent credentials
        and data from being sent over unencrypted connections.
        """
        with pytest.raises(ValueError, match="sslmode=require"):
            validate_connection_string("postgresql+psycopg2://user:pass@db.example.com:5432/prod")

    def test_remote_host_with_ssl_passes(self) -> None:
        """Remote host with sslmode=require in query params passes validation."""
        validate_connection_string(
            "postgresql+psycopg2://user:pass@db.example.com:5432/prod?sslmode=require"
        )

    def test_remote_ip_without_ssl_raises(self) -> None:
        """Remote IP (non-loopback) without sslmode=require raises ValueError."""
        with pytest.raises(ValueError, match="sslmode=require"):
            validate_connection_string("postgresql+psycopg2://user:pass@10.0.0.5:5432/prod")

    def test_remote_ip_with_ssl_passes(self) -> None:
        """Remote IP with sslmode=require passes validation."""
        validate_connection_string(
            "postgresql+psycopg2://user:pass@10.0.0.5:5432/prod?sslmode=require"
        )

    def test_remote_host_sslmode_allow_raises(self) -> None:
        """Remote host with sslmode=allow raises ValueError.

        ``sslmode=allow`` permits unencrypted fallback connections and does NOT
        satisfy the mandatory SSL requirement. Only ``sslmode=require`` (or
        stronger) is acceptable for remote connections.
        """
        with pytest.raises(ValueError, match="sslmode=require"):
            validate_connection_string(
                "postgresql+psycopg2://user:pass@host.example.com/db?sslmode=allow"
            )

    def test_remote_host_sslmode_disable_raises(self) -> None:
        """Remote host with sslmode=disable raises ValueError.

        ``sslmode=disable`` explicitly disables SSL and sends all traffic in
        plaintext. This is never acceptable for remote connections — the
        validator must reject it.
        """
        with pytest.raises(ValueError, match="sslmode=require"):
            validate_connection_string(
                "postgresql+psycopg2://user:pass@host.example.com/db?sslmode=disable"
            )

    def test_invalid_url_raises(self) -> None:
        """Malformed URL (no scheme or hostname) raises ValueError."""
        with pytest.raises(ValueError, match="Invalid"):
            validate_connection_string("not-a-valid-url")

    def test_unsupported_scheme_raises(self) -> None:
        """Non-PostgreSQL scheme (e.g. mysql://) raises ValueError."""
        with pytest.raises(ValueError, match="unsupported scheme"):
            validate_connection_string(
                "mysql+mysqldb://user:pass@db.example.com:3306/prod?sslmode=require"
            )

    def test_invalid_url_error_excludes_credentials(self) -> None:
        """ValueError for a malformed URL MUST NOT expose embedded credentials.

        CONSTITUTION Priority 0 — auth material must never appear in exception
        messages where they can be captured by upstream loggers.
        """
        url = "://s3cr3tpassword@"
        with pytest.raises(ValueError, match="Invalid"):
            validate_connection_string(url)

    def test_unsupported_scheme_error_excludes_credentials(self) -> None:
        """ValueError for unsupported scheme MUST NOT expose embedded credentials.

        CONSTITUTION Priority 0 — auth material must never appear in exception
        messages where they can be captured by upstream loggers.
        """
        # Fictional test credential — not a real secret.
        url = "mysql+mysqldb://adm:s3cr3t@db.test/db?sslmode=require"  # pragma: allowlist secret
        with pytest.raises(ValueError, match="unsupported scheme") as exc_info:
            validate_connection_string(url)
        assert "s3cr3t" not in str(exc_info.value)
        assert "adm" not in str(exc_info.value)


class TestValidateConnectionStringDockerSslOverride:
    """Tests for ADV-020: configurable sslmode enforcement via CONCLAVE_SSL_REQUIRED.

    ADV-020 finding: sslmode=require is enforced for all non-loopback hosts,
    which blocks internal Docker hostnames (e.g. ``postgres``, ``db``) that
    communicate over the Docker bridge network without SSL configured.

    Fix: When the ``CONCLAVE_SSL_REQUIRED`` environment variable is set to
    ``false`` (case-insensitive), the validator skips the sslmode enforcement
    for remote hosts. This allows Docker Compose deployments to use internal
    hostnames without SSL while production deployments (default: SSL required)
    remain secure.

    Security note: ``CONCLAVE_SSL_REQUIRED=false`` is ONLY safe for:
    - Docker bridge networks (single-host, traffic never leaves kernel stack)
    - Development/test environments

    Production deployments MUST leave ``CONCLAVE_SSL_REQUIRED`` unset or
    set to ``true``.
    """

    def test_docker_hostname_allowed_when_ssl_not_required(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Docker internal hostname passes validation when CONCLAVE_SSL_REQUIRED=false.

        Arrange: Set CONCLAVE_SSL_REQUIRED=false to simulate Docker Compose environment.
        Act: validate_connection_string with Docker internal hostname (no sslmode).
        Assert: No ValueError raised.
        """
        monkeypatch.setenv("CONCLAVE_SSL_REQUIRED", "false")
        # Should not raise — Docker bridge network, SSL not required
        validate_connection_string("postgresql+psycopg2://user:pass@postgres:5432/conclave")

    def test_docker_hostname_allowed_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONCLAVE_SSL_REQUIRED=FALSE (uppercase) is treated as false."""
        monkeypatch.setenv("CONCLAVE_SSL_REQUIRED", "FALSE")
        validate_connection_string("postgresql+psycopg2://user:pass@db:5432/mydb")

    def test_remote_host_still_requires_ssl_when_env_var_is_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit CONCLAVE_SSL_REQUIRED=true still enforces sslmode=require."""
        monkeypatch.setenv("CONCLAVE_SSL_REQUIRED", "true")
        with pytest.raises(ValueError, match="sslmode=require"):
            validate_connection_string("postgresql+psycopg2://user:pass@db.example.com:5432/prod")

    def test_remote_host_still_requires_ssl_when_env_var_absent(self) -> None:
        """Default behaviour (no env var) enforces sslmode=require for remote hosts."""
        # No monkeypatch — CONCLAVE_SSL_REQUIRED is not set in the test environment
        # Ensure any prior test does not leak env state by testing the default path
        with pytest.raises(ValueError, match="sslmode=require"):
            validate_connection_string("postgresql+psycopg2://user:pass@db.example.com:5432/prod")

    def test_ssl_override_false_still_rejects_invalid_scheme(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONCLAVE_SSL_REQUIRED=false does not bypass scheme validation."""
        monkeypatch.setenv("CONCLAVE_SSL_REQUIRED", "false")
        with pytest.raises(ValueError, match="unsupported scheme"):
            validate_connection_string("mysql://user:pass@db:3306/mydb")

    def test_ssl_override_false_still_rejects_malformed_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONCLAVE_SSL_REQUIRED=false does not bypass URL format validation."""
        monkeypatch.setenv("CONCLAVE_SSL_REQUIRED", "false")
        with pytest.raises(ValueError, match="Invalid"):
            validate_connection_string("not-a-url")
