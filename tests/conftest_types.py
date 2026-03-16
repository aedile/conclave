"""Shared type aliases for the test suite.

This module centralises type aliases that eliminate ``# type: ignore[valid-type]``
suppressions caused by third-party libraries that expose no exported runtime type.

Usage
-----
Import ``PostgreSQLProc`` instead of annotating with ``factories.postgresql_proc``:

.. code-block:: python

    from tests.conftest_types import PostgreSQLProc

    def _create_database(proc: PostgreSQLProc) -> None:
        ...

Background
----------
``pytest-postgresql``'s ``factories.postgresql_proc`` is a **factory function**,
not a type.  When used as a type annotation it triggers mypy ``[valid-type]``
errors.  The correct annotation for the fixture value is
``pytest_postgresql.executor.PostgreSQLExecutor``, which is the concrete class
that the factory-produced fixture injects into test functions.

Task: P18-T18.1 — Type Ignore Suppression Audit & Reduction
"""

from __future__ import annotations

from pytest_postgresql.executor import PostgreSQLExecutor

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

#: Type alias for a ``pytest-postgresql`` process executor fixture.
#: Use this instead of ``factories.postgresql_proc`` as a type annotation to
#: avoid ``mypy [valid-type]`` errors.
PostgreSQLProc = PostgreSQLExecutor

__all__ = ["PostgreSQLProc"]
