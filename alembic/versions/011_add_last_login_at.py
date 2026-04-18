"""Add last_login_at column to the users table.

Revision ID: 011
Revises: 010
Create Date: 2026-04-09

Background
----------
Phase 81 adds OIDC/SSO authentication. When a user logs in via OIDC, the
``last_login_at`` column is updated with the UTC timestamp of the login.
This enables:
- Security auditing: when was a user last seen?
- Inactivity detection: users inactive for N days can be flagged.
- Session management: the OIDC callback updates this on every login.

The column is nullable with no default — existing users simply have a null
value indicating they have not yet logged in via OIDC (or have only used
passphrase auth, which does not update this field).

Downgrade note
--------------
The down migration drops the column. No data is lost (the column only
contains derived audit information, not primary user data).

CONSTITUTION Priority 0: Security — audit trail for authentication events
CONSTITUTION Priority 4: Correctness — schema must match ORM definition
Task: P81-T81.2 — User Provisioning + Migration 011
ADR: ADR-0067 — OIDC Integration (Decision 15)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers — auto-detected by Alembic migration runner.
revision: str = "011"
down_revision: str = "010"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    """Add last_login_at column (nullable datetime) to the users table."""
    op.add_column(
        "users",
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "UTC timestamp of the last successful OIDC login. "
                "Null for passphrase-auth-only users or users not yet logged in via OIDC."
            ),
        ),
    )


def downgrade() -> None:
    """Remove last_login_at column from the users table."""
    op.drop_column("users", "last_login_at")
