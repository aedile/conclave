"""Pytest configuration and shared fixtures for the Conclave Engine test suite.

This module registers custom markers and scaffolds future DB fixtures.

Task: P1-T1.2 — TDD Framework
"""

from __future__ import annotations

import warnings

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register custom pytest markers and configure warning filters.

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

    # ---------------------------------------------------------------------------
    # Warning suppression for third-party stdlib-deprecated imports
    # ---------------------------------------------------------------------------
    # rdt 1.x (an SDV dependency) imports sre_parse, sre_constants, and
    # sre_compile at module scope.  These stdlib modules are deprecated in
    # Python 3.14 (PEP 594) for removal in 3.16.  The warnings fire during
    # pytest collection — before pyproject.toml filterwarnings take effect —
    # so we register the filters here via warnings.filterwarnings() instead.
    # These are third-party packages we cannot modify.
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
    # Warning suppression for pytest-asyncio 0.26.x event loop policy
    # ---------------------------------------------------------------------------
    # pytest-asyncio 0.26.x calls asyncio.get_event_loop_policy() and
    # asyncio.set_event_loop_policy() during test setup — before pyproject.toml
    # filterwarnings become active.  These APIs are deprecated in Python 3.14
    # (slated for removal in Python 3.16) and fire DeprecationWarning from
    # inside asyncio.events.  The calls live inside the pytest-asyncio plugin;
    # we cannot fix them.  The filter is registered here (pytest_configure) so
    # it is active before any test setup runs.
    warnings.filterwarnings(
        "ignore",
        message="'asyncio.get_event_loop_policy' is deprecated",
        category=DeprecationWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message="'asyncio.set_event_loop_policy' is deprecated",
        category=DeprecationWarning,
    )


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
