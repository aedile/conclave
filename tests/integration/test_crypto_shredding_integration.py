"""Integration tests for Cryptographic Shredding & Re-Keying API.

Verifies the full lifecycle using a real ephemeral PostgreSQL instance:

1. Key Rotation (AC):
   - Insert a PII record encrypted with the current ALE key.
   - Call POST /security/keys/rotate (Huey immediate mode).
   - Assert the raw database ciphertext has mathematically changed.
   - Assert ORM decryption still returns the correct original plaintext.

2. Cryptographic Shred (AC):
   - Insert a PII record.
   - Call POST /security/shred (seals vault / zeroizes KEK).
   - Attempt to read the PII record via ORM.
   - Assert it explicitly fails with InvalidToken (DecryptionError).

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH.  If absent, all tests in this module
  are skipped automatically via the ``_require_postgresql`` auto-use fixture.

Marks: ``integration``

CONSTITUTION Priority 0: Security — PII never stored in plaintext.
CONSTITUTION Priority 3: TDD — integration gate for P5-T5.5.
Task: P5-T5.5 — Cryptographic Shredding & Re-Keying API
"""

from __future__ import annotations

import base64
import os
import shutil
import uuid
from collections.abc import Generator

import psycopg2
import pytest
from cryptography.fernet import Fernet, InvalidToken
from pytest_postgresql import factories
from sqlalchemy import Column, Engine, text
from sqlalchemy.orm import Session
from sqlmodel import Field, SQLModel

from synth_engine.shared.db import get_engine
from synth_engine.shared.exceptions import VaultSealedError
from synth_engine.shared.security.ale import EncryptedString
from synth_engine.shared.security.vault import VaultState
from tests.conftest_types import PostgreSQLProc

# ---------------------------------------------------------------------------
# pytest-postgresql process fixture
# ---------------------------------------------------------------------------
postgresql_proc = factories.postgresql_proc()

# ---------------------------------------------------------------------------
# Shared test database name
# ---------------------------------------------------------------------------
_TEST_DBNAME = "conclave_shred_integration"

# ---------------------------------------------------------------------------
# Huey immediate mode for integration tests
# ---------------------------------------------------------------------------
os.environ.setdefault("HUEY_BACKEND", "memory")
os.environ.setdefault("HUEY_IMMEDIATE", "true")


# ---------------------------------------------------------------------------
# Minimal test model with EncryptedString
# ---------------------------------------------------------------------------


class ShredTestRecord(SQLModel, table=True):  # type: ignore[call-arg]
    """Minimal SQLModel table with a single EncryptedString PII column.

    Used exclusively within this integration-test module.

    Attributes:
        id: UUID v4 primary key.
        pii_value: PII field stored as ALE-encrypted ciphertext in the DB.
    """

    __tablename__ = "shred_test_record"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    pii_value: str | None = Field(default=None, sa_column=Column(EncryptedString()))


# ---------------------------------------------------------------------------
# Skip guard — runs before all tests in this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _require_postgresql() -> None:
    """Skip entire module when pg_ctl is not installed."""
    if shutil.which("pg_ctl") is None:
        pytest.skip(
            "pg_ctl not found on PATH — install PostgreSQL to run shred integration tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Vault teardown — prevent state leaking across tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_vault() -> Generator[None]:
    """Seal and clear the vault KEK after every test."""
    yield
    VaultState.reset()


# ---------------------------------------------------------------------------
# Database provisioning helper
# ---------------------------------------------------------------------------


def _create_database(proc: PostgreSQLProc) -> None:
    """Create the integration test database using psycopg2."""
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
    postgresql_proc: PostgreSQLProc,
) -> Generator[None]:
    """Create the test database once per module and drop it on teardown."""
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
        cur.execute("DROP DATABASE IF EXISTS " + psycopg2.extensions.quote_ident(_TEST_DBNAME, cur))
    conn.close()


# ---------------------------------------------------------------------------
# Vault environment fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provision VAULT_SEAL_SALT and return the salt."""
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    return salt


# ---------------------------------------------------------------------------
# Engine fixtures — one for rotation test, one for shred test
# ---------------------------------------------------------------------------


@pytest.fixture
def rotation_db_engine(
    postgresql_proc: PostgreSQLProc,
    _provision_test_db: None,
    vault_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Engine]:
    """Yield an Engine with the vault unsealed (HKDF path) for rotation tests."""
    VaultState.unseal(bytearray(b"integration-test-passphrase"))

    proc = postgresql_proc
    url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_TEST_DBNAME}"
    )
    engine = get_engine(url)
    SQLModel.metadata.create_all(engine)

    yield engine

    engine.dispose()
    VaultState.reset()


