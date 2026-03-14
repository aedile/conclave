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
Task: P3.5-T3.5.3 -- Virtual FK integration test
"""

from __future__ import annotations

import shutil
from collections.abc import Generator
from unittest.mock import patch

import psycopg2
import pytest
from pytest_postgresql import factories
from sqlalchemy import create_engine, text

from synth_engine.modules.mapping.reflection import SchemaReflector
from synth_engine.modules.subsetting.core import SubsettingEngine
from synth_engine.modules.subsetting.egress import EgressWriter
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
_ROLLBACK_SOURCE_DBNAME = "conclave_rollback_source"
_ROLLBACK_TARGET_DBNAME = "conclave_rollback_target"
_VFK_SOURCE_DBNAME = "conclave_vfk_source"
_VFK_TARGET_DBNAME = "conclave_vfk_target"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connect_pg(
    proc: factories.postgresql_proc,  # type: ignore[valid-type]  # pytest-postgresql proc executor has no exported runtime type
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


def _create_database(proc: factories.postgresql_proc, dbname: str) -> None:  # type: ignore[valid-type]  # pytest-postgresql proc executor has no exported runtime type
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


def _drop_database(proc: factories.postgresql_proc, dbname: str) -> None:  # type: ignore[valid-type]  # pytest-postgresql proc executor has no exported runtime type
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


def _create_three_table_schema(
    proc: factories.postgresql_proc,  # type: ignore[valid-type]  # pytest-postgresql proc executor has no exported runtime type
    dbname: str,
    with_serial: bool = False,
) -> None:
    """Create the departments → employees → salaries schema in the given DB.

    Args:
        proc: The postgresql_proc executor.
        dbname: Target database name (must already exist).
        with_serial: Use SERIAL primary keys (source) rather than INTEGER (target).
    """
    pk_type = "SERIAL" if with_serial else "INTEGER"
    conn = _connect_pg(proc, dbname)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS departments (
                id   {pk_type} PRIMARY KEY,
                name TEXT NOT NULL
            )
            """  # nosec B608
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS employees (
                id      {pk_type} PRIMARY KEY,
                dept_id INTEGER NOT NULL REFERENCES departments(id),
                name    TEXT NOT NULL
            )
            """  # nosec B608
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS salaries (
                id          {pk_type} PRIMARY KEY,
                employee_id INTEGER NOT NULL REFERENCES employees(id),
                amount      NUMERIC(10, 2) NOT NULL
            )
            """  # nosec B608
        )
    conn.close()


def _populate_source(
    proc: factories.postgresql_proc,  # type: ignore[valid-type]  # pytest-postgresql proc executor has no exported runtime type
    dbname: str,
) -> None:
    """Populate a source database with 10 departments, 30 employees, 60 salaries.

    Args:
        proc: The postgresql_proc executor.
        dbname: Source database name (schema must already exist).
    """
    conn = _connect_pg(proc, dbname)
    with conn.cursor() as cur:
        for d in range(1, 11):
            cur.execute(
                "INSERT INTO departments (name) VALUES (%s) RETURNING id",
                (f"Dept-{d}",),
            )
            dept_id = cur.fetchone()[0]  # type: ignore[index]  # psycopg2 fetchone() returns tuple[Any, ...] | None; index 0 is always valid after RETURNING
            for e in range(1, 4):
                cur.execute(
                    "INSERT INTO employees (dept_id, name) VALUES (%s, %s) RETURNING id",
                    (dept_id, f"Emp-{d}-{e}"),
                )
                emp_id = cur.fetchone()[0]  # type: ignore[index]  # psycopg2 fetchone() returns tuple[Any, ...] | None; index 0 is always valid after RETURNING
                for s in range(1, 3):
                    cur.execute(
                        "INSERT INTO salaries (employee_id, amount) VALUES (%s, %s)",
                        (emp_id, 50000 + s * 1000),
                    )
    conn.close()


