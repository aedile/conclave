"""Add num_rows and output_path columns to synthesis_job table.

Revision ID: 006
Revises: 005
Create Date: 2026-03-17

Adds two columns required for the generation step in the Huey task
pipeline (P23-T23.1):

- ``num_rows``:    INTEGER, NOT NULL, server_default 0
- ``output_path``: VARCHAR, nullable

Background
----------
Phase 23 task T23.1 wires the generation step into ``run_synthesis_job``
so that after CTGAN training completes, synthetic data is generated and
persisted as a Parquet file.

- ``num_rows`` controls how many synthetic rows to generate.
  Defaulting server-side to 0 for existing rows (caller must set a
  sensible value when creating new jobs via POST /jobs).
- ``output_path`` records the filesystem path to the generated Parquet
  file.  It is distinct from ``artifact_path`` (which holds the model
  pickle) following Option B from the T23.1 spec: backward-compatible
  separation of concerns.

Manual migration rationale
--------------------------
We use explicit ``op.add_column`` rather than ``alembic revision
--autogenerate`` because autogenerate requires a live database connection,
which may not be available in an air-gapped or CI environment.

SQLite compatibility
--------------------
SQLite supports ``ALTER TABLE … ADD COLUMN`` with a constant
``server_default``.  ``num_rows`` uses ``server_default="0"`` so existing
rows receive a safe value on upgrade.  ``output_path`` is nullable with no
default needed.

CONSTITUTION Priority 4: Correctness — schema must match ORM definition.
Task: P23-T23.1 — Generation Step in Huey Task
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Revision identifiers used by Alembic.
revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add num_rows and output_path columns to synthesis_job.

    Both columns are appended to the ``synthesis_job`` table.
    ``num_rows`` carries a server default of ``0`` so existing rows are
    not left with a NULL in a NOT NULL column.  ``output_path`` is
    nullable and set by the Huey task after generation completes.
    """
    op.add_column(
        "synthesis_job",
        sa.Column(
            "num_rows",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "synthesis_job",
        sa.Column(
            "output_path",
            sa.String(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Remove num_rows and output_path columns from synthesis_job.

    Columns are dropped in reverse order to mirror the upgrade.
    """
    op.drop_column("synthesis_job", "output_path")
    op.drop_column("synthesis_job", "num_rows")
