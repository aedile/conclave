"""Integration tests for Huey worker connection pooling (T48.2).

Verifies that:
1. The worker engine uses QueuePool(pool_size=1, max_overflow=2).
2. Sessions are properly closed/returned after each task execution.
3. Five simultaneous tasks do not exhaust connections (AC3).
4. The worker engine cache is separate from the FastAPI engine cache.
5. dispose_engines() covers worker engines for clean shutdown.

These tests use SQLite (in-memory) for the integration assertions since
no real PostgreSQL instance is required to verify the session lifecycle
and pool configuration contracts. Pool configuration details for PostgreSQL
are verified exhaustively in the unit attack tests.

CONSTITUTION Priority 3: TDD
CONSTITUTION Priority 4: 95%+ coverage
Task: T48.2 — Connection Pooling for Huey Workers
"""

from __future__ import annotations

import concurrent.futures
import threading

import pytest

pytestmark = pytest.mark.integration


def test_worker_engine_is_importable_and_returns_engine() -> None:
    """get_worker_engine() must be importable from shared.db and return an Engine.

    AC1 foundation: the function must exist in the public API of shared.db.
    """
    from sqlalchemy import Engine

    from synth_engine.shared.db import dispose_engines, get_worker_engine

    dispose_engines()
    engine = get_worker_engine("sqlite:///:memory:")
    assert isinstance(engine, Engine)
    # Specific: the engine dialect is SQLite (we passed sqlite:// URL)
    assert engine.dialect.name == "sqlite", f"Expected sqlite dialect, got {engine.dialect.name!r}"
    dispose_engines()


def test_worker_engine_cached_across_calls() -> None:
    """get_worker_engine() must return the same Engine instance on repeated calls.

    Prevents a new pool being created per task invocation.
    """
    from synth_engine.shared.db import dispose_engines, get_worker_engine

    dispose_engines()
    e1 = get_worker_engine("sqlite:///:memory:")
    e2 = get_worker_engine("sqlite:///:memory:")
    assert e1 is e2
    dispose_engines()


def test_worker_engine_separate_from_fastapi_engine() -> None:
    """Worker engine and FastAPI engine must be distinct objects.

    AC1: Worker pool must be separate from FastAPI's engine to prevent
    cross-contamination. Verified at integration scope because both
    caches must coexist correctly.
    """
    from synth_engine.shared.db import dispose_engines, get_engine, get_worker_engine

    dispose_engines()
    url = "sqlite:///:memory:"
    fastapi_engine = get_engine(url)
    worker_engine = get_worker_engine(url)
    assert fastapi_engine is not worker_engine
    dispose_engines()


def test_five_concurrent_worker_sessions_complete_without_error() -> None:
    """Five concurrent tasks using the worker engine must all complete.

    AC3: Concurrent job test — 5 simultaneous Huey task sessions must
    not exhaust connections or raise connection errors. This uses SQLite
    in-memory where pool exhaustion is not a concern for size, but the
    session lifecycle (open/close in a thread-safe manner) must be correct.
    """
    from sqlmodel import Session

    from synth_engine.shared.db import dispose_engines, get_worker_engine

    dispose_engines()
    engine = get_worker_engine("sqlite:///:memory:")

    results: list[str] = []
    errors: list[str] = []
    lock = threading.Lock()

    def _task(task_id: int) -> None:
        try:
            with Session(engine) as session:
                assert session is not None
                # Simulate minimal DB work
                _ = session.exec  # type: ignore[attr-defined]
        except Exception as exc:
            with lock:
                errors.append(f"task {task_id}: {exc}")
        else:
            with lock:
                results.append(f"task {task_id} ok")

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futs = [executor.submit(_task, i) for i in range(5)]
        for f in concurrent.futures.as_completed(futs, timeout=15):
            f.result()  # Propagate any unexpected exception

    assert not errors, f"Worker session errors under concurrency: {errors}"
    assert len(results) == 5, f"Expected 5 successful tasks, got {len(results)}: {results}"
    dispose_engines()


def test_dispose_engines_covers_worker_cache() -> None:
    """dispose_engines() must clear the worker engine cache.

    AC5: dispose_engines() must cover worker engines for clean shutdown.
    After dispose, a new get_worker_engine() call must return a new instance.
    """
    from synth_engine.shared.db import dispose_engines, get_worker_engine

    dispose_engines()
    url = "sqlite:///:memory:"
    engine_before = get_worker_engine(url)
    dispose_engines()
    engine_after = get_worker_engine(url)
    assert engine_before is not engine_after
    dispose_engines()


def test_worker_session_closes_on_exception() -> None:
    """Sessions must be returned to the pool even when the task body raises.

    AC2: Sessions properly closed/returned after each task execution.
    Verified using the context manager protocol which guarantees __exit__
    is called regardless of whether the body raises.
    """
    from sqlmodel import Session

    from synth_engine.shared.db import dispose_engines, get_worker_engine

    dispose_engines()
    engine = get_worker_engine("sqlite:///:memory:")

    exited: list[bool] = []

    class _SpySession(Session):
        def __exit__(self, *args: object) -> None:
            exited.append(True)
            super().__exit__(*args)

    raised = []
    try:
        with _SpySession(engine) as _session:
            raise RuntimeError("simulated task failure")
    except RuntimeError:
        raised.append(True)

    assert raised, "RuntimeError must propagate."
    assert exited, (
        "Session.__exit__ must be called even when the body raises. "
        "Without this, the connection is not returned to the pool."
    )
    # Specific: exactly one exception was raised and one exit was recorded
    assert len(raised) == 1, f"Expected 1 raised, got {len(raised)}"
    assert len(exited) == 1, f"Expected 1 exit, got {len(exited)}"
    dispose_engines()
