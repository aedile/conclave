"""E2E integration tests for masked subsetting pipeline.

Tests the full pipeline: SubsettingEngine + row_transformer (masking) over a
3-table FK hierarchy: persons → accounts → transactions.

Tests use an ephemeral PostgreSQL instance managed by ``pytest-postgresql``.

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH.  If absent, all tests are skipped via
  the ``_require_postgresql`` autouse fixture.

Marks: ``integration``

CONSTITUTION Priority 0: Security — no PII, parameterised SQL only.
CONSTITUTION Priority 3: TDD — E2E integration gate for P3-T3.5.
Task: P3-T3.5 -- Execute E2E Subsetting Subsystem Tests
"""

from __future__ import annotations

import shutil
from collections.abc import Generator
from typing import Any

import psycopg2
import pytest
from pytest_postgresql import factories
from sqlalchemy import create_engine, text

from synth_engine.modules.ingestion.core import SubsettingEngine
from synth_engine.modules.ingestion.egress import EgressWriter
from synth_engine.modules.masking.algorithms import mask_email, mask_name, mask_ssn
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
            "pg_ctl not found on PATH — install PostgreSQL to run E2E subsetting tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Masking transformer (injected from test layer — no ingestion → masking import)
# ---------------------------------------------------------------------------

_SALT = "e2e-test-salt"

# Mapping: table → {column → masking function(value, salt) -> str}
_COLUMN_MASKS: dict[str, dict[str, Any]] = {
    "persons": {
        "full_name": mask_name,
        "email": mask_email,
        "ssn": mask_ssn,
    }
}


def _mask_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """Apply deterministic masking to PII columns in the given row.

    Non-PII tables and non-PII columns are returned unchanged.

    Args:
        table: The table name; used to look up which columns to mask.
        row: A single row dict from the source database.

    Returns:
        A new row dict with PII columns replaced by deterministic masked values.
    """
    masks = _COLUMN_MASKS.get(table, {})
    if not masks:
        return row
    result = dict(row)
    for col, fn in masks.items():
        if col in result and result[col] is not None:
            result[col] = fn(str(result[col]), _SALT)
    return result


# ---------------------------------------------------------------------------
# Database names
# ---------------------------------------------------------------------------

_E2E_SOURCE_DBNAME = "conclave_e2e_source"
_E2E_TARGET_DBNAME = "conclave_e2e_target"
_E2E_DETERM_SOURCE_DBNAME = "conclave_e2e_determ_source"
_E2E_DETERM_TARGET_DBNAME = "conclave_e2e_determ_target"
_E2E_PASSTHRU_SOURCE_DBNAME = "conclave_e2e_passthru_source"
_E2E_PASSTHRU_TARGET_DBNAME = "conclave_e2e_passthru_target"


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


def _create_database(
    proc: factories.postgresql_proc,  # type: ignore[valid-type]
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
    proc: factories.postgresql_proc,  # type: ignore[valid-type]
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


def _create_pii_schema(
    proc: factories.postgresql_proc,  # type: ignore[valid-type]
    dbname: str,
    *,
    with_serial: bool = False,
) -> None:
    """Create the persons → accounts → transactions schema in the given DB.

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
            CREATE TABLE IF NOT EXISTS persons (
                id        {pk_type} PRIMARY KEY,
                full_name VARCHAR(100) NOT NULL,
                email     VARCHAR(150) NOT NULL,
                ssn       CHAR(11) NOT NULL
            )
            """  # nosec B608
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS accounts (
                id             {pk_type} PRIMARY KEY,
                person_id      INTEGER NOT NULL REFERENCES persons(id),
                account_number VARCHAR(20) NOT NULL
            )
            """  # nosec B608
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS transactions (
                id          {pk_type} PRIMARY KEY,
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                amount      NUMERIC(10, 2) NOT NULL,
                description VARCHAR(200)
            )
            """  # nosec B608
        )
    conn.close()


