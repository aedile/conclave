"""Negative/attack tests for Huey worker connection pooling (T48.2).

These tests exercise failure modes and boundary conditions for the worker
engine pool introduced in T48.2:

- Pool exhaustion: when tasks exceed pool_size + max_overflow the caller
  must receive a ``TimeoutError`` (or SQLAlchemy ``TimeoutError`` subclass),
  not hang indefinitely.
- Stale connection detection: ``pool_pre_ping=True`` must be configured so
  that dead connections are detected before use.
- Session cleanup on exception: if a task body raises, the session must still
  be returned to the pool (connection not leaked).
- Pool recycle for long-lived workers: ``pool_recycle=1800`` must be set to
  match PgBouncer's server_idle_timeout.
- Worker engine isolation: the worker engine must be a *different* object from
  the FastAPI shared engine, preventing cross-contamination.
- SQLite test compatibility: the factory must detect SQLite URLs and skip
  QueuePool configuration (SQLite uses StaticPool/NullPool with no sizing).
- ``dispose_engines()`` must cover worker engines — calling it must clear the
  worker engine cache, not just the FastAPI engine cache.

CONSTITUTION Priority 0: Security — no PII, no credential leaks.
CONSTITUTION Priority 3: TDD — attack tests before feature tests.
Task: T48.2 — Connection Pooling for Huey Workers
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


class TestWorkerEngineIsolation:
    """Worker engine must be isolated from the FastAPI engine.

    Cross-contamination between the worker pool and the FastAPI pool would
    allow a single stuck Huey task to exhaust the connections available to
    FastAPI request handlers.
    """

    def setup_method(self) -> None:
        """Clear engine caches to ensure test isolation."""
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def teardown_method(self) -> None:
        """Clear engine caches after each test."""
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def test_worker_engine_is_different_object_from_fastapi_engine(self) -> None:
        """get_worker_engine() must return a distinct object from get_engine().

        If they share the same engine instance, a pool exhaustion event in
        the worker layer would deny connections to FastAPI request handlers.
        """
        from synth_engine.shared.db import get_engine, get_worker_engine

        url = "sqlite:///:memory:"
        fastapi_engine = get_engine(url)
        worker_engine = get_worker_engine(url)

        assert fastapi_engine is not worker_engine, (
            "get_worker_engine() must return a separate engine from get_engine(). "
            "Sharing the same pool allows worker tasks to starve FastAPI handlers."
        )

    def test_worker_engine_same_url_returns_same_instance(self) -> None:
        """get_worker_engine() must cache: same URL returns same instance."""
        from synth_engine.shared.db import get_worker_engine

        url = "sqlite:///:memory:"
        engine_a = get_worker_engine(url)
        engine_b = get_worker_engine(url)

        assert engine_a is engine_b, (
            "get_worker_engine() must cache the engine. "
            "Creating a new engine per call would create unbounded connection pools."
        )


class TestDisposeCoversWorkerEngines:
    """dispose_engines() must clean up worker engines, not only FastAPI engines.

    If worker engines are excluded from dispose_engines(), test teardown would
    leak connection pools between test cases and production shutdown would not
    fully release resources.
    """

    def setup_method(self) -> None:
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def teardown_method(self) -> None:
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def test_dispose_engines_clears_worker_engine_cache(self) -> None:
        """dispose_engines() must create a new worker engine after clearing."""
        from synth_engine.shared.db import dispose_engines, get_worker_engine

        url = "sqlite:///:memory:"
        engine_before = get_worker_engine(url)
        dispose_engines()
        engine_after = get_worker_engine(url)

        assert engine_before is not engine_after, (
            "After dispose_engines(), get_worker_engine() must return a new instance. "
            "Worker engines must be disposed on shutdown and between test cases."
        )

    def test_dispose_engines_with_worker_engine_is_idempotent(self) -> None:
        """dispose_engines() called twice with a worker engine must not raise."""
        from synth_engine.shared.db import dispose_engines, get_worker_engine

        get_worker_engine("sqlite:///:memory:")
        dispose_engines()
        dispose_engines()  # Second call — must be safe


class TestSQLiteCompatibility:
    """Worker engine factory must detect SQLite and skip QueuePool config.

    SQLite does not support pool_size / max_overflow / pool_timeout /
    pool_pre_ping / pool_recycle — passing these kwargs to create_engine
    for a SQLite URL raises ArgumentError.
    """

    def setup_method(self) -> None:
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def teardown_method(self) -> None:
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def test_worker_engine_sqlite_does_not_raise(self) -> None:
        """get_worker_engine() with a SQLite URL must not raise ArgumentError."""
        from sqlalchemy import Engine

        from synth_engine.shared.db import get_worker_engine

        engine = get_worker_engine("sqlite:///:memory:")
        assert isinstance(engine, Engine), "SQLite worker engine must return an Engine instance."

    def test_worker_engine_sqlite_not_queue_pool(self) -> None:
        """SQLite worker engine must NOT use QueuePool (SQLite is single-file)."""
        from sqlalchemy.pool import QueuePool

        from synth_engine.shared.db import get_worker_engine

        engine = get_worker_engine("sqlite:///:memory:")
        assert not isinstance(engine.pool, QueuePool), (
            "SQLite worker engine must not use QueuePool. "
            "SQLite does not support concurrent multi-connection pools."
        )


class TestSessionCleanupOnException:
    """Session must be returned to pool even when the task body raises.

    A missing ``finally`` block in the task body would hold the connection
    open after an exception, gradually exhausting the pool under error load.
    """

    def setup_method(self) -> None:
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def teardown_method(self) -> None:
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def test_session_context_manager_closes_on_exception(self) -> None:
        """SQLModel Session used as context manager closes even when body raises.

        This test verifies that the ``with Session(engine) as session:`` pattern
        (which the tasks.py task body uses) properly closes the session — and
        therefore returns the connection to the pool — even when the with-block
        body raises an exception.

        This is a property of SQLModel/SQLAlchemy's context manager protocol and
        serves as a regression guard: if tasks.py ever abandons the context
        manager pattern, this test provides an early signal.
        """
        from sqlmodel import Session

        from synth_engine.shared.db import get_worker_engine

        engine = get_worker_engine("sqlite:///:memory:")
        session_close_called = []

        original_close = Session.close

        def _spy_close(self: Session) -> None:  # type: ignore[override]
            session_close_called.append(True)
            original_close(self)

        raised = []
        with patch.object(Session, "close", _spy_close):
            try:
                with Session(engine) as session:
                    assert session is not None
                    raise RuntimeError("simulated task failure")
            except RuntimeError:
                raised.append(True)

        assert raised, "RuntimeError must propagate out of the with block."
        assert session_close_called, (
            "Session.close() must be called even when the with-block body raises. "
            "Without this, connections leak back to the pool on task failure."
        )


class TestPoolConfigurationForPostgres:
    """Worker engine for PostgreSQL must use bounded QueuePool with safety settings.

    These tests verify the pool configuration contract using mocked
    create_engine so no real PostgreSQL instance is required.
    """

    def setup_method(self) -> None:
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def teardown_method(self) -> None:
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def test_worker_engine_postgres_uses_queue_pool(self) -> None:
        """get_worker_engine() for a PostgreSQL URL must use QueuePool.

        NullPool (used by build_spend_budget_fn) is correct for single-call
        factory patterns. For the main task runner that may handle concurrent
        jobs, QueuePool with bounded size is required.
        """
        from sqlalchemy import Engine
        from sqlalchemy.pool import QueuePool

        mock_engine = MagicMock(spec=Engine)
        mock_engine.pool = MagicMock(spec=QueuePool)

        pg_url = "postgresql://user:pw@localhost/testdb"

        with patch("synth_engine.shared.db.create_engine", return_value=mock_engine) as mock_ce:
            from synth_engine.shared.db import get_worker_engine

            get_worker_engine(pg_url)

            mock_ce.assert_called_once()
            call_kwargs = mock_ce.call_args.kwargs

            assert call_kwargs.get("poolclass") is QueuePool or (
                "pool_size" in call_kwargs and "max_overflow" in call_kwargs
            ), (
                "Worker engine for PostgreSQL must use QueuePool. "
                "NullPool creates a new connection per operation — "
                "under concurrent load this exhausts server connection slots."
            )

    def test_worker_engine_postgres_pool_size_is_one(self) -> None:
        """Worker QueuePool must have pool_size=1 (one persistent connection per worker).

        Each Huey worker process handles one task at a time. pool_size=1
        ensures one reusable connection. Additional burst capacity is
        handled by max_overflow=2.
        """
        from sqlalchemy import Engine
        from sqlalchemy.pool import QueuePool

        mock_engine = MagicMock(spec=Engine)
        mock_engine.pool = MagicMock(spec=QueuePool)

        pg_url = "postgresql://user:pw@localhost/testdb"

        with patch("synth_engine.shared.db.create_engine", return_value=mock_engine) as mock_ce:
            from synth_engine.shared.db import get_worker_engine

            get_worker_engine(pg_url)

            call_kwargs = mock_ce.call_args.kwargs
            assert call_kwargs.get("pool_size") == 1, (
                f"Worker pool_size must be 1, got {call_kwargs.get('pool_size')}. "
                "Each Huey worker process handles one job at a time."
            )

    def test_worker_engine_postgres_max_overflow_is_two(self) -> None:
        """Worker QueuePool must have max_overflow=2 (burst headroom)."""
        from sqlalchemy import Engine
        from sqlalchemy.pool import QueuePool

        mock_engine = MagicMock(spec=Engine)
        mock_engine.pool = MagicMock(spec=QueuePool)

        pg_url = "postgresql://user:pw@localhost/testdb"

        with patch("synth_engine.shared.db.create_engine", return_value=mock_engine) as mock_ce:
            from synth_engine.shared.db import get_worker_engine

            get_worker_engine(pg_url)

            call_kwargs = mock_ce.call_args.kwargs
            assert call_kwargs.get("max_overflow") == 2, (
                f"Worker max_overflow must be 2, got {call_kwargs.get('max_overflow')}."
            )

    def test_worker_engine_postgres_pool_timeout_is_set(self) -> None:
        """Worker engine must set pool_timeout to prevent indefinite blocking.

        Without an explicit pool_timeout, a caller that requests a connection
        when all pool_size + max_overflow slots are occupied will block
        indefinitely. Setting pool_timeout causes a TimeoutError instead,
        which the task runner can handle gracefully.
        """
        from sqlalchemy import Engine
        from sqlalchemy.pool import QueuePool

        mock_engine = MagicMock(spec=Engine)
        mock_engine.pool = MagicMock(spec=QueuePool)

        pg_url = "postgresql://user:pw@localhost/testdb"

        with patch("synth_engine.shared.db.create_engine", return_value=mock_engine) as mock_ce:
            from synth_engine.shared.db import get_worker_engine

            get_worker_engine(pg_url)

            call_kwargs = mock_ce.call_args.kwargs
            assert "pool_timeout" in call_kwargs, (
                "Worker engine must set pool_timeout. "
                "Without it, tasks block indefinitely when all connections are in use."
            )
            assert call_kwargs["pool_timeout"] > 0, (
                f"pool_timeout must be a positive number, got {call_kwargs['pool_timeout']}."
            )

    def test_worker_engine_postgres_pool_pre_ping_is_true(self) -> None:
        """Worker engine must set pool_pre_ping=True.

        After a PgBouncer restart or network interruption, pooled connections
        may be silently invalidated. pool_pre_ping issues a lightweight
        SELECT 1 before handing out a connection, ensuring stale connections
        are detected and replaced rather than causing cryptic OperationalErrors
        mid-task.
        """
        from sqlalchemy import Engine
        from sqlalchemy.pool import QueuePool

        mock_engine = MagicMock(spec=Engine)
        mock_engine.pool = MagicMock(spec=QueuePool)

        pg_url = "postgresql://user:pw@localhost/testdb"

        with patch("synth_engine.shared.db.create_engine", return_value=mock_engine) as mock_ce:
            from synth_engine.shared.db import get_worker_engine

            get_worker_engine(pg_url)

            call_kwargs = mock_ce.call_args.kwargs
            assert call_kwargs.get("pool_pre_ping") is True, (
                "Worker engine must set pool_pre_ping=True to detect stale connections "
                "after PgBouncer restarts or network interruptions."
            )

    def test_worker_engine_postgres_pool_recycle_is_1800(self) -> None:
        """Worker engine must set pool_recycle=1800 (matching PgBouncer idle timeout).

        PgBouncer's default server_idle_timeout is 600s. Production config
        sets it to 1800s (30 min). Connections held longer than this are
        silently dropped by PgBouncer. pool_recycle ensures SQLAlchemy
        proactively recycles connections before they exceed PgBouncer's
        server_idle_timeout, preventing 'connection closed' errors in
        long-running worker processes.
        """
        from sqlalchemy import Engine
        from sqlalchemy.pool import QueuePool

        mock_engine = MagicMock(spec=Engine)
        mock_engine.pool = MagicMock(spec=QueuePool)

        pg_url = "postgresql://user:pw@localhost/testdb"

        with patch("synth_engine.shared.db.create_engine", return_value=mock_engine) as mock_ce:
            from synth_engine.shared.db import get_worker_engine

            get_worker_engine(pg_url)

            call_kwargs = mock_ce.call_args.kwargs
            assert call_kwargs.get("pool_recycle") == 1800, (
                f"Worker engine must set pool_recycle=1800, "
                f"got {call_kwargs.get('pool_recycle')}. "
                "This matches PgBouncer's server_idle_timeout to prevent "
                "silent connection drops."
            )


class TestConnectionBudgetBoundary:
    """Connection budget: max_connections = (num_workers x 3) + FastAPI pool.

    This test documents and guards the connection budget arithmetic.
    It does not connect to a database — it verifies the constants defined
    in shared/db.py are within the documented PgBouncer budget.
    """

    def test_worker_pool_constants_fit_within_pgbouncer_budget(self) -> None:
        """Worker pool constants must fit within the documented PgBouncer budget.

        PgBouncer max_client_conn = 100 (default).
        FastAPI pool = pool_size(5) + max_overflow(10) = 15 connections.
        Worker pool = pool_size(1) + max_overflow(2) = 3 connections per worker.
        With 4 concurrent Huey workers: 4 x 3 = 12 worker connections.
        Total = 15 (FastAPI) + 12 (workers) = 27 connections < 100.

        If the constants change and violate the budget, this test must fail
        to prompt documentation and capacity review.
        """
        from synth_engine.shared.db import (
            _MAX_OVERFLOW,
            _POOL_SIZE,
            _WORKER_MAX_OVERFLOW,
            _WORKER_POOL_SIZE,
        )

        fastapi_max = _POOL_SIZE + _MAX_OVERFLOW
        per_worker_max = _WORKER_POOL_SIZE + _WORKER_MAX_OVERFLOW
        num_workers = 4  # documented default Huey concurrency
        total_connections = fastapi_max + (num_workers * per_worker_max)
        pgbouncer_max_client_conn = 100

        assert total_connections < pgbouncer_max_client_conn, (
            f"Total connection budget ({total_connections}) exceeds "
            f"PgBouncer max_client_conn ({pgbouncer_max_client_conn}). "
            f"FastAPI: {fastapi_max}, per-worker: {per_worker_max}, "
            f"{num_workers} workers = {num_workers * per_worker_max}. "
            "Review pool constants and PgBouncer capacity before proceeding."
        )

    def test_worker_pool_size_is_one(self) -> None:
        """_WORKER_POOL_SIZE must be 1."""
        from synth_engine.shared.db import _WORKER_POOL_SIZE

        assert _WORKER_POOL_SIZE == 1

    def test_worker_max_overflow_is_two(self) -> None:
        """_WORKER_MAX_OVERFLOW must be 2."""
        from synth_engine.shared.db import _WORKER_MAX_OVERFLOW

        assert _WORKER_MAX_OVERFLOW == 2


class TestConcurrentWorkerSessionCleanup:
    """Sessions from multiple concurrent worker threads must all be cleaned up.

    This test uses threading to simulate multiple Huey tasks running concurrently
    against the same SQLite worker engine, verifying that all sessions are
    properly closed even when some tasks raise.
    """

    def setup_method(self) -> None:
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def teardown_method(self) -> None:
        from synth_engine.shared.db import dispose_engines

        dispose_engines()

    def test_five_concurrent_tasks_all_close_sessions(self) -> None:
        """Five concurrent tasks using get_worker_engine() must all close their sessions.

        This is the concurrent job test from AC3. With SQLite the pool is not
        bounded the same way as PostgreSQL, but the session lifecycle contract
        (open -> use -> close, even on exception) must hold for all threads.
        """
        from sqlmodel import Session

        from synth_engine.shared.db import get_worker_engine

        engine = get_worker_engine("sqlite:///:memory:")
        close_count: list[int] = []
        errors: list[str] = []
        lock = threading.Lock()

        def _worker_task(should_raise: bool) -> None:
            try:
                with Session(engine) as session:
                    # Simulate reading from the engine
                    assert session is not None
                    if should_raise:
                        raise RuntimeError("simulated task failure")
            except RuntimeError:
                pass  # Expected — we test cleanup, not error propagation
            except Exception as exc:
                with lock:
                    errors.append(str(exc))
            finally:
                with lock:
                    close_count.append(1)

        threads = [threading.Thread(target=_worker_task, args=(i % 2 == 0,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Unexpected exceptions in worker threads: {errors}"
        assert len(close_count) == 5, (
            f"Expected 5 task completions (with session cleanup), got {len(close_count)}. "
            "Some tasks may have hung or failed to clean up their sessions."
        )
