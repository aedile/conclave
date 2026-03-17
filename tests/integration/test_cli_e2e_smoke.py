"""E2E smoke test for the CLI subset+mask pipeline against the real sample schema.

Exercises the full CLI ``subset --mask`` pipeline against the customers
→ orders → order_items → payments schema that mirrors the project's
``sample_data/`` directory.  This test closes the structural gap exposed
by P21: a table-name mismatch (``"persons"`` vs ``"customers"``) and a
masking format bug (``mask_name`` producing "First Last" for ``first_name``
columns) survived 1000+ unit tests but were caught instantly by a manual
E2E run.

What is tested
--------------
1. CLI exits 0.
2. Masking applied to all ``_COLUMN_MASKS["customers"]`` columns.
3. Masking format correctness — ``first_name`` and ``last_name`` are single
   words; ``email`` contains ``@``; ``ssn`` matches ``\\d{3}-\\d{2}-\\d{4}``.
4. Foreign-key referential integrity preserved in target.
5. Row counts match expected traversal results.
6. Non-PII columns pass through unchanged.
7. Config-drift detection — ``_COLUMN_MASKS["customers"]`` keys are all
   valid column names in the live source schema.

Tests use an ephemeral PostgreSQL instance managed by ``pytest-postgresql``.

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH.  If absent, all tests are skipped via
  the ``_require_postgresql`` autouse fixture.

Marks: ``integration``

CONSTITUTION Priority 0: Security — no PII, parameterised SQL only.
CONSTITUTION Priority 3: TDD — E2E integration gate for P21-T21.3.
Task: P21-T21.3 — Automated E2E Smoke Test for CLI Subset+Mask Pipeline
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import psycopg2
import pytest
from click.testing import CliRunner
from pytest_postgresql import factories
from sqlalchemy import create_engine, text

from synth_engine.bootstrapper.cli import _COLUMN_MASKS, subset
from synth_engine.shared.schema_topology import (
    ColumnInfo,
    ForeignKeyInfo,
    SchemaTopology,
)
from tests.conftest_types import PostgreSQLProc

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
            "pg_ctl not found on PATH — install PostgreSQL to run E2E smoke tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Database names
# ---------------------------------------------------------------------------

_SMOKE_SOURCE_DBNAME = "conclave_smoke_source"
_SMOKE_TARGET_DBNAME = "conclave_smoke_target"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connect_pg(
    proc: PostgreSQLProc,
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


def _create_database(
    proc: PostgreSQLProc,
    dbname: str,
) -> None:
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


def _drop_database(
    proc: PostgreSQLProc,
    dbname: str,
) -> None:
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


def _create_customers_schema(
    proc: PostgreSQLProc,
    dbname: str,
    *,
    with_serial: bool = False,
) -> None:
    """Create the customers → orders → order_items → payments schema.

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
            CREATE TABLE IF NOT EXISTS customers (
                id         {pk_type} PRIMARY KEY,
                first_name VARCHAR(100) NOT NULL,
                last_name  VARCHAR(100) NOT NULL,
                email      VARCHAR(150) NOT NULL,
                ssn        CHAR(11) NOT NULL,
                phone      VARCHAR(30) NOT NULL,
                address    TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """  # nosec B608
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS orders (
                id           {pk_type} PRIMARY KEY,
                customer_id  INTEGER NOT NULL REFERENCES customers(id),
                order_date   TIMESTAMP NOT NULL DEFAULT NOW(),
                total_amount NUMERIC(12, 2) NOT NULL,
                status       VARCHAR(30) NOT NULL
            )
            """  # nosec B608
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS order_items (
                id           {pk_type} PRIMARY KEY,
                order_id     INTEGER NOT NULL REFERENCES orders(id),
                product_name VARCHAR(200) NOT NULL,
                quantity     INTEGER NOT NULL,
                unit_price   NUMERIC(10, 2) NOT NULL
            )
            """  # nosec B608
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS payments (
                id             {pk_type} PRIMARY KEY,
                order_id       INTEGER NOT NULL REFERENCES orders(id),
                payment_date   TIMESTAMP NOT NULL DEFAULT NOW(),
                amount         NUMERIC(12, 2) NOT NULL,
                payment_method VARCHAR(30) NOT NULL
            )
            """  # nosec B608
        )
    conn.close()


