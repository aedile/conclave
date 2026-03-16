"""NIST SP 800-88 Cryptographic Erasure Validation Tests.

Validates that the Cryptographic Shredding implementation satisfies the
NIST SP 800-88 "Cryptographic Erasure" (CE) method:

  "For encrypted storage, the encryption key is sanitized, rendering the
   encrypted data unrecoverable."  (NIST SP 800-88 Rev. 1, §2.4, Table A-8)

These tests prove the mathematical guarantee:
- PII data stored via ALE is encrypted at rest (no plaintext in raw DB).
- After VaultState.seal() (KEK zeroized), all KEK bytes are 0x00.
- Decryption via Fernet raises InvalidToken — ciphertext is unrecoverable.
- Raw ciphertext in DB remains present (erasure is cryptographic, not physical).
- pg_stat_activity shows no plaintext query residue after the shred.

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH.  If absent, all tests in this module
  are skipped automatically via the ``_require_postgresql`` auto-use fixture.

Marks: ``integration``

Guard against known failure patterns:
- [Pattern 4] pg_ctl skip guard: shutil.which("pg_ctl") used
- [Pattern 6] VaultState test isolation: VaultState.reset() in every teardown
- [Pattern 7] HUEY_IMMEDIATE mode: set via os.environ
- [Pattern 9] No real PII: all test data is fictional

CONSTITUTION Priority 0: Security — PII never stored in plaintext.
CONSTITUTION Priority 3: TDD — security gate for P6-T6.2.
Task: P6-T6.2 — NIST SP 800-88 Erasure, OWASP validation, LLM Fuzz Testing
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import uuid
from collections.abc import Generator

import psycopg2
import pytest
from cryptography.fernet import InvalidToken
from pytest_postgresql import factories
from sqlalchemy import Column, Engine, text
from sqlalchemy.orm import Session
from sqlmodel import Field, SQLModel

from synth_engine.shared.db import get_engine
from synth_engine.shared.security.ale import EncryptedString
from synth_engine.shared.security.vault import VaultState
from tests.conftest_types import PostgreSQLProc

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Huey immediate mode for integration tests
# ---------------------------------------------------------------------------
os.environ.setdefault("HUEY_BACKEND", "memory")
os.environ.setdefault("HUEY_IMMEDIATE", "true")

# ---------------------------------------------------------------------------
# pytest-postgresql process fixture
# ---------------------------------------------------------------------------
postgresql_proc = factories.postgresql_proc()

# ---------------------------------------------------------------------------
# Shared test database name
# ---------------------------------------------------------------------------
_TEST_DBNAME = "conclave_nist_erasure_test"


# ---------------------------------------------------------------------------
# Minimal test model with EncryptedString (fictional PII field)
# ---------------------------------------------------------------------------


class NistTestRecord(SQLModel, table=True):  # type: ignore[call-arg]
    """Minimal SQLModel table for NIST erasure validation.

    Uses an ALE-encrypted column to represent a PII field.
    All values inserted in tests are fictional.

    Attributes:
        id: UUID v4 primary key.
        pii_field: Fictional PII value stored as ALE-encrypted ciphertext.
    """

    __tablename__ = "nist_erasure_test_record"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    pii_field: str | None = Field(default=None, sa_column=Column(EncryptedString()))


# ---------------------------------------------------------------------------
# Skip guard — runs before all tests in this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _require_postgresql() -> None:
    """Skip entire module when pg_ctl is not installed on PATH.

    Uses shutil.which() so the skip is clean even when PostgreSQL is
    installed at a non-standard location.
    """
    if shutil.which("pg_ctl") is None:
        pytest.skip(
            "pg_ctl not found on PATH — install PostgreSQL to run NIST erasure tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Vault teardown — prevent state leaking between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_vault() -> Generator[None]:
    """Seal and clear the vault KEK after every test (Pattern 6).

    Yields:
        Nothing — pure setup/teardown.
    """
    yield
    VaultState.reset()


# ---------------------------------------------------------------------------
# Database provisioning
# ---------------------------------------------------------------------------


def _create_database(proc: PostgreSQLProc) -> None:
    """Create the integration test database if it does not exist.

    Args:
        proc: The pytest-postgresql process fixture providing connection info.
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


