"""Shared helper functions for full pipeline E2E integration tests (T56.3).

Contains database setup helpers, schema creation utilities, and
topology builders used by both test_full_pipeline_e2e.py and
test_full_pipeline_budget.py.

Not a conftest.py — imported directly to avoid affecting other integration
tests that do not need the PostgreSQL helpers.
"""

from __future__ import annotations

from typing import Any

import psycopg2  # type: ignore[import-untyped]

from synth_engine.modules.masking.algorithms import mask_email, mask_name
from synth_engine.shared.schema_topology import (
    ColumnInfo,
    ForeignKeyInfo,
    SchemaTopology,
)
from tests.conftest_types import PostgreSQLProc

_E2E_SOURCE_DB = "conclave_full_pipeline_source"
_E2E_TARGET_DB = "conclave_full_pipeline_target"
_E2E_CONCURRENT_DB = "conclave_full_pipeline_concurrent"

# ---------------------------------------------------------------------------
# Masking salt and column map (used by test_full_pipeline_e2e.py via _mask_row)
# ---------------------------------------------------------------------------

_MASKING_SALT = "full-pipeline-e2e-salt"

#: Map of table name → {column → masking function(value, salt) -> str}
_COLUMN_MASKS: dict[str, dict[str, Any]] = {
    "customers": {
        "full_name": mask_name,
        "email": mask_email,
    },
}


# ---------------------------------------------------------------------------
# psycopg2 helpers
# ---------------------------------------------------------------------------


def _connect_pg(
    proc: PostgreSQLProc,
    dbname: str = "postgres",
) -> psycopg2.extensions.connection:
    """Open an autocommit psycopg2 superuser connection to the ephemeral PG instance.

    Args:
        proc: The postgresql_proc executor providing host/port/user/password.
        dbname: Database name to connect to (defaults to the postgres system DB).

    Returns:
        An open psycopg2 connection with ``autocommit=True``.
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


def _create_db(proc: PostgreSQLProc, dbname: str) -> None:
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


def _drop_db(proc: PostgreSQLProc, dbname: str) -> None:
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
            "DROP DATABASE IF EXISTS "  # nosec B608
            + psycopg2.extensions.quote_ident(dbname, cur)
        )
    conn.close()


# ---------------------------------------------------------------------------
# Schema creation and seeding helpers
# ---------------------------------------------------------------------------


def _create_pipeline_schema(proc: PostgreSQLProc, dbname: str, *, with_serial: bool) -> None:
    """Create the 5-table linear FK chain in the given database.

    Schema topology (single root, linear chain):
        regions → customers → accounts → orders → order_lines

    Args:
        proc: The postgresql_proc executor.
        dbname: Target database (must already exist).
        with_serial: If ``True`` use SERIAL PKs (source DB); otherwise INTEGER (target DB).
    """
    pk_type = "SERIAL" if with_serial else "INTEGER"
    conn = _connect_pg(proc, dbname)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS regions (
                id      {pk_type} PRIMARY KEY,
                name    VARCHAR(80) NOT NULL
            )
            """  # nosec B608
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS customers (
                id          {pk_type} PRIMARY KEY,
                region_id   INTEGER NOT NULL REFERENCES regions(id),
                full_name   VARCHAR(120) NOT NULL,
                email       VARCHAR(150) NOT NULL
            )
            """  # nosec B608
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS accounts (
                id              {pk_type} PRIMARY KEY,
                customer_id     INTEGER NOT NULL REFERENCES customers(id),
                account_ref     VARCHAR(30) NOT NULL
            )
            """  # nosec B608
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS orders (
                id          {pk_type} PRIMARY KEY,
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                total_price NUMERIC(10, 2) NOT NULL
            )
            """  # nosec B608
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS order_lines (
                id          {pk_type} PRIMARY KEY,
                order_id    INTEGER NOT NULL REFERENCES orders(id),
                product_sku VARCHAR(30) NOT NULL,
                quantity    INTEGER NOT NULL,
                unit_price  NUMERIC(10, 2) NOT NULL
            )
            """  # nosec B608
        )
    conn.close()


