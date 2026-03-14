"""Integration tests for the PostgreSQL ingestion adapter.

These tests verify the pre-flight privilege check against a live, ephemeral
PostgreSQL instance managed by ``pytest-postgresql``.  They prove:

1. **Superuser rejection**: Connecting as a superuser raises
   :class:`~synth_engine.modules.ingestion.postgres_adapter.PrivilegeEscalationError`.
2. **Read-only pass**: Connecting as a user with only SELECT/CONNECT privileges
   passes the pre-flight check without raising.
3. **Streaming correctness**: ``stream_table()`` yields all inserted rows across
   multiple batches, never loading the full table into memory.

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH. If absent, all tests in this module are
  skipped automatically via the ``_require_postgresql`` autouse fixture.

Marks: ``integration``

CONSTITUTION Priority 0: Security — superuser connection MUST be rejected.
CONSTITUTION Priority 3: TDD — integration gate for P3-T3.1.
Task: P3-T3.1 — Target Ingestion Engine
"""

from __future__ import annotations

import shutil

import psycopg2
import pytest
from pytest_postgresql import factories

from synth_engine.modules.ingestion.postgres_adapter import (
    PostgresIngestionAdapter,
    PrivilegeEscalationError,
)

# ---------------------------------------------------------------------------
# pytest-postgresql process fixture
# ---------------------------------------------------------------------------

postgresql_proc = factories.postgresql_proc()

# ---------------------------------------------------------------------------
# Shared test database name
# ---------------------------------------------------------------------------

_TEST_DBNAME = "conclave_ingestion_integration"
_READONLY_USER = "readonly_ingestion_tester"
_READONLY_PASS = "readonly_pass_only"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Skip guard — runs before all tests in this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _require_postgresql() -> None:
    """Skip entire module when pg_ctl is not installed.

    ``postgresql_proc`` from pytest-postgresql spawns a real PostgreSQL process
    using ``pg_ctl``.  If the binary is absent (e.g. developer laptops without
    a local PG installation), all tests would error rather than skip.  This
    fixture detects the absence early and issues a clean module-level skip.

    In CI the PostgreSQL service is always present so the guard has no effect.
    """
    if shutil.which("pg_ctl") is None:
        pytest.skip(
            "pg_ctl not found on PATH — install PostgreSQL to run ingestion integration tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Database provisioning helper
# ---------------------------------------------------------------------------


def _create_database(proc: factories.postgresql_proc) -> None:  # type: ignore[valid-type]
    """Create the integration test database using psycopg2.

    Args:
        proc: The ``postgresql_proc`` executor providing host/port/user.
    """
    conn = psycopg2.connect(
        dbname="postgres",
        user=proc.user,
        host=proc.host,
        port=proc.port,
        password=proc.password or "",
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (_TEST_DBNAME,),
        )
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{_TEST_DBNAME}"')
    conn.close()


# ---------------------------------------------------------------------------
# Module-scoped DB provisioning
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _provision_test_db(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]
) -> None:
    """Create the test database once per module.

    Also creates a read-only user that is used by the readonly test.

    Args:
        postgresql_proc: The running PostgreSQL process executor.

    Yields:
        None — setup/teardown only.
    """
    _create_database(postgresql_proc)

    # Provision the read-only user in the test database.
    conn = psycopg2.connect(
        dbname=_TEST_DBNAME,
        user=postgresql_proc.user,
        host=postgresql_proc.host,
        port=postgresql_proc.port,
        password=postgresql_proc.password or "",
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        # Drop user if leftover from a previous failed run.
        cur.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (_READONLY_USER,),
        )
        if not cur.fetchone():
            # User doesn't use %s for password because CREATE ROLE with
            # PASSWORD requires a string literal; the value is a test-only
            # credential defined as a module constant — not user input.
            cur.execute(
                f"CREATE USER {_READONLY_USER} PASSWORD '{_READONLY_PASS}'"  # nosec B608
            )
        cur.execute(
            "GRANT CONNECT ON DATABASE " + _TEST_DBNAME + " TO " + _READONLY_USER  # nosec B608
        )
        cur.execute(
            "GRANT SELECT ON ALL TABLES IN SCHEMA public TO " + _READONLY_USER  # nosec B608
        )
    conn.close()

    yield

    # Teardown: drop the test database.
    drop_conn = psycopg2.connect(
        dbname="postgres",
        user=postgresql_proc.user,
        host=postgresql_proc.host,
        port=postgresql_proc.port,
        password=postgresql_proc.password or "",
    )
    drop_conn.autocommit = True
    with drop_conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (_TEST_DBNAME,),
        )
        cur.execute("DROP DATABASE IF EXISTS " + psycopg2.extensions.quote_ident(_TEST_DBNAME, cur))
    drop_conn.close()


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("_provision_test_db")
def test_preflight_fails_for_superuser(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]
) -> None:
    """preflight_check raises PrivilegeEscalationError when connecting as superuser.

    The pytest-postgresql default user IS a superuser, which means the adapter
    must detect and refuse the connection before any data access occurs.

    Arrange: Build a connection URL using the superuser credentials from the
        ephemeral PostgreSQL process.
    Act: Call ``adapter.preflight_check()``.
    Assert: :class:`PrivilegeEscalationError` is raised.
    """
    proc = postgresql_proc
    url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_TEST_DBNAME}"
    )
    adapter = PostgresIngestionAdapter(url)

    with pytest.raises(PrivilegeEscalationError, match="superuser"):
        adapter.preflight_check()


