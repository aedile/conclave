"""Cross-module Protocol definitions for DI callback contracts.

These Protocols define the structural interfaces for dependency-injected
callbacks that cross module boundaries.  They live in ``shared/`` because they
are consumed by both ``bootstrapper/factories.py`` (producer) and
``modules/synthesizer/tasks.py`` (consumer).

Per CLAUDE.md: "A file that is a pure data-carrier... consumed by two or
more modules belongs in shared/."

Task: P22-T22.3 — Wire spend_budget() into Synthesis Pipeline (F6 review fix)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DPWrapperProtocol(Protocol):
    """Structural interface for a DP training wrapper.

    ``DPTrainingWrapper`` from ``modules/privacy/dp_engine`` satisfies this
    Protocol structurally.  Defining the contract here avoids any import from
    ``modules/privacy/`` while preserving full type-checking coverage across
    the bootstrapper and synthesizer module boundary.
    """

    def epsilon_spent(self, *, delta: float) -> float:
        """Return the privacy budget spent so far.

        Args:
            delta: The delta value for (epsilon, delta)-DP.

        Returns:
            The actual epsilon spent.
        """
        ...  # pragma: no cover — abstract Protocol stub; body is never executed


class SpendBudgetProtocol(Protocol):
    """Structural interface for the sync spend_budget wrapper callable.

    The concrete implementation is ``build_spend_budget_fn()`` in
    ``bootstrapper/factories.py``, which wraps the async
    ``modules/privacy/accountant.spend_budget()`` via ``asyncio.run()``.

    Defining the protocol in ``shared/`` allows both ``bootstrapper/factories.py``
    (producer) and ``modules/synthesizer/tasks.py`` (consumer) to reference
    the same structural contract without creating a cross-module import
    violation.
    """

    def __call__(
        self,
        *,
        amount: float,
        job_id: int,
        ledger_id: int,
        note: str | None = None,
    ) -> None:
        """Deduct epsilon from the privacy budget ledger synchronously.

        Args:
            amount: Epsilon to deduct.  Must be positive.
            job_id: Synthesis job identifier written to the audit trail.
            ledger_id: Primary key of the PrivacyLedger row to debit.
            note: Optional human-readable annotation for the transaction.

        Raises:
            BudgetExhaustionError: (from modules/privacy) if exhausted.
        """
        ...  # pragma: no cover — abstract Protocol stub; body is never executed
