"""Unit tests for the shared database base layer — RED phase.

Verifies that:
- BaseModel provides a UUID primary key and created_at / updated_at timestamps.
- get_engine() returns a usable SQLAlchemy Engine instance.

No real database is required for these unit tests; SQLite in-memory is used
where an engine is needed.

CONSTITUTION Priority 3: TDD — Red Phase
Task: P2-T2.2 — Secure Database Layer
"""

import uuid
from datetime import datetime

import pytest


def test_base_model_has_uuid_primary_key() -> None:
    """Concrete BaseModel subclass must default-assign a UUID to `id`."""
    from sqlmodel import Field, SQLModel

    from synth_engine.shared.db import BaseModel

    class _Stub(BaseModel, table=True):
        __tablename__ = "stub_uuid"  # type: ignore[assignment]
        name: str = Field(default="")

    instance = _Stub()
    assert isinstance(instance.id, uuid.UUID), (
        f"Expected uuid.UUID, got {type(instance.id)}"
    )


def test_base_model_has_created_at() -> None:
    """BaseModel must provide a datetime `created_at` field auto-populated on init."""
    from sqlmodel import Field, SQLModel

    from synth_engine.shared.db import BaseModel

    class _StubCreated(BaseModel, table=True):
        __tablename__ = "stub_created_at"  # type: ignore[assignment]
        label: str = Field(default="")

    instance = _StubCreated()
    assert isinstance(instance.created_at, datetime), (
        f"Expected datetime, got {type(instance.created_at)}"
    )


def test_get_engine_returns_engine() -> None:
    """get_engine() must return a SQLAlchemy Engine for a valid connection URL."""
    from sqlalchemy import Engine

    from synth_engine.shared.db import get_engine

    engine = get_engine("sqlite:///:memory:")
    assert isinstance(engine, Engine)