def _make_three_table_topology() -> SchemaTopology:
    """Build the standard departments → employees → salaries SchemaTopology.

    Returns:
        A SchemaTopology value object for the 3-table hierarchy.
    """
    return SchemaTopology(
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


# ---------------------------------------------------------------------------
# Module-scoped fixture: provision source and target databases (happy-path)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def subsetting_dbs(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]  # pytest-postgresql proc executor has no exported runtime type
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
            dept_id = cur.fetchone()[0]  # type: ignore[index]  # psycopg2 fetchone() returns tuple[Any, ...] | None; index 0 is always valid after RETURNING
            # Insert 3 employees per department
            for e in range(1, 4):
                cur.execute(
                    "INSERT INTO employees (dept_id, name) VALUES (%s, %s) RETURNING id",
                    (dept_id, f"Emp-{d}-{e}"),
                )
                emp_id = cur.fetchone()[0]  # type: ignore[index]  # psycopg2 fetchone() returns tuple[Any, ...] | None; index 0 is always valid after RETURNING
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
# Module-scoped fixture: provision isolated databases for rollback test
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rollback_dbs(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]  # pytest-postgresql proc executor has no exported runtime type
) -> Generator[tuple[str, str]]:
    """Create isolated source and target databases for the Saga rollback test.

    Source DB is seeded with the same 3-table hierarchy.  Target DB is
    created with the same schema but no data.  These databases are kept
    separate from the happy-path fixture to avoid cross-test contamination.

    Args:
        postgresql_proc: The running PostgreSQL process executor.

    Yields:
        Tuple of (source_url, target_url) as SQLAlchemy connection strings.
    """
    proc = postgresql_proc

    _create_database(proc, _ROLLBACK_SOURCE_DBNAME)
    _create_database(proc, _ROLLBACK_TARGET_DBNAME)

    src_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_ROLLBACK_SOURCE_DBNAME}"
    )
    tgt_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_ROLLBACK_TARGET_DBNAME}"
    )

    _create_three_table_schema(proc, _ROLLBACK_SOURCE_DBNAME, with_serial=True)
    _populate_source(proc, _ROLLBACK_SOURCE_DBNAME)
    _create_three_table_schema(proc, _ROLLBACK_TARGET_DBNAME, with_serial=False)

    yield src_url, tgt_url

    _drop_database(proc, _ROLLBACK_SOURCE_DBNAME)
    _drop_database(proc, _ROLLBACK_TARGET_DBNAME)


# ---------------------------------------------------------------------------
# Module-scoped fixture: Virtual FK test databases (NO physical FK constraints)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vfk_dbs(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]  # pytest-postgresql proc executor has no exported runtime type
) -> Generator[tuple[str, str]]:
    """Create source and target DBs with NO physical FK constraints for VFK test.

    The source DB has accounts and transactions tables with only an application-
    level relationship (no FK constraint).  This simulates production schemas
    (data warehouses, legacy systems) where FK constraints are absent for
    performance reasons.

    Schema:
      accounts(id SERIAL PK, name TEXT)
      transactions(id SERIAL PK, account_id INTEGER, amount NUMERIC)
      -- NO FK constraint on transactions.account_id

    Args:
        postgresql_proc: The running PostgreSQL process executor.

    Yields:
        Tuple of (source_url, target_url) as SQLAlchemy connection strings.
    """
    proc = postgresql_proc

    _create_database(proc, _VFK_SOURCE_DBNAME)
    _create_database(proc, _VFK_TARGET_DBNAME)

    src_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_VFK_SOURCE_DBNAME}"
    )
    tgt_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_VFK_TARGET_DBNAME}"
    )

    # Source: no FK constraint between transactions and accounts
    src_conn = _connect_pg(proc, _VFK_SOURCE_DBNAME)
    with src_conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id   SERIAL PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id         SERIAL PRIMARY KEY,
                account_id INTEGER NOT NULL,
                amount     NUMERIC(10, 2) NOT NULL
            )
            """
            # Deliberately NO REFERENCES accounts(id) — no FK constraint
        )
        # Insert 5 accounts, 3 transactions each (15 total transactions)
        for a in range(1, 6):
            cur.execute(
                "INSERT INTO accounts (name) VALUES (%s) RETURNING id",
                (f"Acct-{a}",),
            )
            acct_id = cur.fetchone()[0]  # type: ignore[index]  # psycopg2 fetchone() returns tuple[Any, ...] | None; index 0 is always valid after RETURNING
            for t in range(1, 4):
                cur.execute(
                    "INSERT INTO transactions (account_id, amount) VALUES (%s, %s)",
                    (acct_id, 100 * t),
                )
    src_conn.close()

    # Target: matching schema with NO FK constraint (mirrors source)
    tgt_conn = _connect_pg(proc, _VFK_TARGET_DBNAME)
    with tgt_conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id   INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id         INTEGER PRIMARY KEY,
                account_id INTEGER NOT NULL,
                amount     NUMERIC(10, 2) NOT NULL
            )
            """
        )
    tgt_conn.close()

    yield src_url, tgt_url

    _drop_database(proc, _VFK_SOURCE_DBNAME)
    _drop_database(proc, _VFK_TARGET_DBNAME)


