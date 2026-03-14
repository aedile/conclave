"""Unit tests for ingestion connection string validators.

These tests verify that ``validate_connection_string`` enforces SSL requirements
for non-local connections and accepts valid local or SSL-equipped remote URLs.

CONSTITUTION Priority 0: Security — SSL enforcement is mandatory for remote connections.
CONSTITUTION Priority 3: TDD — RED phase for P3-T3.1.
Task: P3-T3.1 — Target Ingestion Engine
"""

from __future__ import annotations

import pytest

from synth_engine.modules.ingestion.validators import validate_connection_string


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
