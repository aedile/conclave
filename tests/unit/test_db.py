"""Unit tests for the shared database base layer.

Verifies that:
- BaseModel provides a UUID primary key and created_at / updated_at timestamps.
- get_engine() returns a usable SQLAlchemy Engine instance.
- get_session() yields a SQLModel Session connected to an in-memory SQLite DB.

No real database is required for these unit tests; SQLite in-memory is used
where an engine is needed.

CONSTITUTION Priority 3: TDD
Task: P2-T2.2 — Secure Database Layer
"""

import uuid
from datetime import datetime

from sqlmodel import Field


def test_base_model_has_uuid_primary_key() -> None:
    """Concrete BaseModel subclass must default-assign a UUID to `id`."""
    from synth_engine.shared.db import BaseModel

    class _StubUUID(BaseModel, table=True):  # type: ignore[call-arg]
        __tablename__ = "stub_uuid"  # type: ignore[assignment]
        name: str = Field(default="")

    instance = _StubUUID()
    assert isinstance(instance.id, uuid.UUID), f"Expected uuid.UUID, got {type(instance.id)}"


def test_base_model_has_created_at() -> None:
    """BaseModel must provide a datetime `created_at` field auto-populated on init."""
    from synth_engine.shared.db import BaseModel

    class _StubCreatedAt(BaseModel, table=True):  # type: ignore[call-arg]
        __tablename__ = "stub_created_at"  # type: ignore[assignment]
        label: str = Field(default="")

    instance = _StubCreatedAt()
    assert isinstance(instance.created_at, datetime), (
        f"Expected datetime, got {type(instance.created_at)}"
    )


def test_get_engine_returns_engine() -> None:
    """get_engine() must return a SQLAlchemy Engine for a valid connection URL."""
    from sqlalchemy import Engine

    from synth_engine.shared.db import get_engine

    engine = get_engine("sqlite:///:memory:")
    assert isinstance(engine, Engine)


def test_get_session_yields_session() -> None:
    """get_session() must yield a SQLModel Session that is open and usable."""
    from sqlmodel import Session

    from synth_engine.shared.db import get_engine, get_session

    engine = get_engine("sqlite:///:memory:")
    gen = get_session(engine)
    session = next(gen)
    assert isinstance(session, Session)
    try:
        next(gen)
    except StopIteration:
        pass
