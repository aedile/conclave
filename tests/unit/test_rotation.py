"""Unit tests for the ALE key rotation logic.

Tests verify:
- re_encrypt_column_values() decrypts with old key and re-encrypts with new key.
- find_encrypted_columns() finds columns using EncryptedString TypeDecorator.
- rotate_ale_keys() orchestrates the full re-encryption workflow.
- rotate_ale_keys_task() is a Huey task that wraps the orchestrator.
- Edge cases: no encrypted columns, empty tables, error propagation.

CONSTITUTION Priority 3: TDD — Red Phase
Task: P5-T5.5 — Cryptographic Shredding & Re-Keying API
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, create_engine
from sqlalchemy.orm import Session
from sqlmodel import Field, SQLModel

from synth_engine.shared.security.ale import EncryptedString

# ---------------------------------------------------------------------------
# Minimal test models
# ---------------------------------------------------------------------------


class _TestEncryptedModel(SQLModel, table=True):  # type: ignore[call-arg]
    """Minimal SQLModel table with an EncryptedString column for rotation tests."""

    __tablename__ = "test_rotation_encrypted"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    secret: str | None = Field(
        default=None,
        sa_column=Column("secret", EncryptedString()),
    )


class _TestPlainModel(SQLModel, table=True):  # type: ignore[call-arg]
    """Minimal SQLModel table without EncryptedString (plain String column)."""

    __tablename__ = "test_rotation_plain"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    name: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_vault() -> None:
    """Seal vault and clear ALE_KEY after every test."""
    yield  # type: ignore[misc]
    from synth_engine.shared.security.vault import VaultState

    VaultState.reset()


@pytest.fixture
def ale_key_old(monkeypatch: pytest.MonkeyPatch) -> str:
    """Generate an old Fernet key and set it as ALE_KEY."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ALE_KEY", key)
    return key


@pytest.fixture
def ale_key_new() -> str:
    """Generate a fresh Fernet key for the new key (not set in env)."""
    return Fernet.generate_key().decode()


@pytest.fixture
def in_memory_engine(ale_key_old: str) -> object:
    """Create an in-memory SQLite engine with the test tables.

    Note: SQLite is used for unit tests; PostgreSQL is used for integration tests.
    The rotation logic uses generic SQLAlchemy and works with both.
    """
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return engine


# ---------------------------------------------------------------------------
# find_encrypted_columns
# ---------------------------------------------------------------------------


def test_find_encrypted_columns_detects_encrypted_string(
    in_memory_engine: object,
) -> None:
    """find_encrypted_columns must return (table, column) pairs for EncryptedString columns.

    Arrange: engine with _TestEncryptedModel and _TestPlainModel tables.
    Act: call find_encrypted_columns(engine).
    Assert: result includes ("test_rotation_encrypted", "secret");
            does NOT include ("test_rotation_plain", "name").
    """
    from synth_engine.shared.security.rotation import find_encrypted_columns

    results = find_encrypted_columns(in_memory_engine)  # type: ignore[arg-type]

    assert isinstance(results, list)
    encrypted_table_cols = [(table, col) for table, col in results]
    assert ("test_rotation_encrypted", "secret") in encrypted_table_cols
    assert ("test_rotation_plain", "name") not in encrypted_table_cols


def test_find_encrypted_columns_returns_list() -> None:
    """find_encrypted_columns must always return a list, never None."""
    from synth_engine.shared.security.rotation import find_encrypted_columns

    engine = create_engine("sqlite:///:memory:")
    results = find_encrypted_columns(engine)
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# re_encrypt_column_values
# ---------------------------------------------------------------------------


