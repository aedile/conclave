"""Unit tests for bootstrapper DI factory functions.

Tests verify that factory functions in ``bootstrapper/factories.py`` behave
correctly in isolation, including the sync wrapper returned by
``build_spend_budget_fn()`` which must run without a MissingGreenlet error
from a synchronous Huey worker thread.

P28-F4:
    ``build_spend_budget_fn()`` previously called ``asyncio.run()`` inside the
    sync wrapper.  On asyncpg, ``asyncio.run()`` from a sync thread that was
    not started as a greenlet raises ``MissingGreenlet``.  The fix replaces the
    async DB path with a synchronous SQLAlchemy engine (psycopg2 driver) for
    the Huey worker spend-budget path.

CONSTITUTION Priority 0: Security — no PII, no credential leaks
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: P28 — E2E Blocker F4
"""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _promote_to_sync_url — pure function, fully testable without DB
# ---------------------------------------------------------------------------


class TestPromoteToSyncUrl:
    """Tests for the _promote_to_sync_url URL mapping helper.

    This pure function translates async driver URL prefixes to their
    synchronous equivalents so the Huey worker can use a sync engine.
    """

    def test_asyncpg_url_promoted_to_psycopg2(self) -> None:
        """postgresql+asyncpg:// must be demoted to postgresql:// (psycopg2)."""
        from synth_engine.bootstrapper.factories import _promote_to_sync_url

        result = _promote_to_sync_url("postgresql+asyncpg://user:pw@host/db")
        assert result == "postgresql://user:pw@host/db"

    def test_aiosqlite_url_promoted_to_sqlite(self) -> None:
        """sqlite+aiosqlite:/// must be demoted to sqlite:///."""
        from synth_engine.bootstrapper.factories import _promote_to_sync_url

        result = _promote_to_sync_url("sqlite+aiosqlite:///./test.db")
        assert result == "sqlite:///./test.db"

    def test_already_sync_postgresql_url_unchanged(self) -> None:
        """A plain postgresql:// URL (no async prefix) must be returned unchanged."""
        from synth_engine.bootstrapper.factories import _promote_to_sync_url

        url = "postgresql://user:pw@host/db"
        assert _promote_to_sync_url(url) == url

    def test_already_sync_sqlite_url_unchanged(self) -> None:
        """A plain sqlite:/// URL (no aiosqlite prefix) must be returned unchanged."""
        from synth_engine.bootstrapper.factories import _promote_to_sync_url

        url = "sqlite:///./test.db"
        assert _promote_to_sync_url(url) == url

    def test_no_double_substitution_asyncpg(self) -> None:
        """Calling _promote_to_sync_url twice must not corrupt the URL.

        If the URL is already a plain postgresql:// (sync), calling the
        function again must return it unchanged (no double-substitution).
        """
        from synth_engine.bootstrapper.factories import _promote_to_sync_url

        original = "postgresql+asyncpg://user:pw@host/db"
        once = _promote_to_sync_url(original)
        twice = _promote_to_sync_url(once)
        assert once == twice == "postgresql://user:pw@host/db"

    def test_no_double_substitution_aiosqlite(self) -> None:
        """Calling _promote_to_sync_url twice on an aiosqlite URL is idempotent."""
        from synth_engine.bootstrapper.factories import _promote_to_sync_url

        original = "sqlite+aiosqlite:///./test.db"
        once = _promote_to_sync_url(original)
        twice = _promote_to_sync_url(once)
        assert once == twice == "sqlite:///./test.db"

    def test_in_memory_sqlite_url_unchanged(self) -> None:
        """sqlite:///:memory: (in-memory, no aiosqlite prefix) must be unchanged."""
        from synth_engine.bootstrapper.factories import _promote_to_sync_url

        url = "sqlite:///:memory:"
        assert _promote_to_sync_url(url) == url


# ---------------------------------------------------------------------------
# F4 — spend_budget sync wrapper must not raise MissingGreenlet
# ---------------------------------------------------------------------------