def _populate_customers_source(
    proc: PostgreSQLProc,
    dbname: str,
) -> None:
    """Populate source DB with fictional customers, orders, order_items, payments.

    Inserts 5 customers, 3 orders (across first 3 customers), 5 order_items,
    and 3 payments.  All values are fictional and safe to commit.

    Args:
        proc: The postgresql_proc executor.
        dbname: Source database name (schema must already exist).
    """
    conn = _connect_pg(proc, dbname)
    with conn.cursor() as cur:
        # 5 fictional customers — no real PII
        customers = [
            ("Alice", "Wonderland", "alice@example.test", "123-45-6789", "555-0101", "1 Main St"),
            ("Bob", "Builder", "bob@example.test", "234-56-7890", "555-0102", "2 Oak Ave"),
            ("Carol", "Danvers", "carol@example.test", "345-67-8901", "555-0103", "3 Elm Blvd"),
            ("Dave", "Bowman", "dave@example.test", "456-78-9012", "555-0104", "4 Pine Rd"),
            ("Eve", "Polastri", "eve@example.test", "567-89-0123", "555-0105", "5 Cedar Ln"),
        ]
        customer_ids: list[int] = []
        for first, last, email, ssn, phone, address in customers:
            cur.execute(
                "INSERT INTO customers (first_name, last_name, email, ssn, phone, address)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (first, last, email, ssn, phone, address),
            )
            row = cur.fetchone()
            assert row is not None
            customer_ids.append(row[0])

        # 3 orders across first 3 customers
        order_ids: list[int] = []
        orders = [
            (customer_ids[0], "99.99", "pending"),
            (customer_ids[1], "149.50", "shipped"),
            (customer_ids[2], "24.00", "delivered"),
        ]
        for cust_id, amount, status in orders:
            cur.execute(
                "INSERT INTO orders (customer_id, total_amount, status)"
                " VALUES (%s, %s, %s) RETURNING id",
                (cust_id, amount, status),
            )
            row = cur.fetchone()
            assert row is not None
            order_ids.append(row[0])

        # 5 order_items spread across the 3 orders
        items = [
            (order_ids[0], "Widget A", 2, "19.99"),
            (order_ids[0], "Widget B", 1, "60.01"),
            (order_ids[1], "Gadget X", 3, "49.83"),
            (order_ids[2], "Gizmo Y", 1, "10.00"),
            (order_ids[2], "Gizmo Z", 2, "7.00"),
        ]
        for ord_id, product, qty, price in items:
            cur.execute(
                "INSERT INTO order_items (order_id, product_name, quantity, unit_price)"
                " VALUES (%s, %s, %s, %s)",
                (ord_id, product, qty, price),
            )

        # 3 payments (one per order)
        payments = [
            (order_ids[0], "99.99", "credit_card"),
            (order_ids[1], "149.50", "paypal"),
            (order_ids[2], "24.00", "bank_transfer"),
        ]
        for ord_id, amount, method in payments:
            cur.execute(
                "INSERT INTO payments (order_id, amount, payment_method) VALUES (%s, %s, %s)",
                (ord_id, amount, method),
            )
    conn.close()


def _make_customers_topology() -> SchemaTopology:
    """Build the customers → orders → order_items / payments SchemaTopology.

    Returns:
        A SchemaTopology value object for the 4-table sample-data hierarchy.
    """
    return SchemaTopology(
        table_order=("customers", "orders", "order_items", "payments"),
        columns={
            "customers": (
                ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                ColumnInfo(name="first_name", type="varchar", primary_key=0, nullable=False),
                ColumnInfo(name="last_name", type="varchar", primary_key=0, nullable=False),
                ColumnInfo(name="email", type="varchar", primary_key=0, nullable=False),
                ColumnInfo(name="ssn", type="varchar", primary_key=0, nullable=False),
                ColumnInfo(name="phone", type="varchar", primary_key=0, nullable=False),
                ColumnInfo(name="address", type="text", primary_key=0, nullable=False),
                ColumnInfo(name="created_at", type="timestamp", primary_key=0, nullable=False),
            ),
            "orders": (
                ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                ColumnInfo(name="customer_id", type="integer", primary_key=0, nullable=False),
                ColumnInfo(name="order_date", type="timestamp", primary_key=0, nullable=False),
                ColumnInfo(name="total_amount", type="numeric", primary_key=0, nullable=False),
                ColumnInfo(name="status", type="varchar", primary_key=0, nullable=False),
            ),
            "order_items": (
                ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                ColumnInfo(name="order_id", type="integer", primary_key=0, nullable=False),
                ColumnInfo(name="product_name", type="varchar", primary_key=0, nullable=False),
                ColumnInfo(name="quantity", type="integer", primary_key=0, nullable=False),
                ColumnInfo(name="unit_price", type="numeric", primary_key=0, nullable=False),
            ),
            "payments": (
                ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                ColumnInfo(name="order_id", type="integer", primary_key=0, nullable=False),
                ColumnInfo(name="payment_date", type="timestamp", primary_key=0, nullable=False),
                ColumnInfo(name="amount", type="numeric", primary_key=0, nullable=False),
                ColumnInfo(name="payment_method", type="varchar", primary_key=0, nullable=False),
            ),
        },
        foreign_keys={
            "customers": (),
            "orders": (
                ForeignKeyInfo(
                    constrained_columns=("customer_id",),
                    referred_table="customers",
                    referred_columns=("id",),
                ),
            ),
            "order_items": (
                ForeignKeyInfo(
                    constrained_columns=("order_id",),
                    referred_table="orders",
                    referred_columns=("id",),
                ),
            ),
            "payments": (
                ForeignKeyInfo(
                    constrained_columns=("order_id",),
                    referred_table="orders",
                    referred_columns=("id",),
                ),
            ),
        },
    )


