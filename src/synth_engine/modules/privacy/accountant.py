"""Global epsilon budget accountant — pessimistic locking spend_budget() and reset_budget().

Provides two async functions:

- :func:`spend_budget`: Atomically deducts epsilon from the global
  :class:`~synth_engine.modules.privacy.ledger.PrivacyLedger` using
  ``SELECT ... FOR UPDATE`` to prevent concurrent synthesis jobs from
  overrunning the privacy budget.

- :func:`reset_budget`: Atomically resets ``total_spent_epsilon`` to zero
  (and optionally updates ``total_allocated_epsilon``) using
  ``SELECT ... FOR UPDATE`` to prevent races with concurrent
  ``spend_budget()`` calls.

Locking protocol
----------------
1. Begin an explicit transaction (via ``async with session.begin()``).
2. Acquire a ``SELECT ... FOR UPDATE`` lock on the target ``PrivacyLedger`` row.
3. Read ``total_spent_epsilon`` and ``total_allocated_epsilon`` under the lock.
4. For ``spend_budget``: raise
   :exc:`~synth_engine.modules.privacy.dp_engine.BudgetExhaustionError` if
   ``total_spent + amount > total_allocated``.
   The transaction context manager rolls back automatically on exception,
   releasing the lock.
5. For ``spend_budget``: deduct ``amount``, write a :class:`PrivacyTransaction`
   record, and let the transaction context manager commit — releasing the lock.
6. For ``reset_budget``: reset ``total_spent_epsilon`` to ``Decimal("0.0")``,
   optionally update ``total_allocated_epsilon``, and commit.

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
Task: P22-T22.4 — Budget Management API (reset_budget)
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


async def reset_budget(
    *,
    ledger_id: int,
    session: AsyncSession,
    new_allocated_epsilon: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """Atomically reset the privacy budget spent counter.

    Opens an explicit transaction via ``async with session.begin()``.  Within
    the transaction, acquires a ``SELECT ... FOR UPDATE`` pessimistic lock on
    the :class:`~synth_engine.modules.privacy.ledger.PrivacyLedger` row
    identified by ``ledger_id``.

    Under the lock, sets ``total_spent_epsilon`` to ``Decimal("0.0")`` and,
    if ``new_allocated_epsilon`` is provided, updates ``total_allocated_epsilon``
    to that value.  The lock is released on commit.

    This function does NOT emit audit events — that responsibility belongs to
    the router layer.

    Args:
        ledger_id: Primary key of the :class:`PrivacyLedger` row to reset.
        session: An open :class:`~sqlalchemy.ext.asyncio.AsyncSession`.
            The caller is responsible for providing a fresh session per call;
            sharing a session across concurrent calls is not supported.
        new_allocated_epsilon: Optional new total epsilon allocation ceiling.
            When provided, ``total_allocated_epsilon`` is updated to this value.
            When ``None``, the existing allocation is preserved.  Must be
            positive if provided.

    Returns:
        A 2-tuple ``(allocated, spent)`` reflecting the post-reset ledger
        state, where ``spent`` is always ``Decimal("0.0")``.

    Raises:
        ValueError: If ``new_allocated_epsilon`` is provided and is not
            strictly positive.
        sqlalchemy.exc.NoResultFound: If no ``PrivacyLedger`` row exists for
            the given ``ledger_id``.

    Example::

        async with get_async_session(engine) as session:
            allocated, spent = await reset_budget(
                ledger_id=1,
                session=session,
                new_allocated_epsilon=Decimal("20.0"),
            )
    """
    if new_allocated_epsilon is not None and new_allocated_epsilon <= 0:
        raise ValueError(f"new_allocated_epsilon must be positive, got {new_allocated_epsilon!r}")

    async with session.begin():
        # Acquire pessimistic lock — same pattern as spend_budget() to prevent
        # races between concurrent refresh and spend operations.
        stmt = (
            select(PrivacyLedger)
            .where(PrivacyLedger.id == ledger_id)  # type: ignore[arg-type]
            .with_for_update()
        )
        result = await session.execute(stmt)
        ledger = result.scalar_one()

        # Apply the reset: spent always returns to zero.
        ledger.total_spent_epsilon = Decimal("0.0")
        if new_allocated_epsilon is not None:
            ledger.total_allocated_epsilon = new_allocated_epsilon

        # session.begin() context manager commits automatically on successful exit.

    _logger.info(
        "Budget reset: ledger_id=%d, allocated=%s, spent reset to 0",
        ledger_id,
        ledger.total_allocated_epsilon,
    )
    return ledger.total_allocated_epsilon, ledger.total_spent_epsilon
