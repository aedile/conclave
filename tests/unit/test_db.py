"""Unit tests for the shared database base layer.

Verifies that:
- BaseModel provides a UUID primary key and created_at / updated_at timestamps.
- get_engine() returns a usable SQLAlchemy Engine instance.
- get_session() yields a SQLModel Session connected to an in-memory SQLite DB.
- get_engine() returns a cached singleton per URL (T19.1).
- get_async_engine() returns a cached singleton per URL (T19.1).
- dispose_engines() clears the engine cache (T19.1).

No real database is required for these unit tests; SQLite in-memory is used
where an engine is needed.

CONSTITUTION Priority 3: TDD
Task: P2-T2.2 — Secure Database Layer
Task: T19.1 — Middleware & Engine Singleton Fixes
"""

import uuid
from datetime import datetime

import pytest
from sqlmodel import Field

pytestmark = pytest.mark.unit


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

    from synth_engine.shared.db import dispose_engines, get_engine

    dispose_engines()
    engine = get_engine("sqlite:///:memory:")
    assert isinstance(engine, Engine)


def test_get_session_yields_session() -> None:
    """get_session() must yield a SQLModel Session that is open and usable."""
    from sqlmodel import Session

    from synth_engine.shared.db import dispose_engines, get_engine, get_session

    dispose_engines()
    engine = get_engine("sqlite:///:memory:")
    gen = get_session(engine)
    session = next(gen)
    assert isinstance(session, Session)
    try:
        next(gen)
    except StopIteration:
        pass


class TestEngineCache:
    """T19.1: Tests for engine singleton caching in get_engine() and get_async_engine()."""

    def setup_method(self) -> None:
        """Clear engine caches before each test to ensure isolation."""
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def test_get_engine_same_url_returns_same_instance(self) -> None:
        """get_engine() called twice with the same URL must return the same Engine instance."""
        from synth_engine.shared.db import get_engine

        url = "sqlite:///:memory:"
        engine_a = get_engine(url)
        engine_b = get_engine(url)

        assert engine_a is engine_b, (
            "get_engine() must return a cached singleton — same URL must return same instance. "
            "Creating a new engine per call wastes connection pool resources."
        )

    def test_get_engine_different_urls_returns_different_instances(self) -> None:
        """get_engine() with different URLs must return distinct Engine instances."""
        from synth_engine.shared.db import get_engine

        engine_a = get_engine("sqlite:///test_a.db")
        engine_b = get_engine("sqlite:///test_b.db")

        assert engine_a is not engine_b, (
            "get_engine() with different URLs must return distinct instances."
        )

    @pytest.mark.asyncio
    async def test_get_async_engine_same_url_returns_same_instance(self) -> None:
        """get_async_engine() called twice with the same URL must return same instance."""
        from synth_engine.shared.db import get_async_engine

        url = "sqlite+aiosqlite:///:memory:"
        engine_a = get_async_engine(url)
        engine_b = get_async_engine(url)

        assert engine_a is engine_b, (
            "get_async_engine() must return a cached singleton — same URL must return "
            "the same AsyncEngine instance."
        )

    @pytest.mark.asyncio
    async def test_get_async_engine_different_urls_returns_different_instances(self) -> None:
        """get_async_engine() with different URLs must return distinct AsyncEngine instances."""
        from synth_engine.shared.db import get_async_engine

        engine_a = get_async_engine("sqlite+aiosqlite:///test_a.db")
        engine_b = get_async_engine("sqlite+aiosqlite:///test_b.db")

        assert engine_a is not engine_b, (
            "get_async_engine() with different URLs must return distinct instances."
        )

    def test_dispose_engines_clears_sync_cache(self) -> None:
        """dispose_engines() must clear the sync engine cache so new instances are created."""
        from synth_engine.shared.db import dispose_engines, get_engine

        url = "sqlite:///:memory:"
        engine_before = get_engine(url)
        dispose_engines()
        engine_after = get_engine(url)

        assert engine_before is not engine_after, (
            "After dispose_engines(), get_engine() must return a new instance."
        )

    @pytest.mark.asyncio
    async def test_dispose_engines_clears_async_cache(self) -> None:
        """dispose_engines() must clear the async engine cache."""
        from synth_engine.shared.db import dispose_engines, get_async_engine

        url = "sqlite+aiosqlite:///:memory:"
        engine_before = get_async_engine(url)
        dispose_engines()
        engine_after = get_async_engine(url)

        assert engine_before is not engine_after, (
            "After dispose_engines(), get_async_engine() must return a new instance."
        )

    def test_dispose_engines_is_callable_with_empty_cache(self) -> None:
        """dispose_engines() on an empty cache must not raise."""
        from synth_engine.shared.db import dispose_engines

        dispose_engines()  # First call — cache is already empty (setup_method cleared it)
        dispose_engines()  # Second call — must be idempotent, no error

    def test_dispose_engines_returns_none(self) -> None:
        """dispose_engines() must return None (no meaningful return value)."""
        from synth_engine.shared.db import dispose_engines

        result = dispose_engines()
        assert result is None
