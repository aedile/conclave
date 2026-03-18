"""Integration tests for masking FK join preservation on real PostgreSQL.

These tests verify that after deterministic masking is applied to the parent
table, foreign-key joins with the child table still succeed — no orphan rows.

The test leverages ``ON UPDATE CASCADE`` on the FK constraint, which means
that when ``departments.name`` is updated to a masked value, PostgreSQL
automatically cascades that update to all ``employees.dept_name`` rows
referencing the old name.  This is precisely the production behaviour:
the masking engine updates the parent table and relies on CASCADE to keep
child tables consistent.

After the CASCADE update, the INNER JOIN must still return all 4 employee
rows and a LEFT JOIN orphan check must return 0 rows — confirming that
referential integrity is preserved end-to-end.

Setup:
- parent table: ``test_departments`` (id PK, name TEXT UNIQUE)
- child table:  ``test_employees``   (id PK, name TEXT, dept_name TEXT FK with CASCADE)

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH.  If absent, this module is skipped.
- ``MASKING_SALT`` env var is monkeypatched — no real secret is used.

Marks: ``integration``

CONSTITUTION Priority 0: Security — test data is entirely fictional.
CONSTITUTION Priority 3: TDD — integration gate for P26-T26.5.
Task: P26-T26.5 — Licensing + Migration + FK Masking Integration Tests
"""

from __future__ import annotations

import shutil
from collections.abc import Generator

import psycopg2
import psycopg2.extensions
import pytest
from pytest_postgresql import factories

from synth_engine.modules.masking.registry import ColumnType, MaskingRegistry
from tests.conftest_types import PostgreSQLProc

# ---------------------------------------------------------------------------
# pytest-postgresql process fixture
# ---------------------------------------------------------------------------

postgresql_proc = factories.postgresql_proc()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_DBNAME = "conclave_masking_fk_integration"

# Salt is a column-identity string per the masking module design rationale.
# MASKING_SALT is injected via monkeypatch at the fixture level.
_DEPT_NAME_SALT = "departments.name"