def _populate_pii_source(
    proc: factories.postgresql_proc,  # type: ignore[valid-type]
    dbname: str,
    *,
    num_persons: int = 20,
) -> None:
    """Populate source DB with persons, accounts, and transactions.

    Inserts ``num_persons`` persons, 2 accounts per person, and 3 transactions
    per account.  All PII values are fictional.

    Args:
        proc: The postgresql_proc executor.
        dbname: Source database name (schema must already exist).
        num_persons: Number of person rows to insert (default 20).
    """
    conn = _connect_pg(proc, dbname)
    with conn.cursor() as cur:
        for n in range(1, num_persons + 1):
            cur.execute(
                "INSERT INTO persons (full_name, email, ssn) VALUES (%s, %s, %s) RETURNING id",
                (
                    f"Person-{n}",
                    f"person_{n}@example.com",
                    f"123-45-{n:04d}",
                ),
            )
            person_id = cur.fetchone()[0]  # type: ignore[index]
            for a in range(1, 3):
                cur.execute(
                    "INSERT INTO accounts (person_id, account_number) VALUES (%s, %s) RETURNING id",
                    (person_id, f"ACCT-{person_id:04d}-{a:02d}"),
                )
                account_id = cur.fetchone()[0]  # type: ignore[index]
                for t in range(1, 4):
                    cur.execute(
                        "INSERT INTO transactions (account_id, amount, description) "
                        "VALUES (%s, %s, %s)",
                        (account_id, 100 * t, f"Txn-{account_id}-{t}"),
                    )
    conn.close()


def _truncate_target(
    proc: factories.postgresql_proc,  # type: ignore[valid-type]
    dbname: str,
) -> None:
    """Truncate all tables in the target database.

    Args:
        proc: The postgresql_proc executor.
        dbname: Target database name.
    """
    conn = _connect_pg(proc, dbname)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE transactions, accounts, persons CASCADE")
    conn.close()


def _make_pii_topology() -> SchemaTopology:
    """Build the persons → accounts → transactions SchemaTopology.

    Returns:
        A SchemaTopology value object for the 3-table PII hierarchy.
    """
    return SchemaTopology(
        table_order=("persons", "accounts", "transactions"),
        columns={
            "persons": (
                ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                ColumnInfo(name="full_name", type="varchar", primary_key=0, nullable=False),
                ColumnInfo(name="email", type="varchar", primary_key=0, nullable=False),
                ColumnInfo(name="ssn", type="varchar", primary_key=0, nullable=False),
            ),
            "accounts": (
                ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                ColumnInfo(name="person_id", type="integer", primary_key=0, nullable=False),
                ColumnInfo(name="account_number", type="varchar", primary_key=0, nullable=False),
            ),
            "transactions": (
                ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                ColumnInfo(name="account_id", type="integer", primary_key=0, nullable=False),
                ColumnInfo(name="amount", type="numeric", primary_key=0, nullable=False),
                ColumnInfo(name="description", type="varchar", primary_key=0, nullable=True),
            ),
        },
        foreign_keys={
            "persons": (),
            "accounts": (
                ForeignKeyInfo(
                    constrained_columns=("person_id",),
                    referred_table="persons",
                    referred_columns=("id",),
                ),
            ),
            "transactions": (
                ForeignKeyInfo(
                    constrained_columns=("account_id",),
                    referred_table="accounts",
                    referred_columns=("id",),
                ),
            ),
        },
    )


# ---------------------------------------------------------------------------
# Module-scoped fixture: provision source and target for masking + passthru
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def e2e_dbs(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]
) -> Generator[tuple[str, str]]:
    """Create source + target databases; yield their connection URLs.

    Source DB seeded with 20 persons / 40 accounts / 120 transactions.

    Args:
        postgresql_proc: The running PostgreSQL process executor.

    Yields:
        Tuple of (source_url, target_url) as SQLAlchemy connection strings.
    """
    proc = postgresql_proc

    _create_database(proc, _E2E_SOURCE_DBNAME)
    _create_database(proc, _E2E_TARGET_DBNAME)

    src_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_E2E_SOURCE_DBNAME}"
    )
    tgt_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_E2E_TARGET_DBNAME}"
    )

    _create_pii_schema(proc, _E2E_SOURCE_DBNAME, with_serial=True)
    _populate_pii_source(proc, _E2E_SOURCE_DBNAME)
    _create_pii_schema(proc, _E2E_TARGET_DBNAME, with_serial=False)

    yield src_url, tgt_url

    _drop_database(proc, _E2E_SOURCE_DBNAME)
    _drop_database(proc, _E2E_TARGET_DBNAME)