@pytest.mark.integration
@pytest.mark.usefixtures("_provision_test_db")
def test_preflight_passes_for_readonly_user(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]
) -> None:
    """preflight_check passes when connecting as a read-only user.

    Arrange: Connect as ``readonly_ingestion_tester``, a user created by the
        ``_provision_test_db`` fixture with only CONNECT + SELECT privileges.
    Act: Call ``adapter.preflight_check()``.
    Assert: No exception is raised.
    """
    proc = postgresql_proc
    url = (
        f"postgresql+psycopg2://{_READONLY_USER}:{_READONLY_PASS}"
        f"@{proc.host}:{proc.port}/{_TEST_DBNAME}"
    )
    adapter = PostgresIngestionAdapter(url)

    # Must not raise — this is the required read-only path.
    adapter.preflight_check()


@pytest.mark.integration
@pytest.mark.usefixtures("_provision_test_db")
def test_stream_table_yields_rows(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]
) -> None:
    """stream_table yields all rows across multiple batches.

    Arrange: Insert 5 rows into a test table as superuser.
    Act: Stream the table as the read-only user with batch_size=2.
    Assert: All 5 rows are received across multiple batches; no rows are lost.
    """
    proc = postgresql_proc

    # Create the test table and insert rows as superuser via direct psycopg2.
    setup_conn = psycopg2.connect(
        dbname=_TEST_DBNAME,
        user=proc.user,
        host=proc.host,
        port=proc.port,
        password=proc.password or "",
    )
    setup_conn.autocommit = True
    with setup_conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS ingestion_test_items "
            "(id SERIAL PRIMARY KEY, label TEXT NOT NULL)"
        )
        cur.execute("TRUNCATE TABLE ingestion_test_items RESTART IDENTITY")
        for i in range(5):
            cur.execute(
                "INSERT INTO ingestion_test_items (label) VALUES (%s)",
                (f"item-{i}",),
            )
        # Grant SELECT on the new table to the readonly user.
        cur.execute(
            "GRANT SELECT ON ingestion_test_items TO " + _READONLY_USER  # nosec B608
        )
    setup_conn.close()

    # Stream as the read-only user.
    readonly_url = (
        f"postgresql+psycopg2://{_READONLY_USER}:{_READONLY_PASS}"
        f"@{proc.host}:{proc.port}/{_TEST_DBNAME}"
    )
    adapter = PostgresIngestionAdapter(readonly_url)

    all_rows: list[dict[str, object]] = []
    batch_count = 0
    for batch in adapter.stream_table("ingestion_test_items", batch_size=2):
        batch_count += 1
        all_rows.extend(batch)

    assert len(all_rows) == 5, f"Expected 5 rows, got {len(all_rows)}"
    assert batch_count >= 3, (
        f"Expected at least 3 batches (5 rows / batch_size=2), got {batch_count}"
    )
    labels = {row["label"] for row in all_rows}
    assert labels == {f"item-{i}" for i in range(5)}, f"Unexpected labels: {labels}"

    # Cleanup: drop the test table.
    cleanup_conn = psycopg2.connect(
        dbname=_TEST_DBNAME,
        user=proc.user,
        host=proc.host,
        port=proc.port,
        password=proc.password or "",
    )
    cleanup_conn.autocommit = True
    with cleanup_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS ingestion_test_items")
    cleanup_conn.close()