# ---------------------------------------------------------------------------
# Module-scoped fixture: provision source and target databases
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def smoke_dbs(
    postgresql_proc: PostgreSQLProc,
) -> Generator[tuple[str, str]]:
    """Create source + target databases; yield their connection URLs.

    Source seeded with 5 customers / 3 orders / 5 order_items / 3 payments.
    Target schema created empty (no data) for the CLI to populate.

    Args:
        postgresql_proc: The running PostgreSQL process executor.

    Yields:
        Tuple of (source_url, target_url) as SQLAlchemy connection strings.
    """
    proc = postgresql_proc

    _create_database(proc, _SMOKE_SOURCE_DBNAME)
    _create_database(proc, _SMOKE_TARGET_DBNAME)

    src_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_SMOKE_SOURCE_DBNAME}"
    )
    tgt_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_SMOKE_TARGET_DBNAME}"
    )

    _create_customers_schema(proc, _SMOKE_SOURCE_DBNAME, with_serial=True)
    _populate_customers_source(proc, _SMOKE_SOURCE_DBNAME)
    _create_customers_schema(proc, _SMOKE_TARGET_DBNAME, with_serial=False)

    yield src_url, tgt_url

    _drop_database(proc, _SMOKE_SOURCE_DBNAME)
    _drop_database(proc, _SMOKE_TARGET_DBNAME)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_smoke_config_keys_match_source_schema(
    smoke_dbs: tuple[str, str],
) -> None:
    """Verify _COLUMN_MASKS["customers"] keys are all real column names.

    This is the guard that would have caught the P21-T21.1 bug where the
    masking config referenced "persons" instead of "customers".  If the
    config key set is not a subset of the actual columns, this test fails
    immediately — long before any masking or subsetting is attempted.

    Asserts:
    - Every key in ``_COLUMN_MASKS["customers"]`` exists in the
      ``customers`` table of the source database.
    """
    src_url, _ = smoke_dbs
    engine = create_engine(src_url)

    with engine.connect() as conn:
        result = conn.execute(
            text(  # nosec B608
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = 'customers'"
            )
        )
        actual_columns = {row[0] for row in result}

    engine.dispose()

    config_columns = set(_COLUMN_MASKS.get("customers", {}).keys())
    assert config_columns, "_COLUMN_MASKS has no 'customers' entry — table name mismatch?"

    unknown = config_columns - actual_columns
    assert not unknown, (
        f"_COLUMN_MASKS['customers'] references columns not in the live schema: {unknown!r}. "
        f"Live columns: {sorted(actual_columns)}"
    )


@pytest.mark.integration
def test_smoke_cli_exits_zero_and_writes_correct_row_counts(
    smoke_dbs: tuple[str, str],
) -> None:
    """CLI subset --mask exits 0 and writes the expected row counts.

    Seeds with all 5 customers; expects all dependent rows to be written.

    Asserts:
    - Exit code is 0.
    - ``customers``: 5 rows.
    - ``orders``: 3 rows.
    - ``order_items``: 5 rows.
    - ``payments``: 3 rows.
    """
    src_url, tgt_url = smoke_dbs
    topology = _make_customers_topology()

    runner = CliRunner()
    with patch("synth_engine.bootstrapper.cli._load_topology", return_value=topology):
        result = runner.invoke(
            subset,
            [
                "--source",
                src_url,
                "--target",
                tgt_url,
                "--seed-table",
                "customers",
                "--seed-query",
                "SELECT * FROM customers ORDER BY id",  # nosec B608
                "--mask",
            ],
        )

    assert result.exit_code == 0, (
        f"CLI exited with code {result.exit_code}. Output:\n{result.output}"
    )
    assert "Subset complete" in result.output

    tgt_engine = create_engine(tgt_url)
    with tgt_engine.connect() as conn:
        customers_count = conn.execute(text("SELECT COUNT(*) FROM customers")).scalar()  # nosec B608
        orders_count = conn.execute(text("SELECT COUNT(*) FROM orders")).scalar()  # nosec B608
        items_count = conn.execute(text("SELECT COUNT(*) FROM order_items")).scalar()  # nosec B608
        payments_count = conn.execute(text("SELECT COUNT(*) FROM payments")).scalar()  # nosec B608
    tgt_engine.dispose()

    assert customers_count == 5, f"Expected 5 customers, got {customers_count}"
    assert orders_count == 3, f"Expected 3 orders, got {orders_count}"
    assert items_count == 5, f"Expected 5 order_items, got {items_count}"
    assert payments_count == 3, f"Expected 3 payments, got {payments_count}"


