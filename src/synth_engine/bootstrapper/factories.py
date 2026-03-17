"""DI factory functions for synthesis-layer application dependencies.

Houses the lazy factory functions that construct :class:`SynthesisEngine`,
:class:`DPTrainingWrapper`, and the sync ``spend_budget`` wrapper instances.
These factories are called at synthesis-job start time, never at application
startup, so missing GPU or database infrastructure does not prevent the
health check from responding.

The Docker-secrets cluster (``_read_secret``, ``_SECRETS_DIR``,
``_MINIO_ENDPOINT``, ``_EPHEMERAL_BUCKET``, ``MinioStorageBackend``,
``build_ephemeral_storage_client``) lives in ``main.py`` so that existing
test patches against ``synth_engine.bootstrapper.main.*`` continue to work
without modification (AC3 of the bootstrapper-decomposition task).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
    from synth_engine.modules.synthesizer.engine import SynthesisEngine

_logger = logging.getLogger(__name__)


def build_synthesis_engine(epochs: int = 300) -> SynthesisEngine:
    """Build a SynthesisEngine with the given epoch count.

    This factory is called lazily at synthesis job start time, not at
    application startup.  Callers receive a stateless engine instance;
    model artifacts are returned from :meth:`SynthesisEngine.train` and
    must be persisted by the caller.

    Args:
        epochs: Number of CTGAN training epochs.  Defaults to 300 (SDV
            default).  Use a lower value (2-5) for integration-test runs.

    Returns:
        A configured :class:`SynthesisEngine` instance.
    """
    from synth_engine.modules.synthesizer.engine import SynthesisEngine as _SynthesisEngine

    _logger.info("SynthesisEngine initialised (epochs=%d).", epochs)
    return _SynthesisEngine(epochs=epochs)


def build_dp_wrapper(
    max_grad_norm: float = 1.0,
    noise_multiplier: float = 1.1,
) -> DPTrainingWrapper:
    """Build a DPTrainingWrapper configured for DP-SGD training.

    This factory is the sole entry point for constructing a
    :class:`~synth_engine.modules.privacy.dp_engine.DPTrainingWrapper`.
    It is the bootstrapper's responsibility to wire the wrapper into
    ``SynthesisEngine.train(dp_wrapper=...)`` — callers must not instantiate
    ``DPTrainingWrapper`` directly outside of tests.

    The bootstrapper is the only layer that imports from both
    ``modules/privacy/`` and ``modules/synthesizer/`` — this factory is
    therefore the correct and only place for this wiring.

    This factory drains ADV-048.

    Args:
        max_grad_norm: Maximum L2 norm for per-sample gradient clipping.
            Must be strictly positive.  Default: 1.0 (canonical DP-SGD value).
        noise_multiplier: Ratio of Gaussian noise std to max_grad_norm.
            Higher values yield stronger privacy but lower utility.
            Must be strictly positive.  Default: 1.1 (canonical DP-SGD value).

    Returns:
        A configured :class:`DPTrainingWrapper` instance ready to be passed
        to :meth:`SynthesisEngine.train`.

    Raises:
        ValueError: If ``max_grad_norm`` or ``noise_multiplier`` is not
            strictly positive.

    Example::

        wrapper = build_dp_wrapper(max_grad_norm=1.0, noise_multiplier=1.1)
        engine = build_synthesis_engine(epochs=2)
        artifact = engine.train(
            "persons", "/data/persons.parquet", dp_wrapper=wrapper
        )
        epsilon = wrapper.epsilon_spent(delta=1e-5)
    """
    from synth_engine.modules.privacy.dp_engine import (
        DPTrainingWrapper as _DPTrainingWrapper,
    )

    _logger.info(
        "DPTrainingWrapper initialised (max_grad_norm=%.2f, noise_multiplier=%.2f).",
        max_grad_norm,
        noise_multiplier,
    )
    return _DPTrainingWrapper(max_grad_norm=max_grad_norm, noise_multiplier=noise_multiplier)


def build_spend_budget_fn() -> Callable[..., None]:
    """Build a sync callable wrapping async ``spend_budget()`` for Huey context.

    The Huey task runner is synchronous.  ``spend_budget()`` in
    ``modules/privacy/accountant`` is ``async def`` and requires an
    ``AsyncSession``.  This factory returns a sync wrapper that:

    1. Creates a fresh ``AsyncSession`` for each call (required by
       ``spend_budget``'s concurrency contract — sessions must not be shared).
    2. Calls ``asyncio.run()`` to execute the async code from Huey's
       synchronous context.  ``asyncio.run()`` creates a new event loop,
       making this safe even if no event loop exists in the current thread.

    The returned callable signature matches ``_SpendBudgetProtocol`` in
    ``modules/synthesizer/tasks.py`` and is registered via
    ``set_spend_budget_fn()`` at bootstrapper startup (Rule 8).

    Import note:
        This factory defers all privacy-module imports inside the closure so
        that environments without a live database do not fail at import time.
        The ``spend_budget`` function and ``BudgetExhaustionError`` are imported
        lazily inside ``_async_spend``.

    Returns:
        A sync callable ``(*, amount, job_id, ledger_id, note=None) -> None``
        that deducts epsilon from the global ``PrivacyLedger`` atomically.

    Raises:
        Any exception raised by ``spend_budget()`` propagates to the caller,
        including ``BudgetExhaustionError`` when budget is exhausted.

    Example::

        fn = build_spend_budget_fn()
        set_spend_budget_fn(fn)
        # Later, in Huey task:
        fn(amount=0.5, job_id=42, ledger_id=1)
    """

    async def _async_spend(
        amount: float | Decimal,
        job_id: int,
        ledger_id: int,
        note: str | None,
    ) -> None:
        """Async inner function: opens a session and calls spend_budget().

        Args:
            amount: Epsilon to deduct.
            job_id: Synthesis job identifier.
            ledger_id: Primary key of the PrivacyLedger row.
            note: Optional annotation.
        """
        # Deferred imports — keeps startup fast and avoids import errors
        # in environments where aiosqlite/asyncpg is not installed.
        from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
        from sqlalchemy.ext.asyncio import create_async_engine

        from synth_engine.modules.privacy.accountant import spend_budget

        database_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
        # Promote sync postgres URL to async driver if needed.
        async_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        # Promote sync sqlite URL to async driver if needed.
        async_url = async_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

        async_engine = create_async_engine(async_url)
        async with _AsyncSession(async_engine) as session:
            await spend_budget(
                amount=amount,
                job_id=job_id,
                ledger_id=ledger_id,
                session=session,
                note=note,
            )

    def _sync_wrapper(
        *,
        amount: float,
        job_id: int,
        ledger_id: int,
        note: str | None = None,
    ) -> None:
        """Sync wrapper: calls _async_spend via asyncio.run() for Huey compat.

        Args:
            amount: Epsilon to deduct.  Must be positive.
            job_id: Synthesis job identifier written to the audit trail.
            ledger_id: Primary key of the PrivacyLedger row to debit.
            note: Optional human-readable annotation for the transaction.

        Raises:
            BudgetExhaustionError: If the privacy budget is exhausted.
            ValueError: If ``amount`` is not positive.
        """
        asyncio.run(_async_spend(amount, job_id, ledger_id, note))

    _logger.info("spend_budget sync wrapper built.")
    return _sync_wrapper
