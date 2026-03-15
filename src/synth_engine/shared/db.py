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

``get_async_engine`` provides an :class:`~sqlalchemy.ext.asyncio.AsyncEngine`
for use with async sessions.  Required by the Privacy Accountant (T4.4)
which needs ``SELECT ... FOR UPDATE`` within an async FastAPI request context.
For PostgreSQL async the driver is ``asyncpg`` (``postgresql+asyncpg://``).
For in-process unit tests the driver is ``aiosqlite`` (``sqlite+aiosqlite://``).

Session management
------------------
``get_session`` is a FastAPI-compatible generator dependency.  It yields
a ``Session`` and guarantees cleanup on exit, including on exceptions.

``get_async_session`` is an async context manager that yields an
:class:`~sqlalchemy.ext.asyncio.AsyncSession`.  Use it as::

    async with get_async_session(engine) as session:
        ...

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
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlmodel import Field, Session, SQLModel
from sqlmodel._compat import SQLModelConfig

# ---------------------------------------------------------------------------
# Engine factories
# ---------------------------------------------------------------------------

_POOL_SIZE = 5
_MAX_OVERFLOW = 10


def get_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine with connection pool configuration.

    For SQLite URLs (``sqlite://``) pool sizing arguments are omitted
    because SQLite uses a ``StaticPool`` that does not accept them.

    Args:
        database_url: A SQLAlchemy-compatible connection URL.  Credentials
            must be sourced from environment variables at call-site, never
            hard-coded.  Example format (values supplied at runtime)::

                postgresql+psycopg2://<USER>:<PASSWORD>@<HOST>:<PORT>/<DBNAME>

            or ``sqlite:///:memory:`` for in-process tests.

    Returns:
        A configured :class:`sqlalchemy.Engine` instance.
    """
    if database_url.startswith("sqlite"):
        return create_engine(database_url)
    return create_engine(
        database_url,
        pool_size=_POOL_SIZE,
        max_overflow=_MAX_OVERFLOW,
    )


def get_async_engine(database_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine for use with AsyncSession.

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

    Args:
        database_url: An async-driver-compatible SQLAlchemy URL.  Credentials
            must be sourced from environment variables at call-site, never
            hard-coded.

    Returns:
        A configured :class:`~sqlalchemy.ext.asyncio.AsyncEngine` instance.
    """
    if database_url.startswith("sqlite"):
        return create_async_engine(database_url)
    return create_async_engine(
        database_url,
        pool_size=_POOL_SIZE,
        max_overflow=_MAX_OVERFLOW,
    )


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
        An open :class:`sqlmodel.Session`; the session is closed in the
        ``finally`` block so cleanup is guaranteed even on exception.
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
        An open :class:`~sqlalchemy.ext.asyncio.AsyncSession`.
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
