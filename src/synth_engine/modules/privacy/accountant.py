"""Global epsilon budget accountant — pessimistic locking spend_budget().

Provides :func:`spend_budget`, an async function that atomically deducts
epsilon from the global :class:`~synth_engine.modules.privacy.ledger.PrivacyLedger`
using ``SELECT ... FOR UPDATE`` to prevent concurrent synthesis jobs from
overrunning the privacy budget.

Locking protocol
----------------
1. Begin an explicit transaction (via ``async with session.begin()``).
2. Acquire a ``SELECT ... FOR UPDATE`` lock on the target ``PrivacyLedger`` row.
3. Read ``total_spent_epsilon`` and ``total_allocated_epsilon`` under the lock.
4. If ``total_spent + amount > total_allocated``: raise
   :exc:`~synth_engine.modules.privacy.dp_engine.BudgetExhaustionError`.
   The transaction context manager rolls back automatically on exception,
   releasing the lock.
5. If budget is available: deduct ``amount``, write a :class:`PrivacyTransaction`
   record, and let the transaction context manager commit — releasing the lock.

The ``async with session.begin()`` pattern ensures rollback occurs automatically
when an exception propagates out of the block.  This avoids calling
``await session.rollback()`` explicitly, which can fail in some async drivers
(aiosqlite on ARM64) when called outside an active greenlet context.

The function must be called with a fresh :class:`sqlalchemy.ext.asyncio.AsyncSession`
for each invocation to ensure proper concurrency semantics.  The session must
NOT be shared across concurrent calls.

Decimal arithmetic (ADV-050)
-----------------------------
The ``amount`` parameter accepts ``float`` for API ergonomics but is converted
to :class:`decimal.Decimal` immediately on entry using ``Decimal(str(amount))``.
This preserves decimal precision before any arithmetic against the ledger's
``NUMERIC(20, 10)`` columns.  Callers may also pass a ``Decimal`` directly.
Mixed-type arithmetic (``Decimal + float``) raises ``TypeError`` in Python;
the conversion at the function boundary prevents this error.

Import boundaries:
  Must NOT import from any other module in ``modules/``, from
  ``bootstrapper/``, or from application-layer code.  Only ``shared/`` and
  sibling files within ``modules/privacy/`` are permitted.

CONSTITUTION Priority 0: Security — no PII, no credential leaks
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: P4-T4.4 — Privacy Accountant
Task: P8-T8.3 — Data Model & Architecture Cleanup (ADV-050)
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError
from synth_engine.modules.privacy.ledger import PrivacyLedger, PrivacyTransaction

_logger = logging.getLogger(__name__)


async def spend_budget(
    *,
    amount: float | Decimal,
    job_id: int,
    ledger_id: int,
    session: AsyncSession,
    note: str | None = None,
) -> None:
    """Atomically deduct epsilon from the global privacy budget.

    Opens an explicit transaction via ``async with session.begin()``.  Within
    the transaction, acquires a ``SELECT ... FOR UPDATE`` pessimistic lock on
    the :class:`~synth_engine.modules.privacy.ledger.PrivacyLedger` row
    identified by ``ledger_id``.  Under the lock, checks whether
    ``total_spent + amount <= total_allocated``.

    If sufficient budget exists: deducts the amount, writes a
    :class:`~synth_engine.modules.privacy.ledger.PrivacyTransaction` record,
    and commits — all atomically.  The lock is released on commit.

    If budget is exhausted: raises
    :exc:`~synth_engine.modules.privacy.dp_engine.BudgetExhaustionError`.
    The transaction context manager rolls back automatically, releasing the
    lock without writing any transaction record.

    Args:
        amount: The epsilon to deduct.  Must be positive.  Accepts ``float``
            or :class:`decimal.Decimal`.  ``float`` values are converted to
            ``Decimal`` via ``Decimal(str(amount))`` to avoid mixed-type
            arithmetic errors against the ledger's ``NUMERIC(20, 10)`` columns.
        job_id: Identifier of the synthesis job requesting the allocation.
            Stored in the :class:`PrivacyTransaction` audit record.
        ledger_id: Primary key of the :class:`PrivacyLedger` row to debit.
        session: An open :class:`~sqlalchemy.ext.asyncio.AsyncSession`.
            The caller is responsible for providing a fresh session per call;
            sharing a session across concurrent calls is not supported.
        note: Optional human-readable annotation written to the transaction
            record (e.g. job label, operator comment).  Defaults to ``None``.

    Returns:
        None on success.

    Raises:
        ValueError: If ``amount`` is not positive (zero or negative).
        BudgetExhaustionError: If ``total_spent + amount > total_allocated``.
            The ledger row is left unchanged; no transaction record is written.
        sqlalchemy.exc.NoResultFound: If no ``PrivacyLedger`` row exists for
            the given ``ledger_id``.

    Example::

        async with get_async_session(engine) as session:
            await spend_budget(
                amount=0.5,
                job_id=42,
                ledger_id=1,
                session=session,
            )
    """
    # Normalise to Decimal immediately to prevent mixed-type arithmetic errors
    # when operating against NUMERIC(20, 10) ledger columns (ADV-050).
    decimal_amount: Decimal = amount if isinstance(amount, Decimal) else Decimal(str(amount))

    if decimal_amount <= 0:
        raise ValueError(f"amount must be positive, got {amount!r}")
    async with session.begin():
        # Acquire pessimistic lock — blocks until previous holder commits.
        # SQLModel class-level attribute comparison — instrumented at runtime
        # by SQLAlchemy, not a plain Python bool despite what mypy infers.
        stmt = (
            select(PrivacyLedger)
            .where(PrivacyLedger.id == ledger_id)  # type: ignore[arg-type]
            .with_for_update()
        )
        result = await session.execute(stmt)
        ledger = result.scalar_one()

        if ledger.total_spent_epsilon + decimal_amount > ledger.total_allocated_epsilon:
            _logger.warning(
                "Budget exhausted: ledger_id=%d, requested=%s, spent=%s, allocated=%s",
                ledger_id,
                decimal_amount,
                ledger.total_spent_epsilon,
                ledger.total_allocated_epsilon,
            )
            # Raise here — session.begin() context manager auto-rolls back
            # when BudgetExhaustionError propagates out of the block.
            raise BudgetExhaustionError(
                f"Global DP budget exhausted: requested epsilon={decimal_amount}, "
                f"total_spent={ledger.total_spent_epsilon}, "
                f"total_allocated={ledger.total_allocated_epsilon}. "
                "Synthesis job cannot proceed — budget exhausted."
            )

        # Deduct epsilon and record the transaction — same DB transaction.
        ledger.total_spent_epsilon += decimal_amount
        transaction = PrivacyTransaction(
            ledger_id=ledger_id,
            job_id=job_id,
            epsilon_spent=decimal_amount,
            note=note,
        )
        session.add(transaction)
        # session.begin() context manager commits automatically on successful exit.

    _logger.info(
        "Epsilon allocated: ledger_id=%d, job_id=%d, amount=%s, total_spent=%s, remaining=%s",
        ledger_id,
        job_id,
        decimal_amount,
        ledger.total_spent_epsilon,
        ledger.total_allocated_epsilon - ledger.total_spent_epsilon,
    )
