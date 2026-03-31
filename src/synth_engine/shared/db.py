"""Shared database engine, session factory, and abstract base model.

All SQLModel table classes in the Conclave Engine extend :class:`BaseModel`
to inherit consistent primary-key and audit-timestamp behaviour.

Engine configuration
--------------------
``get_engine`` pools connections through SQLAlchemy's built-in connection
pool (``QueuePool`` for PostgreSQL, ``StaticPool`` for SQLite in tests).
In production the application connects through PgBouncer, so
``pool_size`` and ``max_overflow`` are intentionally modest: PgBouncer
handles external multiplexing.

Engine singleton caching (T19.1)
---------------------------------
Both ``get_engine`` and ``get_async_engine`` cache the engine they create,
keyed by a composite ``"{database_url}|mtls={mtls_enabled}"`` string for
PostgreSQL connections (``"{database_url}"`` for SQLite, which never uses
TLS).  Caching by the composite key prevents returning a cached plaintext
engine when the same URL is subsequently requested with mTLS enabled — a
scenario that can occur in tests that toggle ``MTLS_ENABLED`` between cases.

Without caching, each call would create a new ``QueuePool`` with up to
``pool_size + max_overflow = 15`` connections.  In a request-heavy
environment this could exhaust the available PostgreSQL connections.

Call :func:`dispose_engines` to release all cached engines and their
connection pools — required between test cases that use different
``database_url`` values or mTLS state, and at application shutdown.

``get_async_engine`` provides an :class:`~sqlalchemy.ext.asyncio.AsyncEngine`
for use with async sessions.  Required by the Privacy Accountant (T4.4)
which needs ``SELECT ... FOR UPDATE`` within an async FastAPI request context.
For PostgreSQL async the driver is ``asyncpg`` (``postgresql+asyncpg://``).
For in-process unit tests the driver is ``aiosqlite`` (``sqlite+aiosqlite://``).

mTLS engine configuration (T46.2)
----------------------------------
When ``MTLS_ENABLED=true`` is set in the environment:

- ``get_engine()`` (sync / psycopg2) adds::

      connect_args={
          "sslmode": "verify-full",
          "sslcert": <MTLS_CLIENT_CERT_PATH>,
          "sslkey": <MTLS_CLIENT_KEY_PATH>,
          "sslrootcert": <MTLS_CA_CERT_PATH>,
      }

- ``get_async_engine()`` (asyncpg) builds an ``ssl.SSLContext`` with the
  client cert/key loaded and the CA cert as the trust anchor, then passes::

      connect_args={"ssl": <ssl.SSLContext>}

SQLite connections are never modified — they do not support TLS.

Session management
------------------
``get_session`` is a FastAPI-compatible generator dependency.  It yields
a ``Session`` and guarantees cleanup on exit, including on exceptions.

``get_async_session`` is an async context manager that yields an
:class:`~sqlalchemy.ext.asyncio.AsyncSession`.  Use it as::

    async with get_async_session(engine) as session:
        ...

SessionFactory
--------------
``SessionFactory`` is a type alias for a zero-argument callable that returns
a context manager yielding a :class:`sqlmodel.Session`.  Used as the
parameter type for :func:`~synth_engine.bootstrapper.sse.job_event_stream`
so the SSE generator can open its own sessions after the request session
has been closed.

BaseModel
---------
Abstract base class for all database entities.  Provides:

- ``id``:  UUID v4 primary key, auto-generated on instantiation.
- ``created_at``: UTC timestamp, set on first insert.
- ``updated_at``: UTC timestamp, updated by SQLAlchemy on every UPDATE.

Alembic metadata note
---------------------
Any SQLModel table NOT extending ``BaseModel`` must be explicitly imported
in ``alembic/env.py`` so that ``target_metadata`` remains complete.

CONSTITUTION Priority 5: Code Quality
Task: P2-T2.2 — Secure Database Layer
Task: P4-T4.4 — Privacy Accountant (async engine + session)
Task: P5-T5.1 — Task Orchestration API Core (SessionFactory type alias)
Task: T19.1 — Engine singleton caching (dispose_engines)
Task: T46.2 — Wire mTLS on All Container-to-Container Connections
Task: T47.8 — ADV-P46-01 TLS 1.3 minimum version pin for asyncpg
"""

