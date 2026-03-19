"""Pytest configuration and shared fixtures for the Conclave Engine test suite.

This module registers custom markers and scaffolds future DB fixtures.

Task: P1-T1.2 — TDD Framework
Task: T36.1 — Add settings cache-clear autouse fixture
"""

from __future__ import annotations

import gc
import os
import warnings
from collections.abc import Generator

import pytest

#: Minimal test-safe defaults injected when not already set.
#: These prevent ``ConclaveSettings`` construction failures in tests that
#: exercise non-DB / non-audit code paths but still trigger ``get_settings()``.
_TEST_DATABASE_URL: str = "sqlite:///:memory:"
_TEST_AUDIT_KEY: str = "aa" * 32  # 64 hex chars = 32 bytes  # pragma: allowlist secret


def pytest_configure(config: pytest.Config) -> None:
    """Register custom pytest markers.

    Args:
        config: The active pytest configuration object.
    """
    config.addinivalue_line(
        "markers",
        "unit: Fast, isolated unit tests with no external dependencies",
    )
    config.addinivalue_line(
        "markers",
        "integration: Tests requiring live databases or external services (Task 2.2)",
    )


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Clear the get_settings() lru_cache and ensure minimal env vars are set.

    Tests that manipulate environment variables via ``monkeypatch.setenv``
    or ``monkeypatch.delenv`` must see their changes reflected when code
    calls ``get_settings()``.  Without this fixture the lru_cache would
    return a stale :class:`ConclaveSettings` instance from a previous test,
    causing flaky failures.

    This fixture also ensures that ``DATABASE_URL`` and ``AUDIT_KEY`` are
    set to test-safe defaults when not already present.  Many tests exercise
    code that incidentally calls ``get_settings()`` but does not care about
    the DB or audit key values.  Without the defaults, ``ConclaveSettings``
    raises ``ValidationError`` for required-but-absent fields.

    Tests that explicitly need to control ``DATABASE_URL`` or ``AUDIT_KEY``
    (e.g., ``test_settings.py``) use ``monkeypatch.setenv`` / ``delenv``
    which override these defaults within the test scope.

    Args:
        monkeypatch: The pytest monkeypatch fixture for reversible env manipulation.

    Yields:
        None — setup/teardown only.
    """
    # Clear cache before the test so monkeypatched env vars take effect.
    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass  # Module not yet created during test discovery

    # Inject test-safe defaults only if not already set.
    if not os.environ.get("DATABASE_URL"):
        monkeypatch.setenv("DATABASE_URL", _TEST_DATABASE_URL)
    if not os.environ.get("AUDIT_KEY"):
        monkeypatch.setenv("AUDIT_KEY", _TEST_AUDIT_KEY)

    yield

    # Clear cache after the test so the next test starts clean.
    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _suppress_third_party_deprecation_warnings() -> Generator[None]:
    """Suppress known third-party warnings that cannot be fixed upstream.

    Background
    ----------
    pytest's ``-W error`` command-line flag is applied by ``apply_warning_filters``
    AFTER the pyproject.toml ``filterwarnings`` config entries.  Because
    ``warnings.filterwarnings()`` prepends to the filter chain, the cmdline ``-W
    error`` ends up at the TOP of the chain and overrides every ``"ignore"`` entry
    in pyproject.toml.

    The solution is to add the "ignore" filters INSIDE a ``warnings.catch_warnings()``
    context manager per test, so they are prepended AFTER ``-W error`` is already at
    the top, which puts the "ignore" entries ABOVE it and restores correct precedence.
    The ``catch_warnings()`` context manager also guarantees that any mutations to the
    global filter chain are rolled back when the context exits — regardless of fixture
    scope.  This is safer than relying on pytest's internal ``catch_warnings_for_item``
    to restore the state.

    This fixture also calls ``gc.collect()`` in its teardown (after yield, but still
    inside the ``catch_warnings`` context) to force GC of any short-lived SQLite engines
    before the context exits.  Without this, SQLite engines created in helper functions
    (e.g., ``_make_connections_app()``) are GC-collected after the test session ends —
    during ``pytest._ensure_unconfigure`` — where no ``ResourceWarning`` filter is
    active and ``PytestUnraisableExceptionWarning`` fires, causing a non-zero exit code
    despite all tests passing.

    The warnings suppressed here are all from third-party packages we cannot modify:

    * ``rdt`` / ``sdv`` import chain: ``rdt.transformers.utils`` imports ``sre_parse``,
      ``sre_constants``, and ``sre_compile`` at module scope.  These stdlib modules
      are deprecated in Python 3.14 (PEP 594) for removal in 3.16.
    * ``chromadb`` telemetry: ``chromadb.telemetry.opentelemetry`` calls
      ``asyncio.iscoroutinefunction()`` at class-definition time.  This API is
      deprecated in Python 3.14 for removal in 3.16; the fix is
      ``inspect.iscoroutinefunction()``.
    * ``SQLite ResourceWarning``: SQLAlchemy in-memory engines used in unit-test
      helpers emit ``ResourceWarning`` when the engine is GC-collected without an
      explicit ``engine.dispose()`` call.  These are intentionally short-lived
      test engines.
    * ``opacus`` 1.5.x: emits ``UserWarning`` about secure RNG being disabled
      when ``secure_mode=False`` (the default for non-production experimentation).
      Cannot be changed in third-party code.
    * ``torch`` 2.10+: emits ``UserWarning`` from full backward hooks registered
      by Opacus when no input tensor requires gradients.  This is an Opacus
      internal implementation detail fired through ``torch.utils.hooks`` during
      DP-SGD backward passes. Cannot be changed in third-party code.

    Yields:
        None — this is a setup/teardown fixture with no yielded value.
    """
    with warnings.catch_warnings():
        # -----------------------------------------------------------------------
        # rdt 1.x (SDV dependency): sre_parse / sre_constants / sre_compile imports
        # -----------------------------------------------------------------------
        warnings.filterwarnings(
            "ignore",
            message="module 'sre_parse' is deprecated",
            category=DeprecationWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message="module 'sre_constants' is deprecated",
            category=DeprecationWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message="module 'sre_compile' is deprecated",
            category=DeprecationWarning,
        )

        # -----------------------------------------------------------------------
        # chromadb 1.5.x: asyncio.iscoroutinefunction at class-definition time
        # -----------------------------------------------------------------------
        warnings.filterwarnings(
            "ignore",
            message="'asyncio.iscoroutinefunction' is deprecated",
            category=DeprecationWarning,
        )

        # -----------------------------------------------------------------------
        # SQLite ResourceWarning: short-lived in-memory test engines
        # -----------------------------------------------------------------------
        warnings.filterwarnings(
            "ignore",
            category=ResourceWarning,
        )

        # -----------------------------------------------------------------------
        # opacus 1.5.x: secure RNG disabled advisory warning (non-production mode)
        # -----------------------------------------------------------------------
        warnings.filterwarnings(
            "ignore",
            message="Secure RNG turned off",
            category=UserWarning,
        )

        # -----------------------------------------------------------------------
        # torch 2.10+ / Opacus: full backward hook fires when no inputs require
        # gradients.  This is an Opacus internal detail; we cannot fix it upstream.
        # -----------------------------------------------------------------------
        warnings.filterwarnings(
            "ignore",
            message="Full backward hook is firing when gradients are computed",
            category=UserWarning,
        )

        yield

        # -----------------------------------------------------------------------
        # Force GC after each test to collect short-lived SQLite engines NOW —
        # while the ResourceWarning filter above is still active in this context.
        # Without this, CPython defers collection to session teardown (outside our
        # filter scope), where PytestUnraisableExceptionWarning would fire.
        # -----------------------------------------------------------------------
        gc.collect()


# ---------------------------------------------------------------------------
# DB rollback fixture scaffold — wired in Task 2.2
# ---------------------------------------------------------------------------
# When Task 2.2 initialises the PostgreSQL schema, replace this scaffold with:
#
#   @pytest.fixture(scope="function")
#   def db_session(postgresql):
#       """Yield a transactional DB session that rolls back after each test.
#
#       Args:
#           postgresql: pytest-postgresql fixture providing a live connection.
#
#       Yields:
#           A psycopg connection in an uncommitted transaction.
#       """
#       conn = postgresql
#       yield conn
#       conn.rollback()
#
# The `postgresql` fixture is supplied by pytest-postgresql (integration group).
# Activate by running: poetry install --with dev,integration
