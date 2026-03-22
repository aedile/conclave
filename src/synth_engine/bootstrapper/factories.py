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

P28-F4 — Sync spend_budget path
---------------------------------
The previous implementation called ``asyncio.run()`` inside the sync wrapper
returned by ``build_spend_budget_fn()``.  When asyncpg is the database driver
(``postgresql+asyncpg://``), ``asyncio.run()`` from a Huey worker thread that
was not started in a greenlet context raises::

    sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called

The fix replaces the async DB path with a **synchronous** SQLAlchemy engine
(psycopg2 driver, ``postgresql://``).  The sync path never calls
``asyncio.run()`` and therefore never encounters the MissingGreenlet error.
The existing async API routes (FastAPI handlers) are unaffected — they
continue to use the async engine via ``get_async_session`` in ``shared/db.py``.

URL mapping applied by ``build_spend_budget_fn()``:
- ``postgresql+asyncpg://`` → ``postgresql://`` (psycopg2 sync driver)
- ``sqlite+aiosqlite:///`` → ``sqlite:///`` (sync SQLite for unit tests)
- Any other URL is used as-is (already a sync-compatible URL).

Engine lifecycle (ADR-0035):
The synchronous engine is constructed **once** at factory build time (not on
every ``_sync_wrapper`` call) using ``NullPool``.  ``NullPool`` is correct for
Huey workers because each task is a single DB round-trip — pooling idle
connections between invocations provides no benefit and wastes server resources.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from synth_engine.shared.protocols import SpendBudgetProtocol

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


def _promote_to_sync_url(database_url: str) -> str:
    """Convert an async database URL to its synchronous driver equivalent.

    Maps async driver prefixes used by the FastAPI/asyncpg stack to their
    synchronous psycopg2/SQLite equivalents so the Huey worker can open a
    synchronous connection without requiring a greenlet context.

    Mapping table:
    - ``postgresql+asyncpg://`` → ``postgresql://``  (psycopg2)
    - ``sqlite+aiosqlite:///``  → ``sqlite:///``     (stdlib sqlite3)
    - Anything else             → returned unchanged  (already sync)

    Each branch guards against double-substitution: the check for the async
    prefix ensures we never transform an already-sync URL.

    Args:
        database_url: The raw ``DATABASE_URL`` value from the environment.

    Returns:
        A synchronous-driver URL suitable for ``sqlalchemy.create_engine``.
    """
    if "postgresql+asyncpg://" in database_url:
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if "sqlite+aiosqlite:///" in database_url:
        return database_url.replace("sqlite+aiosqlite:///", "sqlite:///", 1)
    # Also handle bare postgresql:// with no async prefix (already sync)
    # and any other sync-compatible URL — return as-is.
    return database_url


def build_spend_budget_fn() -> SpendBudgetProtocol:
    """Build a sync callable wrapping ``spend_budget`` logic for Huey context.

    The Huey task runner is synchronous.  The previous implementation called
    ``asyncio.run()`` to execute the async ``spend_budget()`` from
    ``modules/privacy/accountant``.  When asyncpg is the database driver,
    ``asyncio.run()`` from a Huey worker thread (which is not spawned as a
    greenlet) raises ``MissingGreenlet`` (P28-F4).

    This implementation uses a **synchronous** SQLAlchemy engine (psycopg2
    for PostgreSQL, stdlib sqlite3 for SQLite) to avoid the greenlet
    requirement entirely.  The async API routes are unaffected — they
    continue to use the async engine via ``shared/db.py:get_async_session``.

    The sync engine is constructed **once** at factory build time using
    ``NullPool`` (ADR-0035).  ``NullPool`` is correct here because Huey
    workers are single-call-per-job — pooling idle connections between
    invocations wastes DB server resources.

    The returned callable implements the same pessimistic-locking protocol
    as the async ``spend_budget()``:

    1. ``SELECT ... FOR UPDATE`` on the ``PrivacyLedger`` row.
    2. Budget exhaustion check — raises ``BudgetExhaustionError`` if exceeded.
    3. Deduct epsilon and write a ``PrivacyTransaction`` audit row.
    4. Commit (or rollback on error).

    Import note:
        All privacy-module and SQLAlchemy imports are deferred inside this
        function so environments without a live database do not fail at
        import time.

    URL promotion note:
        ``DATABASE_URL`` may contain an async driver prefix
        (``postgresql+asyncpg://`` or ``sqlite+aiosqlite:///``).
        :func:`_promote_to_sync_url` demotes these to their sync equivalents
        before calling ``sqlalchemy.create_engine``.

    Returns:
        A sync callable ``(*, amount, job_id, ledger_id, note=None) -> None``
        that deducts epsilon from the global ``PrivacyLedger`` atomically.
        The returned callable satisfies ``SpendBudgetProtocol``.

    Example::

        fn = build_spend_budget_fn()
        set_spend_budget_fn(fn)
        # Later, in Huey task:
        fn(amount=0.5, job_id=42, ledger_id=1)
    """
    # Deferred imports — keeps startup fast and avoids import errors in
    # environments where psycopg2 or the privacy module is not installed.
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    from synth_engine.shared.settings import get_settings

    settings = get_settings()
    database_url = settings.database_url or "sqlite:///:memory:"
    sync_url = _promote_to_sync_url(database_url)

    # Build TLS connect_args when mTLS is enabled (T46.2).
    # The sync engine (psycopg2) uses psycopg2-native ssl kwargs.
    # SQLite connections are never modified — they do not support TLS.
    extra_kwargs: dict[str, object] = {}
    if settings.mtls_enabled and not sync_url.startswith("sqlite"):
        extra_kwargs["connect_args"] = {
            "sslmode": "verify-full",
            "sslcert": settings.mtls_client_cert_path,
            "sslkey": settings.mtls_client_key_path,
            "sslrootcert": settings.mtls_ca_cert_path,
        }

    # Build the engine once at factory scope — reused for every invocation of
    # the returned _sync_wrapper.  NullPool: no idle connections between calls.
    engine = create_engine(sync_url, poolclass=NullPool, **extra_kwargs)

    def _sync_wrapper(
        *,
        amount: float,
        job_id: int,
        ledger_id: int,
        note: str | None = None,
    ) -> None:
        """Sync wrapper: calls spend_budget logic via a synchronous DB session.

        Uses the factory-scoped synchronous SQLAlchemy engine (psycopg2 for
        PostgreSQL, stdlib sqlite3 for SQLite) to avoid ``MissingGreenlet``
        errors when called from a Huey worker thread (P28-F4, ADR-0035).

        Args:
            amount: Epsilon to deduct.  Must be positive.
            job_id: Synthesis job identifier written to the audit trail.
            ledger_id: Primary key of the PrivacyLedger row to debit.
            note: Optional human-readable annotation for the transaction.

        Raises:
            BudgetExhaustionError: If the privacy budget is exhausted.
            ValueError: If ``amount`` is not positive.
        """
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

    _logger.info("spend_budget sync wrapper built (P28-F4: uses sync engine, no asyncio.run).")
    return _sync_wrapper
