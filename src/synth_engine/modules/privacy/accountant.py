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
   :exc:`~synth_engine.shared.exceptions.BudgetExhaustionError` if
   ``total_spent + amount > total_allocated``.
5. For ``spend_budget``: deduct ``amount``, write a :class:`PrivacyTransaction`
   record, and let the transaction context manager commit.
6. For ``reset_budget``: reset ``total_spent_epsilon`` to ``Decimal("0.0")``,
   optionally update ``total_allocated_epsilon``, and commit.

The ``async with session.begin()`` pattern ensures rollback occurs automatically
when an exception propagates out of the block.

Decimal arithmetic (ADV-050)
-----------------------------
The ``amount`` parameter accepts ``float`` for API ergonomics but is converted
to :class:`decimal.Decimal` immediately on entry using ``Decimal(str(amount))``.
Mixed-type arithmetic (``Decimal + float``) raises ``TypeError`` in Python;
the conversion at the function boundary prevents this error.

Single-operator model (T66.6)
------------------------------
The privacy ledger currently assumes a single operator.  Queries do not filter
by ``owner_id`` — they match the ledger row by primary key only.
See ADR-0062 for the documented assumption and the migration path.

Cross-org budget validation (RF1 — Fix Round 2)
------------------------------------------------
``spend_budget()`` now validates that the calling job's ``org_id`` matches the
ledger's ``org_id`` before deducting epsilon.  This prevents cross-org budget
violations where a job from Org B could silently deplete Org A's privacy budget.

The check fires inside the transaction, after fetching the ledger under lock,
so the validation is atomic with the spend.  A mismatch raises
:exc:`~synth_engine.shared.exceptions.BudgetExhaustionError` — treating it
as a budget failure rather than an auth error keeps the fail-closed guarantee
consistent with budget exhaustion handling (T50.1 / ADR-0050).

The check is skipped when either ``org_id`` (the caller) or ``ledger.org_id``
is empty — this preserves backward compatibility with pre-P79 single-operator
ledgers that carry an empty ``org_id``.

Import boundaries:
  Must NOT import from any other module in ``modules/``, from
  ``bootstrapper/``, or from application-layer code.