@pytest.mark.integration
def test_smoke_all_pii_columns_masked(
    smoke_dbs: tuple[str, str],
) -> None:
    """All _COLUMN_MASKS columns differ between source and target.

    Verifies that masking was actually applied for every PII column
    (first_name, last_name, email, ssn, phone, address).  Any column
    that is unchanged for all rows means the masking transformer did not
    run for that column.

    Asserts:
    - For each PII column, at least one row in target differs from source.
    """
    src_url, tgt_url = smoke_dbs
    src_engine = create_engine(src_url)
    tgt_engine = create_engine(tgt_url)

    pii_columns = list(_COLUMN_MASKS.get("customers", {}).keys())
    assert pii_columns, "_COLUMN_MASKS['customers'] is empty — nothing to verify"

    col_list = ", ".join(f'"{c}"' for c in ["id", *pii_columns])

    with src_engine.connect() as conn:
        src_rows: dict[int, dict[str, Any]] = {
            row["id"]: dict(row)
            for row in conn.execute(
                text(f"SELECT {col_list} FROM customers ORDER BY id")  # noqa: S608  # nosec B608
            ).mappings()
        }
    with tgt_engine.connect() as conn:
        tgt_rows: dict[int, dict[str, Any]] = {
            row["id"]: dict(row)
            for row in conn.execute(
                text(f"SELECT {col_list} FROM customers ORDER BY id")  # noqa: S608  # nosec B608
            ).mappings()
        }

    src_engine.dispose()
    tgt_engine.dispose()

    assert len(tgt_rows) == 5, f"Expected 5 target customers, got {len(tgt_rows)}"

    for col in pii_columns:
        values_differ = any(
            str(tgt_rows[pid][col]).strip() != str(src_rows[pid][col]).strip() for pid in tgt_rows
        )
        assert values_differ, (
            f"Column 'customers.{col}' was NOT masked — "
            f"all target values equal source values. "
            f"Masking transformer may have silently skipped this column."
        )


@pytest.mark.integration
def test_smoke_masking_format_correctness(
    smoke_dbs: tuple[str, str],
) -> None:
    """Masked PII columns conform to their expected output format.

    This test catches the P21-T21.2 bug class: mask_name() produces
    "First Last" (two words) for columns that should contain a single
    word (first_name, last_name).

    Asserts:
    - ``first_name``: single word — no spaces.
    - ``last_name``: single word — no spaces.
    - ``email``: contains ``@``.
    - ``ssn``: matches ``\\d{3}-\\d{2}-\\d{4}``.
    """
    src_url, tgt_url = smoke_dbs
    tgt_engine = create_engine(tgt_url)

    with tgt_engine.connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                text(  # nosec B608
                    "SELECT first_name, last_name, email, ssn FROM customers ORDER BY id"
                )
            ).mappings()
        ]
    tgt_engine.dispose()

    assert len(rows) == 5, f"Expected 5 target customers, got {len(rows)}"

    ssn_pattern = re.compile(r"^\d{3}-\d{2}-\d{4}$")

    for i, row in enumerate(rows):
        masked_first = str(row["first_name"])
        masked_last = str(row["last_name"])
        masked_email = str(row["email"])
        masked_ssn = str(row["ssn"]).strip()

        assert " " not in masked_first, (
            f"Row {i}: first_name {masked_first!r} contains a space — "
            f"mask_name() was used instead of mask_first_name() (P21-T21.2 regression)"
        )
        assert " " not in masked_last, (
            f"Row {i}: last_name {masked_last!r} contains a space — "
            f"mask_name() was used instead of mask_last_name() (P21-T21.2 regression)"
        )
        assert "@" in masked_email, (
            f"Row {i}: email {masked_email!r} does not contain '@' — email masking broken"
        )
        assert ssn_pattern.match(masked_ssn), (
            f"Row {i}: ssn {masked_ssn!r} does not match \\d{{3}}-\\d{{2}}-\\d{{4}}"
        )


