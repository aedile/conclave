"""Cross-module Protocol definitions for DI callback contracts.

These Protocols define the structural interfaces for dependency-injected
callbacks that cross module boundaries.  They live in ``shared/`` because they
are consumed by both ``bootstrapper/factories.py`` (producer) and
``modules/synthesizer/tasks.py`` (consumer).

Per CLAUDE.md: "A file that is a pure data-carrier... consumed by two or
more modules belongs in shared/."

Task: P22-T22.3 — Wire spend_budget() into Synthesis Pipeline (F6 review fix)
Task: P26-T26.3 — Protocol Typing + DP-SGD Hardening (complete DPWrapperProtocol)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DPWrapperProtocol(Protocol):
    """Structural interface for a DP training wrapper.

    ``DPTrainingWrapper`` from ``modules/privacy/dp_engine`` satisfies this
    Protocol structurally.  Defining the contract here avoids any import from
    ``modules/privacy/`` while preserving full type-checking coverage across
    the bootstrapper and synthesizer module boundary.

    The three methods below match the exact signatures on ``DPTrainingWrapper``
    (per Known Failure Pattern #1 guard: parameter names verified against the
    implementation, not just types).

    import-linter note:
        ``shared/`` must not import from ``modules/privacy/`` — this Protocol
        is the boundary contract that allows structural duck-typing without
        crossing that boundary.
    """

    def wrap(
        self,
        optimizer: Any,
        model: Any,
        dataloader: Any,
        *,
        max_grad_norm: float,
        noise_multiplier: float,
    ) -> Any:
        """Wrap optimizer with Opacus PrivacyEngine for DP-SGD training.

        Args:
            optimizer: A PyTorch optimizer (e.g. ``torch.optim.Adam``).
            model: The ``nn.Module`` to be trained.
            dataloader: The ``DataLoader`` providing training batches.
            max_grad_norm: Maximum L2 norm for per-sample gradient clipping.
                Must be strictly positive.
            noise_multiplier: Ratio of Gaussian noise std to max_grad_norm.
                Must be strictly positive.

        Returns:
            The DP-wrapped optimizer.
        """
        ...  # pragma: no cover — abstract Protocol stub; body is never executed

    def epsilon_spent(self, *, delta: float) -> float:
        """Return the privacy budget spent so far.

        Args:
            delta: The delta value for (epsilon, delta)-DP.

        Returns:
            The actual epsilon spent.
        """
        ...  # pragma: no cover — abstract Protocol stub; body is never executed

    def check_budget(self, *, allocated_epsilon: float, delta: float) -> None:
        """Raise BudgetExhaustionError if Epsilon spend has reached the allocation.

        Args:
            allocated_epsilon: The maximum Epsilon budget for this training run.
                Must be strictly positive.
            delta: The delta value used to compute the current Epsilon spend.
                Must be strictly positive.

        """
        ...  # pragma: no cover — abstract Protocol stub; body is never executed


class SpendBudgetProtocol(Protocol):
    """Structural interface for the sync spend_budget wrapper callable.

    The concrete implementation is ``build_spend_budget_fn()`` in
    ``bootstrapper/factories.py``, which uses a synchronous SQLAlchemy engine
    (psycopg2 for PostgreSQL, stdlib sqlite3 for SQLite) to deduct epsilon from
    the ``PrivacyLedger`` without requiring a greenlet context.  This avoids the
    ``sqlalchemy.exc.MissingGreenlet`` error raised when asyncpg is called from
    a Huey worker thread (see ADR-0035).

    The async API routes (FastAPI handlers) are unaffected — they continue to use
    the async engine via ``shared/db.py:get_async_session``.

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

        """
        ...  # pragma: no cover — abstract Protocol stub; body is never executed
