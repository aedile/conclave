"""Unit tests for T39.4 — Connection metadata ALE encryption.

Verifies that ``host``, ``database``, and ``schema_name`` columns on the
``Connection`` SQLModel use the ``EncryptedString`` TypeDecorator, that raw
database values are encrypted (not plaintext), that ORM reads return decrypted
plaintext, and that ``port`` remains a plain integer.

CONSTITUTION Priority 3: TDD — RED phase
Task: T39.4 — Encrypt Connection Metadata with ALE
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, text

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_vault_and_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Seal the vault and clear settings cache after every test.

    Ensures ALE key state does not bleed between tests.
    """
    from synth_engine.shared.security.ale import _reset_fernet_cache
    from synth_engine.shared.security.vault import VaultState
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    _reset_fernet_cache()
    VaultState.reset()
    get_settings.cache_clear()


@pytest.fixture
def ale_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provision a fresh Fernet key in the environment and clear settings cache.

    Returns:
        The base64-encoded Fernet key string set as ALE_KEY.
    """
    from synth_engine.shared.settings import get_settings

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ALE_KEY", key)
    get_settings.cache_clear()
    return key


@pytest.fixture
def db_engine(ale_key: str) -> Any:
    """Return an in-memory SQLite engine with Connection table created.

    Args:
        ale_key: ALE key fixture (ensures key is set before engine creation).

    Yields:
        A SQLAlchemy engine with the schema initialised.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Column type assertions — verify EncryptedString is applied
# ---------------------------------------------------------------------------


def test_host_column_uses_encrypted_string_type() -> None:
    """Connection.host column must use EncryptedString TypeDecorator."""
    from sqlalchemy import inspect as sa_inspect

    from synth_engine.bootstrapper.schemas.connections import Connection
    from synth_engine.shared.security.ale import EncryptedString

    mapper = sa_inspect(Connection)
    col = mapper.columns["host"]
    assert isinstance(
        col.type,
        EncryptedString,
    ), f"Expected EncryptedString on 'host', got {type(col.type)}"


def test_database_column_uses_encrypted_string_type() -> None:
    """Connection.database column must use EncryptedString TypeDecorator."""
    from sqlalchemy import inspect as sa_inspect

    from synth_engine.bootstrapper.schemas.connections import Connection
    from synth_engine.shared.security.ale import EncryptedString

    mapper = sa_inspect(Connection)
    col = mapper.columns["database"]
    assert isinstance(
        col.type,
        EncryptedString,
    ), f"Expected EncryptedString on 'database', got {type(col.type)}"


def test_schema_name_column_uses_encrypted_string_type() -> None:
    """Connection.schema_name column must use EncryptedString TypeDecorator."""
    from sqlalchemy import inspect as sa_inspect

    from synth_engine.bootstrapper.schemas.connections import Connection
    from synth_engine.shared.security.ale import EncryptedString

    mapper = sa_inspect(Connection)
    col = mapper.columns["schema_name"]
    assert isinstance(
        col.type,
        EncryptedString,
    ), f"Expected EncryptedString on 'schema_name', got {type(col.type)}"


def test_port_column_is_not_encrypted() -> None:
    """Connection.port column must remain a plain Integer, not EncryptedString."""
    from sqlalchemy import Integer
    from sqlalchemy import inspect as sa_inspect

    from synth_engine.bootstrapper.schemas.connections import Connection
    from synth_engine.shared.security.ale import EncryptedString

    mapper = sa_inspect(Connection)
    col = mapper.columns["port"]
    assert isinstance(
        col.type,
        Integer,
    ), f"Expected Integer on 'port', got {type(col.type)}"
    assert not isinstance(
        col.type,
        EncryptedString,
    ), "port must NOT be encrypted"


# ---------------------------------------------------------------------------
# Encryption at rest — raw SQL must not reveal plaintext
# ---------------------------------------------------------------------------


def test_raw_database_value_is_encrypted_for_host(db_engine: Any, ale_key: str) -> None:
    """Raw SQL read of the host column must return ciphertext, not plaintext.

    This is the key security assertion: even if an attacker obtains a direct
    database connection, the sensitive host value must not be readable.
    """
    from synth_engine.bootstrapper.schemas.connections import Connection

    plaintext_host = "prod-postgres.internal"

    with Session(db_engine) as session:
        conn = Connection(
            name="enc-test",
            host=plaintext_host,
            port=5432,
            database="testdb",
            schema_name="public",
        )
        session.add(conn)
        session.commit()
        conn_id = conn.id

    with db_engine.connect() as raw_conn:
        result = raw_conn.execute(
            text("SELECT host FROM connection WHERE id = :id"),
            {"id": conn_id},
        )
        raw_value: str = result.scalar_one()

    assert raw_value != plaintext_host, (
        f"host stored in plaintext! raw value '{raw_value}' matches plaintext '{plaintext_host}'"
    )
    # The raw value must be a Fernet token (non-empty ciphertext)
    assert len(raw_value) > len(plaintext_host), (
        "Encrypted value should be longer than plaintext (Fernet overhead)"
    )


