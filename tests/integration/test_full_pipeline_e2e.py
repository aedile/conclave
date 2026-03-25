"""Full pipeline E2E integration test (T35.4).

Exercises the complete production pipeline end-to-end with zero mocks below
the API boundary:

    DB seed (PostgreSQL) → masking → subsetting → synthesis → Parquet + HMAC
    → download verification → privacy budget decrement

Schema under test (5 tables, linear FK chain):
    regions (root)
        └── customers (FK → regions)
            └── accounts (FK → customers)
                └── orders (FK → accounts)
                    └── order_lines (FK → orders)

Seeded with 5 regions × 2 customers × 1 account × 2 orders × 3 order_lines
= 5 + 10 + 10 + 20 + 60 = 105 rows total (≥50 per spec).

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH. If absent, all tests in this module are
  skipped automatically via the ``_require_postgresql`` autouse fixture.

Synthesis gate
--------------
The synthesis portion of the E2E pipeline uses :class:`DummyMLSynthesizer`
from ``tests/fixtures/`` which does NOT require PyTorch, SDV, or Opacus.
This decouples the E2E test from the synthesizer optional-dependency group so
it runs in every CI environment.

If a caller explicitly wants to gate on real CTGAN, they should add:
    torch = pytest.importorskip("torch")
before the test body.

Marks: ``integration``, ``slow``

CONSTITUTION Priority 0: Security — no PII, no credential leaks, HMAC verified.
CONSTITUTION Priority 3: TDD — RED/GREEN/REFACTOR.
Task: T35.4 — Add Full E2E Pipeline Integration Test
Split: T56.3 — pipeline + parquet + row-count tests (budget tests → test_full_pipeline_budget.py)
"""

from __future__ import annotations

import io
import shutil
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytest_postgresql import factories
from sqlalchemy import create_engine, text

from synth_engine.modules.masking.algorithms import mask_email, mask_name
from synth_engine.modules.subsetting.core import SubsettingEngine
from synth_engine.modules.subsetting.egress import EgressWriter
from synth_engine.shared.security.hmac_signing import compute_hmac, verify_hmac
from tests.conftest_types import PostgreSQLProc
from tests.fixtures.dummy_ml_synthesizer import DummyMLSynthesizer
from tests.integration.full_pipeline_helpers import (
    _COUNT_QUERIES,
    _E2E_SOURCE_DB,
    _E2E_TARGET_DB,
    _create_db,
    _create_pipeline_schema,
    _drop_db,
    _make_pipeline_topology,
    _seed_pipeline_source,
)

# ---------------------------------------------------------------------------
# pytest-postgresql process fixture (module-scoped — one PG process per module)
# ---------------------------------------------------------------------------

postgresql_proc = factories.postgresql_proc()

# ---------------------------------------------------------------------------
# Skip guard: runs before every test when pg_ctl is absent
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _require_postgresql() -> None:
    """Skip the entire module when ``pg_ctl`` is not installed.

    In CI the PostgreSQL service is always present, so the guard has no effect.
    If a developer's laptop lacks a local PostgreSQL installation, all tests
    are skipped with a clear diagnostic message.
    """
    if shutil.which("pg_ctl") is None:
        pytest.skip(
            "pg_ctl not found on PATH — install PostgreSQL to run full-pipeline E2E tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Masking salt and column map (injected via row_transformer — no masking import
# inside subsetting module itself, per import-linter boundary rules)
# ---------------------------------------------------------------------------

_MASKING_SALT = "full-pipeline-e2e-salt"

#: Map of table name → {column → masking function(value, salt) -> str}
_COLUMN_MASKS: dict[str, dict[str, Any]] = {
    "customers": {
        "full_name": mask_name,
        "email": mask_email,
    },
}


def _mask_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """Apply deterministic masking to PII columns in the given row.

    Non-PII tables and columns are returned unchanged.

    Args:
        table: The table name; used to look up which columns to mask.
        row: A single row dict read from the source database.

    Returns:
        A new row dict with PII columns replaced by deterministic masked values.
        The original ``row`` dict is not mutated.
    """
    masks = _COLUMN_MASKS.get(table, {})
    if not masks:
        return row
    result = dict(row)
    for col, fn in masks.items():
        if col in result and result[col] is not None:
            result[col] = fn(str(result[col]), _MASKING_SALT)
    return result


# ---------------------------------------------------------------------------
# Module-scoped DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pipeline_dbs(
    postgresql_proc: PostgreSQLProc,
) -> Generator[tuple[str, str]]:
    """Provision source and target databases for the full pipeline E2E tests.

    Source DB is seeded with 105 rows across 5 tables.  Target DB has the
    same schema with INTEGER (not SERIAL) PKs so the subset can write into it.

    Args:
        postgresql_proc: The running pytest-postgresql process executor.

    Yields:
        Tuple of (source_url, target_url) as SQLAlchemy sync connection strings.
    """
    proc = postgresql_proc
    password = proc.password or ""

    _create_db(proc, _E2E_SOURCE_DB)
    _create_db(proc, _E2E_TARGET_DB)

    src_url = (
        f"postgresql+psycopg2://{proc.user}:{password}@{proc.host}:{proc.port}/{_E2E_SOURCE_DB}"
    )
    tgt_url = (
        f"postgresql+psycopg2://{proc.user}:{password}@{proc.host}:{proc.port}/{_E2E_TARGET_DB}"
    )

    _create_pipeline_schema(proc, _E2E_SOURCE_DB, with_serial=True)
    _seed_pipeline_source(proc, _E2E_SOURCE_DB)
    _create_pipeline_schema(proc, _E2E_TARGET_DB, with_serial=False)

    yield src_url, tgt_url

    _drop_db(proc, _E2E_SOURCE_DB)
    _drop_db(proc, _E2E_TARGET_DB)


