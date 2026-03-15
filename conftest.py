"""Root conftest.py — applies collection-time warning filters.

This file is evaluated by pytest before any test file is imported.
Programmatic warning filters registered here take effect during collection,
unlike pyproject.toml [tool.pytest.ini_options].filterwarnings entries which
are applied only during test execution.

Task: P4-T4.2b — SDV/CTGAN integration requires collection-time suppression
of DeprecationWarnings from rdt's use of stdlib modules deprecated in
Python 3.14 (sre_parse, sre_constants, sre_compile).
"""

from __future__ import annotations

import warnings

# ---------------------------------------------------------------------------
# Suppress DeprecationWarnings from deprecated stdlib modules imported by rdt
# ---------------------------------------------------------------------------
# rdt 1.x (an SDV dependency) imports sre_parse, sre_constants, and sre_compile
# at module scope.  These stdlib modules are deprecated in Python 3.14 (PEP 594)
# for removal in Python 3.16.  The warnings fire during pytest *collection*
# (module import), before pyproject.toml filterwarnings entries take effect.
# We register the filters programmatically here so they apply at collection
# time.  These are third-party packages we cannot modify.
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
# Suppress DeprecationWarnings from pytest-asyncio 0.26.x event loop policy
# ---------------------------------------------------------------------------
# pytest-asyncio 0.26.x calls asyncio.get_event_loop_policy() and
# asyncio.set_event_loop_policy() during plugin setup — before pyproject.toml
# filterwarnings entries become active.  These APIs are deprecated in
# Python 3.14 (slated for removal in Python 3.16).  The calls are inside the
# pytest-asyncio plugin; we cannot modify them.  Registering the filter here
# (root conftest.py, evaluated before any plugin hooks) ensures the warning is
# suppressed before pytest-asyncio's event_loop_policy fixture fires.
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