def test_raw_database_value_is_encrypted_for_database_field(
    db_engine: Any, ale_key: str
) -> None:
    """Raw SQL read of the database column must return ciphertext, not plaintext."""
    from synth_engine.bootstrapper.schemas.connections import Connection

    plaintext_db = "sensitive_database_name"

    with Session(db_engine) as session:
        conn = Connection(
            name="enc-test-db",
            host="localhost",
            port=5432,
            database=plaintext_db,
            schema_name="public",
        )
        session.add(conn)
        session.commit()
        conn_id = conn.id

    with db_engine.connect() as raw_conn:
        result = raw_conn.execute(
            text("SELECT database FROM connection WHERE id = :id"),
            {"id": conn_id},
        )
        raw_value = result.scalar_one()

    assert raw_value != plaintext_db, (
        f"database stored in plaintext! raw value '{raw_value}' matches plaintext '{plaintext_db}'"
    )


def test_raw_database_value_is_encrypted_for_schema_name(
    db_engine: Any, ale_key: str
) -> None:
    """Raw SQL read of the schema_name column must return ciphertext, not plaintext."""
    from synth_engine.bootstrapper.schemas.connections import Connection

    plaintext_schema = "confidential_schema"

    with Session(db_engine) as session:
        conn = Connection(
            name="enc-test-schema",
            host="localhost",
            port=5432,
            database="testdb",
            schema_name=plaintext_schema,
        )
        session.add(conn)
        session.commit()
        conn_id = conn.id

    with db_engine.connect() as raw_conn:
        result = raw_conn.execute(
            text("SELECT schema_name FROM connection WHERE id = :id"),
            {"id": conn_id},
        )
        raw_value = result.scalar_one()

    assert raw_value != plaintext_schema, (
        f"schema_name stored in plaintext! raw value '{raw_value}' matches plaintext '{plaintext_schema}'"
    )


# ---------------------------------------------------------------------------
# ORM transparency — ORM read must return decrypted plaintext
# ---------------------------------------------------------------------------


def test_orm_read_returns_decrypted_host(db_engine: Any, ale_key: str) -> None:
    """ORM read of Connection.host must return the original plaintext value.

    The EncryptedString TypeDecorator must transparently decrypt on SELECT.
    """
    from synth_engine.bootstrapper.schemas.connections import Connection

    plaintext_host = "my-database-host.example.com"

    with Session(db_engine) as session:
        conn = Connection(
            name="orm-decrypt-test",
            host=plaintext_host,
            port=5432,
            database="mydb",
            schema_name="public",
        )
        session.add(conn)
        session.commit()
        conn_id = conn.id

    with Session(db_engine) as session:
        fetched = session.get(Connection, conn_id)
        assert fetched is not None
        assert fetched.host == plaintext_host, (
            f"ORM must decrypt host transparently; got '{fetched.host}'"
        )


def test_orm_read_returns_decrypted_database(db_engine: Any, ale_key: str) -> None:
    """ORM read of Connection.database must return the original plaintext value."""
    from synth_engine.bootstrapper.schemas.connections import Connection

    plaintext_db = "production_warehouse"

    with Session(db_engine) as session:
        conn = Connection(
            name="orm-decrypt-db",
            host="localhost",
            port=5432,
            database=plaintext_db,
            schema_name="public",
        )
        session.add(conn)
        session.commit()
        conn_id = conn.id

    with Session(db_engine) as session:
        fetched = session.get(Connection, conn_id)
        assert fetched is not None
        assert fetched.database == plaintext_db


def test_orm_read_returns_decrypted_schema_name(db_engine: Any, ale_key: str) -> None:
    """ORM read of Connection.schema_name must return the original plaintext value."""
    from synth_engine.bootstrapper.schemas.connections import Connection

    plaintext_schema = "analytics"

    with Session(db_engine) as session:
        conn = Connection(
            name="orm-decrypt-schema",
            host="localhost",
            port=5432,
            database="mydb",
            schema_name=plaintext_schema,
        )
        session.add(conn)
        session.commit()
        conn_id = conn.id

    with Session(db_engine) as session:
        fetched = session.get(Connection, conn_id)
        assert fetched is not None
        assert fetched.schema_name == plaintext_schema


def test_port_value_is_stored_and_read_as_plain_integer(
    db_engine: Any, ale_key: str
) -> None:
    """port must be stored and retrieved as a plain integer, not ciphertext."""
    from synth_engine.bootstrapper.schemas.connections import Connection

    with Session(db_engine) as session:
        conn = Connection(
            name="port-test",
            host="localhost",
            port=5432,
            database="mydb",
            schema_name="public",
        )
        session.add(conn)
        session.commit()
        conn_id = conn.id

    with db_engine.connect() as raw_conn:
        result = raw_conn.execute(
            text("SELECT port FROM connection WHERE id = :id"),
            {"id": conn_id},
        )
        raw_port = result.scalar_one()

    assert raw_port == 5432, f"port must be plaintext integer 5432, got {raw_port!r}"
