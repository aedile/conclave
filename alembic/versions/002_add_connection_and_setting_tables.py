"""Add connection and setting tables.

Revision ID: 002
Revises: 001
Create Date: 2026-03-15

Drains ADV-052: No Alembic migration for ``connection`` and ``setting`` tables.

This migration creates two tables required by the Task Orchestration API (T5.1):

- ``connection``: Stores database connection configurations used as ingestion
  sources.  The primary key is a UUID stored as VARCHAR for SQLite/PostgreSQL
  portability.

- ``setting``: Key-value store for application configuration, persisted to the
  database.  The primary key is the setting key string itself.

Manual migration rationale:
  We use a manual migration (explicit ``op.create_table``) rather than
  ``alembic revision --autogenerate`` because autogenerate requires a live
  database connection.  In an air-gapped or CI environment this may not be
  available.  The explicit DDL matches exactly what the SQLModel ORM would
  create via ``SQLModel.metadata.create_all()``.

CONSTITUTION Priority 0: Security — no credentials, no PII
Task: P8-T8.4 — CI Infrastructure (drains ADV-052)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Revision identifiers used by Alembic.
revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create connection and setting tables.

    connection
    ----------
    - ``id``: VARCHAR primary key (UUID v4 as string; SQLite/PostgreSQL
      compatible).
    - ``name``: Human-readable display name (indexed for lookup performance).
    - ``host``: Database hostname or IP address.
    - ``port``: Database port number.
    - ``database``: Database name.
    - ``schema_name``: Schema within the database (default: public).

    setting
    -------
    - ``key``: Setting key (VARCHAR primary key; unique identifier).
    - ``value``: Setting value (VARCHAR).
    """
    op.create_table(
        "connection",
        sa.Column("id", sa.VARCHAR(), nullable=False),
        sa.Column("name", sa.VARCHAR(), nullable=False),
        sa.Column("host", sa.VARCHAR(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database", sa.VARCHAR(), nullable=False),
        sa.Column("schema_name", sa.VARCHAR(), nullable=False, server_default="public"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connection_name", "connection", ["name"])

    op.create_table(
        "setting",
        sa.Column("key", sa.VARCHAR(), nullable=False),
        sa.Column("value", sa.VARCHAR(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    """Drop connection and setting tables."""
    op.drop_table("setting")
    op.drop_index("ix_connection_name", table_name="connection")
    op.drop_table("connection")
