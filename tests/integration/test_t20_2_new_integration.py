"""T20.2 — New integration tests: PostgreSQL-backed ingestion, subsetting masking,
and real SDV/CTGAN training path.

Acceptance criteria addressed by this module:
- AC1: Ingestion adapter pre-flight privilege check against real PostgreSQL.
       (already exists in test_ingestion_integration.py — this module adds the
       new "masking deterministic output verified in real PostgreSQL write" test)
- AC2: Subsetting engine FK traversal against real PostgreSQL schema.
       (already exists in test_subsetting_integration.py — this module adds the
       "masking deterministic output in real PostgreSQL write" test that was
       explicitly listed as AC1/3 coverage gap)
- AC3: Masking engine deterministic output verified in real PostgreSQL write.
- AC1 (SDV): At least 1 integration test exercising real SDV/CTGAN training.
- AC4: Fixture singleton teardown pattern reviewed — setup verification added.

All PostgreSQL tests use ``pytest-postgresql`` for real ephemeral database
instances (not mocks or in-memory SQLite).

Task: P20-T20.2 — Integration Test Expansion (Real Infrastructure)
CONSTITUTION Priority 3: TDD
CONSTITUTION Priority 4: 90%+ test coverage
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Generator
from typing import Any

import psycopg2
import pytest
from pytest_postgresql import factories

from tests.conftest_types import PostgreSQLProc

# ---------------------------------------------------------------------------
# pytest-postgresql process fixture
# ---------------------------------------------------------------------------

postgresql_proc = factories.postgresql_proc()

# ---------------------------------------------------------------------------
# Database names for test isolation
# ---------------------------------------------------------------------------

_MASKING_DBNAME = "conclave_masking_integration"
_MASKING_USER = "masking_readonly_tester"
_MASKING_PASS = "masking_readonly_pass"  # pragma: allowlist secret  # nosec B105

_INGESTION_PREFLIGHT_DBNAME = "conclave_ingestion_preflight_t202"
_INGESTION_READONLY_USER = "ingestion_preflight_tester"
_INGESTION_READONLY_PASS = "ingestion_preflight_pass"  # pragma: allowlist secret  # nosec B105

_SUBSETTING_FK_DBNAME = "conclave_subsetting_fk_t202"

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
            "pg_ctl not found on PATH — install PostgreSQL to run T20.2 integration tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connect_pg(
    proc: PostgreSQLProc,
    dbname: str = "postgres",
) -> psycopg2.extensions.connection:
    """Open an autocommit psycopg2 superuser connection to the ephemeral PG instance.

    Args:
        proc: The postgresql_proc executor providing host/port/user/password.
        dbname: Name of the database to connect to (defaults to "postgres").

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


def _create_database_if_not_exists(proc: PostgreSQLProc, dbname: str) -> None:
    """Create a database on the ephemeral PG instance if it does not already exist.

    Args:
        proc: The postgresql_proc executor.
        dbname: Name of the database to create.
    """
    conn = _connect_pg(proc)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if not cur.fetchone():
            cur.execute(
                "CREATE DATABASE "  # nosec B608
                + psycopg2.extensions.quote_ident(dbname, cur)
            )
    conn.close()


