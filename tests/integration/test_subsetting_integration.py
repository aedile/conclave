"""Integration tests for SubsettingEngine — full 3-table hierarchy subset.

Tests use an ephemeral PostgreSQL instance managed by ``pytest-postgresql``.

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH.  If absent, all tests are skipped via
  the ``_require_postgresql`` autouse fixture.

Marks: ``integration``

CONSTITUTION Priority 0: Security — no PII, parameterised SQL only.
CONSTITUTION Priority 3: TDD — integration gate for P3-T3.4.
Task: P3-T3.4 -- Subsetting & Materialization Core
"""

from __future__ import annotations

import shutil
from collections.abc import Generator

import psycopg2
import pytest
from pytest_postgresql import factories
from sqlalchemy import create_engine, text

from synth_engine.modules.ingestion.core import SubsettingEngine
from synth_engine.modules.ingestion.egress import EgressWriter
from synth_engine.shared.schema_topology import (
    ColumnInfo,
    ForeignKeyInfo,
    SchemaTopology,
)

# ---------------------------------------------------------------------------
# pytest-postgresql process fixture
# ---------------------------------------------------------------------------

postgresql_proc = factories.postgresql_proc()

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _require_postgresql() -> None:
    """Skip entire module when pg_ctl is not installed.

    In CI the PostgreSQL service is always present so the guard has no effect.
    """
    if shutil.which("pg_ctl") is None:
        pytest.skip(
            "pg_ctl not found on PATH — install PostgreSQL to run subsetting integration tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Database names
# ---------------------------------------------------------------------------

_SOURCE_DBNAME = "conclave_subsetting_source"
_TARGET_DBNAME = "conclave_subsetting_target"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connect_pg(
    proc: factories.postgresql_proc,  # type: ignore[valid-type]
    dbname: str = "postgres",
) -> psycopg2.extensions.connection:
    """Open a psycopg2 superuser connection to the ephemeral PG instance.

    Args:
        proc: The postgresql_proc executor.
        dbname: Database name to connect to.

    Returns:
        An open psycopg2 connection with autocommit enabled.
    """
    conn = psycopg2.connect(
        dbname=dbname,
        user=proc.user,
        host=proc.host,
        port=proc.port,
        password=proc.password or "",
    )
    conn.autocommit = True
    return conn


def _create_database(proc: factories.postgresql_proc, dbname: str) -> None:  # type: ignore[valid-type]
    """Create a database if it does not already exist.

    Args:
        proc: The postgresql_proc executor.
        dbname: Name of the database to create.
    """
    conn = _connect_pg(proc)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{dbname}"')  # nosec B608
    conn.close()


def _drop_database(proc: factories.postgresql_proc, dbname: str) -> None:  # type: ignore[valid-type]
    """Terminate connections and drop a database.

    Args:
        proc: The postgresql_proc executor.
        dbname: Name of the database to drop.
    """
    conn = _connect_pg(proc)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (dbname,),
        )
        cur.execute(
            "DROP DATABASE IF EXISTS " + psycopg2.extensions.quote_ident(dbname, cur)  # nosec B608
        )
    conn.close()


