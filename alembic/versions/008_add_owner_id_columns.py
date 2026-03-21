"""Add owner_id columns to synthesis_job and connection tables.

Revision ID: 008
Revises: 007
Create Date: 2026-03-20

Adds ``owner_id`` VARCHAR column to both ``synthesis_job`` and ``connection``
tables for IDOR (Insecure Direct Object Reference) protection (T39.2).

Background
----------
Phase 39 task T39.2 wires authorization into all resource endpoints.
Every resource record is stamped with the JWT ``sub`` claim of the operator
who created it.  All ``GET``, ``POST``, and ``DELETE`` resource endpoints
filter by ``owner_id`` to prevent horizontal privilege escalation.

Migration strategy
------------------
``owner_id`` defaults to an empty string ``''`` on all existing rows.
This matches the Python-level ``Field(default="")`` in the ORM models and
preserves backward compatibility for single-operator deployments where JWT
is not yet configured (pass-through mode).

In pass-through mode (JWT_SECRET_KEY is empty), ``get_current_operator()``
returns ``""`` and all pre-T39.2 rows with ``owner_id=""`` remain accessible
without any data migration.

For multi-operator deployments that upgrade from a pre-T39.2 release,
existing rows will remain accessible only by operators whose ``sub`` claim
is ``""``.  A backfill to a known operator sub is recommended post-upgrade.

Manual migration rationale
--------------------------
We use explicit ``op.add_column`` rather than ``alembic revision
--autogenerate`` because autogenerate requires a live database connection,
which may not be available in an air-gapped or CI environment.

SQLite compatibility
--------------------
SQLite supports ``ALTER TABLE … ADD COLUMN`` with a constant
``server_default``.  The ``server_default=""`` is a constant literal so
SQLite handles it correctly.

Index rationale (ADR-0040)
--------------------------
All ``owner_id`` columns carry an index because every resource query filters
by this column (``WHERE owner_id = ?``).  Without an index, a full table
scan is required for every authenticated list or single-item fetch, which
degrades linearly with table size in a multi-operator deployment.

CONSTITUTION Priority 0: Security — IDOR prevention
CONSTITUTION Priority 4: Correctness — schema must match ORM definition.
Task: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Revision identifiers used by Alembic.
revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add owner_id column and index to synthesis_job and connection tables.

    Both columns are appended with a server default of ``''`` (empty string)
    so existing rows are not left with NULL and remain accessible in
    pass-through mode (JWT_SECRET_KEY not configured).

    Indexes are created explicitly to match the ``index=True`` ORM field
    declaration and to ensure efficient per-operator filtering queries.
    """
    op.add_column(
        "synthesis_job",
        sa.Column(
            "owner_id",
            sa.String(),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "connection",
        sa.Column(
            "owner_id",
            sa.String(),
            nullable=False,
            server_default="",
        ),
    )
    op.create_index("ix_synthesis_job_owner_id", "synthesis_job", ["owner_id"])
    op.create_index("ix_connection_owner_id", "connection", ["owner_id"])


def downgrade() -> None:
    """Remove owner_id column and index from synthesis_job and connection tables.

    Indexes are dropped before columns in reverse order to mirror the upgrade.
    """
    op.drop_index("ix_connection_owner_id", table_name="connection")
    op.drop_index("ix_synthesis_job_owner_id", table_name="synthesis_job")
    op.drop_column("connection", "owner_id")
    op.drop_column("synthesis_job", "owner_id")