def _drop_database(proc: PostgreSQLProc, dbname: str) -> None:
    """Terminate connections to and drop a database on the ephemeral PG instance.

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


# ===========================================================================
# AC3 — Masking engine deterministic output verified in real PostgreSQL write
# ===========================================================================


@pytest.fixture(scope="module")
def masking_pg_db(
    postgresql_proc: PostgreSQLProc,
) -> Generator[tuple[str, str]]:
    """Provision a real PostgreSQL database with a masked_users table.

    Creates the ``conclave_masking_integration`` database and a ``masked_users``
    table.  Yields the superuser URL and the read-only user URL.  Tears down on
    exit.

    Setup verification: asserts the table exists and is empty before yielding,
    so that a misconfigured test environment is detected immediately at fixture
    setup time rather than as a false-negative test pass.

    Args:
        postgresql_proc: The running PostgreSQL process executor.

    Yields:
        Tuple of (superuser_url, readonly_url) as SQLAlchemy-compatible
        connection strings for the masking integration database.
    """
    proc = postgresql_proc
    _create_database_if_not_exists(proc, _MASKING_DBNAME)

    conn = _connect_pg(proc, _MASKING_DBNAME)
    with conn.cursor() as cur:
        # Create table and read-only user
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS masked_users (
                id      SERIAL PRIMARY KEY,
                email   TEXT NOT NULL,
                name    TEXT NOT NULL
            )
            """
        )
        cur.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (_MASKING_USER,),
        )
        if not cur.fetchone():
            cur.execute(
                f"CREATE USER {_MASKING_USER} PASSWORD '{_MASKING_PASS}'"  # nosec B608
            )
        cur.execute(
            "GRANT CONNECT ON DATABASE "  # nosec B608
            + psycopg2.extensions.quote_ident(_MASKING_DBNAME, cur)
            + " TO "
            + _MASKING_USER
        )
        cur.execute("GRANT SELECT ON masked_users TO " + _MASKING_USER)  # nosec B608

        # Setup verification: table must exist and be empty before test runs
        cur.execute("SELECT COUNT(*) FROM masked_users")
        count = cur.fetchone()
        assert count is not None, "masked_users table not found after creation"
        assert count[0] == 0, (
            f"masked_users table should be empty at fixture setup; found {count[0]} rows. "
            "A previous test run may not have cleaned up properly."
        )
    conn.close()

    superuser_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_MASKING_DBNAME}"
    )
    readonly_url = (
        f"postgresql+psycopg2://{_MASKING_USER}:{_MASKING_PASS}"
        f"@{proc.host}:{proc.port}/{_MASKING_DBNAME}"
    )

    yield superuser_url, readonly_url

    _drop_database(proc, _MASKING_DBNAME)


@pytest.mark.integration
def test_masking_deterministic_output_in_real_postgresql(
    masking_pg_db: tuple[str, str],
    postgresql_proc: PostgreSQLProc,
) -> None:
    """Masking engine produces deterministic output verified by real PostgreSQL read-back.

    Verifies AC3: masking engine deterministic output verified in real PostgreSQL write.

    Arrange:
    - A real PostgreSQL ``masked_users`` table (via masking_pg_db fixture).
    - Two plaintext email/name values to mask deterministically.

    Act:
    - Apply MaskingRegistry.mask() to generate masked email and name values.
    - Write the masked values to the real PostgreSQL database via psycopg2.
    - Read back the rows via a second query.

    Assert:
    - The exact same masked values are present in the database (round-trip
      fidelity — no corruption during INSERT/SELECT).
    - Calling MaskingRegistry.mask() a second time on the same inputs produces
      the same outputs (determinism property holds across calls).
    - The masked email contains "@" (format-preservation property).
    - The masked email differs from the original (masking actually changed the value).

    This test deliberately does NOT mock the masking layer — it exercises the
    real MaskingRegistry and real PostgreSQL in combination (T19.4 retro guard).
    """
    from synth_engine.modules.masking.registry import ColumnType, MaskingRegistry

    _superuser_url, _readonly_url = masking_pg_db
    proc = postgresql_proc

    registry = MaskingRegistry()

    # Two plaintext values to mask
    real_email_1 = "alice.johnson@example.com"
    real_name_1 = "Alice Johnson"
    real_email_2 = "bob.smith@example.com"
    real_name_2 = "Bob Smith"

    # Apply masking — deterministic per (value, salt) pair
    masked_email_1 = registry.mask(real_email_1, ColumnType.EMAIL, "users.email")
    masked_name_1 = registry.mask(real_name_1, ColumnType.NAME, "users.name")
    masked_email_2 = registry.mask(real_email_2, ColumnType.EMAIL, "users.email")
    masked_name_2 = registry.mask(real_name_2, ColumnType.NAME, "users.name")

    # Write masked values to real PostgreSQL
    write_conn = _connect_pg(proc, _MASKING_DBNAME)
    with write_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO masked_users (email, name) VALUES (%s, %s)",
            (masked_email_1, masked_name_1),
        )
        cur.execute(
            "INSERT INTO masked_users (email, name) VALUES (%s, %s)",
            (masked_email_2, masked_name_2),
        )
    write_conn.close()

    # Read back via a second independent connection (round-trip verification)
    read_conn = _connect_pg(proc, _MASKING_DBNAME)
    with read_conn.cursor() as cur:
        cur.execute("SELECT email, name FROM masked_users ORDER BY id")
        rows = cur.fetchall()
    read_conn.close()

    # Assert round-trip fidelity
    assert len(rows) == 2, f"Expected 2 rows in masked_users, got {len(rows)}"
    assert rows[0][0] == masked_email_1, (
        f"Round-trip email mismatch: DB={rows[0][0]!r}, expected={masked_email_1!r}"
    )
    assert rows[0][1] == masked_name_1, (
        f"Round-trip name mismatch: DB={rows[0][1]!r}, expected={masked_name_1!r}"
    )
    assert rows[1][0] == masked_email_2, (
        f"Round-trip email mismatch: DB={rows[1][0]!r}, expected={masked_email_2!r}"
    )

    # Assert determinism: calling mask() again on the same inputs gives the same output
    registry2 = MaskingRegistry()
    assert registry2.mask(real_email_1, ColumnType.EMAIL, "users.email") == masked_email_1, (
        "Masking is not deterministic — second call produced a different result for the same input"
    )
    assert registry2.mask(real_name_1, ColumnType.NAME, "users.name") == masked_name_1, (
        "Masking is not deterministic — second call produced a different result for the same input"
    )

    # Assert format preservation for email
    assert "@" in masked_email_1, (
        f"Masked email does not contain '@': {masked_email_1!r} — format preservation violated"
    )

    # Assert masking actually changed the value
    assert masked_email_1 != real_email_1, (
        f"Masked email equals the original — masking did not transform the value: {real_email_1!r}"
    )