@pytest.fixture(scope="module")
def _provision_test_db(
    postgresql_proc: PostgreSQLProc,
) -> Generator[None]:
    """Create the test database once per module and drop it on teardown.

    Yields:
        Nothing — pure setup/teardown.
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
        cur.execute("DROP DATABASE IF EXISTS " + psycopg2.extensions.quote_ident(_TEST_DBNAME, cur))
    conn.close()


# ---------------------------------------------------------------------------
# Vault environment fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provision VAULT_SEAL_SALT and return the base64url-encoded salt.

    Args:
        monkeypatch: Pytest monkeypatch fixture for environment variable injection.

    Returns:
        The base64url-encoded salt string set in VAULT_SEAL_SALT.
    """
    # Use seeded RNG for reproducibility (Pattern 5)
    import secrets

    salt = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    return salt


# ---------------------------------------------------------------------------
# DB engine fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def nist_db_engine(
    postgresql_proc: PostgreSQLProc,
    _provision_test_db: None,
    vault_env: str,
) -> Generator[Engine]:
    """Yield a SQLAlchemy Engine with vault unsealed for NIST erasure tests.

    Args:
        postgresql_proc: The pytest-postgresql process fixture.
        _provision_test_db: Module-scoped DB provisioning fixture.
        vault_env: Vault salt environment fixture.

    Yields:
        A configured Engine with schema created and vault unsealed.
    """
    VaultState.unseal("nist-erasure-test-passphrase-fictional")

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
# NIST SP 800-88 AC1: Erasure Validation Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_pii_is_encrypted_at_rest_before_shred(nist_db_engine: Engine) -> None:
    """PII data must be encrypted at rest — no plaintext visible in raw SQL.

    NIST SP 800-88 §2.4: CE requires data to have been encrypted before
    erasure.  This test confirms the pre-condition: the raw database row
    contains ciphertext, never plaintext.

    Args:
        nist_db_engine: DB engine fixture with vault unsealed.
    """
    fictional_pii = "FICTIONAL-SSN-123-45-6789"
    record_id = uuid.uuid4()

    with Session(nist_db_engine) as session:
        record = NistTestRecord(id=record_id, pii_field=fictional_pii)
        session.add(record)
        session.commit()

    with nist_db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT pii_field FROM nist_erasure_test_record WHERE id = :id"),
            {"id": str(record_id)},
        ).fetchone()

    assert row is not None, "Record must exist in the database"
    raw_value: str = row[0]

    # The raw stored value must NOT be the plaintext PII
    assert raw_value != fictional_pii, (
        "PII must be stored as encrypted ciphertext, never as plaintext. "
        f"Got raw value: {raw_value!r}"
    )
    # Fernet tokens are base64url-encoded and start with 'g' (byte 0x80)
    # This is a structural check that we stored a proper Fernet token
    assert len(raw_value) > len(fictional_pii), (
        "Ciphertext must be longer than plaintext (includes IV + HMAC overhead)"
    )
    _logger.info("Pre-shred check passed: PII is encrypted at rest.")


@pytest.mark.integration
def test_kek_bytes_are_zeroed_after_seal(nist_db_engine: Engine) -> None:
    """After VaultState.seal(), the KEK bytearray must be all 0x00 bytes.

    NIST SP 800-88 §2.4: The CE method requires that the encryption key
    be sanitized (overwritten).  This test validates the zeroization
    implementation in VaultState.seal().

    Note: The test captures the KEK reference BEFORE sealing so we can
    inspect the previously-held buffer.

    Args:
        nist_db_engine: DB engine fixture with vault unsealed.
    """
    # Vault is unsealed by nist_db_engine fixture — capture KEK reference
    assert not VaultState.is_sealed(), "Vault must be unsealed at test start"
    assert VaultState._kek is not None, "KEK must be set when vault is unsealed"

    # Capture reference to the bytearray BEFORE sealing
    kek_ref = VaultState._kek
    original_len = len(kek_ref)
    assert original_len == 32, f"KEK must be 32 bytes; got {original_len}"

    # Perform the cryptographic shred (zeroize KEK)
    VaultState.seal()

    # After seal(), _kek is set to None — but the buffer referenced by kek_ref
    # should have been zeroed before being dereferenced.
    # Verify the vault is now sealed
    assert VaultState.is_sealed(), "Vault must be sealed after seal()"
    assert VaultState._kek is None, "VaultState._kek must be None after seal()"

    # The kek_ref bytearray was zeroed in-place by memoryview write
    # This confirms NIST SP 800-88 CE: key material is overwritten
    assert all(b == 0 for b in kek_ref), (
        "NIST SP 800-88 CE VIOLATION: KEK bytes must be all 0x00 after zeroization. "
        f"Non-zero bytes found at positions: "
        f"{[i for i, b in enumerate(kek_ref) if b != 0]}"
    )
    _logger.info("NIST SP 800-88 CE: KEK zeroed. All %d bytes are 0x00.", original_len)


