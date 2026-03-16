"""Integration tests for Application-Level Encryption (ALE) via real PostgreSQL.

These tests verify the full ALE round-trip using a live, ephemeral PostgreSQL
instance managed by ``pytest-postgresql``.  They prove that:

1. **Raw-SQL assertion** — querying the database column directly (bypassing the
   SQLAlchemy ORM TypeDecorator) returns Fernet ciphertext, NOT the plaintext.
2. **ORM assertion** — querying via SQLModel transparently decrypts the value,
   returning the original plaintext.
3. **Vault-wired path** — unsealing the vault causes ALE to derive its key via
   HKDF-SHA256 from the vault KEK; the round-trip still succeeds end-to-end.

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH (supplied by a local PostgreSQL installation
  or the CI image).  If the binary is absent, all tests in this module are
  skipped automatically via the ``_require_postgresql`` auto-use fixture.

Marks: ``integration``

CONSTITUTION Priority 0: Security — PII never stored as plaintext.
CONSTITUTION Priority 3: TDD — integration gate for P2-T2.2.
Task: P2-T2.2 — Secure Database Layer (debt item D2)
"""

from __future__ import annotations

import base64
import os
import shutil
import uuid
from collections.abc import Generator

import psycopg2
import pytest
from cryptography.fernet import Fernet
from pytest_postgresql import factories
from sqlalchemy import Column, Engine, text
from sqlalchemy.orm import Session
from sqlmodel import Field, SQLModel

from synth_engine.shared.db import get_engine
from synth_engine.shared.security.ale import EncryptedString
from synth_engine.shared.security.vault import VaultState

# ---------------------------------------------------------------------------
# pytest-postgresql process fixture
# ---------------------------------------------------------------------------
postgresql_proc = factories.postgresql_proc()

# ---------------------------------------------------------------------------
# Shared test database name
# ---------------------------------------------------------------------------
_TEST_DBNAME = "conclave_ale_integration"


# ---------------------------------------------------------------------------
# Minimal test table
# ---------------------------------------------------------------------------


class SensitiveRecord(SQLModel, table=True):  # type: ignore[call-arg]
    """Minimal SQLModel table with a single EncryptedString PII column.

    Used exclusively within this integration-test module.

    Attributes:
        id: UUID v4 primary key.
        pii_value: PII field stored as ALE-encrypted ciphertext in the DB.
    """

    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    pii_value: str = Field(sa_column=Column(EncryptedString()))


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
            "pg_ctl not found on PATH — install PostgreSQL to run ALE integration tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Vault teardown — prevent state leaking across tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_vault() -> Generator[None]:
    """Seal and clear the vault KEK after every test.

    Ensures that vault state set in one test (e.g. the vault-wired path test)
    does not bleed into subsequent tests that expect a sealed vault.

    Yields:
        None — this is a teardown-only fixture.
    """
    yield
    VaultState.reset()


# ---------------------------------------------------------------------------
# Database provisioning helper
# ---------------------------------------------------------------------------


