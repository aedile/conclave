"""Pytest configuration and shared fixtures for the Conclave Engine test suite.

This module registers custom markers and scaffolds future DB fixtures.

Task: P1-T1.2 — TDD Framework
"""

from __future__ import annotations

import gc
import warnings
from collections.abc import Generator

import pytest


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
def _suppress_third_party_deprecation_warnings() -> Generator[None]:
    """Suppress known third-party warnings that cannot be fixed upstream.

    Background
    ----------
    pytest's ``-W error`` command-line flag is applied by ``apply_warning_filters``
    AFTER the pyproject.toml ``filterwarnings`` config entries.  Because
    ``warnings.filterwarnings()`` prepends to the filter chain, the cmdline ``-W
    error`` ends up at the TOP of the chain and overrides every ``"ignore"`` entry
    in pyproject.toml.

    The solution is to add the "ignore" filters INSIDE the per-test
    ``catch_warnings_for_item`` context — i.e., from a fixture body — so they are
    prepended AFTER ``-W error`` is already at the top, which puts the "ignore"
    entries ABOVE it and restores correct precedence.

    This fixture also calls ``gc.collect()`` in its teardown (after yield) to force
    GC of any short-lived SQLite engines before the test's ``catch_warnings_for_item``
    context exits.  Without this, SQLite engines created in helper functions (e.g.,
    ``_make_connections_app()``) are GC-collected after the test session ends — during
    ``pytest._ensure_unconfigure`` — where no ``ResourceWarning`` filter is active and
    ``PytestUnraisableExceptionWarning`` fires, causing a non-zero exit code despite
    all tests passing.

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

    Yields:
        None — this is a setup/teardown fixture with no yielded value.
    """
    # ---------------------------------------------------------------------------
    # rdt 1.x (SDV dependency): sre_parse / sre_constants / sre_compile imports
    # ---------------------------------------------------------------------------
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

    # ---------------------------------------------------------------------------
    # chromadb 1.5.x: asyncio.iscoroutinefunction at class-definition time
    # ---------------------------------------------------------------------------
    warnings.filterwarnings(
        "ignore",
        message="'asyncio.iscoroutinefunction' is deprecated",
        category=DeprecationWarning,
    )

    # ---------------------------------------------------------------------------
    # SQLite ResourceWarning: short-lived in-memory test engines
    # ---------------------------------------------------------------------------
    warnings.filterwarnings(
        "ignore",
        category=ResourceWarning,
    )

    yield

    # ---------------------------------------------------------------------------
    # Force GC after each test to collect short-lived SQLite engines NOW —
    # while the ResourceWarning filter above is still active in this context.
    # Without this, CPython defers collection to session teardown (outside our
    # filter scope), where PytestUnraisableExceptionWarning would fire.
    # ---------------------------------------------------------------------------
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