from __future__ import annotations

import contextlib
import ssl
import uuid
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import QueuePool
from sqlmodel import Field, Session, SQLModel
from sqlmodel._compat import SQLModelConfig

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

#: Type alias for a zero-argument callable that returns a context manager
#: yielding a :class:`sqlmodel.Session`.
#:
#: Used as the parameter type for
#: :func:`~synth_engine.bootstrapper.sse.job_event_stream` so the SSE
#: generator can open its own sessions independently of the request session.
SessionFactory = Callable[[], contextlib.AbstractContextManager[Session]]

# ---------------------------------------------------------------------------
# Engine factories
# ---------------------------------------------------------------------------

# Pool sizing is externalized to ConclaveSettings (T74.1).
# Read via get_settings() inside get_engine(), get_async_engine(), and
# get_worker_engine() to allow runtime configuration via env vars.
# See ConclaveSettings fields: conclave_db_pool_size, conclave_db_max_overflow,
# conclave_db_worker_pool_size, conclave_db_worker_max_overflow,
# conclave_db_worker_pool_recycle, conclave_db_worker_pool_timeout.

#: Module-level cache for synchronous engines, keyed by composite cache key.
#: For PostgreSQL: ``"{database_url}|mtls={mtls_enabled}"``.
#: For SQLite: ``"{database_url}"`` (TLS not applicable).
#: Populated lazily on first call to :func:`get_engine`.
#: Call :func:`dispose_engines` to clear.
_engine_cache: dict[str, Engine] = {}

#: Module-level cache for asynchronous engines, keyed by composite cache key.
#: For PostgreSQL: ``"{database_url}|mtls={mtls_enabled}"``.
#: For SQLite: ``"{database_url}"`` (TLS not applicable).
#: Populated lazily on first call to :func:`get_async_engine`.
#: Call :func:`dispose_engines` to clear.
_async_engine_cache: dict[str, AsyncEngine] = {}

#: Module-level cache for Huey worker engines.
#: Separate from ``_engine_cache`` to prevent cross-contamination with the
#: FastAPI pool. Call :func:`dispose_engines` to clear.
_worker_engine_cache: dict[str, Engine] = {}


def _engine_cache_key(database_url: str) -> str:
    """Return the cache key for the given database URL.

    For SQLite connections the URL itself is the key — SQLite never uses TLS.
    For all other drivers (PostgreSQL) the key is a composite that includes the
    current ``mtls_enabled`` setting, preventing a cached plaintext engine from
    being returned after mTLS is enabled (e.g., between test cases).

    Args:
        database_url: A SQLAlchemy-compatible connection URL.

    Returns:
        A string suitable for use as the key in ``_engine_cache`` or
        ``_async_engine_cache``.
    """
    if database_url.startswith("sqlite"):
        return database_url
    from synth_engine.shared.settings import get_settings

    settings = get_settings()
    return f"{database_url}|mtls={settings.mtls_enabled}"


def _build_psycopg2_connect_args() -> dict[str, str]:
    """Build psycopg2 TLS connect_args from current settings.

    Called only when ``MTLS_ENABLED=true`` and the database URL is a
    PostgreSQL connection (not SQLite).  Reads the mTLS cert paths from
    :func:`get_settings` and returns a dict suitable for passing as
    ``connect_args`` to :func:`sqlalchemy.create_engine`.

    Returns:
        A dict with ``sslmode``, ``sslcert``, ``sslkey``, and ``sslrootcert``
        keys, all populated from :class:`ConclaveSettings`.
    """
    from synth_engine.shared.settings import get_settings

    settings = get_settings()
    return {
        "sslmode": "verify-full",
        "sslcert": settings.mtls_client_cert_path,
        "sslkey": settings.mtls_client_key_path,
        "sslrootcert": settings.mtls_ca_cert_path,
    }