@pytest.fixture(scope="module")
def e2e_determ_dbs(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]
) -> Generator[tuple[str, str]]:
    """Create isolated source + target databases for the determinism test.

    Args:
        postgresql_proc: The running PostgreSQL process executor.

    Yields:
        Tuple of (source_url, target_url) as SQLAlchemy connection strings.
    """
    proc = postgresql_proc

    _create_database(proc, _E2E_DETERM_SOURCE_DBNAME)
    _create_database(proc, _E2E_DETERM_TARGET_DBNAME)

    src_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_E2E_DETERM_SOURCE_DBNAME}"
    )
    tgt_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_E2E_DETERM_TARGET_DBNAME}"
    )

    _create_pii_schema(proc, _E2E_DETERM_SOURCE_DBNAME, with_serial=True)
    _populate_pii_source(proc, _E2E_DETERM_SOURCE_DBNAME)
    _create_pii_schema(proc, _E2E_DETERM_TARGET_DBNAME, with_serial=False)

    yield src_url, tgt_url

    _drop_database(proc, _E2E_DETERM_SOURCE_DBNAME)
    _drop_database(proc, _E2E_DETERM_TARGET_DBNAME)


@pytest.fixture(scope="module")
def e2e_passthru_dbs(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]
) -> Generator[tuple[str, str]]:
    """Create isolated source + target databases for the passthrough test.

    Args:
        postgresql_proc: The running PostgreSQL process executor.

    Yields:
        Tuple of (source_url, target_url) as SQLAlchemy connection strings.
    """
    proc = postgresql_proc

    _create_database(proc, _E2E_PASSTHRU_SOURCE_DBNAME)
    _create_database(proc, _E2E_PASSTHRU_TARGET_DBNAME)

    src_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_E2E_PASSTHRU_SOURCE_DBNAME}"
    )
    tgt_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_E2E_PASSTHRU_TARGET_DBNAME}"
    )

    _create_pii_schema(proc, _E2E_PASSTHRU_SOURCE_DBNAME, with_serial=True)
    _populate_pii_source(proc, _E2E_PASSTHRU_SOURCE_DBNAME)
    _create_pii_schema(proc, _E2E_PASSTHRU_TARGET_DBNAME, with_serial=False)

    yield src_url, tgt_url

    _drop_database(proc, _E2E_PASSTHRU_SOURCE_DBNAME)
    _drop_database(proc, _E2E_PASSTHRU_TARGET_DBNAME)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_e2e_subset_applies_masking(
    e2e_dbs: tuple[str, str],
) -> None:
    """Subset 5 persons with masking; assert referential integrity + PII masked.

    Seed: 20 persons, 40 accounts, 120 transactions.
    Subset: LIMIT 5 persons → expect 10 accounts, 30 transactions.

    Assert:
    - Target persons table has exactly 5 rows.
    - Target accounts table has exactly 10 rows (2 per person).
    - Target transactions table has exactly 30 rows (3 per account).
    - No orphaned FK references (accounts → persons, transactions → accounts).
    - At least one full_name in target differs from source (masking applied).
    - At least one email in target differs from source.
    - At least one ssn in target differs from source.
    """
    src_url, tgt_url = e2e_dbs
    topology = _make_pii_topology()

    src_engine = create_engine(src_url)
    tgt_engine = create_engine(tgt_url)

    egress = EgressWriter(target_engine=tgt_engine)
    se = SubsettingEngine(
        source_engine=src_engine,
        topology=topology,
        egress=egress,
        row_transformer=_mask_row,
    )

    result = se.run(
        seed_table="persons",
        seed_query="SELECT * FROM persons ORDER BY id LIMIT 5",  # nosec B608
    )

    # --- SubsetResult counts ---
    assert result.row_counts.get("persons") == 5
    assert result.row_counts.get("accounts") == 10
    assert result.row_counts.get("transactions") == 30

    # --- Target table counts ---
    with tgt_engine.connect() as conn:
        persons_count = conn.execute(text("SELECT COUNT(*) FROM persons")).scalar()  # nosec B608
        accounts_count = conn.execute(
            text("SELECT COUNT(*) FROM accounts")  # nosec B608
        ).scalar()
        txn_count = conn.execute(
            text("SELECT COUNT(*) FROM transactions")  # nosec B608
        ).scalar()

    assert persons_count == 5, f"Expected 5 persons, got {persons_count}"
    assert accounts_count == 10, f"Expected 10 accounts, got {accounts_count}"
    assert txn_count == 30, f"Expected 30 transactions, got {txn_count}"

    # --- FK integrity ---
    with tgt_engine.connect() as conn:
        orphaned_accounts = conn.execute(
            text(  # nosec B608
                "SELECT COUNT(*) FROM accounts a "
                "WHERE NOT EXISTS (SELECT 1 FROM persons p WHERE p.id = a.person_id)"
            )
        ).scalar()
        orphaned_txns = conn.execute(
            text(  # nosec B608
                "SELECT COUNT(*) FROM transactions t "
                "WHERE NOT EXISTS (SELECT 1 FROM accounts a WHERE a.id = t.account_id)"
            )
        ).scalar()

    assert orphaned_accounts == 0, f"Orphaned accounts: {orphaned_accounts}"
    assert orphaned_txns == 0, f"Orphaned transactions: {orphaned_txns}"

    # --- Masking was applied: at least one value differs from source ---
    with src_engine.connect() as conn:
        src_persons = {
            row["id"]: row
            for row in conn.execute(
                text("SELECT id, full_name, email, ssn FROM persons ORDER BY id LIMIT 5")  # nosec B608
            ).mappings()
        }
    with tgt_engine.connect() as conn:
        tgt_persons = {
            row["id"]: row
            for row in conn.execute(
                text("SELECT id, full_name, email, ssn FROM persons ORDER BY id")  # nosec B608
            ).mappings()
        }

    names_differ = any(
        tgt_persons[pid]["full_name"] != src_persons[pid]["full_name"] for pid in tgt_persons
    )
    emails_differ = any(
        tgt_persons[pid]["email"] != src_persons[pid]["email"] for pid in tgt_persons
    )
    ssns_differ = any(tgt_persons[pid]["ssn"] != src_persons[pid]["ssn"] for pid in tgt_persons)

    assert names_differ, "No full_name values were masked — transformer may not have run"
    assert emails_differ, "No email values were masked — transformer may not have run"
    assert ssns_differ, "No ssn values were masked — transformer may not have run"

    src_engine.dispose()
    tgt_engine.dispose()