CONSTITUTION Priority 0: Security — no PII, no credential leaks
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
"""

from __future__ import annotations

import logging
from decimal import Decimal

from prometheus_client import Counter
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from synth_engine.modules.privacy.ledger import PrivacyLedger, PrivacyTransaction
from synth_engine.shared.exceptions import BudgetExhaustionError, LedgerNotFoundError

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# T25.1 — Custom Prometheus business metric: epsilon_spent_total Counter.
# Cardinality note: labels are (job_id, dataset_id, org_id). Cardinality is
# bounded by the number of synthesis jobs x ledgers x orgs.  In single-tenant
# deployments org_id is always the default UUID so cardinality is effectively
# (job, ledger).  In multi-tenant deployments cardinality grows by a factor
# of the number of active orgs — acceptable for bounded org counts. (P79-F5)
# ---------------------------------------------------------------------------
EPSILON_SPENT_TOTAL: Counter = Counter(
    "epsilon_spent_total",
    "Total epsilon budget deducted, counted per successful spend_budget() call.",
    labelnames=["job_id", "dataset_id", "org_id"],
)


# ---------------------------------------------------------------------------
# Shared async helper
# ---------------------------------------------------------------------------


async def _fetch_ledger_or_raise(
    session: AsyncSession, ledger_id: int, caller: str
) -> PrivacyLedger:
    """Fetch a PrivacyLedger row with ``SELECT ... FOR UPDATE`` or raise.

    Must be called within an active ``session.begin()`` transaction block.

    Args:
        session: Open AsyncSession (inside an active transaction).
        ledger_id: Primary key of the PrivacyLedger row.
        caller: Caller name for the warning message (e.g. "spend_budget").

    Returns:
        The locked PrivacyLedger row.

    Raises:
        LedgerNotFoundError: If no row exists for ``ledger_id``.
    """
    stmt = (
        select(PrivacyLedger)
        .where(PrivacyLedger.id == ledger_id)  # type: ignore[arg-type]
        .with_for_update()
    )
    result = await session.execute(stmt)
    try:
        return result.scalar_one()
    except NoResultFound:
        _logger.warning(
            "%s: ledger_id=%d not found — no PrivacyLedger row exists", caller, ledger_id
        )
        raise LedgerNotFoundError(ledger_id=ledger_id) from None


async def _deduct_epsilon_in_transaction(
    session: AsyncSession,
    ledger: PrivacyLedger,
    decimal_amount: Decimal,
    ledger_id: int,
    job_id: int,
    note: str | None,
) -> None:
    """Deduct epsilon and record the transaction row (within an active transaction).

    Args:
        session: Open AsyncSession (inside an active transaction).
        ledger: The locked PrivacyLedger row.
        decimal_amount: Validated positive Decimal epsilon to deduct.
        ledger_id: Primary key (for transaction record).
        job_id: Synthesis job PK (for transaction record).
        note: Optional human-readable annotation for the transaction record.

    Raises:
        BudgetExhaustionError: If ``total_spent + decimal_amount > total_allocated``.
    """
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
    session.add(
        PrivacyTransaction(
            ledger_id=ledger_id,
            job_id=job_id,
            epsilon_spent=decimal_amount,
            note=note,
        )
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def spend_budget(
    *,
    amount: float | Decimal,
    job_id: int,
    ledger_id: int,
    session: AsyncSession,
    note: str | None = None,
    org_id: str = "",
) -> None:
    """Atomically deduct epsilon from the global privacy budget.

    Opens an explicit transaction, acquires ``SELECT ... FOR UPDATE`` on the
    :class:`PrivacyLedger` row, checks budget, deducts epsilon, and writes a
    :class:`PrivacyTransaction` record — all atomically.

    Cross-org validation (RF1 — Fix Round 2): If both ``org_id`` (the caller's
    org) and ``ledger.org_id`` are non-empty, they must match.  A mismatch
    raises :exc:`BudgetExhaustionError` to prevent cross-org budget depletion.
    The check is skipped when either value is empty, preserving backward
    compatibility with pre-P79 single-operator ledgers.

    Args:
        amount: The epsilon to deduct.  Must be positive.
        job_id: Synthesis job PK — stored in the PrivacyTransaction record.
        ledger_id: Primary key of the PrivacyLedger row to debit.
        session: Fresh AsyncSession per call (not shared across concurrency).
        note: Optional human-readable annotation for the transaction record.
        org_id: Organization UUID of the requesting job (P79-F5).
            Used both for cross-org validation (RF1) and as the Prometheus
            ``EPSILON_SPENT_TOTAL`` label for per-org spend dashboards.

    Returns:
        None on success.

    Raises:
        ValueError: If ``amount`` is not positive.
        LedgerNotFoundError: If no PrivacyLedger row exists for ``ledger_id``.
        BudgetExhaustionError: If ``total_spent + amount > total_allocated``,
            or if ``org_id`` does not match ``ledger.org_id`` when both are
            non-empty (cross-org budget violation).
    """  # noqa: DOC503
    decimal_amount: Decimal = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    if decimal_amount <= 0:
        raise ValueError(f"amount must be positive, got {amount!r}")

    async with session.begin():
        ledger = await _fetch_ledger_or_raise(session, ledger_id, "spend_budget")

        # RF1 — Cross-org budget guard: validate org_id matches ledger.org_id.
        # The check is skipped when either side is empty to preserve backward
        # compatibility with pre-P79 single-operator ledgers (ledger.org_id="").
        if org_id and ledger.org_id and ledger.org_id != org_id:
            _logger.warning(
                "Cross-org budget violation: job org_id=%r does not match "
                "ledger org_id=%r (ledger_id=%d, job_id=%d) — refusing spend.",
                org_id,
                ledger.org_id,
                ledger_id,
                job_id,
            )
            raise BudgetExhaustionError(
                requested_epsilon=decimal_amount,
                total_spent=ledger.total_spent_epsilon,
                total_allocated=ledger.total_allocated_epsilon,
            )

        await _deduct_epsilon_in_transaction(
            session, ledger, decimal_amount, ledger_id, job_id, note
        )

    # T57.4: Epsilon values logged at DEBUG (privacy-sensitive operational data).
    _logger.debug(
        "Epsilon allocated: ledger_id=%d, job_id=%d, amount=%s, total_spent=%s, remaining=%s",
        ledger_id,
        job_id,
        decimal_amount,
        ledger.total_spent_epsilon,
        ledger.total_allocated_epsilon - ledger.total_spent_epsilon,
    )
    # T25.1: Increment only after confirmed successful commit.
    EPSILON_SPENT_TOTAL.labels(job_id=str(job_id), dataset_id=str(ledger_id), org_id=org_id).inc()


async def reset_budget(
    *,
    ledger_id: int,
    session: AsyncSession,
    new_allocated_epsilon: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """Atomically reset the privacy budget spent counter.

    Opens an explicit transaction, acquires ``SELECT ... FOR UPDATE`` on the
    :class:`PrivacyLedger` row, resets ``total_spent_epsilon`` to zero, and
    optionally updates ``total_allocated_epsilon``.

    This function does NOT emit audit events — that responsibility belongs to
    the router layer.

    Args:
        ledger_id: Primary key of the PrivacyLedger row to reset.
        session: Fresh AsyncSession per call (not shared across concurrency).
        new_allocated_epsilon: Optional new total epsilon allocation ceiling.
            When ``None``, the existing allocation is preserved.  Must be
            positive if provided.

    Returns:
        A 2-tuple ``(allocated, spent)`` reflecting the post-reset ledger state,
        where ``spent`` is always ``Decimal("0.0")``.

    Raises:
        ValueError: If ``new_allocated_epsilon`` is provided and is not positive.
        LedgerNotFoundError: If no PrivacyLedger row exists for ``ledger_id``.
    """  # noqa: DOC503
    if new_allocated_epsilon is not None and new_allocated_epsilon <= 0:
        raise ValueError(f"new_allocated_epsilon must be positive, got {new_allocated_epsilon!r}")

    async with session.begin():
        ledger = await _fetch_ledger_or_raise(session, ledger_id, "reset_budget")
        ledger.total_spent_epsilon = Decimal("0.0")
        if new_allocated_epsilon is not None:
            ledger.total_allocated_epsilon = new_allocated_epsilon

    # T57.4: Epsilon values logged at DEBUG (privacy-sensitive operational data).
    _logger.debug(
        "Budget reset: ledger_id=%d, allocated=%s, spent reset to 0",
        ledger_id,
        ledger.total_allocated_epsilon,
    )
    return ledger.total_allocated_epsilon, ledger.total_spent_epsilon
