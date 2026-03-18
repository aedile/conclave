"""ALE key rotation logic for the Conclave Engine.

Provides utilities to:
- Discover all SQLAlchemy/SQLModel columns using the
  :class:`~synth_engine.shared.security.ale.EncryptedString` TypeDecorator
  via table introspection.
- Re-encrypt every row in those columns from an old Fernet key to a new one.
- An orchestrating ``rotate_ale_keys()`` function that combines both steps.
- A Huey task ``rotate_ale_keys_task`` that wraps the orchestrator for
  asynchronous background execution (required for large datasets).

Security properties
-------------------
- The old Fernet key is only held in memory for the duration of the task and
  is not persisted anywhere.
- The new Fernet key is transmitted to the task as a KEK-wrapped ciphertext
  (encrypted with the current vault Fernet) so that it is never stored in the
  Huey broker (Redis) in plaintext.
- Re-encryption is performed inside a single ``engine.begin()`` transaction
  spanning all batches. A failure at any point rolls back ALL changes — the
  operation is all-or-nothing. This prevents partial-rotation states where
  some rows hold new-key ciphertext while others hold old-key ciphertext.
- NULL column values are skipped: they represent absent PII and must remain NULL
  after rotation (converting NULL -> ciphertext of empty string would be wrong).

OOM safety
----------
``re_encrypt_column_values`` uses ``fetchmany(batch_size)`` to iterate over rows
in configurable batches rather than ``fetchall()``, which would load the entire
encrypted column into memory at once.  The default batch size is 1000 rows.
For a typical 200-byte Fernet ciphertext, 1000 rows ~ 200 KB peak overhead per
column -- a negligible fraction of available memory even on constrained hosts.

Boundary constraints
--------------------
This module lives in ``shared/security/`` and has no imports from
``bootstrapper/`` or ``modules/``.  It uses only stdlib, SQLAlchemy core,
and ``shared/`` siblings.

CONSTITUTION Priority 0: Security
Task: P5-T5.5 -- Cryptographic Shredding & Re-Keying API
Task: P20-T20.4 -- Architecture Tightening (AC4: OOM-safe batched reads)
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet
from sqlalchemy import Engine, text

from synth_engine.shared.db import get_engine
from synth_engine.shared.security.ale import get_fernet
from synth_engine.shared.task_queue import huey

_logger = logging.getLogger(__name__)

# Default number of rows fetched per batch during key rotation.
# 1000 rows x ~200 bytes/ciphertext ~ 200 KB peak memory per column.
_DEFAULT_ROTATION_BATCH_SIZE: int = 1000


def find_encrypted_columns(engine: Engine) -> list[tuple[str, str]]:
    """Discover all (table_name, column_name) pairs using EncryptedString.

    Introspects the SQLAlchemy/SQLModel global metadata to find every column
    whose type is an instance of
    :class:`~synth_engine.shared.security.ale.EncryptedString`.

    Args:
        engine: A SQLAlchemy :class:`~sqlalchemy.Engine` (used only to
            obtain the correct dialect-bound metadata; not queried here).

    Returns:
        A list of ``(table_name, column_name)`` tuples for every column
        backed by :class:`~synth_engine.shared.security.ale.EncryptedString`.
        Returns ``[]`` if none are found.
    """
    from sqlmodel import SQLModel

    from synth_engine.shared.security.ale import EncryptedString

    results: list[tuple[str, str]] = []

    # Walk every table registered in the SQLModel/SQLAlchemy MetaData
    for table_name, table in SQLModel.metadata.tables.items():
        for column in table.columns:
            # column.type is the TypeDecorator instance on the column
            if isinstance(column.type, EncryptedString):
                results.append((table_name, column.name))
                _logger.debug(
                    "find_encrypted_columns: found %s.%s",
                    table_name,
                    column.name,
                )

    return results


def re_encrypt_column_values(
    *,
    engine: Engine,
    table_name: str,
    column_name: str,
    old_fernet: Fernet,
    new_fernet: Fernet,
    batch_size: int = _DEFAULT_ROTATION_BATCH_SIZE,
) -> int:
    """Decrypt every non-NULL value with ``old_fernet`` and re-encrypt with ``new_fernet``.

    Processes rows in configurable batches using ``fetchmany(batch_size)`` to
    avoid loading the entire column into memory at once (OOM safety -- AC4 T20.4).
    All batches execute inside a single ``engine.begin()`` transaction. If any
    error occurs mid-loop, the entire operation is rolled back — no partial
    rotation state is possible. The primary key column name is assumed to be
    ``id`` -- this is sufficient for the current data model. If a future table
    uses a different PK name, this function should be extended to accept a
    ``pk_column`` argument.

    NULL values are preserved unchanged (they represent absent PII fields).

    Args:
        engine: SQLAlchemy engine connected to the target database.
        table_name: Name of the table containing the encrypted column.
        column_name: Name of the column to re-encrypt.
        old_fernet: :class:`~cryptography.fernet.Fernet` instance for
            decrypting the existing ciphertext.
        new_fernet: :class:`~cryptography.fernet.Fernet` instance for
            producing the new ciphertext.
        batch_size: Number of rows fetched per iteration. Defaults to
            ``_DEFAULT_ROTATION_BATCH_SIZE`` (1000).

    Returns:
        Number of rows that were re-encrypted (non-NULL rows processed).

    Raises:
        ValueError: If ``batch_size`` is not a positive integer.
    """
    if batch_size <= 0:
        raise ValueError(f"batch_size must be a positive integer, got {batch_size!r}")

    # Use raw SQL text queries to stay generic across dialects (SQLite for unit
    # tests, PostgreSQL for integration tests and production).
    # NOTE: table_name and column_name come from SQLModel metadata introspection,
    # not from user input, so they are not SQL-injection vectors.
    # nosec B608 — inputs are from internal metadata, not user-controlled.
    select_sql = text(f"SELECT id, {column_name} FROM {table_name}")  # noqa: S608  # nosec B608
    update_sql = text(
        f"UPDATE {table_name} SET {column_name} = :new_ct WHERE id = :row_id"  # noqa: S608  # nosec B608
    )

    rows_processed = 0

    with engine.begin() as conn:
        result = conn.execute(select_sql)
        while True:
            batch = result.fetchmany(batch_size)
            if not batch:
                break
            for row in batch:
                row_id, ciphertext = row[0], row[1]
                if ciphertext is None:
                    continue
                # Decrypt with old key, re-encrypt with new key
                plaintext_bytes: bytes = old_fernet.decrypt(ciphertext.encode())
                new_ciphertext: str = new_fernet.encrypt(plaintext_bytes).decode()
                conn.execute(update_sql, {"new_ct": new_ciphertext, "row_id": str(row_id)})
                rows_processed += 1
                _logger.debug(
                    "re_encrypt_column_values: re-encrypted row id=%s in %s.%s",
                    row_id,
                    table_name,
                    column_name,
                )

    _logger.info(
        "re_encrypt_column_values: completed %d rows in %s.%s",
        rows_processed,
        table_name,
        column_name,
    )
    return rows_processed


def rotate_ale_keys(
    *,
    engine: Engine,
    old_fernet: Fernet,
    new_fernet: Fernet,
) -> dict[str, int]:
    """Orchestrate re-encryption of all ALE-encrypted columns.

    Calls :func:`find_encrypted_columns` to discover affected columns, then
    calls :func:`re_encrypt_column_values` for each one.

    Args:
        engine: SQLAlchemy engine connected to the target database.
        old_fernet: Fernet instance backed by the current (pre-rotation) ALE key.
        new_fernet: Fernet instance backed by the new (post-rotation) ALE key.

    Returns:
        A dict mapping ``"<table_name>.<column_name>"`` to the number of rows
        re-encrypted for that column.  Empty dict if no encrypted columns exist.

    Raises:
        Exception: Any exception from :func:`re_encrypt_column_values` is
            logged and re-raised after the failed column is identified.
    """
    columns = find_encrypted_columns(engine)
    _logger.info("rotate_ale_keys: found %d encrypted column(s) to rotate.", len(columns))

    results: dict[str, int] = {}
    for table_name, column_name in columns:
        key = f"{table_name}.{column_name}"
        try:
            count = re_encrypt_column_values(
                engine=engine,
                table_name=table_name,
                column_name=column_name,
                old_fernet=old_fernet,
                new_fernet=new_fernet,
            )
            results[key] = count
        except Exception as exc:  # Broad catch intentional: log and re-raise per-column errors
            _logger.error("rotate_ale_keys: failed to re-encrypt %s: %s", key, exc)
            raise

    _logger.info("rotate_ale_keys: rotation complete. Results: %s", results)
    return results


@huey.task()  # type: ignore[untyped-decorator]  # huey.task() has no type stub; unfixable without upstream py.typed marker
def rotate_ale_keys_task(
    database_url: str,
    wrapped_fernet_key: str,
) -> dict[str, int]:
    """Huey background task: rotate all ALE-encrypted columns.

    This task is enqueued by ``POST /security/keys/rotate``.  It runs
    asynchronously in the Huey worker process.

    Strategy:
    1. Build a SQLAlchemy engine from ``database_url``.
    2. Derive the old Fernet from the current vault KEK (vault must be unsealed
       in the worker process — the same passphrase that unseals the API server
       must also unseal the Huey worker).
    3. Unwrap ``wrapped_fernet_key`` using the current vault Fernet (it was
       wrapped by the API handler to avoid storing a plaintext key in Redis).
    4. Build the new Fernet from the unwrapped key bytes.
    5. Call :func:`rotate_ale_keys` to re-encrypt all columns.

    Args:
        database_url: SQLAlchemy-compatible database URL for the target DB.
        wrapped_fernet_key: KEK-wrapped (Fernet-encrypted) URL-safe base64-encoded
            Fernet key for the new ALE key.  The API handler encrypts the raw key
            with the current vault Fernet before enqueuing; this task unwraps it
            using the same vault Fernet before constructing the new
            :class:`~cryptography.fernet.Fernet` instance.

    Returns:
        Dict mapping ``"<table>.<column>"`` to rows rotated.

    """
    engine = get_engine(database_url)
    old_fernet = get_fernet()  # derives from current vault KEK or ALE_KEY env

    # Unwrap the new key: it was wrapped with the vault Fernet by the API handler
    # to prevent the raw key from appearing in the Redis broker payload.
    unwrapped_key: bytes = old_fernet.decrypt(wrapped_fernet_key.encode())
    new_fernet = Fernet(unwrapped_key)

    try:
        return rotate_ale_keys(
            engine=engine,
            old_fernet=old_fernet,
            new_fernet=new_fernet,
        )
    finally:
        engine.dispose()