def _create_database(proc: factories.postgresql_proc) -> None:  # type: ignore[valid-type]
    """Create the integration test database using psycopg2.

    Connects to the ``postgres`` maintenance database on the ephemeral
    PostgreSQL process, then issues ``CREATE DATABASE`` with ``autocommit``
    enabled (required because DDL cannot run inside a transaction block).

    The database is dropped and recreated each test session by dropping it in
    :func:`_provision_test_db` teardown, guaranteeing a clean schema slate.

    Args:
        proc: The ``postgresql_proc`` executor providing host/port/user.
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


# ---------------------------------------------------------------------------
# Session-scoped DB provisioning
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ale_env_key() -> str:
    """Generate a fresh Fernet key for the env-fallback ALE path.

    Returns:
        URL-safe base64-encoded 32-byte Fernet key string.
    """
    return Fernet.generate_key().decode()  # pragma: allowlist secret


@pytest.fixture(scope="module")
def _provision_test_db(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]
) -> Generator[None]:
    """Create the test database once per module and drop it on teardown.

    Args:
        postgresql_proc: The running PostgreSQL process executor.

    Yields:
        None — setup/teardown only.
    """
    _create_database(postgresql_proc)
    yield
    # Teardown: drop the test database so the next session starts clean.
    conn = psycopg2.connect(
        dbname="postgres",
        user=postgresql_proc.user,
        host=postgresql_proc.host,
        port=postgresql_proc.port,
        password=postgresql_proc.password or "",
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        # Terminate active connections — uses %s placeholder to avoid S608.
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (_TEST_DBNAME,),
        )
        # DROP DATABASE cannot use parameterised placeholders; _TEST_DBNAME is a
        # compile-time constant (not user input), so interpolation is safe here.
        cur.execute("DROP DATABASE IF EXISTS " + psycopg2.extensions.quote_ident(_TEST_DBNAME, cur))
    conn.close()


# ---------------------------------------------------------------------------
# Engine fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_engine(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]
    _provision_test_db: None,
    ale_env_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Engine]:
    """Yield a SQLAlchemy Engine connected to the ephemeral PostgreSQL instance.

    Builds the psycopg2 connection URL from the ``postgresql_proc`` executor
    attributes, creates the ``SensitiveRecord`` schema, and disposes the engine
    on exit.

    Args:
        postgresql_proc: pytest-postgresql process executor with connection attrs.
        _provision_test_db: Module-scoped fixture that ensures the DB exists.
        ale_env_key: Fresh Fernet key injected as ``ALE_KEY`` env var.
        monkeypatch: pytest monkeypatch for environment variable injection.

    Yields:
        A configured :class:`sqlalchemy.Engine` instance.
    """
    monkeypatch.setenv("ALE_KEY", ale_env_key)

    proc = postgresql_proc
    url = (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_TEST_DBNAME}"
    )
    engine = get_engine(url)
    SQLModel.metadata.create_all(engine)

    yield engine

    engine.dispose()


@pytest.fixture
def vault_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provision VAULT_SEAL_SALT so VaultState.unseal() can derive the KEK.

    Args:
        monkeypatch: pytest monkeypatch for environment variable injection.
    """
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)


@pytest.fixture
def vault_db_engine(
    postgresql_proc: factories.postgresql_proc,  # type: ignore[valid-type]
    _provision_test_db: None,
    vault_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Engine]:
    """Yield a SQLAlchemy Engine with the vault unsealed (HKDF path).

    Unseals ``VaultState`` so that ``get_fernet()`` derives the ALE key via
    HKDF-SHA256 from the vault KEK.  Tears down by calling ``VaultState.reset()``.

    Args:
        postgresql_proc: pytest-postgresql process executor with connection attrs.
        _provision_test_db: Module-scoped fixture that ensures the DB exists.
        vault_env: Fixture that provisions ``VAULT_SEAL_SALT``.
        monkeypatch: pytest monkeypatch (unused here; vault_env injects the salt).

    Yields:
        A configured :class:`sqlalchemy.Engine` instance.
    """
    VaultState.unseal("integration-test-passphrase")

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
# Integration tests — env-fallback ALE path (vault sealed)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_raw_sql_returns_ciphertext(db_engine: Engine) -> None:
    """Raw SQL bypassing the ORM must return Fernet ciphertext, not plaintext.

    This test is the definitive proof that PII is never stored as plaintext:
    even with direct database access (e.g. a compromised DBA console), an
    attacker sees only opaque ciphertext.

    Arrange: insert a ``SensitiveRecord`` via ORM session.
    Act: query ``pii_value`` directly via ``engine.connect()`` + ``text()``.
    Assert: raw DB value != plaintext AND starts with Fernet token prefix.
    """
    plaintext = "super-secret-pii-value"
    record_id = uuid.uuid4()

    with Session(db_engine) as session:
        record = SensitiveRecord(id=record_id, pii_value=plaintext)
        session.add(record)
        session.commit()

    with db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT pii_value FROM sensitiverecord WHERE id = :id"),
            {"id": str(record_id)},
        ).fetchone()

    assert row is not None, "record must exist in the database"
    raw_value: str = row[0]

    assert raw_value != plaintext, (
        "raw DB value must NOT equal plaintext — PII must be encrypted at rest"
    )
    # Fernet tokens are URL-safe base64 and always start with "gAAAAA"
    assert isinstance(raw_value, str)
    assert raw_value.startswith("gAAAAA"), (
        f"expected Fernet token prefix 'gAAAAA', got: {raw_value[:10]!r}"
    )