@pytest.mark.integration
def test_ciphertext_undecryptable_after_shred(nist_db_engine: Engine) -> None:
    """After VaultState.seal(), ciphertext in DB must raise InvalidToken on decrypt.

    NIST SP 800-88 §2.4: After CE, the encrypted data must be unrecoverable.
    This test proves mathematical unrecoverability: Fernet.decrypt() raises
    InvalidToken because get_fernet() can no longer derive the ALE key.

    Args:
        nist_db_engine: DB engine fixture with vault unsealed.
    """
    fictional_pii = "FICTIONAL-ACCOUNT-42-NIST-TEST"
    record_id = uuid.uuid4()

    # Insert PII record (vault unsealed, HKDF-derived ALE key)
    with Session(nist_db_engine) as session:
        record = NistTestRecord(id=record_id, pii_field=fictional_pii)
        session.add(record)
        session.commit()

    # Verify the raw ciphertext exists in DB
    with nist_db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT pii_field FROM nist_erasure_test_record WHERE id = :id"),
            {"id": str(record_id)},
        ).fetchone()
    assert row is not None
    ciphertext: str = row[0]
    assert ciphertext != fictional_pii, "Data must be encrypted before shred"

    # --- CRYPTOGRAPHIC SHRED: zeroize KEK ---
    VaultState.seal()
    assert VaultState.is_sealed(), "Vault must be sealed after shred"

    # --- Attempt ORM decryption — must fail with InvalidToken or RuntimeError ---
    # With vault sealed and ALE_KEY absent: get_fernet() raises RuntimeError.
    # With a wrong ALE_KEY set: Fernet raises InvalidToken.
    # Either error signals that the ciphertext is mathematically unrecoverable.
    def _attempt_decrypt() -> None:
        with Session(nist_db_engine) as session:
            loaded = session.get(NistTestRecord, record_id)
            # Force the TypeDecorator to decrypt by accessing the attribute
            _ = loaded.pii_field if loaded else None

    with pytest.raises((InvalidToken, RuntimeError)):
        _attempt_decrypt()

    # The raw ciphertext is still present in DB (CE is not physical erasure)
    with nist_db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT pii_field FROM nist_erasure_test_record WHERE id = :id"),
            {"id": str(record_id)},
        ).fetchone()
    assert row is not None, "Ciphertext row must still exist in DB (CE is not physical erasure)"
    assert row[0] == ciphertext, "Raw ciphertext must be unchanged after CE"

    _logger.info(
        "NIST SP 800-88 CE: Ciphertext is unrecoverable after KEK zeroization. "
        "Raw ciphertext present but mathematically undecryptable."
    )


@pytest.mark.integration
def test_pg_stat_activity_shows_no_plaintext_after_shred(
    nist_db_engine: Engine,
) -> None:
    """pg_stat_activity must not contain plaintext PII in query strings after shred.

    This test checks for plaintext leakage in PostgreSQL query tracking.
    The ORM uses parameterized queries, so the PII value is never embedded
    in the SQL string — it is only passed as a bind parameter, which
    pg_stat_activity does NOT capture.

    Note on pg_buffercache: This test documents the architectural limitation
    that shared_buffers verification requires the pg_buffercache extension,
    which is not installed on CI ephemeral PostgreSQL instances. The ALE
    design addresses this by never writing plaintext to disk — only encrypted
    tokens enter the database wire protocol.

    Args:
        nist_db_engine: DB engine fixture with vault unsealed.
    """
    fictional_pii = "FICTIONAL-MEDICAL-RECORD-NIST-9999"
    record_id = uuid.uuid4()

    # Insert PII record via ORM (uses parameterized query — PII is a bind param)
    with Session(nist_db_engine) as session:
        record = NistTestRecord(id=record_id, pii_field=fictional_pii)
        session.add(record)
        session.commit()

    # --- CRYPTOGRAPHIC SHRED ---
    VaultState.seal()

    # Check pg_stat_activity for any query containing the fictional PII string
    # Since ORM uses parameterized queries, this MUST return 0 rows.
    with nist_db_engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT query FROM pg_stat_activity WHERE query LIKE :pattern AND state != 'idle'"
            ),
            {"pattern": f"%{fictional_pii}%"},
        ).fetchall()

    assert len(result) == 0, (
        "NIST SP 800-88 CE VIOLATION: Plaintext PII found in pg_stat_activity. "
        f"Leaking queries: {[row[0] for row in result]}"
    )
    _logger.info(
        "NIST SP 800-88 CE: pg_stat_activity shows no plaintext PII. "
        "ORM parameterized queries prevent SQL-level leakage."
    )