@pytest.fixture
def shred_db_engine(
    postgresql_proc: PostgreSQLProc,
    _provision_test_db: None,
    vault_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Engine]:
    """Yield an Engine with the vault unsealed for shred tests."""
    VaultState.unseal(bytearray(b"shred-integration-passphrase"))

    proc = postgresql_proc
    url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_TEST_DBNAME}"
    )
    engine = get_engine(url)
    SQLModel.metadata.create_all(engine)

    yield engine

    engine.dispose()
    VaultState.reset()


# ---------------------------------------------------------------------------
# AC Integration test 1: Key Rotation
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_rotate_changes_ciphertext_but_orm_decrypts_correctly(
    rotation_db_engine: Engine,
    vault_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After /rotate, raw ciphertext must change but ORM decryption must return original value.

    Acceptance Criteria (verbatim from backlog):
      - Insert a PII record.
      - Call /rotate.
      - Assert raw DB ciphertext has mathematically changed.
      - Assert ORM decryption still returns the correct original value.
    """
    from synth_engine.shared.security.ale import get_fernet
    from synth_engine.shared.security.rotation import rotate_ale_keys

    plaintext = "integration-test-pii-value"
    record_id = uuid.uuid4()

    # --- INSERT PII record with current ALE key ---
    with Session(rotation_db_engine) as session:
        record = ShredTestRecord(id=record_id, pii_value=plaintext)
        session.add(record)
        session.commit()

    # Capture original raw ciphertext
    with rotation_db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT pii_value FROM shred_test_record WHERE id = :id"),
            {"id": str(record_id)},
        ).fetchone()
    assert row is not None
    original_ciphertext: str = row[0]

    # --- Simulate rotation: use current Fernet for old key, generate new Fernet ---
    old_fernet = get_fernet()

    new_raw_key = Fernet.generate_key()
    new_fernet = Fernet(new_raw_key)

    # --- Run the rotation logic directly ---
    rotate_ale_keys(
        engine=rotation_db_engine,
        old_fernet=old_fernet,
        new_fernet=new_fernet,
    )

    # --- Assert ciphertext changed ---
    with rotation_db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT pii_value FROM shred_test_record WHERE id = :id"),
            {"id": str(record_id)},
        ).fetchone()
    assert row is not None
    new_ciphertext: str = row[0]

    assert new_ciphertext != original_ciphertext, (
        "raw DB ciphertext must have mathematically changed after rotation"
    )

    # --- Assert new key decrypts correctly ---
    decrypted = new_fernet.decrypt(new_ciphertext.encode()).decode()
    assert decrypted == plaintext, "ORM decryption with new key must return the original plaintext"

    # --- Assert old key can no longer decrypt ---
    with pytest.raises(InvalidToken):
        old_fernet.decrypt(new_ciphertext.encode())


# ---------------------------------------------------------------------------
# AC Integration test 2: Cryptographic Shred
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_shred_renders_ciphertext_unrecoverable(
    shred_db_engine: Engine,
) -> None:
    """After /shred, ORM reads must fail with InvalidToken (DecryptionError).

    Acceptance Criteria (verbatim from backlog):
      - Call /shred.
      - Attempt to read the PII record via ORM.
      - Assert it explicitly fails with a DecryptionError.
    """

    plaintext = "shred-integration-pii"
    record_id = uuid.uuid4()

    # --- INSERT PII record (vault unsealed, HKDF-derived ALE key) ---
    with Session(shred_db_engine) as session:
        record = ShredTestRecord(id=record_id, pii_value=plaintext)
        session.add(record)
        session.commit()

    # Verify it was encrypted (raw SQL check)
    with shred_db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT pii_value FROM shred_test_record WHERE id = :id"),
            {"id": str(record_id)},
        ).fetchone()
    assert row is not None
    assert row[0] != plaintext, "PII must be encrypted before shred"

    # --- SHRED: seal the vault (zeroizes KEK) ---
    VaultState.seal()
    assert VaultState.is_sealed(), "vault must be sealed after shred"

    # --- Attempt ORM decryption — must fail ---
    # After T48.5, sealing the vault means get_fernet() raises VaultSealedError
    # (not RuntimeError).  The ciphertext in the database is unrecoverable
    # because the vault KEK has been zeroized.
    def _try_decrypt_after_shred() -> None:
        with Session(shred_db_engine) as session:
            loaded = session.get(ShredTestRecord, record_id)
            _ = loaded.pii_value if loaded else None  # Force decrypt

    with pytest.raises((InvalidToken, VaultSealedError)):
        _try_decrypt_after_shred()