@pytest.mark.integration
def test_e2e_masking_is_deterministic(
    e2e_determ_dbs: tuple[str, str],
) -> None:
    """Two identical runs produce identical masked values in the target.

    Run SubsettingEngine twice on the same source with the same transformer,
    clearing the target between runs.  Assert that all PII columns in the
    target are identical after both runs.
    """
    src_url, tgt_url = e2e_determ_dbs
    topology = _make_pii_topology()

    src_engine = create_engine(src_url)
    tgt_engine = create_engine(tgt_url)

    def _run_once() -> None:
        """Execute one full subset run with masking transformer."""
        egress = EgressWriter(target_engine=tgt_engine)
        se = SubsettingEngine(
            source_engine=src_engine,
            topology=topology,
            egress=egress,
            row_transformer=_mask_row,
        )
        se.run(
            seed_table="persons",
            seed_query="SELECT * FROM persons ORDER BY id LIMIT 5",  # nosec B608
        )

    def _read_persons() -> list[dict[str, Any]]:
        """Read all persons rows from the target, ordered by id."""
        with tgt_engine.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    text(  # nosec B608
                        "SELECT id, full_name, email, ssn FROM persons ORDER BY id"
                    )
                ).mappings()
            ]

    # First run
    _run_once()
    first_run = _read_persons()

    # Clear target between runs
    with tgt_engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE transactions, accounts, persons CASCADE"))  # nosec B608
        conn.commit()

    # Second run
    _run_once()
    second_run = _read_persons()

    assert len(first_run) == len(second_run) == 5

    for first, second in zip(first_run, second_run, strict=True):
        assert first["full_name"] == second["full_name"], (
            f"Non-deterministic full_name for id={first['id']}: "
            f"{first['full_name']!r} vs {second['full_name']!r}"
        )
        assert first["email"] == second["email"], (
            f"Non-deterministic email for id={first['id']}: "
            f"{first['email']!r} vs {second['email']!r}"
        )
        assert first["ssn"] == second["ssn"], (
            f"Non-deterministic ssn for id={first['id']}: {first['ssn']!r} vs {second['ssn']!r}"
        )

    src_engine.dispose()
    tgt_engine.dispose()