def _seed_pipeline_source(proc: PostgreSQLProc, dbname: str) -> None:
    """Populate the source DB with fictional (non-PII) test data.

    Row counts (single-root linear chain from regions):
        regions:     5
        customers:   10 (2 per region)
        accounts:    10 (1 per customer)
        orders:      20 (2 per account)
        order_lines: 60 (3 per order)

    Total: 105 rows across 5 tables — satisfies the ≥50 row spec (T35.4 C&C §2).

    All names and emails are in the format ``Name-N`` / ``user_N@example.com``
    — these are test-only values, not real PII.

    Args:
        proc: The postgresql_proc executor.
        dbname: Source database name (schema must already exist).
    """
    conn = _connect_pg(proc, dbname)
    with conn.cursor() as cur:
        for r in range(1, 6):
            cur.execute(
                "INSERT INTO regions (name) VALUES (%s) RETURNING id",
                (f"Region-{r}",),
            )
            region_id = cur.fetchone()[0]  # psycopg2: fetchone() always valid after RETURNING

            for c in range(1, 3):
                cur.execute(
                    "INSERT INTO customers (region_id, full_name, email) VALUES (%s, %s, %s) "
                    "RETURNING id",
                    (region_id, f"Customer-{region_id}-{c}", f"user_{region_id}_{c}@example.com"),
                )
                customer_id = cur.fetchone()[0]  # psycopg2: fetchone() always valid after RETURNING

                cur.execute(
                    "INSERT INTO accounts (customer_id, account_ref) VALUES (%s, %s) RETURNING id",
                    (customer_id, f"REF-{customer_id:04d}"),
                )
                account_id = cur.fetchone()[0]  # psycopg2: fetchone() always valid after RETURNING

                for o in range(1, 3):
                    cur.execute(
                        "INSERT INTO orders (account_id, total_price) VALUES (%s, %s) RETURNING id",
                        (account_id, float(o * 49.99)),
                    )
                    order_id = cur.fetchone()[0]  # psycopg2: always valid after RETURNING

                    for line in range(1, 4):
                        cur.execute(
                            "INSERT INTO order_lines "
                            "(order_id, product_sku, quantity, unit_price) "
                            "VALUES (%s, %s, %s, %s)",
                            (order_id, f"SKU-{order_id:04d}-{line}", line, float(line * 9.99)),
                        )
    conn.close()


# ---------------------------------------------------------------------------
# Hardcoded row-count queries — avoids f-string SQL (S608 / B608).
# ---------------------------------------------------------------------------

#: Map from table name to its count query.
_COUNT_QUERIES: dict[str, str] = {
    "regions": "SELECT COUNT(*) FROM regions",  # nosec B608
    "customers": "SELECT COUNT(*) FROM customers",  # nosec B608
    "accounts": "SELECT COUNT(*) FROM accounts",  # nosec B608
    "orders": "SELECT COUNT(*) FROM orders",  # nosec B608
    "order_lines": "SELECT COUNT(*) FROM order_lines",  # nosec B608
}


# ---------------------------------------------------------------------------
# SchemaTopology for the full 5-table linear pipeline schema
# ---------------------------------------------------------------------------


def _make_pipeline_topology() -> SchemaTopology:
    """Build the SchemaTopology for the linear 5-table pipeline schema.

    Topological order: regions → customers → accounts → orders → order_lines

    Returns:
        A :class:`SchemaTopology` describing all 5 tables and their FK
        relationships.
    """
    return SchemaTopology(
        table_order=("regions", "customers", "accounts", "orders", "order_lines"),
        columns={
            "regions": (
                ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                ColumnInfo(name="name", type="varchar", primary_key=0, nullable=False),
            ),
            "customers": (
                ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                ColumnInfo(name="region_id", type="integer", primary_key=0, nullable=False),
                ColumnInfo(name="full_name", type="varchar", primary_key=0, nullable=False),
                ColumnInfo(name="email", type="varchar", primary_key=0, nullable=False),
            ),
            "accounts": (
                ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                ColumnInfo(name="customer_id", type="integer", primary_key=0, nullable=False),
                ColumnInfo(name="account_ref", type="varchar", primary_key=0, nullable=False),
            ),
            "orders": (
                ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                ColumnInfo(name="account_id", type="integer", primary_key=0, nullable=False),
                ColumnInfo(name="total_price", type="numeric", primary_key=0, nullable=False),
            ),
            "order_lines": (
                ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                ColumnInfo(name="order_id", type="integer", primary_key=0, nullable=False),
                ColumnInfo(name="product_sku", type="varchar", primary_key=0, nullable=False),
                ColumnInfo(name="quantity", type="integer", primary_key=0, nullable=False),
                ColumnInfo(name="unit_price", type="numeric", primary_key=0, nullable=False),
            ),
        },
        foreign_keys={
            "regions": (),
            "customers": (
                ForeignKeyInfo(
                    constrained_columns=("region_id",),
                    referred_table="regions",
                    referred_columns=("id",),
                ),
            ),
            "accounts": (
                ForeignKeyInfo(
                    constrained_columns=("customer_id",),
                    referred_table="customers",
                    referred_columns=("id",),
                ),
            ),
            "orders": (
                ForeignKeyInfo(
                    constrained_columns=("account_id",),
                    referred_table="accounts",
                    referred_columns=("id",),
                ),
            ),
            "order_lines": (
                ForeignKeyInfo(
                    constrained_columns=("order_id",),
                    referred_table="orders",
                    referred_columns=("id",),
                ),
            ),
        },
    )
