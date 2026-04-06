"""SQLModel table definitions for the global Privacy Accountant ledger.

Two tables are defined:

- :class:`PrivacyLedger`: A single-row (or per-tenant row) budget tracker
  recording the total allocated epsilon and the running total spent epsilon.
  Pessimistic locking via ``SELECT ... FOR UPDATE`` in
  :func:`~synth_engine.modules.privacy.accountant.spend_budget` ensures
  concurrent synthesis jobs cannot overdraw the budget.

- :class:`PrivacyTransaction`: An immutable audit trail recording each
  individual epsilon expenditure.  One row is written per successful
  ``spend_budget()`` call.

Design notes
------------
- Integer primary keys are used (not UUID) because ``SELECT FOR UPDATE``
  on a single known row-ID is the simplest and most performant lock target.
- ``last_updated`` and ``timestamp`` carry timezone-aware UTC datetimes stored
  as ``TIMESTAMP WITH TIME ZONE`` (TIMESTAMPTZ) in PostgreSQL.  Using
  ``DateTime(timezone=True)`` ensures the column accepts timezone-aware
  datetimes from Python's ``datetime.now(UTC)``.  SQLite stores these as text
  and ignores the timezone parameter — both dialects work correctly.
- Both models extend ``SQLModel`` directly (not ``BaseModel``) because
  ``BaseModel`` provides UUID PKs — these tables require integer PKs.
  Both patterns share the same ``SQLModel.metadata`` registry so Alembic
  discovers them automatically.
- Epsilon columns use ``Numeric(precision=20, scale=10)`` rather than
  ``Float`` to prevent floating-point accumulation drift over long-running
  epsilon accounting (ADV-050).  This ensures decimal-exact arithmetic at
  the database layer.  Python callers may pass ``float`` or ``Decimal``
  values; the database driver converts to exact decimal storage.

  Migration note (resolved — migration 003):
      Migration 001 originally created the epsilon columns as ``FLOAT8``
      (``DOUBLE PRECISION``).  Migration 003
      (``003_fix_epsilon_column_precision.py``) ALTERs those columns to
      ``NUMERIC(20, 10)`` to match this ORM definition, eliminating the
      DDL/ORM mismatch introduced in Phase 8 (ADV-050, P16-T16.1).
      See ``docs/adr/ADR-0030-float-to-numeric-epsilon-precision.md`` for
      the full decision record.

Import boundaries:
  Must NOT import from any other module in ``modules/``, from
  ``bootstrapper/``, or from application-layer code.  Only ``shared/db.py``
  imports are permitted.

CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: P4-T4.4 — Privacy Accountant
Task: P8-T8.3 — Data Model & Architecture Cleanup (ADV-050)
Task: P16-T16.1 — Alembic Migration 003: Epsilon Column Precision Fix
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Column
from sqlalchemy import DateTime as SADateTime
from sqlalchemy import Numeric as SANumeric
from sqlmodel import Field, SQLModel

# ---------------------------------------------------------------------------
# Shared epsilon column type — Numeric(20, 10) for all epsilon storage.
# precision=20: supports values up to 10^10 (more than any realistic budget).
# scale=10:     preserves 10 decimal digits of fractional precision.
# ---------------------------------------------------------------------------
_EPSILON_NUMERIC = SANumeric(precision=20, scale=10)

# ---------------------------------------------------------------------------
# Timezone-aware timestamp column type.
# timezone=True → TIMESTAMP WITH TIME ZONE (TIMESTAMPTZ) on PostgreSQL.
# asyncpg rejects timezone-aware datetimes for TIMESTAMP WITHOUT TIME ZONE;
# this column type accepts them correctly.
# ---------------------------------------------------------------------------
_TIMESTAMPTZ = SADateTime(timezone=True)


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime.

    Returns:
        Current UTC time as a timezone-aware :class:`datetime`.
    """
    return datetime.now(UTC)


