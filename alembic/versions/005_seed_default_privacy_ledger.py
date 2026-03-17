"""Seed a default PrivacyLedger row for the global epsilon budget.

Revision ID: 005
Revises: 004
Create Date: 2026-03-16

Background
----------
``spend_budget()`` in ``modules/privacy/accountant.py`` requires a
``PrivacyLedger`` row to exist before it can be called.  Without a seeded
row the first invocation would raise ``sqlalchemy.exc.NoResultFound``.

This migration inserts the default global ledger row so that the budget
accountant works out-of-the-box without manual operator intervention.

Default values
--------------
- ``total_allocated_epsilon``: Controlled by the ``PRIVACY_BUDGET_EPSILON``
  environment variable (default: ``100.0``).  This allows operators to
  configure the budget at deployment time without a code change.
- ``total_spent_epsilon``: ``0.0`` (no budget spent at install time).

Multi-tenant note
-----------------
The current implementation uses a single global ledger (``id=1``).  When
multi-tenancy is implemented, additional rows will be inserted per tenant
and the ``ledger_id`` parameter of ``spend_budget()`` will be parameterised.
Until then, all synthesis jobs debit ``ledger_id=1``.

Downgrade
---------
Removes the seeded row by deleting ledger rows with
``total_spent_epsilon = 0`` (i.e. default-seeded rows that have never been
used).  If the ledger has been used (``total_spent_epsilon > 0``), the row
is preserved to protect audit integrity.

Migration rationale
-------------------
We use ``op.execute()`` with raw SQL rather than ORM models because Alembic
data migrations must not import application models (the ORM may change after
the migration is written, breaking the migration history).

CONSTITUTION Priority 0: Security — no credentials, no PII
Task: P22-T22.3 — Wire spend_budget() into Synthesis Pipeline
"""

from __future__ import annotations

import os

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Insert a default PrivacyLedger row with the configured epsilon budget.

    Reads ``PRIVACY_BUDGET_EPSILON`` from the environment (default: ``100.0``)
    so operators can configure the budget at deployment time without editing
    migration files.

    Uses ``op.execute()`` with raw SQL — never imports ORM models in migrations
    to avoid coupling migration history to model code changes.

    The INSERT is idempotent in the sense that it only inserts when no rows
    exist (``WHERE NOT EXISTS`` guard), preventing duplicate seeding on
    re-runs of ``alembic upgrade head``.
    """
    epsilon_str = os.environ.get("PRIVACY_BUDGET_EPSILON", "100.0")
    # Validate that the env var is a valid float before embedding in SQL.
    try:
        epsilon_float = float(epsilon_str)
    except ValueError as exc:
        raise ValueError(
            f"PRIVACY_BUDGET_EPSILON must be a valid float; got {epsilon_str!r}"
        ) from exc

    op.execute(
        f"""
        INSERT INTO privacy_ledger (total_allocated_epsilon, total_spent_epsilon)
        SELECT {epsilon_float}, 0.0
        WHERE NOT EXISTS (SELECT 1 FROM privacy_ledger)
        """
    )


def downgrade() -> None:
    """Remove the default-seeded PrivacyLedger row if it has never been used.

    Deletes only rows where ``total_spent_epsilon = 0`` (the seeded default).
    Rows with recorded spend are left intact to preserve the audit chain.
    """
    op.execute("DELETE FROM privacy_ledger WHERE total_spent_epsilon = 0.0")
