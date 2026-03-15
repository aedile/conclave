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
- ``last_updated`` carries timezone-aware UTC datetimes.
- Both models extend ``SQLModel`` directly (not ``BaseModel``) because
  ``BaseModel`` provides UUID PKs — these tables require integer PKs.
  Both patterns share the same ``SQLModel.metadata`` registry so Alembic
  discovers them automatically.

Import boundaries:
  Must NOT import from any other module in ``modules/``, from
  ``bootstrapper/``, or from application-layer code.  Only ``shared/db.py``
  imports are permitted.

CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: P4-T4.4 — Privacy Accountant
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


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
        total_spent_epsilon: Running total of epsilon spent by all synthesis
            jobs that have successfully completed.  Incremented atomically
            by :func:`~synth_engine.modules.privacy.accountant.spend_budget`.
        last_updated: UTC timestamp of the most recent update to this row.

    Example::

        ledger = PrivacyLedger(total_allocated_epsilon=10.0)
        # default: total_spent_epsilon=0.0, id=None (assigned by DB on insert)
    """

    __tablename__ = "privacy_ledger"

    id: int | None = Field(default=None, primary_key=True)
    total_allocated_epsilon: float = Field(default=0.0)
    total_spent_epsilon: float = Field(default=0.0)
    last_updated: datetime = Field(default_factory=_utcnow)


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
        timestamp: UTC timestamp when this transaction was committed.
        note: Optional human-readable annotation (e.g. run label, operator
            comment).  May be ``None``.

    Example::

        tx = PrivacyTransaction(
            ledger_id=1,
            job_id=42,
            epsilon_spent=0.5,
            note="test run 2026-03-15",
        )
    """

    __tablename__ = "privacy_transaction"

    id: int | None = Field(default=None, primary_key=True)
    ledger_id: int = Field(index=True)
    job_id: int = Field(index=True)
    epsilon_spent: float
    timestamp: datetime = Field(default_factory=_utcnow)
    note: str | None = Field(default=None)