class PrivacyLedger(SQLModel, table=True):
    """Global epsilon budget ledger row.

    Tracks the total allocated epsilon for all synthesis jobs in this
    deployment, and the running total actually spent.  The difference
    (``total_allocated_epsilon - total_spent_epsilon``) is the remaining
    budget available for new synthesis jobs.

    Locking:
        Before reading or modifying this row, callers MUST issue a
        ``SELECT ... FOR UPDATE`` to acquire a pessimistic lock.  This
        prevents two concurrent transactions from both reading the same
        remaining balance, both deciding there is enough budget, and both
        committing — which would overdraw the budget by the sum of their
        amounts.

    Attributes:
        id: Auto-incrementing integer primary key.
        total_allocated_epsilon: Maximum cumulative epsilon allowed across
            all synthesis jobs.  Set once at deployment time; never decremented.
            Stored as ``NUMERIC(20, 10)`` to prevent floating-point drift
            (ADV-050).
        total_spent_epsilon: Running total of epsilon spent by all synthesis
            jobs that have successfully completed.  Incremented atomically
            by :func:`~synth_engine.modules.privacy.accountant.spend_budget`.
            Stored as ``NUMERIC(20, 10)`` to prevent floating-point drift
            (ADV-050).
        last_updated: UTC timestamp of the most recent update to this row.
            Updated automatically by SQLAlchemy's ``onupdate`` hook on every
            UPDATE statement.  Stored as ``TIMESTAMP WITH TIME ZONE``
            (TIMESTAMPTZ) to accept timezone-aware Python datetimes.

    Example::

        ledger = PrivacyLedger(total_allocated_epsilon=Decimal("10.0"))
        # default: total_spent_epsilon=Decimal("0.0"), id=None
    """

    __tablename__ = "privacy_ledger"

    id: int | None = Field(default=None, primary_key=True)
    total_allocated_epsilon: Decimal = Field(
        default=Decimal("0.0"),
        sa_column=Column(
            "total_allocated_epsilon",
            _EPSILON_NUMERIC,
            nullable=False,
            default=Decimal("0.0"),
        ),
    )
    total_spent_epsilon: Decimal = Field(
        default=Decimal("0.0"),
        sa_column=Column(
            "total_spent_epsilon",
            _EPSILON_NUMERIC,
            nullable=False,
            default=Decimal("0.0"),
        ),
    )
    last_updated: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(
            "last_updated",
            _TIMESTAMPTZ,
            nullable=False,
            default=_utcnow,
            onupdate=_utcnow,
        ),
    )
    #: Tenant organization UUID for multi-tenant isolation (T79.4, ADR-0065).
    #: Defaults to empty string for backward compat with pre-P79 single-operator ledgers.
    org_id: str = Field(default="", index=True)


class PrivacyTransaction(SQLModel, table=True):
    """Immutable audit record of a single epsilon expenditure.

    One row is written per successful call to
    :func:`~synth_engine.modules.privacy.accountant.spend_budget`.
    Failed calls (budget exhausted) produce no row.

    This table provides an audit trail that allows operators to reconstruct
    the full spending history: which synthesis jobs consumed how much epsilon,
    and in what order.

    Attributes:
        id: Auto-incrementing integer primary key.
        ledger_id: Foreign-key reference to the :class:`PrivacyLedger` row
            that was debited.
        job_id: Identifier of the synthesis job that requested the epsilon
            allocation.  Corresponds to a ``SynthesisJob.id``.
        epsilon_spent: The exact epsilon amount allocated by this transaction.
            Stored as ``NUMERIC(20, 10)`` to prevent floating-point drift
            (ADV-050).
        timestamp: UTC timestamp when this transaction was committed.
            Stored as ``TIMESTAMP WITH TIME ZONE`` (TIMESTAMPTZ) to accept
            timezone-aware Python datetimes.
        note: Optional human-readable annotation (e.g. run label, operator
            comment).  May be ``None``.

    Example::

        tx = PrivacyTransaction(
            ledger_id=1,
            job_id=42,
            epsilon_spent=Decimal("0.5"),
            note="test run 2026-03-15",
        )
    """

    __tablename__ = "privacy_transaction"

    id: int | None = Field(default=None, primary_key=True)
    ledger_id: int = Field(index=True)
    job_id: int = Field(index=True)
    epsilon_spent: Decimal = Field(
        sa_column=Column(
            "epsilon_spent",
            _EPSILON_NUMERIC,
            nullable=False,
        ),
    )
    timestamp: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(
            "timestamp",
            _TIMESTAMPTZ,
            nullable=False,
            default=_utcnow,
        ),
    )
    note: str | None = Field(default=None)
    #: Tenant organization UUID as defense-in-depth (T79.4 ATTACK-06, ADR-0065).
    #: Defaults to empty string for backward compat with pre-P79 transactions.
    org_id: str = Field(default="", index=True)
