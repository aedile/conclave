"""Synchronous budget transaction logic for Huey worker context.

Extracted from ``bootstrapper/factories.py`` where it was domain logic
living in the wrong layer (T60.4).  The bootstrapper is responsible for
IoC wiring; domain accounting belongs in ``modules/privacy/``.

``sync_spend_budget()`` implements the same pessimistic-locking protocol
as the async ``spend_budget()`` in :mod:`accountant`:

1. ``SELECT ... FOR UPDATE`` on the ``PrivacyLedger`` row.
2. Budget exhaustion check — raises :exc:`BudgetExhaustionError` if exceeded.
3. Deduct epsilon and write a ``PrivacyTransaction`` audit row.
4. Commit (or rollback on error — delegated to the Session context manager).

The sync path is required for Huey workers which are not spawned in a
greenlet context.  Using a synchronous SQLAlchemy engine (psycopg2 for
PostgreSQL, stdlib sqlite3 for SQLite) avoids ``MissingGreenlet`` errors.
The async API routes are unaffected (P28-F4, ADR-0035).

Boundary invariants
-------------------
This module MUST NOT import from ``synth_engine.bootstrapper``.  The
import-linter contract "Modules must not import from bootstrapper" is
enforced in CI.  All SQLAlchemy imports are deferred inside the function
body to avoid import errors in environments where the database is not
available at startup.

CONSTITUTION Priority 0: Security — budget enforcement, no bypass
Task: T60.4 — Extract domain transaction logic from factories.py
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

_logger = logging.getLogger(__name__)


def sync_spend_budget(
    engine: Engine,
    *,
    amount: float | Decimal,
    job_id: int,
    ledger_id: int,
    note: str | None = None,
) -> None:
    """Deduct epsilon from the PrivacyLedger atomically (sync / Huey context).

    Uses the supplied synchronous SQLAlchemy engine to execute the
    pessimistic-locking budget deduction in a single transaction.  The
    engine must be a synchronous engine (psycopg2 for PostgreSQL, stdlib
    sqlite3 for SQLite) — asyncpg engines will fail with ``MissingGreenlet``.

    All SQLAlchemy and ORM imports are deferred inside this function to
    avoid import errors in environments without a live database.

    Args:
        engine: A synchronous SQLAlchemy engine (NullPool recommended for
            Huey workers — single call per job, no pooling benefit).
        amount: Epsilon to deduct.  Must be strictly positive.
        job_id: Synthesis job identifier written to the audit trail.
        ledger_id: Primary key of the PrivacyLedger row to debit.
        note: Optional human-readable annotation for the transaction row.

    Raises:
        ValueError: If ``amount`` is not strictly positive.
        BudgetExhaustionError: If deducting ``amount`` would exceed the
            allocated budget on the targeted ``PrivacyLedger`` row.
    """
    # Deferred imports — keeps startup fast and avoids import errors in
    # environments where psycopg2 or the ORM models are not installed.
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from synth_engine.modules.privacy.ledger import PrivacyLedger, PrivacyTransaction
    from synth_engine.shared.exceptions import BudgetExhaustionError

    decimal_amount: Decimal = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    if decimal_amount <= 0:
        raise ValueError(f"amount must be positive, got {amount!r}")

    with Session(engine) as session:
        with session.begin():
            # Pessimistic lock — same protocol as the async spend_budget().
            stmt = (
                select(PrivacyLedger)
                .where(PrivacyLedger.id == ledger_id)  # type: ignore[arg-type]
                .with_for_update()
            )
            result = session.execute(stmt)
            ledger = result.scalar_one()

            if ledger.total_spent_epsilon + decimal_amount > ledger.total_allocated_epsilon:
                _logger.warning(
                    "Budget exhausted: ledger_id=%d, requested=%s, spent=%s, allocated=%s",
                    ledger_id,
                    decimal_amount,
                    ledger.total_spent_epsilon,
                    ledger.total_allocated_epsilon,
                )
                raise BudgetExhaustionError(
                    requested_epsilon=decimal_amount,
                    total_spent=ledger.total_spent_epsilon,
                    total_allocated=ledger.total_allocated_epsilon,
                )

            ledger.total_spent_epsilon += decimal_amount
            transaction = PrivacyTransaction(
                ledger_id=ledger_id,
                job_id=job_id,
                epsilon_spent=decimal_amount,
                note=note,
            )
            session.add(transaction)
            # session.begin() context manager commits on clean exit.

    _logger.info(
        "Epsilon allocated (sync): ledger_id=%d, job_id=%d, amount=%s",
        ledger_id,
        job_id,
        decimal_amount,
    )