@pytest.mark.integration
def test_orm_query_returns_plaintext(db_engine: Engine) -> None:
    """ORM query via SQLModel must transparently decrypt the stored ciphertext.

    The EncryptedString TypeDecorator's ``process_result_value`` hook fires
    automatically when SQLAlchemy loads a column value.  This test verifies
    the seamless decrypt-on-read behaviour end-to-end against a real database.

    Arrange: insert a ``SensitiveRecord`` via ORM session.
    Act: retrieve it with ``session.get(SensitiveRecord, id)``.
    Assert: ``record.pii_value == original_plaintext``.
    """
    plaintext = "another-pii-value-for-orm-read"
    record_id = uuid.uuid4()

    with Session(db_engine) as session:
        record = SensitiveRecord(id=record_id, pii_value=plaintext)
        session.add(record)
        session.commit()

    with Session(db_engine) as session:
        loaded = session.get(SensitiveRecord, record_id)

    assert loaded is not None, "ORM must retrieve the inserted record"
    assert loaded.pii_value == plaintext, (
        "ORM read must transparently decrypt the stored ciphertext back to plaintext"
    )


@pytest.mark.integration
def test_full_roundtrip_write_then_read(db_engine: Engine) -> None:
    """Full ALE round-trip: write encrypted, read decrypted, raw is ciphertext.

    Combines both assertions in a single test to verify coherence: the same
    record is (a) encrypted at rest, (b) decrypted by the ORM, and (c) the
    raw value carries the canonical Fernet token prefix.
    """
    plaintext = "combined-roundtrip-pii"
    record_id = uuid.uuid4()

    with Session(db_engine) as session:
        session.add(SensitiveRecord(id=record_id, pii_value=plaintext))
        session.commit()

    # Raw assertion — must be ciphertext
    with db_engine.connect() as conn:
        raw = conn.execute(
            text("SELECT pii_value FROM sensitiverecord WHERE id = :id"),
            {"id": str(record_id)},
        ).scalar()

    assert raw != plaintext
    assert isinstance(raw, str)
    assert raw.startswith("gAAAAA")

    # ORM assertion — must be plaintext
    with Session(db_engine) as session:
        loaded = session.get(SensitiveRecord, record_id)

    assert loaded is not None
    assert loaded.pii_value == plaintext


# ---------------------------------------------------------------------------
# Integration tests — vault-wired ALE path (vault unsealed)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_vault_wired_ale_encrypts_and_decrypts(vault_db_engine: Engine) -> None:
    """Vault-wired ALE (HKDF path) must encrypt on write and decrypt on read.

    When the vault is unsealed, ``get_fernet()`` uses HKDF-SHA256 to derive
    the ALE key from the vault KEK instead of the ``ALE_KEY`` env var.  This
    test verifies the full round-trip through a real database using that path.

    Arrange: unseal the vault (done by ``vault_db_engine`` fixture) and insert
        a ``SensitiveRecord`` via ORM.
    Act: query raw SQL and then query via ORM.
    Assert:
        - raw value != plaintext (encrypted at rest even on vault path),
        - ORM value == plaintext (transparent decryption still works),
        - vault is re-sealed in teardown (``_reset_vault`` autouse fixture).
    """
    assert not VaultState.is_sealed(), "vault must be unsealed for this test"

    plaintext = "vault-keyed-pii-value"
    record_id = uuid.uuid4()

    with Session(vault_db_engine) as session:
        session.add(SensitiveRecord(id=record_id, pii_value=plaintext))
        session.commit()

    # Raw SQL must return ciphertext (vault path also encrypts at rest)
    with vault_db_engine.connect() as conn:
        raw = conn.execute(
            text("SELECT pii_value FROM sensitiverecord WHERE id = :id"),
            {"id": str(record_id)},
        ).scalar()

    assert raw != plaintext, "vault-path ALE must encrypt PII at rest"
    assert isinstance(raw, str)
    assert raw.startswith("gAAAAA"), (
        f"vault-path ciphertext must be a Fernet token, got {raw[:10]!r}"
    )

    # ORM must transparently decrypt
    with Session(vault_db_engine) as session:
        loaded = session.get(SensitiveRecord, record_id)

    assert loaded is not None
    assert loaded.pii_value == plaintext, (
        "vault-path ORM read must transparently decrypt back to plaintext"
    )


# ---------------------------------------------------------------------------
# Nullable test model — for NULL and edge-case integration tests (ADV-021)
# ---------------------------------------------------------------------------


class NullableSensitiveRecord(SQLModel, table=True):  # type: ignore[call-arg]
    """SQLModel table with a *nullable* EncryptedString PII column.

    Used by ADV-021 integration tests to exercise NULL passthrough, empty-
    string, and unicode/multi-byte paths through the EncryptedString
    TypeDecorator against a real database.

    Attributes:
        id: UUID v4 primary key.
        pii_value: Nullable PII field stored as ALE-encrypted ciphertext.
    """

    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    pii_value: str | None = Field(default=None, sa_column=Column(EncryptedString()))