# ===========================================================================
# AC1 (new) — Ingestion adapter pre-flight privilege check (additional coverage)
# ===========================================================================


@pytest.fixture(scope="module")
def ingestion_preflight_db(
    postgresql_proc: PostgreSQLProc,
) -> Generator[None]:
    """Provision the ingestion pre-flight integration database.

    Creates ``conclave_ingestion_preflight_t202`` database and provisions a
    read-only user.  Setup verification: asserts the database exists after
    creation.

    Args:
        postgresql_proc: The running PostgreSQL process executor.

    Yields:
        None — setup/teardown only.
    """
    proc = postgresql_proc
    _create_database_if_not_exists(proc, _INGESTION_PREFLIGHT_DBNAME)

    conn = _connect_pg(proc, _INGESTION_PREFLIGHT_DBNAME)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (_INGESTION_READONLY_USER,),
        )
        if not cur.fetchone():
            cur.execute(
                f"CREATE USER {_INGESTION_READONLY_USER} "  # nosec B608
                f"PASSWORD '{_INGESTION_READONLY_PASS}'"
            )
        cur.execute(
            "GRANT CONNECT ON DATABASE "  # nosec B608
            + psycopg2.extensions.quote_ident(_INGESTION_PREFLIGHT_DBNAME, cur)
            + " TO "
            + _INGESTION_READONLY_USER
        )

    # Setup verification: database must be connectable as the read-only user
    verify_conn = psycopg2.connect(
        dbname=_INGESTION_PREFLIGHT_DBNAME,
        user=_INGESTION_READONLY_USER,
        host=proc.host,
        port=proc.port,
        password=_INGESTION_READONLY_PASS,
    )
    verify_conn.close()
    conn.close()

    yield

    _drop_database(proc, _INGESTION_PREFLIGHT_DBNAME)


@pytest.mark.integration
@pytest.mark.usefixtures("ingestion_preflight_db")
def test_ingestion_preflight_superuser_rejected_real_postgresql(
    postgresql_proc: PostgreSQLProc,
) -> None:
    """Ingestion adapter rejects superuser connections against real PostgreSQL.

    Verifies AC1: ingestion adapter pre-flight privilege check against real
    PostgreSQL — specifically that superuser connections are rejected.

    This test complements the existing test in test_ingestion_integration.py
    by running against a freshly provisioned database that is independent of
    that module's fixtures.  The isolation prevents fixture-state coupling
    across test modules.

    Arrange: Build a connection URL using the superuser credentials from the
        ephemeral PostgreSQL process.
    Act: Call ``adapter.preflight_check()``.
    Assert: :class:`PrivilegeEscalationError` is raised.
    """
    from synth_engine.modules.ingestion.postgres_adapter import (
        PostgresIngestionAdapter,
        PrivilegeEscalationError,
    )

    proc = postgresql_proc
    url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_INGESTION_PREFLIGHT_DBNAME}"
    )
    adapter = PostgresIngestionAdapter(url)

    with pytest.raises(PrivilegeEscalationError, match="superuser"):
        adapter.preflight_check()