# ---------------------------------------------------------------------------
# Integration tests
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

    topology = _make_three_table_topology()

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


@pytest.mark.integration
def test_saga_rollback_leaves_target_clean(
    rollback_dbs: tuple[str, str],
) -> None:
    """Saga rollback: partial write failure leaves the target database empty.

    Arrange:
    - Source DB with 3-table hierarchy (departments → employees → salaries).
    - Target DB with matching schema but no rows.
    - EgressWriter.write() is patched to succeed for the first table
      (departments) and raise RuntimeError on the second table (employees).

    Act:
    - Run SubsettingEngine.run(); expect RuntimeError to propagate.

    Assert:
    - All three tables in the target DB contain zero rows — the Saga
      compensating action (TRUNCATE CASCADE) left the target clean.
    """
    src_url, tgt_url = rollback_dbs

    topology = _make_three_table_topology()

    src_engine = create_engine(src_url)
    tgt_engine = create_engine(tgt_url)

    real_egress = EgressWriter(target_engine=tgt_engine)

    # Track call count so we can fail on the second write (employees).
    call_count: list[int] = [0]
    original_write = real_egress.write

    def _failing_write(table: str, rows: list[dict]) -> None:  # type: ignore[type-arg]  # dict key/value types omitted intentionally; this is a test helper matching the real write signature
        """Succeed for the first table, raise RuntimeError on the second."""
        call_count[0] += 1
        if call_count[0] >= 2:
            raise RuntimeError("simulated disk failure on second table")
        original_write(table, rows)

    with patch.object(real_egress, "write", side_effect=_failing_write):
        se = SubsettingEngine(
            source_engine=src_engine,
            topology=topology,
            egress=real_egress,
        )

        with pytest.raises(RuntimeError, match="simulated disk failure"):
            se.run(
                seed_table="departments",
                seed_query="SELECT * FROM departments ORDER BY id LIMIT 1",  # nosec B608
            )

    # Saga invariant: target must be empty after the failed run.
    with tgt_engine.connect() as conn:
        dept_count = conn.execute(text("SELECT COUNT(*) FROM departments")).scalar()  # nosec B608
        emp_count = conn.execute(text("SELECT COUNT(*) FROM employees")).scalar()  # nosec B608
        sal_count = conn.execute(text("SELECT COUNT(*) FROM salaries")).scalar()  # nosec B608

    assert dept_count == 0, f"Saga left {dept_count} rows in departments — target not clean"
    assert emp_count == 0, f"Saga left {emp_count} rows in employees — target not clean"
    assert sal_count == 0, f"Saga left {sal_count} rows in salaries — target not clean"

    src_engine.dispose()
    tgt_engine.dispose()