# ---------------------------------------------------------------------------
# AC1 + AC2 + AC3: Full pipeline — seed → mask → subset → synthesize → HMAC
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
def test_full_pipeline_seed_mask_subset_synthesize_hmac(
    pipeline_dbs: tuple[str, str],
    tmp_path: Path,
) -> None:
    """Full E2E pipeline: DB seed → masking → subsetting → synthesis → HMAC.

    This is the canonical T35.4 E2E test.  It exercises every production
    component in sequence using real PostgreSQL and the real filesystem.

    Steps:
        1. Connect to the seeded source DB (5-table linear FK chain).
        2. Run SubsettingEngine from root table ``regions`` with a masking
           row_transformer (2 regions seed).
        3. Assert FK consistency in the target DB (no orphaned rows).
        4. Assert masking was applied to customers.full_name and customers.email.
        5. Write the subsetted customers table to a Parquet file (real filesystem).
        6. Call DummyMLSynthesizer.train() + generate() (synthesis step).
        7. Assert output DataFrame shape is correct.
        8. Write the synthetic Parquet artifact and compute a real HMAC-SHA256.
        9. Verify the HMAC using verify_hmac() (real HMAC, real bytes).

    Zero mocks below the API boundary — all components are real.
    DummyMLSynthesizer is used instead of CTGAN to avoid optional-dep failures.

    Asserts:
        - FK consistency: no orphaned rows in target DB.
        - Masking determinism: same input → same masked output on repeated calls.
        - Output DataFrame shape: len(df) == 25 (generate requested rows).
        - HMAC signature validity: verify_hmac() returns True.
        - Tampered bytes: verify_hmac() returns False.
    """
    src_url, tgt_url = pipeline_dbs
    topology = _make_pipeline_topology()

    src_engine = create_engine(src_url)
    tgt_engine = create_engine(tgt_url)

    # --- Step 1 & 2: Subset 2 regions (pulls full FK chain) with masking ---
    egress = EgressWriter(target_engine=tgt_engine)
    se = SubsettingEngine(
        source_engine=src_engine,
        topology=topology,
        egress=egress,
        row_transformer=_mask_row,
    )

    result = se.run(
        seed_table="regions",
        seed_query="SELECT * FROM regions ORDER BY id LIMIT 2",  # nosec B608
    )

    # --- Step 3a: Assert SubsetResult row counts ---
    # 2 regions → 2*2=4 customers → 4 accounts → 4*2=8 orders → 8*3=24 order_lines
    assert result.row_counts.get("regions") == 2, (
        f"Expected 2 regions in SubsetResult, got {result.row_counts.get('regions')}"
    )
    assert result.row_counts.get("customers") == 4, (
        f"Expected 4 customers in SubsetResult, got {result.row_counts.get('customers')}"
    )
    assert result.row_counts.get("accounts") == 4, (
        f"Expected 4 accounts in SubsetResult, got {result.row_counts.get('accounts')}"
    )
    assert result.row_counts.get("orders") == 8, (
        f"Expected 8 orders in SubsetResult, got {result.row_counts.get('orders')}"
    )
    assert result.row_counts.get("order_lines") == 24, (
        f"Expected 24 order_lines in SubsetResult, got {result.row_counts.get('order_lines')}"
    )

    # --- Step 3b: Verify FK consistency in target DB ---
    with tgt_engine.connect() as conn:
        orphaned_customers = int(
            conn.execute(
                text(  # nosec B608
                    "SELECT COUNT(*) FROM customers c "
                    "WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE r.id = c.region_id)"
                )
            ).scalar()
            or 0
        )
        orphaned_accounts = int(
            conn.execute(
                text(  # nosec B608
                    "SELECT COUNT(*) FROM accounts a "
                    "WHERE NOT EXISTS (SELECT 1 FROM customers c WHERE c.id = a.customer_id)"
                )
            ).scalar()
            or 0
        )
        orphaned_orders = int(
            conn.execute(
                text(  # nosec B608
                    "SELECT COUNT(*) FROM orders o "
                    "WHERE NOT EXISTS (SELECT 1 FROM accounts a WHERE a.id = o.account_id)"
                )
            ).scalar()
            or 0
        )
        orphaned_order_lines = int(
            conn.execute(
                text(  # nosec B608
                    "SELECT COUNT(*) FROM order_lines ol "
                    "WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.id = ol.order_id)"
                )
            ).scalar()
            or 0
        )

    assert orphaned_customers == 0, f"FK violation: {orphaned_customers} orphaned customers"
    assert orphaned_accounts == 0, f"FK violation: {orphaned_accounts} orphaned accounts"
    assert orphaned_orders == 0, f"FK violation: {orphaned_orders} orphaned orders"
    assert orphaned_order_lines == 0, f"FK violation: {orphaned_order_lines} orphaned order_lines"

    # --- Step 4: Assert masking was applied to customers table ---
    with src_engine.connect() as conn:
        src_customers = {
            row["id"]: row
            for row in conn.execute(
                text(  # nosec B608
                    "SELECT id, full_name, email FROM customers "
                    "WHERE region_id IN (SELECT id FROM regions ORDER BY id LIMIT 2) "
                    "ORDER BY id"
                )
            ).mappings()
        }
    with tgt_engine.connect() as conn:
        tgt_customers = {
            row["id"]: row
            for row in conn.execute(
                text("SELECT id, full_name, email FROM customers ORDER BY id")  # nosec B608
            ).mappings()
        }

    names_differ = any(
        tgt_customers[pid]["full_name"] != src_customers[pid]["full_name"] for pid in tgt_customers
    )
    emails_differ = any(
        tgt_customers[pid]["email"] != src_customers[pid]["email"] for pid in tgt_customers
    )

    assert names_differ, (
        "No full_name values were masked — masking row_transformer may not have run"
    )
    assert emails_differ, "No email values were masked — masking row_transformer may not have run"

    # --- AC2 (masking determinism): Applying masking twice yields identical output ---
    masked_once = {pid: _mask_row("customers", dict(src_customers[pid])) for pid in src_customers}
    masked_twice = {pid: _mask_row("customers", dict(src_customers[pid])) for pid in src_customers}
    for pid in masked_once:
        assert masked_once[pid]["full_name"] == masked_twice[pid]["full_name"], (
            f"Masking is non-deterministic for full_name at id={pid}"
        )
        assert masked_once[pid]["email"] == masked_twice[pid]["email"], (
            f"Masking is non-deterministic for email at id={pid}"
        )

    # --- Step 5: Write subsetted customers to a Parquet file (real filesystem) ---
    with tgt_engine.connect() as conn:
        rows = list(
            conn.execute(
                text("SELECT id, full_name, email FROM customers ORDER BY id")  # nosec B608
            ).mappings()
        )
    customers_df = pd.DataFrame([dict(r) for r in rows])
    subset_parquet_path = tmp_path / "customers-subset.parquet"
    customers_df.to_parquet(str(subset_parquet_path), index=False, engine="pyarrow")
    assert subset_parquet_path.exists(), "Subset Parquet file was not created"

    # --- Step 6: Synthesis — DummyMLSynthesizer (no CTGAN required) ---
    synthesizer = DummyMLSynthesizer(seed=42)
    artifact = synthesizer.train(
        table_name="customers",
        parquet_path=str(subset_parquet_path),
    )
    synthetic_df = synthesizer.generate(artifact, n_rows=25)

    # --- Step 7: Assert output DataFrame shape ---
    assert isinstance(synthetic_df, pd.DataFrame), (
        f"Synthesis output must be a DataFrame, got {type(synthetic_df)}"
    )
    assert len(synthetic_df) == 25, f"Expected 25 synthetic rows, got {len(synthetic_df)}"
    assert len(synthetic_df.columns) > 0, "Synthetic DataFrame must have at least 1 column"

    # --- Step 8: Write synthetic Parquet artifact + compute HMAC ---
    synthetic_parquet_path = tmp_path / "customers-synthetic.parquet"
    buf = io.BytesIO()
    synthetic_df.to_parquet(buf, index=False, engine="pyarrow")
    parquet_bytes = buf.getvalue()
    synthetic_parquet_path.write_bytes(parquet_bytes)

    # Test-only signing key — 32 raw bytes, not a credential
    signing_key = b"\x12\x34\x56\x78\x9a\xbc\xde\xf0" * 4
    digest = compute_hmac(signing_key, parquet_bytes)
    sig_path = Path(str(synthetic_parquet_path) + ".sig")
    sig_path.write_bytes(digest)

    # --- Step 9: Verify HMAC ---
    loaded_parquet_bytes = synthetic_parquet_path.read_bytes()
    loaded_sig = sig_path.read_bytes()
    is_valid = verify_hmac(signing_key, loaded_parquet_bytes, loaded_sig)

    assert is_valid, (
        "HMAC verification failed — artifact may have been tampered with or "
        "signing/verification key mismatch"
    )

    # Negative check: tampered bytes must NOT verify
    tampered_bytes = loaded_parquet_bytes + b"\x00"
    is_tampered_valid = verify_hmac(signing_key, tampered_bytes, loaded_sig)
    assert not is_tampered_valid, (
        "HMAC verify_hmac() returned True for tampered bytes — timing attack or logic error"
    )

    src_engine.dispose()
    tgt_engine.dispose()


