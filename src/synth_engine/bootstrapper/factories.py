"""DI factory functions for synthesis-layer application dependencies.

Houses the lazy factory functions that construct :class:`SynthesisEngine`,
:class:`DPTrainingWrapper`, :class:`EphemeralStorageClient`, and the sync
``spend_budget`` wrapper instances.  These factories are called at
synthesis-job start time, never at application startup, so missing GPU or
database infrastructure does not prevent the health check from responding.

Task: T60.3 — Move build_ephemeral_storage_client from main.py to factories.py
    ``build_ephemeral_storage_client`` previously lived in ``main.py`` because
    it relies on Docker-secrets helpers also defined there.  Those helpers now
    live in :mod:`docker_secrets`, so there is no obstacle to moving the factory
    here where all other factories live.

    Backward compatibility: ``main.py`` re-exports
    ``build_ephemeral_storage_client`` from this module so that existing test
    patches against ``synth_engine.bootstrapper.main.build_ephemeral_storage_client``
    continue to resolve correctly (AC2 of T60.3).

Task: T60.4 — Extract domain transaction logic to modules/privacy/sync_budget.py
    ``_sync_wrapper`` in ``build_spend_budget_fn()`` previously contained the
    full pessimistic-locking transaction inline.  That logic now lives in
    :func:`~synth_engine.modules.privacy.sync_budget.sync_spend_budget`, and
    ``_sync_wrapper`` simply delegates to it.  The bootstrapper remains
    responsible for wiring (engine construction, settings, URL promotion) but
    no longer owns domain accounting code.

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
from typing import TYPE_CHECKING

from synth_engine.shared.protocols import SpendBudgetProtocol

if TYPE_CHECKING:
    from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
    from synth_engine.modules.synthesizer.storage.storage import EphemeralStorageClient
    from synth_engine.modules.synthesizer.training.engine import SynthesisEngine

_logger = logging.getLogger(__name__)

# Deferred import so environments without the synthesizer group don't fail.
# Bound at module scope for patch("synth_engine.bootstrapper.factories.MinioStorageBackend").
try:
    from synth_engine.modules.synthesizer.storage.storage import MinioStorageBackend
except ImportError:  # pragma: no cover — synthesizer group not installed
    MinioStorageBackend = None  # type: ignore[assignment,misc]  # conditional import fallback


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
    from synth_engine.modules.synthesizer.training.engine import SynthesisEngine as _SynthesisEngine

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


def build_ephemeral_storage_client() -> EphemeralStorageClient:
    """Build an EphemeralStorageClient backed by MinioStorageBackend.

    Reads MinIO credentials from Docker secrets at synthesis-job start time,
    not at application startup, so a missing MinIO service does not break
    the /health endpoint.

    Returns:
        A configured :class:`EphemeralStorageClient` ready to upload/download
        Parquet files.

    Raises:
        RuntimeError: If ``MinioStorageBackend`` is unavailable because the
            synthesizer dependency group is not installed.  Install it with
            ``pip install 'synth-engine[synthesizer]'`` or
            ``poetry install --extras synthesizer``.
    """
    from synth_engine.bootstrapper.docker_secrets import (
        EPHEMERAL_BUCKET as _EPHEMERAL_BUCKET,
    )
    from synth_engine.bootstrapper.docker_secrets import (
        MINIO_ENDPOINT as _MINIO_ENDPOINT,
    )
    from synth_engine.bootstrapper.docker_secrets import (
        _read_secret,
    )
    from synth_engine.modules.synthesizer.storage.storage import EphemeralStorageClient

    access_key = _read_secret("minio_ephemeral_access_key")
    secret_key = _read_secret("minio_ephemeral_secret_key")

    # T57.2: Replace assert with RuntimeError — asserts are stripped by python -O
    # and raise unhelpful AssertionError.  RuntimeError carries install instructions.
    if MinioStorageBackend is None:
        raise RuntimeError(
            "MinioStorageBackend unavailable — install the synthesizer dependency group: "
            "pip install 'synth-engine[synthesizer]' or poetry install --extras synthesizer"
        )

    backend = MinioStorageBackend(
        endpoint_url=_MINIO_ENDPOINT,
        access_key=access_key,
        secret_key=secret_key,
    )
    _logger.info(
        "EphemeralStorageClient initialised (bucket=%s, endpoint=%s).",
        _EPHEMERAL_BUCKET,
        _MINIO_ENDPOINT,
    )
    return EphemeralStorageClient(bucket=_EPHEMERAL_BUCKET, backend=backend)


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

    The returned callable delegates all budget accounting to
    :func:`~synth_engine.modules.privacy.sync_budget.sync_spend_budget`
    (T60.4).  The bootstrapper retains responsibility for engine construction
    and URL promotion; the domain logic lives in ``modules/privacy/``.

    URL promotion note:
        ``DATABASE_URL`` may contain an async driver prefix
        (``postgresql+asyncpg://`` or ``sqlite+aiosqlite:///``).
        :func:`_promote_to_sync_url` demotes these to their sync equivalents
        before calling ``sqlalchemy.create_engine``.

    Returns:
        A sync callable ``(*, amount, job_id, ledger_id, note=None) -> None``
        that deducts epsilon from the privacy ledger atomically.
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
        org_id: str = "",
    ) -> None:
        """Delegate budget deduction to sync_spend_budget (T60.4).

        Passes the factory-scoped synchronous engine to
        :func:`~synth_engine.modules.privacy.sync_budget.sync_spend_budget`,
        which owns all pessimistic-locking and transaction logic.

        Args:
            amount: Epsilon to deduct.  Must be positive.
            job_id: Synthesis job identifier written to the audit trail.
            ledger_id: Privacy ledger row primary key to debit.
            note: Optional human-readable annotation for the transaction.
            org_id: Organization UUID of the requesting job (P79-B5).
        """
        from synth_engine.modules.privacy.sync_budget import sync_spend_budget

        sync_spend_budget(
            engine, amount=amount, job_id=job_id, ledger_id=ledger_id, note=note, org_id=org_id
        )

    _logger.info("spend_budget sync wrapper built (P28-F4: uses sync engine, no asyncio.run).")
    return _sync_wrapper
