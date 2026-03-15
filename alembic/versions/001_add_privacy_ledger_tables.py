"""Add privacy_ledger and privacy_transaction tables.

Revision ID: 001
Revises:
Create Date: 2026-03-15

This is the first migration in this project.  It creates two tables required
by the Privacy Accountant (T4.4):

- ``privacy_ledger``: Global epsilon budget tracker.  A single row (or one row
  per tenant) records the total allocated epsilon and total spent epsilon.
  ``SELECT ... FOR UPDATE`` is used on this row by ``spend_budget()`` to prevent
  concurrent overdraw.

- ``privacy_transaction``: Immutable audit log.  One row per successful
  ``spend_budget()`` call, recording which job consumed how much epsilon.

Manual migration rationale:
  We use a manual migration (explicit ``op.create_table``) rather than
  ``alembic revision --autogenerate`` because autogenerate requires a live
  database connection to introspect the current schema.  In an air-gapped or
  CI environment this may not be available.  The explicit DDL is equivalent to
  what autogenerate would produce.

CONSTITUTION Priority 0: Security — no credentials, no PII
Task: P4-T4.4 — Privacy Accountant
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Revision identifiers used by Alembic.
revision: str = "001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create privacy_ledger and privacy_transaction tables.

    privacy_ledger
    --------------
    - ``id``: Serial (auto-increment) integer primary key.
    - ``total_allocated_epsilon``: Maximum cumulative epsilon allowed.
    - ``total_spent_epsilon``: Running total epsilon spent.
    - ``last_updated``: UTC timestamp of the most recent update.

    privacy_transaction
    -------------------
    - ``id``: Serial integer primary key.
    - ``ledger_id``: FK to privacy_ledger.id (indexed).
    - ``job_id``: Synthesis job ID (indexed).
    - ``epsilon_spent``: Epsilon amount for this transaction.
    - ``timestamp``: UTC timestamp when the transaction was committed.
    - ``note``: Optional text annotation.
    """
    op.create_table(
        "privacy_ledger",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("total_allocated_epsilon", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_spent_epsilon", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "privacy_transaction",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("ledger_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("epsilon_spent", sa.Float(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["ledger_id"], ["privacy_ledger.id"], name="fk_transaction_ledger"),
    )

    op.create_index("ix_privacy_transaction_ledger_id", "privacy_transaction", ["ledger_id"])
    op.create_index("ix_privacy_transaction_job_id", "privacy_transaction", ["job_id"])


def downgrade() -> None:
    """Drop privacy_ledger and privacy_transaction tables.

    Drops in reverse dependency order (transaction before ledger to satisfy FK).
    """
    op.drop_index("ix_privacy_transaction_job_id", table_name="privacy_transaction")
    op.drop_index("ix_privacy_transaction_ledger_id", table_name="privacy_transaction")
    op.drop_table("privacy_transaction")
    op.drop_table("privacy_ledger")
