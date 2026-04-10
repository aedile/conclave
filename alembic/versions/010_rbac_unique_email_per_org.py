"""RBAC: add unique constraint on (org_id, email) in the users table.

Revision ID: 010
Revises: 009
Create Date: 2026-04-09

Background
----------
Phase 80 adds the :class:`~synth_engine.shared.models.user.User` ORM model
with a ``UniqueConstraint("org_id", "email")`` so that a given email address
can appear at most once per organization.

Migration 009 created the ``users`` table without this constraint.  This
migration adds it retroactively.  In a typical fresh deployment the table
will only contain the single default-org seed user, so the constraint add
is a fast metadata-only operation.

On large existing deployments, if duplicate ``(org_id, email)`` rows exist
(e.g., due to a bug in an earlier provisioning script), the ``ALTER TABLE``
will fail.  Resolve duplicates before running this migration.

Downgrade note
--------------
The down migration drops the unique constraint, which is safe — it only
relaxes a restriction.  No data is lost.

CONSTITUTION Priority 0: Security — unique email per org prevents duplicate
identity anchors that could enable session confusion or audit attribution errors.
CONSTITUTION Priority 4: Correctness — schema must match ORM definition
Task: P80-B6 — Add Alembic migration for UniqueConstraint(org_id, email)
"""

from __future__ import annotations

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add unique constraint on (org_id, email) to the users table."""
    op.create_unique_constraint("uq_users_org_id_email", "users", ["org_id", "email"])


def downgrade() -> None:
    """Remove unique constraint on (org_id, email) from the users table."""
    op.drop_constraint("uq_users_org_id_email", "users")