def test_re_encrypt_column_values_changes_ciphertext(
    in_memory_engine: object,
    ale_key_old: str,
    ale_key_new: str,
) -> None:
    """re_encrypt_column_values must re-encrypt all rows with the new key.

    Arrange: insert a record encrypted with the old key.
    Act: call re_encrypt_column_values with old_fernet and new_fernet.
    Assert:
        - raw ciphertext in DB has changed (differs from original).
        - decrypting the new ciphertext with the new key returns the original plaintext.
        - decrypting the new ciphertext with the old key raises InvalidToken.
    """
    from cryptography.fernet import InvalidToken
    from sqlalchemy import text

    from synth_engine.shared.security.rotation import re_encrypt_column_values

    old_fernet = Fernet(ale_key_old.encode())
    new_fernet = Fernet(ale_key_new.encode())

    # Write a record using old key
    with Session(in_memory_engine) as session:  # type: ignore[arg-type]
        record = _TestEncryptedModel(id=1, secret="original-plaintext")
        session.add(record)
        session.commit()

    # Capture original ciphertext via raw SQL
    with in_memory_engine.connect() as conn:  # type: ignore[union-attr]
        row = conn.execute(
            text("SELECT secret FROM test_rotation_encrypted WHERE id = 1")
        ).fetchone()
    assert row is not None
    original_ciphertext: str = row[0]

    # Run re-encryption
    count = re_encrypt_column_values(
        engine=in_memory_engine,  # type: ignore[arg-type]
        table_name="test_rotation_encrypted",
        column_name="secret",
        old_fernet=old_fernet,
        new_fernet=new_fernet,
    )

    assert count == 1, "one row should have been re-encrypted"

    # Assert ciphertext changed
    with in_memory_engine.connect() as conn:  # type: ignore[union-attr]
        row = conn.execute(
            text("SELECT secret FROM test_rotation_encrypted WHERE id = 1")
        ).fetchone()
    assert row is not None
    new_ciphertext: str = row[0]
    assert new_ciphertext != original_ciphertext, (
        "re-encrypted ciphertext must differ from the original"
    )

    # Assert new ciphertext decrypts correctly with new key
    assert new_fernet.decrypt(new_ciphertext.encode()) == b"original-plaintext"

    # Assert new ciphertext is NOT decryptable with old key
    with pytest.raises(InvalidToken):
        old_fernet.decrypt(new_ciphertext.encode())


def test_re_encrypt_column_values_handles_null_values(
    in_memory_engine: object,
    ale_key_old: str,
    ale_key_new: str,
) -> None:
    """re_encrypt_column_values must skip NULL values without raising.

    NULL columns represent absent PII (e.g. optional fields). They must
    be preserved as NULL after re-encryption — not converted to ciphertext.
    """
    from sqlalchemy import text

    from synth_engine.shared.security.rotation import re_encrypt_column_values

    old_fernet = Fernet(ale_key_old.encode())
    new_fernet = Fernet(ale_key_new.encode())

    # Write a record with NULL secret
    with Session(in_memory_engine) as session:  # type: ignore[arg-type]
        record = _TestEncryptedModel(id=2, secret=None)
        session.add(record)
        session.commit()

    # Must not raise; null rows are skipped so count == 0
    count = re_encrypt_column_values(
        engine=in_memory_engine,  # type: ignore[arg-type]
        table_name="test_rotation_encrypted",
        column_name="secret",
        old_fernet=old_fernet,
        new_fernet=new_fernet,
    )

    assert count == 0, "NULL rows must be skipped (count == 0)"

    # NULL must remain NULL
    with in_memory_engine.connect() as conn:  # type: ignore[union-attr]
        row = conn.execute(
            text("SELECT secret FROM test_rotation_encrypted WHERE id = 2")
        ).fetchone()
    assert row is not None
    assert row[0] is None, "NULL values must remain NULL after re-encryption"


def test_re_encrypt_column_values_empty_table(
    in_memory_engine: object,
    ale_key_old: str,
    ale_key_new: str,
) -> None:
    """re_encrypt_column_values on an empty table must return 0 without error."""
    from synth_engine.shared.security.rotation import re_encrypt_column_values

    old_fernet = Fernet(ale_key_old.encode())
    new_fernet = Fernet(ale_key_new.encode())

    count = re_encrypt_column_values(
        engine=in_memory_engine,  # type: ignore[arg-type]
        table_name="test_rotation_encrypted",
        column_name="secret",
        old_fernet=old_fernet,
        new_fernet=new_fernet,
    )
    assert count == 0


# ---------------------------------------------------------------------------
# rotate_ale_keys (orchestrator)
# ---------------------------------------------------------------------------


