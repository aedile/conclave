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

Session management
------------------
``get_session`` is a FastAPI-compatible generator dependency.  It yields
a ``Session`` and guarantees cleanup on exit, including on exceptions.

BaseModel
---------
Abstract base class for all database entities.  Provides:

- ``id``:  UUID v4 primary key, auto-generated on instantiation.
- ``created_at``: UTC timestamp, set on first insert.
- ``updated_at``: UTC timestamp, updated by SQLAlchemy on every UPDATE.

CONSTITUTION Priority 5: Code Quality
Task: P2-T2.2 — Secure Database Layer
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, datetime

from sqlalchemy import Engine, create_engine
from sqlmodel import Field, Session, SQLModel
from sqlmodel._compat import SQLModelConfig

# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------

_POOL_SIZE = 5
_MAX_OVERFLOW = 10


def get_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine with connection pool configuration.

    For SQLite URLs (``sqlite://``) pool sizing arguments are omitted
    because SQLite uses a ``StaticPool`` that does not accept them.

    Args:
        database_url: A SQLAlchemy-compatible connection URL, e.g.
            ``postgresql+psycopg2://user:pass@host:5432/dbname`` or
            ``sqlite:///:memory:`` for in-process tests.

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


# ---------------------------------------------------------------------------
# Session dependency
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime.

    Using ``datetime.now(timezone.utc)`` instead of the deprecated
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