def _build_asyncpg_ssl_context() -> ssl.SSLContext:
    """Build an ssl.SSLContext for asyncpg mTLS connections.

    Creates a client-side TLS context that:
    - Pins the minimum TLS version to TLSv1.3 (ADV-P46-01).
    - Verifies the server certificate against the configured CA cert.
    - Loads the client certificate and key for mutual authentication.

    Returns:
        A configured :class:`ssl.SSLContext` ready for asyncpg's
        ``connect_args={"ssl": context}`` parameter.
    """
    from synth_engine.shared.settings import get_settings

    settings = get_settings()
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=settings.mtls_ca_cert_path)
    ctx.load_cert_chain(
        certfile=settings.mtls_client_cert_path,
        keyfile=settings.mtls_client_key_path,
    )
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def get_engine(database_url: str) -> Engine:
    """Return a cached SQLAlchemy engine for the given URL.

    If an engine for ``database_url`` (with the current mTLS state) already
    exists in the module-level cache, it is returned immediately without
    creating a new connection pool.  Otherwise a new engine is created, stored
    in the cache, and returned.

    The cache key is a composite ``"{database_url}|mtls={mtls_enabled}"``
    for PostgreSQL connections so that toggling ``MTLS_ENABLED`` between calls
    (as can happen in tests) always produces a correctly-configured engine,
    never returning a cached plaintext engine for an mTLS-enabled request.

    For SQLite URLs (``sqlite://``) pool sizing arguments are omitted
    because SQLite uses a ``StaticPool`` that does not accept them.

    When ``MTLS_ENABLED=true`` and the URL is a PostgreSQL connection,
    ``connect_args`` carrying ``sslmode=verify-full`` and the cert paths
    are injected automatically.

    Args:
        database_url: A SQLAlchemy-compatible connection URL.  Credentials
            must be sourced from environment variables at call-site, never
            hard-coded.  Example format (values supplied at runtime)::

                postgresql+psycopg2://<USER>:<PASSWORD>@<HOST>:<PORT>/<DBNAME>

            or ``sqlite:///:memory:`` for in-process tests.

    Returns:
        A configured :class:`sqlalchemy.Engine` instance.  The same instance
        is returned on every call with the same ``database_url`` and mTLS
        state.
    """
    cache_key = _engine_cache_key(database_url)
    if cache_key in _engine_cache:
        return _engine_cache[cache_key]

    if database_url.startswith("sqlite"):
        engine = create_engine(database_url)
    else:
        from synth_engine.shared.settings import get_settings

        settings = get_settings()
        extra_kwargs: dict[str, object] = {}
        if settings.mtls_enabled:
            extra_kwargs["connect_args"] = _build_psycopg2_connect_args()

        engine = create_engine(
            database_url,
            pool_size=settings.conclave_db_pool_size,
            max_overflow=settings.conclave_db_max_overflow,
            **extra_kwargs,
        )

    _engine_cache[cache_key] = engine
    return engine


