"""Integration tests for mTLS inter-container connections — T46.2.

These tests verify that the full connection path (API → PostgreSQL, API → Redis,
Huey → Redis) operates correctly when MTLS_ENABLED=true and real TLS-enabled
services are available.

Skip conditions:
    These tests are skipped by default because they require a running TLS-enabled
    infrastructure stack (``docker compose -f docker-compose.yml -f
    docker-compose.mtls.yml up``). They are NOT skipped in CI when the
    ``MTLS_INTEGRATION_TESTS`` environment variable is set to ``true``.

Running locally::

    # Start mTLS-enabled stack first:
    docker compose -f docker-compose.yml -f docker-compose.mtls.yml up -d

    # Then run:
    MTLS_INTEGRATION_TESTS=true poetry run pytest tests/integration/test_mtls_connections.py -v

CONSTITUTION Priority 0: Security — verify mTLS enforcement end-to-end
Task: T46.2 — Wire mTLS on All Container-to-Container Connections
"""

from __future__ import annotations

import os

import pytest

# ---------------------------------------------------------------------------
# Skip guard — these tests require a running mTLS-enabled Docker stack.
# ---------------------------------------------------------------------------

_MTLS_INTEGRATION_ENABLED = os.environ.get("MTLS_INTEGRATION_TESTS", "").lower() == "true"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _MTLS_INTEGRATION_ENABLED,
        reason=(
            "mTLS integration tests require MTLS_INTEGRATION_TESTS=true "
            "and a running mTLS-enabled Docker stack. "
            "Start with: docker compose -f docker-compose.yml -f docker-compose.mtls.yml up -d"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_postgresql_connection_uses_tls_when_mtls_enabled() -> None:
    """API→PostgreSQL connection succeeds with sslmode=verify-full when MTLS_ENABLED=true.

    This test requires:
    - A running PostgreSQL with TLS enabled and client cert verification.
    - MTLS_ENABLED=true, DATABASE_URL pointing to TLS-enabled PostgreSQL.
    - Valid cert files at the configured paths.
    """
    from synth_engine.shared.db import _engine_cache, get_engine
    from synth_engine.shared.settings import get_settings

    settings = get_settings()
    assert settings.mtls_enabled, "MTLS_ENABLED must be true for this test"

    # Clear engine cache to force a fresh engine creation with TLS params
    _engine_cache.pop(settings.database_url, None)
    engine = get_engine(settings.database_url)

    # Verify the engine can execute a simple query over TLS
    from sqlalchemy import text

    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == 1

    # Clean up
    _engine_cache.pop(settings.database_url, None)
    engine.dispose()


def test_redis_connection_uses_tls_when_mtls_enabled() -> None:
    """API→Redis connection succeeds over rediss:// when MTLS_ENABLED=true.

    This test requires:
    - A running Redis with TLS enabled and client cert verification.
    - MTLS_ENABLED=true, REDIS_URL=redis://redis:6379/0.
    - Valid cert files at the configured paths.
    """
    from synth_engine.bootstrapper.dependencies import redis as redis_dep

    # Reset the singleton to force fresh construction with TLS params
    redis_dep._client = None

    client = redis_dep.get_redis_client()

    # Ping verifies the TLS connection is functional
    assert client.ping() is True

    # Clean up
    redis_dep._client = None


def test_plaintext_redis_rejected_when_mtls_enabled() -> None:
    """Plaintext redis:// connection is rejected when Redis requires TLS.

    When Redis is started with --port 0 (plaintext disabled), a redis://
    connection must fail at the socket level. This test verifies the
    fail-closed security property.
    """
    import redis as redis_lib

    from synth_engine.shared.settings import get_settings

    settings = get_settings()
    assert settings.mtls_enabled, "MTLS_ENABLED must be true for this test"

    # Force a plaintext redis:// URL — this must fail
    plaintext_url = settings.redis_url.replace("rediss://", "redis://")
    client = redis_lib.Redis.from_url(plaintext_url)

    with pytest.raises((redis_lib.ConnectionError, redis_lib.TimeoutError, ConnectionRefusedError)):
        client.ping()