@pytest.mark.integration
@pytest.mark.usefixtures("ingestion_preflight_db")
def test_ingestion_preflight_readonly_user_passes_real_postgresql(
    postgresql_proc: PostgreSQLProc,
) -> None:
    """Ingestion adapter accepts read-only user connections against real PostgreSQL.

    Verifies AC1: ingestion adapter pre-flight privilege check against real
    PostgreSQL — specifically that read-only users are accepted.

    Arrange: Connect as ``ingestion_preflight_tester``, a user with only
        CONNECT privilege (no superuser, no DDL rights).
    Act: Call ``adapter.preflight_check()``.
    Assert: No exception is raised.
    """
    from synth_engine.modules.ingestion.postgres_adapter import PostgresIngestionAdapter

    proc = postgresql_proc
    url = (
        f"postgresql+psycopg2://{_INGESTION_READONLY_USER}:{_INGESTION_READONLY_PASS}"
        f"@{proc.host}:{proc.port}/{_INGESTION_PREFLIGHT_DBNAME}"
    )
    adapter = PostgresIngestionAdapter(url)

    # Must not raise — this is the required read-only path.
    adapter.preflight_check()

    # Positive assertion: verify the adapter's engine remains usable after preflight
    # (proves the function body actually executed and did not short-circuit before
    # establishing the connection).
    from sqlalchemy import text

    with adapter._engine.connect() as conn:
        row = conn.execute(text("SELECT 1")).scalar()
    assert row == 1, (
        f"Engine.execute SELECT 1 returned {row!r} — adapter not usable after preflight_check()"
    )


# ===========================================================================
# AC2 — Subsetting engine FK traversal against real PostgreSQL schema
# ===========================================================================


@pytest.fixture(scope="module")
def subsetting_fk_db(
    postgresql_proc: PostgreSQLProc,
) -> Generator[tuple[str, str]]:
    """Provision source and target databases for FK traversal verification.

    Schema: orders(id, customer_id) → customers(id) — a simple 2-table parent/child
    hierarchy.  Source is seeded with 5 customers and 10 orders (2 per customer).
    Target has matching schema with no data.

    Setup verification: asserts source row counts match expectations before yielding.

    Args:
        postgresql_proc: The running PostgreSQL process executor.

    Yields:
        Tuple of (source_url, target_url) as SQLAlchemy connection strings.
    """
    proc = postgresql_proc
    src_db = _SUBSETTING_FK_DBNAME + "_src"
    tgt_db = _SUBSETTING_FK_DBNAME + "_tgt"

    _create_database_if_not_exists(proc, src_db)
    _create_database_if_not_exists(proc, tgt_db)

    src_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}@{proc.host}:{proc.port}/{src_db}"
    )
    tgt_url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}@{proc.host}:{proc.port}/{tgt_db}"
    )

    # Seed source database
    src_conn = _connect_pg(proc, src_db)
    with src_conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id   SERIAL PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id          SERIAL PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id),
                amount      NUMERIC(10, 2) NOT NULL
            )
            """
        )
        for c in range(1, 6):
            cur.execute(
                "INSERT INTO customers (name) VALUES (%s) RETURNING id",
                (f"Customer-{c}",),
            )
            cust_id = cur.fetchone()[0]  # type: ignore[index]  # psycopg2 fetchone always valid after RETURNING
            for o in range(1, 3):
                cur.execute(
                    "INSERT INTO orders (customer_id, amount) VALUES (%s, %s)",
                    (cust_id, 100 * o),
                )

        # Setup verification: assert source has correct row counts before yielding
        cur.execute("SELECT COUNT(*) FROM customers")
        cust_count = cur.fetchone()
        assert cust_count is not None, "Source setup failure: customers query returned None"
        assert cust_count[0] == 5, (
            f"Source setup failure: expected 5 customers, got {cust_count[0]}"
        )
        cur.execute("SELECT COUNT(*) FROM orders")
        order_count = cur.fetchone()
        assert order_count is not None, "Source setup failure: orders query returned None"
        assert order_count[0] == 10, (
            f"Source setup failure: expected 10 orders, got {order_count[0]}"
        )
    src_conn.close()

    # Create target schema (no data)
    tgt_conn = _connect_pg(proc, tgt_db)
    with tgt_conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id   INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id          INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id),
                amount      NUMERIC(10, 2) NOT NULL
            )
            """
        )
    tgt_conn.close()

    yield src_url, tgt_url

    _drop_database(proc, src_db)
    _drop_database(proc, tgt_db)