def get_async_engine(database_url: str) -> AsyncEngine:
    """Return a cached async SQLAlchemy engine for the given URL.

    If an async engine for ``database_url`` (with the current mTLS state)
    already exists in the module-level cache, it is returned immediately.
    Otherwise a new engine is created, cached, and returned.

    The cache key is a composite ``"{database_url}|mtls={mtls_enabled}"``
    for PostgreSQL connections — the same rationale as :func:`get_engine`.

    Supports two driver schemes:

    - ``postgresql+asyncpg://...``  — production PostgreSQL via asyncpg.
      ``SELECT ... FOR UPDATE`` works correctly here; used by the Privacy
      Accountant (T4.4) concurrency integration tests.
    - ``sqlite+aiosqlite://...``    — in-process SQLite via aiosqlite.
      Suitable for unit tests.  Note: SQLite ignores ``FOR UPDATE`` clauses,
      so concurrency correctness cannot be verified with this driver.

    Pool sizing arguments are omitted for SQLite (``sqlite+aiosqlite``)
    because ``StaticPool`` is automatically chosen by SQLAlchemy for
    in-memory SQLite and does not accept pool configuration.

    When ``MTLS_ENABLED=true`` and the URL is a PostgreSQL asyncpg connection,
    an :class:`ssl.SSLContext` is built from the configured cert paths and
    passed as ``connect_args={"ssl": ctx}``.

    Args:
        database_url: An async-driver-compatible SQLAlchemy URL.  Credentials
            must be sourced from environment variables at call-site, never
            hard-coded.

    Returns:
        A configured :class:`~sqlalchemy.ext.asyncio.AsyncEngine` instance.
        The same instance is returned on every call with the same
        ``database_url`` and mTLS state.
    """
    cache_key = _engine_cache_key(database_url)
    if cache_key in _async_engine_cache:
        return _async_engine_cache[cache_key]

    if database_url.startswith("sqlite"):
        engine = create_async_engine(database_url)
    else:
        from synth_engine.shared.settings import get_settings

        settings = get_settings()
        extra_kwargs: dict[str, object] = {}
        if settings.mtls_enabled:
            extra_kwargs["connect_args"] = {"ssl": _build_asyncpg_ssl_context()}

        engine = create_async_engine(
            database_url,
            pool_size=settings.conclave_db_pool_size,
            max_overflow=settings.conclave_db_max_overflow,
            **extra_kwargs,
        )

    _async_engine_cache[cache_key] = engine
    return engine


def get_worker_engine(database_url: str) -> Engine:
    """Return a cached, bounded SQLAlchemy engine for Huey worker tasks.

    Returns a dedicated synchronous engine separate from the FastAPI pool.
    Isolation is required so that a stuck or slow Huey task cannot exhaust
    the connection pool used by FastAPI request handlers.

    Pool configuration (T48.2, ADR-0035):
    - ``poolclass=QueuePool`` — bounded connection pool.
    - ``pool_size=1`` — one persistent connection per worker process.
    - ``max_overflow=2`` — two additional overflow connections for burst.
    - ``pool_timeout=30`` — raise TimeoutError after 30s on pool exhaustion.
    - ``pool_pre_ping=True`` — detect stale connections before use.
    - ``pool_recycle=1800`` — match PgBouncer server_idle_timeout.

    For SQLite URLs, pool size arguments are skipped (StaticPool used).

    When ``MTLS_ENABLED=true`` the same psycopg2 TLS connect_args as
    :func:`get_engine` are applied.

    Args:
        database_url: A synchronous-driver SQLAlchemy URL (psycopg2 for
            PostgreSQL, stdlib sqlite3 for SQLite).

    Returns:
        A configured :class:`sqlalchemy.Engine` for use by Huey workers.
        The same instance is returned on every call with the same URL and
        mTLS state.
    """
    if database_url.startswith("sqlite"):
        cache_key = f"{database_url}|worker"
    else:
        from synth_engine.shared.settings import get_settings

        settings = get_settings()
        cache_key = f"{database_url}|worker|mtls={settings.mtls_enabled}"

    if cache_key in _worker_engine_cache:
        return _worker_engine_cache[cache_key]

    if database_url.startswith("sqlite"):
        engine = create_engine(database_url)
    else:
        from synth_engine.shared.settings import get_settings

        settings = get_settings()
        extra_kwargs: dict[str, object] = {}
        if settings.mtls_enabled:
            extra_kwargs["connect_args"] = _build_psycopg2_connect_args()

        engine = create_engine(
            database_url,
            poolclass=QueuePool,
            pool_size=settings.conclave_db_worker_pool_size,
            max_overflow=settings.conclave_db_worker_max_overflow,
            pool_timeout=settings.conclave_db_worker_pool_timeout,
            pool_pre_ping=True,
            pool_recycle=settings.conclave_db_worker_pool_recycle,
            **extra_kwargs,
        )

    _worker_engine_cache[cache_key] = engine
    return engine