# Fictional test data — no real PII
_DEPARTMENTS = [
    ("Engineering",),
    ("Product",),
    ("Design",),
]
_EMPLOYEES = [
    # (name, dept_name)
    ("Alice Smith", "Engineering"),
    ("Bob Jones", "Engineering"),
    ("Carol White", "Product"),
    ("Dave Brown", "Design"),
]

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _require_postgresql() -> None:
    """Skip entire module when pg_ctl is not installed."""
    if shutil.which("pg_ctl") is None:
        pytest.skip(
            "pg_ctl not found on PATH — install PostgreSQL to run masking FK integration tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# MASKING_SALT fixture — injected per test, no real secret
# ---------------------------------------------------------------------------


@pytest.fixture
def masking_salt(monkeypatch: pytest.MonkeyPatch) -> str:
    """Inject a deterministic but non-production MASKING_SALT env var.

    The masking layer uses this salt combined with column-identity strings for
    domain separation.  We set it to a fixed test value so results are
    reproducible across test runs.

    Args:
        monkeypatch: pytest monkeypatch for environment variable injection.

    Returns:
        The test masking salt string.
    """
    salt = "test-masking-salt-integration"  # pragma: allowlist secret  # nosec B105
    monkeypatch.setenv("MASKING_SALT", salt)
    return salt


# ---------------------------------------------------------------------------
# Database provisioning
# ---------------------------------------------------------------------------


def _create_database(proc: PostgreSQLProc) -> None:
    """Create the integration test database.

    Args:
        proc: PostgreSQL process executor.
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
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DBNAME,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{_TEST_DBNAME}"')  # nosec B608
    conn.close()


@pytest.fixture(scope="module")
def _provision_test_db(postgresql_proc: PostgreSQLProc) -> Generator[None]:
    """Create the test database once per module and drop on teardown.

    Args:
        postgresql_proc: Running PostgreSQL process executor.

    Yields:
        None — setup/teardown only.
    """
    _create_database(postgresql_proc)
    yield
    conn = psycopg2.connect(
        dbname="postgres",
        user=postgresql_proc.user,
        host=postgresql_proc.host,
        port=postgresql_proc.port,
        password=postgresql_proc.password or "",
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (_TEST_DBNAME,),
        )
        cur.execute(
            "DROP DATABASE IF EXISTS " + psycopg2.extensions.quote_ident(_TEST_DBNAME, cur)  # nosec B608
        )
    conn.close()


@pytest.fixture
def db_conn(
    postgresql_proc: PostgreSQLProc,
    _provision_test_db: None,
) -> Generator[psycopg2.extensions.connection]:
    """Yield a psycopg2 connection to the ephemeral test database.

    Creates the parent/child tables with FK CASCADE constraint, inserts test
    data, and drops the tables on teardown.

    The FK constraint uses ``ON UPDATE CASCADE`` so that when departments.name
    is masked (updated), PostgreSQL automatically cascades the new value to all
    referencing employees.dept_name rows.

    Args:
        postgresql_proc: Running PostgreSQL process executor.
        _provision_test_db: Module fixture ensuring the DB exists.

    Yields:
        An open psycopg2 connection with autocommit enabled.
    """
    proc = postgresql_proc
    conn = psycopg2.connect(
        dbname=_TEST_DBNAME,
        user=proc.user,
        host=proc.host,
        port=proc.port,
        password=proc.password or "",
    )
    conn.autocommit = True

    with conn.cursor() as cur:
        # Drop tables if leftover from a previous failed test run
        cur.execute("DROP TABLE IF EXISTS test_employees")
        cur.execute("DROP TABLE IF EXISTS test_departments")

        # Parent table: departments
        cur.execute(
            """
            CREATE TABLE test_departments (
                id    SERIAL PRIMARY KEY,
                name  TEXT NOT NULL UNIQUE
            )
            """
        )

        # Child table: employees with ON UPDATE CASCADE FK.
        # CASCADE means: UPDATE departments.name -> automatically updates employees.dept_name.
        cur.execute(
            """
            CREATE TABLE test_employees (
                id        SERIAL PRIMARY KEY,
                name      TEXT NOT NULL,
                dept_name TEXT NOT NULL,
                CONSTRAINT fk_employee_dept
                    FOREIGN KEY (dept_name)
                    REFERENCES test_departments(name)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT
            )
            """
        )

        # Insert test departments (parent rows)
        for (dept_name,) in _DEPARTMENTS:
            cur.execute(
                "INSERT INTO test_departments (name) VALUES (%s)",
                (dept_name,),
            )

        # Insert test employees (child rows referencing parent.name)
        for emp_name, dept_name in _EMPLOYEES:
            cur.execute(
                "INSERT INTO test_employees (name, dept_name) VALUES (%s, %s)",
                (emp_name, dept_name),
            )

    yield conn

    # Teardown: drop the test tables in FK-safe order
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_employees")
        cur.execute("DROP TABLE IF EXISTS test_departments")
    conn.close()


# ---------------------------------------------------------------------------
# AC3 integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_masked_fk_joins_succeed_with_no_orphan_rows(
    db_conn: psycopg2.extensions.connection,
    masking_salt: str,
) -> None:
    """After masking the parent table, FK joins must succeed with zero orphan rows.

    The test exercises the ``ON UPDATE CASCADE`` FK path:
    - Masking ``departments.name`` triggers a CASCADE that automatically updates
      all ``employees.dept_name`` rows to the new masked value.
    - After the CASCADE, an INNER JOIN must still return all 4 employee rows.
    - A LEFT JOIN orphan check must return 0, confirming no child rows were
      left referencing a non-existent parent.

    Arrange: db_conn fixture inserts 3 departments (original plaintext names)
        and 4 employees with FK references.
    Act: mask each department name using MaskingRegistry; UPDATE departments.
    Assert: CASCADE preserved FK; JOIN count == 4; orphan count == 0.
    """
    registry = MaskingRegistry()

    # Step 1: read original department names (before any masking)
    with db_conn.cursor() as cur:
        cur.execute("SELECT id, name FROM test_departments ORDER BY id")
        dept_rows = cur.fetchall()

    # Step 2: mask each department name and collect original→masked mapping.
    dept_name_map: dict[str, str] = {}
    for _dept_id, dept_name in dept_rows:
        masked = registry.mask(dept_name, ColumnType.NAME, _DEPT_NAME_SALT)
        dept_name_map[dept_name] = masked

    # Step 3: UPDATE departments.name to masked values.
    # ON UPDATE CASCADE automatically propagates the new names to employees.dept_name.
    with db_conn.cursor() as cur:
        for orig, masked in dept_name_map.items():
            cur.execute(
                "UPDATE test_departments SET name = %s WHERE name = %s",
                (masked, orig),
            )

    # Step 4: assert INNER JOIN still returns all 4 employees after CASCADE.
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM test_employees e
            INNER JOIN test_departments d ON d.name = e.dept_name
            """
        )
        row = cur.fetchone()
    assert row is not None
    join_count: int = row[0]
    assert join_count == len(_EMPLOYEES), (
        f"INNER JOIN must return all {len(_EMPLOYEES)} employee rows after masking; "
        f"got {join_count}.  ON UPDATE CASCADE failed to preserve FK integrity."
    )

    # Step 5: assert no orphan employees (LEFT JOIN ... WHERE d.id IS NULL)
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM test_employees e
            LEFT JOIN test_departments d ON d.name = e.dept_name
            WHERE d.id IS NULL
            """
        )
        orphan_row = cur.fetchone()
    assert orphan_row is not None
    orphan_count: int = orphan_row[0]
    assert orphan_count == 0, (
        f"Expected 0 orphan employee rows after masking; found {orphan_count}.  "
        "ON UPDATE CASCADE should have kept all child rows in sync with parent."
    )


@pytest.mark.integration
def test_masked_values_differ_from_originals(
    db_conn: psycopg2.extensions.connection,
    masking_salt: str,
) -> None:
    """Masked department names must differ from the original plaintext names.

    Verifies that the masking actually changes values — i.e., we are not
    accidentally returning the identity function.

    Arrange: read original department names (fixtures insert plaintext names).
    Act: mask each name using MaskingRegistry.
    Assert: every masked name differs from its original.
    """
    registry = MaskingRegistry()

    with db_conn.cursor() as cur:
        cur.execute("SELECT name FROM test_departments ORDER BY id")
        dept_names = [row[0] for row in cur.fetchall()]

    for orig_name in dept_names:
        masked = registry.mask(orig_name, ColumnType.NAME, _DEPT_NAME_SALT)
        assert masked != orig_name, (
            f"Masked value must differ from original; got {masked!r} == {orig_name!r}"
        )