@pytest.mark.integration
def test_subsetting_fk_traversal_real_postgresql(
    subsetting_fk_db: tuple[str, str],
) -> None:
    """SubsettingEngine FK traversal against real PostgreSQL schema produces correct counts.

    Verifies AC2: Subsetting engine FK traversal against real PostgreSQL schema.

    Arrange:
    - Source DB: customers (5 rows) → orders (10 rows, 2 per customer).
    - Target DB: matching schema, no data.
    - SchemaTopology reflecting the customers → orders FK relationship.

    Act:
    - Run SubsettingEngine seeded on customers LIMIT 1.

    Assert:
    - Target DB has exactly 1 customer.
    - Target DB has exactly 2 orders (those belonging to the seeded customer).
    - No orphaned orders exist in the target (FK integrity preserved).

    This test exercises the real SubsettingEngine FK traversal logic against a
    real PostgreSQL database — not mocks or SQLite (T19.4 retro guard).
    """
    from sqlalchemy import create_engine, text

    from synth_engine.modules.subsetting.core import SubsettingEngine
    from synth_engine.modules.subsetting.egress import EgressWriter
    from synth_engine.shared.schema_topology import (
        ColumnInfo,
        ForeignKeyInfo,
        SchemaTopology,
    )

    src_url, tgt_url = subsetting_fk_db

    src_engine = create_engine(src_url)
    tgt_engine = create_engine(tgt_url)

    topology = SchemaTopology(
        table_order=("customers", "orders"),
        columns={
            "customers": (
                ColumnInfo(name="id", type="INTEGER", primary_key=1, nullable=False),
                ColumnInfo(name="name", type="TEXT", primary_key=0, nullable=False),
            ),
            "orders": (
                ColumnInfo(name="id", type="INTEGER", primary_key=1, nullable=False),
                ColumnInfo(name="customer_id", type="INTEGER", primary_key=0, nullable=False),
                ColumnInfo(name="amount", type="NUMERIC", primary_key=0, nullable=False),
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
        },
    )

    egress = EgressWriter(target_engine=tgt_engine)
    se = SubsettingEngine(
        source_engine=src_engine,
        topology=topology,
        egress=egress,
    )

    result = se.run(
        seed_table="customers",
        seed_query="SELECT * FROM customers ORDER BY id LIMIT 1",  # nosec B608
    )

    # Verify SubsetResult
    assert "customers" in result.tables_written, (
        f"Expected 'customers' in tables_written, got: {result.tables_written}"
    )
    assert result.row_counts["customers"] == 1, (
        f"Expected 1 customer in result, got {result.row_counts['customers']}"
    )
    assert result.row_counts.get("orders") == 2, (
        f"Expected SubsetResult.row_counts['orders']==2, got {result.row_counts}"
    )

    # Verify target DB counts via real PostgreSQL
    with tgt_engine.connect() as conn:
        cust_count = conn.execute(text("SELECT COUNT(*) FROM customers")).scalar()  # nosec B608
        order_count = conn.execute(text("SELECT COUNT(*) FROM orders")).scalar()  # nosec B608

    assert cust_count == 1, f"Expected 1 customer in target DB, got {cust_count}"
    assert order_count == 2, f"Expected 2 orders in target DB (2 per customer), got {order_count}"

    # Verify FK integrity — no orphaned orders
    with tgt_engine.connect() as conn:
        orphaned = conn.execute(
            text(  # nosec B608
                "SELECT COUNT(*) FROM orders o "
                "WHERE NOT EXISTS (SELECT 1 FROM customers c WHERE c.id = o.customer_id)"
            )
        ).scalar()

    assert orphaned == 0, (
        f"FK traversal left {orphaned} orphaned order(s) in target DB — "
        "referential integrity violated"
    )

    src_engine.dispose()
    tgt_engine.dispose()


# ===========================================================================
# AC SDV — Real SDV/CTGAN training path (small dataset, FORCE_CPU mode)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.synthesizer
def test_real_sdv_ctgan_training_small_dataset() -> None:
    """One integration test exercising the real SDV/CTGAN training path.

    Verifies that the SynthesisEngine.train() → generate() pipeline works
    end-to-end with real SDV libraries (no mocks) on a tiny 20-row dataset.

    If SDV is not installed (synthesizer group absent), the test is skipped
    gracefully via ``pytest.importorskip``.

    Marks: ``slow`` (real CTGAN training, even on tiny data, takes a few seconds).

    Arrange:
    - Create a 20-row DataFrame with age and salary columns.
    - Write it to a Parquet file in a temporary directory.

    Act:
    - Create SynthesisEngine(epochs=2) — minimal epochs to keep the test fast.
    - Call engine.train() on the Parquet file — uses REAL CTGANSynthesizer.
    - Call engine.generate(artifact, n_rows=10) — uses REAL SDV sample().

    Assert:
    - Training completes without raising (no mock — real SDV API exercised).
    - The returned ModelArtifact has the correct table_name.
    - The generated DataFrame has exactly 10 rows.
    - The generated DataFrame has the same columns as the training data.
    - The model artifact can be serialised and deserialised (pickle round-trip).
    """
    sdv = pytest.importorskip("sdv", reason="SDV not installed — skipping CTGAN integration test")
    del sdv  # importorskip used only to skip if absent; we import via SynthesisEngine

    import pickle

    # Build a small but valid training DataFrame (20 rows, 2 numeric columns)
    import numpy as np
    import pandas as pd

    from synth_engine.modules.synthesizer.training.engine import SynthesisEngine

    rng = np.random.default_rng(42)
    df = pd.DataFrame(
        {
            "age": rng.integers(18, 65, size=20).astype(int),
            "salary": rng.integers(30000, 120000, size=20).astype(int),
        }
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        parquet_path = f"{tmpdir}/test_table.parquet"
        df.to_parquet(parquet_path, index=False, engine="pyarrow")

        # Use 2 epochs — real training but fast for CI
        engine = SynthesisEngine(epochs=2)

        # Act: train with REAL CTGANSynthesizer (no mock)
        artifact = engine.train("test_table", parquet_path)

        # Assert: training succeeded — artifact is well-formed
        assert artifact.table_name == "test_table", (
            f"Expected table_name='test_table', got {artifact.table_name!r}"
        )
        assert artifact.column_names == ["age", "salary"], (
            f"Expected column_names=['age', 'salary'], got {artifact.column_names}"
        )

        # Act: generate synthetic rows from the real trained model
        synthetic_df = engine.generate(artifact, n_rows=10)

        # Assert: generation succeeded — correct shape and columns
        assert len(synthetic_df) == 10, f"Expected 10 synthetic rows, got {len(synthetic_df)}"
        assert list(synthetic_df.columns) == ["age", "salary"], (
            f"Expected columns ['age', 'salary'], got {list(synthetic_df.columns)}"
        )

        # Assert: pickle round-trip succeeds (artifact serialisability)
        artifact_path = f"{tmpdir}/test_artifact.pkl"
        artifact.save(artifact_path)
        assert __import__("os").path.exists(artifact_path), (
            "artifact.save() did not create the expected file"
        )
        with open(artifact_path, "rb") as f:
            loaded_artifact: Any = pickle.load(f)  # noqa: S301  # nosec B301 — test-only; loading a file this test just wrote in a temp dir
        assert loaded_artifact.table_name == "test_table", (
            f"Pickle round-trip failed: table_name={loaded_artifact.table_name!r}"
        )