def dispose_engines() -> None:
    """Dispose all cached engines and clear the engine caches.

    Calls ``engine.dispose()`` on every cached synchronous engine and
    ``async_engine.sync_engine.dispose()`` on every cached asynchronous engine
    (synchronous variant, safe to call from non-async contexts) to release
    connection pool resources.  Both caches are then cleared so subsequent calls to
    :func:`get_engine` and :func:`get_async_engine` create fresh engines.

    This function is idempotent: calling it on an already-empty cache is
    a no-op.

    Use cases:
    - Test teardown: clear between test cases that use different URLs or mTLS state.
    - Application shutdown: release all DB connections before exit.
    """
    for sync_engine in _engine_cache.values():
        sync_engine.dispose()
    _engine_cache.clear()

    for async_engine in _async_engine_cache.values():
        async_engine.sync_engine.dispose()
    _async_engine_cache.clear()

    for worker_engine in _worker_engine_cache.values():
        worker_engine.dispose()
    _worker_engine_cache.clear()


# ---------------------------------------------------------------------------
# Session dependencies
# ---------------------------------------------------------------------------


def get_session(engine: Engine) -> Generator[Session]:
    """FastAPI-compatible session dependency that yields a transactional session.

    Usage in a FastAPI route::

        @router.get("/items")
        def list_items(
            session: Session = Depends(lambda: get_session(engine)),
        ) -> list[Item]:
            ...

    Args:
        engine: The SQLAlchemy engine to bind the session to.

    Yields:
        Generator[Session]: An open :class:`sqlmodel.Session`; the session
            is closed in the ``finally`` block so cleanup is guaranteed even
            on exception.
    """
    with Session(engine) as session:
        yield session


@asynccontextmanager
async def get_async_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Async context manager that yields an AsyncSession for the given engine.

    Provides a clean async session lifecycle: the session is opened on entry
    and closed on exit (including on exceptions).  The caller is responsible
    for calling ``await session.commit()`` or ``await session.rollback()``
    within the context.

    Usage::

        async with get_async_session(engine) as session:
            result = await session.execute(select(MyModel))
            ...
            await session.commit()

    For FastAPI routes, wrap this in a ``Depends`` lambda or use it directly
    inside route handler bodies.

    Args:
        engine: The :class:`~sqlalchemy.ext.asyncio.AsyncEngine` to bind the
            session to.  Obtain one via :func:`get_async_engine`.

    Yields:
        AsyncGenerator[AsyncSession]: An open :class:`~sqlalchemy.ext.asyncio.AsyncSession`.
    """
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime.

    Using ``datetime.now(UTC)`` instead of the deprecated
    ``datetime.utcnow()`` ensures the returned object carries explicit
    timezone information, which is required for correct cross-timezone
    arithmetic and is the preferred approach in Python 3.12+.

    Returns:
        Current UTC time as a timezone-aware :class:`datetime`.
    """
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Abstract base model
# ---------------------------------------------------------------------------


class BaseModel(SQLModel):
    """Abstract base class for all Conclave Engine database entities.

    Provides a UUID v4 primary key and UTC audit timestamps.  Concrete
    subclasses must declare ``table=True`` in their class signature::

        class Job(BaseModel, table=True):
            title: str

    Attributes:
        id: UUID v4 primary key generated automatically on instantiation.
        created_at: Timezone-aware UTC datetime recorded on first insert.
        updated_at: Timezone-aware UTC datetime updated on every UPDATE.
    """

    model_config = SQLModelConfig(arbitrary_types_allowed=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column_kwargs={"onupdate": _utcnow},
    )
