"""Unit tests for the ALE key rotation logic.

Tests verify:
- re_encrypt_column_values() decrypts with old key and re-encrypts with new key.
- find_encrypted_columns() finds columns using EncryptedString TypeDecorator.
- rotate_ale_keys() orchestrates the full re-encryption workflow.
- rotate_ale_keys_task() is a Huey task that wraps the orchestrator.
- Edge cases: no encrypted columns, empty tables, error propagation.
- OOM safety: re_encrypt_column_values uses batched reads, not fetchall().
- Input validation: batch_size <= 0 raises ValueError immediately.

CONSTITUTION Priority 3: TDD — Red Phase
Task: P5-T5.5 — Cryptographic Shredding & Re-Keying API
Task: P20-T20.4 — Architecture Tightening (AC4: OOM safety)
"""

from __future__ import annotations

from collections.abc import Generator
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
def _unseal_vault_for_ale(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Unseal the vault before each test and re-seal after (T48.5).

    ALE now requires an unsealed vault.  Rotation tests insert records via
    the EncryptedString TypeDecorator, which requires the vault to be unsealed.
    """
    import base64
    import os

    from synth_engine.shared.security.vault import VaultState
    from synth_engine.shared.settings import get_settings

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    get_settings.cache_clear()
    VaultState.unseal(bytearray(b"rotation-test-passphrase"))
    yield
    VaultState.reset()
    get_settings.cache_clear()


@pytest.fixture
def ale_key_old() -> str:
    """Return a string representation of the current vault-derived ALE key.

    T48.5: ALE_KEY env var fallback removed.  Rotation tests must use the
    vault-derived Fernet as the "old key" since that is what the ORM uses
    when inserting records.  This fixture returns the raw base64-encoded key
    material so tests can construct old_fernet = Fernet(ale_key_old.encode()).
    """
    import base64

    from synth_engine.shared.security.ale import _derive_ale_key_from_kek
    from synth_engine.shared.security.vault import VaultState

    kek = VaultState.get_kek()
    raw_key = _derive_ale_key_from_kek(kek)
    return base64.urlsafe_b64encode(raw_key).decode()


@pytest.fixture
def ale_key_new() -> str:
    """Generate a fresh Fernet key for the new key (not set in env)."""
    return Fernet.generate_key().decode()


@pytest.fixture
def in_memory_engine() -> object:
    """Create an in-memory SQLite engine with the test tables.

    Note: SQLite is used for unit tests; PostgreSQL is used for integration tests.
    The rotation logic uses generic SQLAlchemy and works with both.
    The autouse _unseal_vault_for_ale fixture ensures vault is unsealed
    before this engine is used (T48.5).
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


def test_re_encrypt_column_values_rejects_zero_batch_size(
    in_memory_engine: object,
    ale_key_old: str,
    ale_key_new: str,
) -> None:
    """re_encrypt_column_values must raise ValueError for batch_size=0.

    A batch_size of 0 causes fetchmany(0) to return [] immediately, silently
    processing zero rows. In a security-critical key rotation path this silent
    failure is unacceptable. The function must raise ValueError before executing
    any database operations.

    Arrange: in-memory engine (contents irrelevant — error fires before any DB I/O).
    Act: call re_encrypt_column_values with batch_size=0.
    Assert: ValueError is raised with a message naming batch_size.
    """
    from synth_engine.shared.security.rotation import re_encrypt_column_values

    old_fernet = Fernet(ale_key_old.encode())
    new_fernet = Fernet(ale_key_new.encode())

    with pytest.raises(ValueError, match="batch_size must be a positive integer"):
        re_encrypt_column_values(
            engine=in_memory_engine,  # type: ignore[arg-type]
            table_name="test_rotation_encrypted",
            column_name="secret",
            old_fernet=old_fernet,
            new_fernet=new_fernet,
            batch_size=0,
        )


def test_re_encrypt_column_values_rejects_negative_batch_size(
    in_memory_engine: object,
    ale_key_old: str,
    ale_key_new: str,
) -> None:
    """re_encrypt_column_values must raise ValueError for negative batch_size.

    Negative batch_size values are logically invalid and must be rejected
    immediately with a clear ValueError.
    """
    from synth_engine.shared.security.rotation import re_encrypt_column_values

    old_fernet = Fernet(ale_key_old.encode())
    new_fernet = Fernet(ale_key_new.encode())

    with pytest.raises(ValueError, match="batch_size must be a positive integer"):
        re_encrypt_column_values(
            engine=in_memory_engine,  # type: ignore[arg-type]
            table_name="test_rotation_encrypted",
            column_name="secret",
            old_fernet=old_fernet,
            new_fernet=new_fernet,
            batch_size=-5,
        )


# ---------------------------------------------------------------------------
# OOM safety: re_encrypt_column_values must use batched reads (AC4 — T20.4)
# ---------------------------------------------------------------------------


def test_re_encrypt_column_values_uses_batched_reads(
    in_memory_engine: object,
    ale_key_old: str,
    ale_key_new: str,
) -> None:
    """re_encrypt_column_values must process rows in batches, not via fetchall().

    AC4 (T20.4): The original implementation calls conn.execute(select_sql).fetchall()
    which loads ALL rows into memory at once — an OOM risk for large encrypted tables.
    The fixed implementation must use fetchmany(batch_size) to iterate in chunks.

    Arrange: insert 25 rows with a custom batch_size=10.
    Act: call re_encrypt_column_values with batch_size=10.
    Assert:
        - All 25 rows are re-encrypted correctly.
        - The function accepts a batch_size parameter (signature check).
    """
    from sqlalchemy import text

    from synth_engine.shared.security.rotation import re_encrypt_column_values

    old_fernet = Fernet(ale_key_old.encode())
    new_fernet = Fernet(ale_key_new.encode())

    # Insert 25 rows across multiple batches (batch_size=10 → 3 batches)
    with Session(in_memory_engine) as session:  # type: ignore[arg-type]
        for i in range(100, 125):
            session.add(_TestEncryptedModel(id=i, secret=f"plaintext-{i}"))
        session.commit()

    count = re_encrypt_column_values(
        engine=in_memory_engine,  # type: ignore[arg-type]
        table_name="test_rotation_encrypted",
        column_name="secret",
        old_fernet=old_fernet,
        new_fernet=new_fernet,
        batch_size=10,
    )

    assert count == 25, f"expected 25 rows re-encrypted, got {count}"

    # Verify all rows are decryptable with the new key
    with in_memory_engine.connect() as conn:  # type: ignore[union-attr]
        rows = conn.execute(
            text(
                "SELECT id, secret FROM test_rotation_encrypted "
                "WHERE id >= 100 AND id < 125 ORDER BY id"
            )
        ).fetchall()

    assert len(rows) == 25
    for row in rows:
        row_id, ciphertext = row[0], row[1]
        plaintext = new_fernet.decrypt(ciphertext.encode()).decode()
        assert plaintext == f"plaintext-{row_id}", (
            f"row id={row_id}: expected 'plaintext-{row_id}', got {plaintext!r}"
        )


def test_re_encrypt_column_values_default_batch_size_works(
    in_memory_engine: object,
    ale_key_old: str,
    ale_key_new: str,
) -> None:
    """re_encrypt_column_values works correctly when batch_size is not specified.

    The default batch_size must be used when the caller omits the parameter.
    Functional correctness is the same as the explicit-batch-size test.
    """
    from synth_engine.shared.security.rotation import re_encrypt_column_values

    old_fernet = Fernet(ale_key_old.encode())
    new_fernet = Fernet(ale_key_new.encode())

    with Session(in_memory_engine) as session:  # type: ignore[arg-type]
        for i in range(200, 205):
            session.add(_TestEncryptedModel(id=i, secret=f"value-{i}"))
        session.commit()

    # Call without batch_size — must use the default and succeed
    count = re_encrypt_column_values(
        engine=in_memory_engine,  # type: ignore[arg-type]
        table_name="test_rotation_encrypted",
        column_name="secret",
        old_fernet=old_fernet,
        new_fernet=new_fernet,
    )
    assert count == 5


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

    # callable() is redundant when .call_local is verified: all Huey tasks are callable.
    # Huey exposes .call_local() for synchronous in-process task invocation (no broker)
    assert hasattr(rotate_ale_keys_task, "call_local"), (
        "rotate_ale_keys_task must be a Huey task with a .call_local attribute"
    )


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
