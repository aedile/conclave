"""Database session dependency for FastAPI route handlers.

Provides :func:`get_db_session` as a FastAPI dependency that yields a
SQLModel :class:`Session` bound to the application database engine.

The engine URL is read from :attr:`ConclaveSettings.database_url` via
:func:`get_settings` at dependency resolution time.  Tests override this
dependency via ``app.dependency_overrides`` to inject an in-memory SQLite
session.

Boundary constraints (import-linter enforced):
    - bootstrapper/ may import from shared/ and modules/.

Task: P5-T5.1 — Task Orchestration API Core
Task: T36.1 — Centralize Configuration Into Pydantic Settings Model
"""

from __future__ import annotations

from collections.abc import Generator

from sqlmodel import Session

from synth_engine.shared.db import get_engine
from synth_engine.shared.settings import get_settings

#: Default database URL used when DATABASE_URL is not set (unit-test fallback).
_DEFAULT_DATABASE_URL: str = "sqlite:///:memory:"


def get_db_session() -> Generator[Session]:
    """Yield a transactional SQLModel session for a FastAPI route.

    Reads ``DATABASE_URL`` from :func:`get_settings` to build the engine.
    Tests should override this dependency via ``app.dependency_overrides``
    to inject a session backed by an in-memory SQLite database.

    Yields:
        Generator[Session]: An open :class:`sqlmodel.Session`; closed automatically on exit.
    """
    database_url = get_settings().database_url or _DEFAULT_DATABASE_URL
    engine = get_engine(database_url)
    with Session(engine) as session:
        yield session