def test_rotate_ale_keys_orchestrates_re_encryption(
    in_memory_engine: object,
    ale_key_old: str,
    ale_key_new: str,
) -> None:
    """rotate_ale_keys must find encrypted columns and re-encrypt all of them.

    Arrange: engine with an encrypted record.
    Act: call rotate_ale_keys(engine, old_fernet, new_fernet).
    Assert: record is re-encrypted with the new key.
    """
    from sqlalchemy import text

    from synth_engine.shared.security.rotation import rotate_ale_keys

    old_fernet = Fernet(ale_key_old.encode())
    new_fernet = Fernet(ale_key_new.encode())

    # Insert a record
    with Session(in_memory_engine) as session:  # type: ignore[arg-type]
        record = _TestEncryptedModel(id=10, secret="pii-data")
        session.add(record)
        session.commit()

    # Call the orchestrator
    results = rotate_ale_keys(
        engine=in_memory_engine,  # type: ignore[arg-type]
        old_fernet=old_fernet,
        new_fernet=new_fernet,
    )

    assert isinstance(results, dict)
    # The test_rotation_encrypted table must be in the results
    assert "test_rotation_encrypted.secret" in results

    # Assert the record is re-encrypted with the new key
    with in_memory_engine.connect() as conn:  # type: ignore[union-attr]
        row = conn.execute(
            text("SELECT secret FROM test_rotation_encrypted WHERE id = 10")
        ).fetchone()
    assert row is not None
    new_ciphertext: str = row[0]
    assert new_fernet.decrypt(new_ciphertext.encode()) == b"pii-data"


def test_rotate_ale_keys_propagates_re_encrypt_exception(
    in_memory_engine: object,
    ale_key_old: str,
    ale_key_new: str,
) -> None:
    """rotate_ale_keys must re-raise exceptions from re_encrypt_column_values.

    This covers the error path (lines 200-204 in rotation.py):
    if re_encrypt_column_values raises, rotate_ale_keys logs and re-raises.
    """
    from synth_engine.shared.security.rotation import rotate_ale_keys

    old_fernet = Fernet(ale_key_old.encode())
    new_fernet = Fernet(ale_key_new.encode())

    with patch(
        "synth_engine.shared.security.rotation.re_encrypt_column_values",
        side_effect=RuntimeError("DB error during re-encryption"),
    ):
        with pytest.raises(RuntimeError, match="DB error during re-encryption"):
            rotate_ale_keys(
                engine=in_memory_engine,  # type: ignore[arg-type]
                old_fernet=old_fernet,
                new_fernet=new_fernet,
            )


# ---------------------------------------------------------------------------
# Huey task wrapper — rotate_ale_keys_task
# ---------------------------------------------------------------------------


def test_rotate_ale_keys_task_is_huey_decorated() -> None:
    """rotate_ale_keys_task must be a callable Huey task."""
    from synth_engine.shared.security.rotation import rotate_ale_keys_task

    assert callable(rotate_ale_keys_task), "rotate_ale_keys_task must be callable"
    # Huey exposes .call_local() for synchronous in-process task invocation (no broker)
    assert hasattr(rotate_ale_keys_task, "call_local")


def test_rotate_ale_keys_task_calls_rotate_ale_keys(
    ale_key_old: str,
    ale_key_new: str,
) -> None:
    """rotate_ale_keys_task must call rotate_ale_keys with correct arguments.

    Uses Huey's .call_local() for synchronous in-process invocation,
    which is the recommended integration-test pattern per Huey docs.
    """
    from synth_engine.shared.security.rotation import rotate_ale_keys_task

    engine_mock = MagicMock()
    mock_results: dict[str, int] = {"test_table.secret": 5}

    # Wrap the new key with the old Fernet, matching what the API handler does.
    # The task unwraps it internally — passing a plain key would raise InvalidToken.
    old_fernet_instance = Fernet(ale_key_old.encode())
    wrapped_key = old_fernet_instance.encrypt(ale_key_new.encode()).decode()

    with (
        patch(
            "synth_engine.shared.security.rotation.get_engine",
            return_value=engine_mock,
        ) as mock_get_engine,
        patch(
            "synth_engine.shared.security.rotation.get_fernet",
            return_value=old_fernet_instance,
        ),
        patch(
            "synth_engine.shared.security.rotation.rotate_ale_keys",
            return_value=mock_results,
        ) as mock_rotate,
    ):
        # call_local() executes the task synchronously without Huey broker
        result = rotate_ale_keys_task.call_local("sqlite:///test.db", wrapped_key)

    mock_get_engine.assert_called_once_with("sqlite:///test.db")
    assert mock_rotate.called
    assert result == mock_results
    engine_mock.dispose.assert_called_once()