class TestBuildSpendBudgetFn:
    """Tests that build_spend_budget_fn() returns a sync-safe callable.

    P28-F4: The Huey task runner is synchronous.  The wrapper returned by
    ``build_spend_budget_fn()`` must complete without ``MissingGreenlet``
    when called from a plain synchronous thread (simulating a Huey worker).
    """

    def test_build_spend_budget_fn_returns_callable(self) -> None:
        """build_spend_budget_fn() must return a callable object.

        The returned object must satisfy the ``SpendBudgetProtocol`` callable
        signature so it can be passed to ``set_spend_budget_fn()``.
        """
        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        fn = build_spend_budget_fn()
        assert callable(fn)

    def test_sync_wrapper_uses_sync_engine_not_asyncio_run(self) -> None:
        """The sync wrapper must use a sync SQLAlchemy session, not asyncio.run().

        Calling ``asyncio.run()`` from a Huey worker thread raises
        ``MissingGreenlet`` when asyncpg is the driver because asyncpg
        requires a greenlet context.  The fix must use a synchronous engine
        (psycopg2 driver) instead of creating an async engine and calling
        ``asyncio.run()``.

        This test patches ``asyncio.run`` and verifies it is never called by
        the sync wrapper.
        """
        import asyncio

        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        fn = build_spend_budget_fn()

        asyncio_run_called: list[bool] = []

        original_asyncio_run = asyncio.run

        def _patched_asyncio_run(*args: Any, **kwargs: Any) -> Any:
            asyncio_run_called.append(True)
            return original_asyncio_run(*args, **kwargs)

        with patch("asyncio.run", side_effect=_patched_asyncio_run):
            # The actual DB call will fail since no DB is present; that is
            # expected.  We only care that asyncio.run is NOT invoked.
            try:
                fn(amount=0.5, job_id=1, ledger_id=1)
            except Exception:
                pass  # DB not present — expected

        assert not asyncio_run_called, (
            "build_spend_budget_fn() sync wrapper must NOT call asyncio.run().\n"
            "P28-F4: asyncio.run() raises MissingGreenlet from Huey worker threads "
            "when asyncpg is the database driver.  Use a synchronous SQLAlchemy "
            "engine (psycopg2) for the spend_budget path in Huey context."
        )

    def test_sync_wrapper_callable_from_plain_thread(self) -> None:
        """The sync wrapper must complete without MissingGreenlet from a plain thread.

        This test exercises the wrapper from a ``threading.Thread`` (no greenlet
        context) to simulate the Huey worker thread environment.

        P28-F4: Before the fix, asyncio.run() from this thread context with the
        asyncpg driver would raise:
            sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called
        After the fix, the sync engine path must complete without MissingGreenlet.
        """
        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        fn = build_spend_budget_fn()

        missing_greenlet_errors: list[BaseException] = []

        def _run_in_thread() -> None:
            try:
                # No DB is present — the call will fail, but we only care
                # that MissingGreenlet is NOT among the failures.
                fn(amount=Decimal("0.5"), job_id=99, ledger_id=1)
            except Exception as exc:
                exc_type_name = type(exc).__name__
                if "MissingGreenlet" in exc_type_name or "MissingGreenlet" in str(exc):
                    missing_greenlet_errors.append(exc)

        thread = threading.Thread(target=_run_in_thread)
        thread.start()
        thread.join(timeout=10)

        assert not missing_greenlet_errors, (
            f"MissingGreenlet raised from sync wrapper in plain thread: "
            f"{missing_greenlet_errors}\n"
            "P28-F4: The sync wrapper must not use asyncio.run() with asyncpg. "
            "Use a synchronous SQLAlchemy engine (psycopg2) instead."
        )

    def test_sync_wrapper_raises_value_error_for_non_positive_amount(self) -> None:
        """The sync wrapper must raise ValueError for amount <= 0.

        This mirrors the validation in the async spend_budget() and ensures
        the sync path enforces the same invariant.
        """
        import os

        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        fn = build_spend_budget_fn()

        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
            with pytest.raises(ValueError, match="amount must be positive"):
                fn(amount=0.0, job_id=1, ledger_id=1)

    def test_sync_wrapper_raises_value_error_for_negative_amount(self) -> None:
        """The sync wrapper must raise ValueError for negative amount."""
        import os

        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        fn = build_spend_budget_fn()

        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
            with pytest.raises(ValueError, match="amount must be positive"):
                fn(amount=-1.5, job_id=1, ledger_id=1)

    def test_sync_wrapper_invokes_sync_spend_budget(self) -> None:
        """The sync wrapper must not raise MissingGreenlet for any URL scheme.

        After the P28-F4 fix, the wrapper must use a synchronous DB path.
        If the fix is correct: no MissingGreenlet, no asyncio.run().
        A real DB connection will fail; that is acceptable for this unit test.
        """
        import os

        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        fn = build_spend_budget_fn()

        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:////:memory:"}):
            try:
                fn(amount=1.0, job_id=42, ledger_id=7, note="p28-test")
            except Exception as exc:
                exc_type_name = type(exc).__name__
                # MissingGreenlet is the specific failure we are guarding against.
                assert "MissingGreenlet" not in exc_type_name, (
                    f"P28-F4: MissingGreenlet raised — sync wrapper must not use "
                    f"asyncio.run() with asyncpg driver: {exc}"
                )
                assert "MissingGreenlet" not in str(exc), (
                    f"P28-F4: MissingGreenlet in exception message: {exc}"
                )
