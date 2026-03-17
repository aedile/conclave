"""Add DP parameter columns to synthesis_job table.

Revision ID: 004
Revises: 003
Create Date: 2026-03-16

Adds four columns required for differential-privacy (DP-SGD) synthesis
pipeline wiring (P22-T22.1):

- ``enable_dp``:        BOOLEAN, NOT NULL, server_default TRUE
- ``noise_multiplier``: FLOAT,   NOT NULL, server_default 1.1
- ``max_grad_norm``:    FLOAT,   NOT NULL, server_default 1.0
- ``actual_epsilon``:   FLOAT,   nullable

Background
----------
Phase 22 wires the DP synthesis pipeline end-to-end.  T22.1 adds the
parameter columns to the job table so that the operator can control
differential-privacy settings via ``POST /jobs``.  The defaults are
privacy-maximising (OWASP A04):

- ``enable_dp=TRUE``         — DP is on by default.
- ``noise_multiplier=1.1``   — Calibrated per ADR-0025.
- ``max_grad_norm=1.0``      — Standard gradient clipping bound.
- ``actual_epsilon=NULL``    — Written by the training task (T22.2).

Manual migration rationale
--------------------------
We use explicit ``op.add_column`` rather than ``alembic revision
--autogenerate`` because autogenerate requires a live database connection,
which may not be available in an air-gapped or CI environment.

SQLite compatibility
--------------------
SQLite supports ``ALTER TABLE … ADD COLUMN`` with a ``DEFAULT`` expression
as long as the column is nullable or has a constant server default.  The
``server_default`` values here are all constant literals so SQLite handles
them correctly.  The ``actual_epsilon`` column is nullable (no default
needed).

CONSTITUTION Priority 4: Correctness — schema must match ORM definition.
Task: P22-T22.1 — Job Schema DP Parameters
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Revision identifiers used by Alembic.
revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add enable_dp, noise_multiplier, max_grad_norm, actual_epsilon columns.

    All new columns are appended to the ``synthesis_job`` table with
    server defaults matching the ORM-level Python defaults so that
    existing rows receive sensible values on upgrade.
    """
    op.add_column(
        "synthesis_job",
        sa.Column(
            "enable_dp",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "synthesis_job",
        sa.Column(
            "noise_multiplier",
            sa.Float(),
            nullable=False,
            server_default="1.1",
        ),
    )
    op.add_column(
        "synthesis_job",
        sa.Column(
            "max_grad_norm",
            sa.Float(),
            nullable=False,
            server_default="1.0",
        ),
    )
    op.add_column(
        "synthesis_job",
        sa.Column(
            "actual_epsilon",
            sa.Float(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Remove enable_dp, noise_multiplier, max_grad_norm, actual_epsilon columns.

    Columns are dropped in reverse order to mirror the upgrade.
    """
    op.drop_column("synthesis_job", "actual_epsilon")
    op.drop_column("synthesis_job", "max_grad_norm")
    op.drop_column("synthesis_job", "noise_multiplier")
    op.drop_column("synthesis_job", "enable_dp")