# ---------------------------------------------------------------------------
# Module-scoped fixture: provision source and target databases
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def subsetting_dbs(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]
) -> Generator[tuple[str, str]]:
    """Create source and target databases; yield their connection URLs.

    The source database is seeded with a 3-table hierarchy:
    - departments: 10 rows
    - employees:   30 rows (3 per department)
    - salaries:    60 rows (2 per employee)

    Args:
        postgresql_proc: The running PostgreSQL process executor.

    Yields:
        Tuple of (source_url, target_url) as SQLAlchemy connection strings.
    """
    proc = postgresql_proc

    _create_database(proc, _SOURCE_DBNAME)
    _create_database(proc, _TARGET_DBNAME)

    src_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_SOURCE_DBNAME}"
    )
    tgt_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_TARGET_DBNAME}"
    )

    # ------------------------------------------------------------------
    # Seed source database
    # ------------------------------------------------------------------
    src_conn = _connect_pg(proc, _SOURCE_DBNAME)
    with src_conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS departments (
                id   SERIAL PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id      SERIAL PRIMARY KEY,
                dept_id INTEGER NOT NULL REFERENCES departments(id),
                name    TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS salaries (
                id          SERIAL PRIMARY KEY,
                employee_id INTEGER NOT NULL REFERENCES employees(id),
                amount      NUMERIC(10, 2) NOT NULL
            )
            """
        )
        # Insert 10 departments
        for d in range(1, 11):
            cur.execute(
                "INSERT INTO departments (name) VALUES (%s) RETURNING id",
                (f"Dept-{d}",),
            )
            dept_id = cur.fetchone()[0]  # type: ignore[index]
            # Insert 3 employees per department
            for e in range(1, 4):
                cur.execute(
                    "INSERT INTO employees (dept_id, name) VALUES (%s, %s) RETURNING id",
                    (dept_id, f"Emp-{d}-{e}"),
                )
                emp_id = cur.fetchone()[0]  # type: ignore[index]
                # Insert 2 salaries per employee
                for s in range(1, 3):
                    cur.execute(
                        "INSERT INTO salaries (employee_id, amount) VALUES (%s, %s)",
                        (emp_id, 50000 + s * 1000),
                    )
    src_conn.close()

    # ------------------------------------------------------------------
    # Create matching schema in target (no data — EgressWriter will populate)
    # ------------------------------------------------------------------
    tgt_conn = _connect_pg(proc, _TARGET_DBNAME)
    with tgt_conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS departments (
                id   INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id      INTEGER PRIMARY KEY,
                dept_id INTEGER NOT NULL REFERENCES departments(id),
                name    TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS salaries (
                id          INTEGER PRIMARY KEY,
                employee_id INTEGER NOT NULL REFERENCES employees(id),
                amount      NUMERIC(10, 2) NOT NULL
            )
            """
        )
    tgt_conn.close()

    yield src_url, tgt_url

    # ------------------------------------------------------------------
    # Teardown: drop both databases
    # ------------------------------------------------------------------
    _drop_database(proc, _SOURCE_DBNAME)
    _drop_database(proc, _TARGET_DBNAME)


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_full_10_percent_subset_no_orphaned_fks(
    subsetting_dbs: tuple[str, str],
) -> None:
    """Subset 1 department and verify referential integrity in target DB.

    Seed a source DB with a 3-table hierarchy:
      departments (10 rows) -> employees (30 rows, 3 per dept)
                            -> salaries (60 rows, 2 per employee)

    Run a subset targeting departments LIMIT 1.

    Assert:
    - target DB has exactly 1 department
    - target DB has exactly 3 employees (those belonging to that department)
    - target DB has exactly 6 salaries (those belonging to those employees)
    - all FK constraints in the target DB remain valid (no orphaned records)
    """
    src_url, tgt_url = subsetting_dbs

    # Build SchemaTopology (bootstrapper would normally build this from SchemaReflector)
    topology = SchemaTopology(
        table_order=("departments", "employees", "salaries"),
        columns={
            "departments": (
                ColumnInfo(name="id", type="INTEGER", primary_key=1, nullable=False),
                ColumnInfo(name="name", type="TEXT", primary_key=0, nullable=False),
            ),
            "employees": (
                ColumnInfo(name="id", type="INTEGER", primary_key=1, nullable=False),
                ColumnInfo(name="dept_id", type="INTEGER", primary_key=0, nullable=False),
                ColumnInfo(name="name", type="TEXT", primary_key=0, nullable=False),
            ),
            "salaries": (
                ColumnInfo(name="id", type="INTEGER", primary_key=1, nullable=False),
                ColumnInfo(name="employee_id", type="INTEGER", primary_key=0, nullable=False),
                ColumnInfo(name="amount", type="NUMERIC", primary_key=0, nullable=False),
            ),
        },
        foreign_keys={
            "departments": (),
            "employees": (
                ForeignKeyInfo(
                    constrained_columns=("dept_id",),
                    referred_table="departments",
                    referred_columns=("id",),
                ),
            ),
            "salaries": (
                ForeignKeyInfo(
                    constrained_columns=("employee_id",),
                    referred_table="employees",
                    referred_columns=("id",),
                ),
            ),
        },
    )

    src_engine = create_engine(src_url)
    tgt_engine = create_engine(tgt_url)

    egress = EgressWriter(target_engine=tgt_engine)
    se = SubsettingEngine(
        source_engine=src_engine,
        topology=topology,
        egress=egress,
    )

    result = se.run(
        seed_table="departments",
        seed_query="SELECT * FROM departments ORDER BY id LIMIT 1",  # nosec B608
    )

    # Verify SubsetResult
    assert "departments" in result.tables_written
    assert result.row_counts["departments"] == 1

    # Verify target DB counts
    with tgt_engine.connect() as conn:
        dept_count = conn.execute(text("SELECT COUNT(*) FROM departments")).scalar()  # nosec B608
        emp_count = conn.execute(text("SELECT COUNT(*) FROM employees")).scalar()  # nosec B608
        sal_count = conn.execute(text("SELECT COUNT(*) FROM salaries")).scalar()  # nosec B608

    assert dept_count == 1, f"Expected 1 department, got {dept_count}"
    assert emp_count == 3, f"Expected 3 employees, got {emp_count}"
    assert sal_count == 6, f"Expected 6 salaries, got {sal_count}"

    # Verify referential integrity — no orphaned employees
    with tgt_engine.connect() as conn:
        orphaned_employees = conn.execute(
            text(  # nosec B608
                "SELECT COUNT(*) FROM employees e "
                "WHERE NOT EXISTS (SELECT 1 FROM departments d WHERE d.id = e.dept_id)"
            )
        ).scalar()
        orphaned_salaries = conn.execute(
            text(  # nosec B608
                "SELECT COUNT(*) FROM salaries s "
                "WHERE NOT EXISTS (SELECT 1 FROM employees e WHERE e.id = s.employee_id)"
            )
        ).scalar()

    assert orphaned_employees == 0, f"Orphaned employees: {orphaned_employees}"
    assert orphaned_salaries == 0, f"Orphaned salaries: {orphaned_salaries}"

    src_engine.dispose()
    tgt_engine.dispose()