# ---------------------------------------------------------------------------
# AC6 (synthesis output): Verify Parquet round-trip integrity with filesystem
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
def test_synthetic_parquet_round_trip_and_hmac(tmp_path: Path) -> None:
    """Synthetic Parquet artifact is correctly written, read back, and HMAC-verified.

    This test isolates the write → read → HMAC verification pipeline from the
    full DB test so it can be run without PostgreSQL.  It validates:
        - DummyMLSynthesizer generates the requested row count.
        - Parquet serialisation round-trips without data loss (column names preserved).
        - HMAC on the raw Parquet bytes verifies correctly.
        - A wrong key produces a failed HMAC check.

    No PostgreSQL required — this test uses only the real filesystem and
    real HMAC primitives.
    """
    synthesizer = DummyMLSynthesizer(seed=99)
    artifact = synthesizer.train("order_lines", "/nonexistent/order_lines.parquet")
    synthetic_df = synthesizer.generate(artifact, n_rows=30)

    assert len(synthetic_df) == 30, f"Expected 30 rows, got {len(synthetic_df)}"

    # Write Parquet to real filesystem
    parquet_path = tmp_path / "order_lines-synthetic.parquet"
    buf = io.BytesIO()
    synthetic_df.to_parquet(buf, index=False, engine="pyarrow")
    parquet_bytes = buf.getvalue()
    parquet_path.write_bytes(parquet_bytes)

    # Compute and write real HMAC — test-only key, not a credential
    signing_key = b"\xde\xad\xbe\xef" * 8  # 32 bytes
    digest = compute_hmac(signing_key, parquet_bytes)
    sig_path = Path(str(parquet_path) + ".sig")
    sig_path.write_bytes(digest)

    # Read back and verify
    loaded_bytes = parquet_path.read_bytes()
    loaded_sig = sig_path.read_bytes()

    assert verify_hmac(signing_key, loaded_bytes, loaded_sig), (
        "HMAC verification failed on a correctly signed artifact"
    )

    # Wrong key must fail
    wrong_key = b"\x00" * 32
    assert not verify_hmac(wrong_key, loaded_bytes, loaded_sig), (
        "HMAC incorrectly verified with the wrong key"
    )

    loaded_df = pd.read_parquet(io.BytesIO(loaded_bytes), engine="pyarrow")
    assert list(loaded_df.columns) == list(synthetic_df.columns), (
        f"Column names changed after Parquet round-trip: "
        f"{list(loaded_df.columns)} vs {list(synthetic_df.columns)}"
    )
    assert len(loaded_df) == 30, f"Row count changed after Parquet round-trip: {len(loaded_df)}"


