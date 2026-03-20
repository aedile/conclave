"""Encrypt host, database, and schema_name in the connection table.

Revision ID: 007
Revises: 006
Create Date: 2026-03-20

Background
----------
Task T39.4 applies the ALE ``EncryptedString`` TypeDecorator to the
``host``, ``database``, and ``schema_name`` columns of the ``connection``
table.  Any rows that exist before this migration contain plaintext values
that must be re-written as Fernet tokens.

This migration performs an in-place encryption of all existing rows using
the current ALE key (sourced from the vault KEK if unsealed, or from the
``ALE_KEY`` environment variable if the vault is sealed).

Operational notes
-----------------
- The column definitions are ``VARCHAR`` on both SQLite and PostgreSQL.
  The TypeDecorator stores Fernet tokens as UTF-8 strings, so no DDL
  change is required — only a data migration.
- The migration is **safe for empty tables**: ``get_fernet()`` is only
  called when rows actually need to be encrypted.  A fresh database with
  no connection rows can be migrated without any ALE key being present.
- **The ALE key must be available** (vault unsealed or ``ALE_KEY`` set)
  before running ``alembic upgrade head`` against a database that has
  existing plaintext rows.
- ``downgrade()`` decrypts the ciphertext back to plaintext using the
  same key so the migration can be reversed safely.

CONSTITUTION Priority 0: Security — sensitive connection topology must be
    encrypted at rest.
Task: T39.4 — Encrypt Connection Metadata with ALE
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op

# Revision identifiers used by Alembic.
revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | None = None
depends_on: str | None = None

_CONNECTION_TABLE = sa.table(
    "connection",
    sa.column("id", sa.String()),
    sa.column("host", sa.String()),
    sa.column("database", sa.String()),
    sa.column("schema_name", sa.String()),
)


def _get_fernet() -> Any:
    """Return a Fernet instance using the ALE key.

    Delegates to :func:`synth_engine.shared.security.ale.get_fernet` which
    implements the vault-first key selection strategy: vault KEK when
    unsealed, ``ALE_KEY`` env var as fallback.

    Returns:
        A :class:`cryptography.fernet.Fernet` instance ready for use.

    Raises:
        RuntimeError: If the vault is sealed and ``ALE_KEY`` is not set.
    """
    from synth_engine.shared.security.ale import get_fernet

    return get_fernet()


def upgrade() -> None:
    """Encrypt existing plaintext values in host, database, and schema_name.

    Iterates over all rows in the ``connection`` table and re-writes
    ``host``, ``database``, and ``schema_name`` as Fernet ciphertext.
    Rows that are already Fernet tokens (i.e. re-running upgrade) would
    double-encrypt, so this migration must only be applied once.  Alembic's
    revision tracking guarantees single application in normal usage.

    If the table is empty, this function is a no-op and no ALE key is
    required.
    """
    bind = op.get_bind()
    rows = bind.execute(sa.select(_CONNECTION_TABLE)).fetchall()

    if not rows:
        return  # Nothing to encrypt — no ALE key required.

    fernet = _get_fernet()
    for row in rows:
        bind.execute(
            sa.update(_CONNECTION_TABLE)
            .where(_CONNECTION_TABLE.c.id == row.id)
            .values(
                host=fernet.encrypt(row.host.encode()).decode(),
                database=fernet.encrypt(row.database.encode()).decode(),
                schema_name=fernet.encrypt(row.schema_name.encode()).decode(),
            )
        )


def downgrade() -> None:
    """Decrypt Fernet ciphertext back to plaintext in connection table.

    Reverses the upgrade by decrypting ``host``, ``database``, and
    ``schema_name`` using the current ALE key.  The vault / ALE key must
    be the same key that was used during upgrade for decryption to succeed.

    If the table is empty, this function is a no-op and no ALE key is
    required.
    """
    bind = op.get_bind()
    rows = bind.execute(sa.select(_CONNECTION_TABLE)).fetchall()

    if not rows:
        return  # Nothing to decrypt — no ALE key required.

    fernet = _get_fernet()
    for row in rows:
        bind.execute(
            sa.update(_CONNECTION_TABLE)
            .where(_CONNECTION_TABLE.c.id == row.id)
            .values(
                host=fernet.decrypt(row.host.encode()).decode(),
                database=fernet.decrypt(row.database.encode()).decode(),
                schema_name=fernet.decrypt(row.schema_name.encode()).decode(),
            )
        )