@pytest.mark.integration
def test_smoke_referential_integrity_preserved(
    smoke_dbs: tuple[str, str],
) -> None:
    """FK referential integrity is preserved in the target after subset+mask.

    Asserts:
    - All ``orders.customer_id`` values reference a valid ``customers.id``.
    - All ``order_items.order_id`` values reference a valid ``orders.id``.
    - All ``payments.order_id`` values reference a valid ``orders.id``.
    """
    src_url, tgt_url = smoke_dbs
    tgt_engine = create_engine(tgt_url)

    with tgt_engine.connect() as conn:
        customers_count = conn.execute(
            text("SELECT COUNT(*) FROM customers")  # nosec B608
        ).scalar()
        orphaned_orders = conn.execute(
            text(  # nosec B608
                "SELECT COUNT(*) FROM orders o"
                " WHERE NOT EXISTS (SELECT 1 FROM customers c WHERE c.id = o.customer_id)"
            )
        ).scalar()
        orphaned_items = conn.execute(
            text(  # nosec B608
                "SELECT COUNT(*) FROM order_items oi"
                " WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.id = oi.order_id)"
            )
        ).scalar()
        orphaned_payments = conn.execute(
            text(  # nosec B608
                "SELECT COUNT(*) FROM payments p"
                " WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.id = p.order_id)"
            )
        ).scalar()
    tgt_engine.dispose()

    assert customers_count == 5, f"Expected 5 target customers, got {customers_count}"

    assert orphaned_orders == 0, f"Orphaned orders (no matching customer): {orphaned_orders}"
    assert orphaned_items == 0, f"Orphaned order_items (no matching order): {orphaned_items}"
    assert orphaned_payments == 0, f"Orphaned payments (no matching order): {orphaned_payments}"


@pytest.mark.integration
def test_smoke_non_pii_columns_unchanged(
    smoke_dbs: tuple[str, str],
) -> None:
    """Non-PII columns pass through the masking transformer unchanged.

    Asserts:
    - ``orders.total_amount`` values match source exactly.
    - ``orders.status`` values match source exactly.
    - ``order_items.product_name`` values match source exactly.
    - ``order_items.unit_price`` values match source exactly.
    - ``payments.payment_method`` values match source exactly.
    """
    src_url, tgt_url = smoke_dbs
    src_engine = create_engine(src_url)
    tgt_engine = create_engine(tgt_url)

    with src_engine.connect() as conn:
        src_orders = sorted(
            (float(row["total_amount"]), str(row["status"]))
            for row in conn.execute(
                text("SELECT total_amount, status FROM orders")  # nosec B608
            ).mappings()
        )
        src_items = sorted(
            (str(row["product_name"]), float(row["unit_price"]))
            for row in conn.execute(
                text("SELECT product_name, unit_price FROM order_items")  # nosec B608
            ).mappings()
        )
        src_payments = sorted(
            str(row["payment_method"])
            for row in conn.execute(
                text("SELECT payment_method FROM payments")  # nosec B608
            ).mappings()
        )

    with tgt_engine.connect() as conn:
        tgt_orders = sorted(
            (float(row["total_amount"]), str(row["status"]))
            for row in conn.execute(
                text("SELECT total_amount, status FROM orders")  # nosec B608
            ).mappings()
        )
        tgt_items = sorted(
            (str(row["product_name"]), float(row["unit_price"]))
            for row in conn.execute(
                text("SELECT product_name, unit_price FROM order_items")  # nosec B608
            ).mappings()
        )
        tgt_payments = sorted(
            str(row["payment_method"])
            for row in conn.execute(
                text("SELECT payment_method FROM payments")  # nosec B608
            ).mappings()
        )

    src_engine.dispose()
    tgt_engine.dispose()

    assert len(tgt_orders) == 3, f"Expected 3 target orders, got {len(tgt_orders)}"
    assert len(tgt_items) == 5, f"Expected 5 target order_items, got {len(tgt_items)}"
    assert len(tgt_payments) == 3, f"Expected 3 target payments, got {len(tgt_payments)}"

    assert tgt_orders == src_orders, (
        "orders.total_amount or orders.status changed — non-PII passthrough broken"
    )
    assert tgt_items == src_items, (
        "order_items.product_name or order_items.unit_price changed — non-PII passthrough broken"
    )
    assert tgt_payments == src_payments, (
        "payments.payment_method changed — non-PII passthrough broken"
    )