@pytest.mark.integration
def test_virtual_fk_subsetting_no_orphaned_transactions(
    vfk_dbs: tuple[str, str],
) -> None:
    """Subsetting with a VFK follows the virtual edge with no orphaned rows.

    Arrange:
    - Source DB: accounts and transactions tables with NO physical FK constraint.
    - 5 accounts, 3 transactions each (15 total transactions).
    - VFK config: transactions.account_id -> accounts.id (no DB constraint).

    Act:
    - Build SchemaTopology from SchemaReflector using the VFK config.
    - Run SubsettingEngine seeded on accounts LIMIT 1.

    Assert:
    - Target has exactly 1 account.
    - Target has exactly 3 transactions (those belonging to the seeded account).
    - No orphaned transactions (application-level referential integrity preserved).

    This verifies T3.5.3 AC: "Subsetting run correctly follows the virtual FK
    edge and produces no orphaned transactions in target."
    """
    src_url, tgt_url = vfk_dbs

    src_engine = create_engine(src_url)
    tgt_engine = create_engine(tgt_url)

    # VFK config: declare transactions.account_id -> accounts.id
    # even though no physical FK constraint exists in the DB
    vfks = [
        {
            "table": "transactions",
            "column": "account_id",
            "references_table": "accounts",
            "references_column": "id",
        }
    ]

    # Use SchemaReflector with VFK config to build the DAG
    reflector = SchemaReflector(src_engine, virtual_foreign_keys=vfks)
    dag = reflector.reflect()

    # The VFK must produce the correct topological order: accounts before transactions
    order = dag.topological_sort()
    assert order.index("accounts") < order.index("transactions"), (
        f"Expected accounts before transactions in topological order, got: {order}"
    )

    # Build SchemaTopology manually using the DAG's order and reflected columns
    columns = {
        "accounts": (
            ColumnInfo(name="id", type="INTEGER", primary_key=1, nullable=False),
            ColumnInfo(name="name", type="TEXT", primary_key=0, nullable=False),
        ),
        "transactions": (
            ColumnInfo(name="id", type="INTEGER", primary_key=1, nullable=False),
            ColumnInfo(name="account_id", type="INTEGER", primary_key=0, nullable=False),
            ColumnInfo(name="amount", type="NUMERIC", primary_key=0, nullable=False),
        ),
    }
    topology = SchemaTopology(
        table_order=tuple(order),
        columns=columns,
        foreign_keys={
            "accounts": (),
            "transactions": (
                ForeignKeyInfo(
                    constrained_columns=("account_id",),
                    referred_table="accounts",
                    referred_columns=("id",),
                ),
            ),
        },
    )

    egress = EgressWriter(target_engine=tgt_engine)
    se = SubsettingEngine(
        source_engine=src_engine,
        topology=topology,
        egress=egress,
    )

    result = se.run(
        seed_table="accounts",
        seed_query="SELECT * FROM accounts ORDER BY id LIMIT 1",  # nosec B608
    )

    # Verify SubsetResult
    assert "accounts" in result.tables_written
    assert result.row_counts["accounts"] == 1

    # Verify target DB counts
    with tgt_engine.connect() as conn:
        acct_count = conn.execute(text("SELECT COUNT(*) FROM accounts")).scalar()  # nosec B608
        txn_count = conn.execute(text("SELECT COUNT(*) FROM transactions")).scalar()  # nosec B608

    assert acct_count == 1, f"Expected 1 account, got {acct_count}"
    assert txn_count == 3, f"Expected 3 transactions for the seeded account, got {txn_count}"

    # Verify application-level referential integrity — no orphaned transactions
    with tgt_engine.connect() as conn:
        orphaned_txns = conn.execute(
            text(  # nosec B608
                "SELECT COUNT(*) FROM transactions t "
                "WHERE NOT EXISTS (SELECT 1 FROM accounts a WHERE a.id = t.account_id)"
            )
        ).scalar()

    assert orphaned_txns == 0, f"Orphaned transactions after VFK subsetting: {orphaned_txns}"

    src_engine.dispose()
    tgt_engine.dispose()