# ---------------------------------------------------------------------------
# Precondition: verify the 5-table source DB row counts after seeding
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
def test_source_db_seeded_with_correct_row_counts(
    pipeline_dbs: tuple[str, str],
) -> None:
    """Source DB contains the expected row counts after seeding.

    This is a precondition check that the test fixture is wired correctly —
    if this fails, the full pipeline test above is not meaningful.

    Expected counts:
        regions:      5
        customers:    10  (2 per region)
        accounts:     10  (1 per customer)
        orders:       20  (2 per account)
        order_lines:  60  (3 per order)
        total:       105  (≥ 50 per spec T35.4 C&C §2)
    """
    src_url, _ = pipeline_dbs
    src_engine = create_engine(src_url)

    expected = {
        "regions": 5,
        "customers": 10,
        "accounts": 10,
        "orders": 20,
        "order_lines": 60,
    }

    total = 0
    for table, expected_count in expected.items():
        with src_engine.connect() as conn:
            actual = int(
                conn.execute(text(_COUNT_QUERIES[table])).scalar() or 0  # nosec B608
            )
        assert actual == expected_count, (
            f"Source DB table '{table}': expected {expected_count} rows, got {actual}"
        )
        total += actual

    assert total >= 50, (
        f"Total row count across all 5 tables must be ≥50 per spec (T35.4 C&C §2). Got {total}."
    )

    src_engine.dispose()