# ---------------------------------------------------------------------------
# Integration tests — edge cases (ADV-021)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_null_roundtrip_returns_none(db_engine: Engine) -> None:
    """NULL written to an EncryptedString column must round-trip back as None.

    EncryptedString.process_bind_param returns None unchanged; on read,
    process_result_value also returns None.  This test exercises both paths
    against a real database column, confirming NULL is not coerced to an
    empty string or any other value.

    Arrange: insert a ``NullableSensitiveRecord`` with ``pii_value=None``.
    Act: retrieve it via ORM session.
    Assert: ``loaded.pii_value is None``.
    """
    record_id = uuid.uuid4()

    with Session(db_engine) as session:
        record = NullableSensitiveRecord(id=record_id, pii_value=None)
        session.add(record)
        session.commit()

    with Session(db_engine) as session:
        loaded = session.get(NullableSensitiveRecord, record_id)

    assert loaded is not None, "ORM must retrieve the inserted record"
    assert loaded.pii_value is None, (
        "NULL pii_value must round-trip as None, not empty string or any other value"
    )


@pytest.mark.integration
def test_empty_string_roundtrip_returns_empty_string(db_engine: Engine) -> None:
    """Empty string written to EncryptedString must round-trip back as empty string.

    An empty string is a distinct value from NULL.  The TypeDecorator must
    encrypt the empty byte sequence and decrypt it back to the original empty
    string — not coerce it to None or any other sentinel.

    Arrange: insert a ``NullableSensitiveRecord`` with ``pii_value=""``.
    Act: retrieve it via ORM session.
    Assert: ``loaded.pii_value == ""`` (exact empty string).
    """
    record_id = uuid.uuid4()

    with Session(db_engine) as session:
        record = NullableSensitiveRecord(id=record_id, pii_value="")
        session.add(record)
        session.commit()

    with Session(db_engine) as session:
        loaded = session.get(NullableSensitiveRecord, record_id)

    assert loaded is not None, "ORM must retrieve the inserted record"
    assert loaded.pii_value == "", (
        "empty-string pii_value must round-trip as '' — must not be coerced to None"
    )


@pytest.mark.integration
def test_cjk_unicode_roundtrip_returns_exact_string(db_engine: Engine) -> None:
    """CJK multi-byte unicode PII must survive the encrypt/decrypt round-trip.

    Japanese characters require multi-byte UTF-8 encoding.  This test
    confirms that EncryptedString correctly encodes to bytes before
    encryption and decodes from bytes after decryption — preserving every
    code point of multi-byte unicode PII.

    Arrange: insert a ``NullableSensitiveRecord`` with CJK PII value.
    Act: retrieve it via ORM session.
    Assert: ``loaded.pii_value == "日本語テスト"`` (exact match).
    """
    cjk_pii = "日本語テスト"
    record_id = uuid.uuid4()

    with Session(db_engine) as session:
        record = NullableSensitiveRecord(id=record_id, pii_value=cjk_pii)
        session.add(record)
        session.commit()

    with Session(db_engine) as session:
        loaded = session.get(NullableSensitiveRecord, record_id)

    assert loaded is not None, "ORM must retrieve the inserted record"
    assert loaded.pii_value == cjk_pii, (
        f"CJK pii_value must round-trip exactly; expected {cjk_pii!r}, got {loaded.pii_value!r}"
    )


@pytest.mark.integration
def test_emoji_unicode_roundtrip_returns_exact_string(db_engine: Engine) -> None:
    """Emoji PII (4-byte UTF-8 code points) must survive the round-trip.

    Emoji characters sit above the Basic Multilingual Plane and require
    4-byte UTF-8 encoding.  This is the most demanding unicode path for
    the TypeDecorator's encode/decode logic.

    Arrange: insert a ``NullableSensitiveRecord`` with emoji PII.
    Act: retrieve it via ORM session.
    Assert: ``loaded.pii_value == "🔒 Encrypted PII 🔐"`` (exact match).
    """
    emoji_pii = "🔒 Encrypted PII 🔐"
    record_id = uuid.uuid4()

    with Session(db_engine) as session:
        record = NullableSensitiveRecord(id=record_id, pii_value=emoji_pii)
        session.add(record)
        session.commit()

    with Session(db_engine) as session:
        loaded = session.get(NullableSensitiveRecord, record_id)

    assert loaded is not None, "ORM must retrieve the inserted record"
    assert loaded.pii_value == emoji_pii, (
        f"Emoji pii_value must round-trip exactly; expected {emoji_pii!r}, got {loaded.pii_value!r}"
    )
