"""Fix epsilon column types: Float8 -> NUMERIC(20, 10).

Revision ID: 003
Revises: 002
Create Date: 2026-03-16

Drains the migration debt documented in ``ledger.py`` (lines 28-49 before
this fix) and described in the backlog task P16-T16.1.

Background
----------
Migration 001 created the ``privacy_ledger`` and ``privacy_transaction`` tables
with epsilon columns typed as ``sa.Float()`` (FLOAT8 / DOUBLE PRECISION on
PostgreSQL).  In Phase 8 (ADV-050) the ORM models were updated to use
``Numeric(precision=20, scale=10)`` to prevent floating-point accumulation
drift.  This migration brings the DDL in line with the ORM definition.

Affected columns
----------------
- ``privacy_ledger.total_allocated_epsilon``:  Float8 → NUMERIC(20, 10)
- ``privacy_ledger.total_spent_epsilon``:      Float8 → NUMERIC(20, 10)
- ``privacy_transaction.epsilon_spent``:       Float8 → NUMERIC(20, 10)

PostgreSQL supports casting DOUBLE PRECISION to NUMERIC without data loss:
any value representable as a 64-bit IEEE float can be stored exactly in
NUMERIC(20, 10) as long as it is within the representable range.  The
epsilon values stored in production are cumulative sums of small positive
decimals (ε ∈ (0, budget]); they will never exceed NUMERIC(20, 10)'s
capacity of ±10^10 with 10 fractional digits.

SQLite compatibility
--------------------
SQLite stores NUMERIC columns as TEXT with affinity rules.  The ALTER
statements in this migration use ``existing_type=sa.Float()`` so that the
Alembic autogenerate diff system records the original type correctly.
In SQLite-based tests the ALTER is effectively a no-op (SQLite ignores
column type changes in ALTER TABLE) — correctness is guaranteed by the
ORM layer, which always writes ``Decimal`` values.

Manual migration rationale
--------------------------
We use a manual migration rather than ``alembic revision --autogenerate``
because autogenerate requires a live database connection.  In an air-gapped
or CI environment this may not be available.

ADR reference: ADR-0030 — Float to NUMERIC precision for epsilon columns.

CONSTITUTION Priority 4: Correctness — Float8 / NUMERIC mismatch is a P0
correctness risk (ADV-050, P16-T16.1).
Task: P16-T16.1 — Alembic Migration 003: Epsilon Column Precision Fix
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Revision identifiers used by Alembic.
revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | None = None
depends_on: str | None = None

# Target type for epsilon columns: exact decimal arithmetic (ADV-050, ADR-0030).
_NUMERIC_20_10 = sa.Numeric(precision=20, scale=10)


def upgrade() -> None:
    """ALTER epsilon columns from FLOAT8 to NUMERIC(20, 10).

    Applies to:
    - ``privacy_ledger.total_allocated_epsilon``
    - ``privacy_ledger.total_spent_epsilon``
    - ``privacy_transaction.epsilon_spent``

    PostgreSQL executes this as::

        ALTER TABLE privacy_ledger
            ALTER COLUMN total_allocated_epsilon TYPE NUMERIC(20, 10),
            ALTER COLUMN total_spent_epsilon TYPE NUMERIC(20, 10);
        ALTER TABLE privacy_transaction
            ALTER COLUMN epsilon_spent TYPE NUMERIC(20, 10);
    """
    # privacy_ledger: two epsilon budget columns
    op.alter_column(
        "privacy_ledger",
        "total_allocated_epsilon",
        existing_type=sa.Float(),
        type_=_NUMERIC_20_10,
        nullable=False,
    )
    op.alter_column(
        "privacy_ledger",
        "total_spent_epsilon",
        existing_type=sa.Float(),
        type_=_NUMERIC_20_10,
        nullable=False,
    )

    # privacy_transaction: per-transaction epsilon amount
    op.alter_column(
        "privacy_transaction",
        "epsilon_spent",
        existing_type=sa.Float(),
        type_=_NUMERIC_20_10,
        nullable=False,
    )


def downgrade() -> None:
    """Revert epsilon columns from NUMERIC(20, 10) back to Float (FLOAT8).

    Applies to:
    - ``privacy_transaction.epsilon_spent``
    - ``privacy_ledger.total_spent_epsilon``
    - ``privacy_ledger.total_allocated_epsilon``

    Reverts in reverse table order to mirror the upgrade.

    PostgreSQL executes this as::

        ALTER TABLE privacy_transaction
            ALTER COLUMN epsilon_spent TYPE DOUBLE PRECISION;
        ALTER TABLE privacy_ledger
            ALTER COLUMN total_spent_epsilon TYPE DOUBLE PRECISION,
            ALTER COLUMN total_allocated_epsilon TYPE DOUBLE PRECISION;
    """
    op.alter_column(
        "privacy_transaction",
        "epsilon_spent",
        existing_type=_NUMERIC_20_10,
        type_=sa.Float(),
        nullable=False,
    )
    op.alter_column(
        "privacy_ledger",
        "total_spent_epsilon",
        existing_type=_NUMERIC_20_10,
        type_=sa.Float(),
        nullable=False,
    )
    op.alter_column(
        "privacy_ledger",
        "total_allocated_epsilon",
        existing_type=_NUMERIC_20_10,
        type_=sa.Float(),
        nullable=False,
    )