@pytest.mark.integration
def test_e2e_non_pii_columns_unchanged(
    e2e_passthru_dbs: tuple[str, str],
) -> None:
    """Non-PII columns pass through the transformer unchanged.

    Assert:
    - accounts.account_number values in target match source exactly.
    - transactions.amount values in target match source exactly.
    """
    src_url, tgt_url = e2e_passthru_dbs
    topology = _make_pii_topology()

    src_engine = create_engine(src_url)
    tgt_engine = create_engine(tgt_url)

    egress = EgressWriter(target_engine=tgt_engine)
    se = SubsettingEngine(
        source_engine=src_engine,
        topology=topology,
        egress=egress,
        row_transformer=_mask_row,
    )

    se.run(
        seed_table="persons",
        seed_query="SELECT * FROM persons ORDER BY id LIMIT 5",  # nosec B608
    )

    # Fetch account_number from source (first 10 accounts belonging to persons 1-5)
    with src_engine.connect() as conn:
        src_account_numbers = sorted(
            row["account_number"]
            for row in conn.execute(
                text(  # nosec B608
                    "SELECT a.account_number FROM accounts a "
                    "JOIN persons p ON p.id = a.person_id "
                    "WHERE p.id IN (SELECT id FROM persons ORDER BY id LIMIT 5)"
                )
            ).mappings()
        )

    with tgt_engine.connect() as conn:
        tgt_account_numbers = sorted(
            row["account_number"]
            for row in conn.execute(
                text("SELECT account_number FROM accounts")  # nosec B608
            ).mappings()
        )

    assert tgt_account_numbers == src_account_numbers, (
        "account_number values changed — non-PII passthrough broken"
    )

    # Fetch transaction amounts from source
    with src_engine.connect() as conn:
        src_amounts = sorted(
            float(row["amount"])
            for row in conn.execute(
                text(  # nosec B608
                    "SELECT t.amount FROM transactions t "
                    "JOIN accounts a ON a.id = t.account_id "
                    "JOIN persons p ON p.id = a.person_id "
                    "WHERE p.id IN (SELECT id FROM persons ORDER BY id LIMIT 5)"
                )
            ).mappings()
        )

    with tgt_engine.connect() as conn:
        tgt_amounts = sorted(
            float(row["amount"])
            for row in conn.execute(
                text("SELECT amount FROM transactions")  # nosec B608
            ).mappings()
        )

    assert tgt_amounts == src_amounts, (
        "transaction.amount values changed — non-PII passthrough broken"
    )

    src_engine.dispose()
    tgt_engine.dispose()
