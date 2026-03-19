"""Shared Huey task queue instance for the Conclave Engine.

This module holds the single shared :class:`huey.Huey` instance used by all
background task definitions in the engine.  It is imported by task modules
(e.g., ``modules/synthesizer/tasks.py``) which decorate functions with
``@huey.task()``.

Configuration strategy
----------------------
The Huey backend is selected via the ``HUEY_BACKEND`` environment variable:

- ``redis`` (default): Production Redis-backed queue.  Connection URL is read
  from ``REDIS_URL`` (default: ``redis://redis:6379/0``).
- ``memory``: In-process ``MemoryHuey`` for unit tests and local development
  that does not require a running Redis instance.

The ``immediate`` mode flag is enabled when ``HUEY_IMMEDIATE=true`` is set
in the environment.  In immediate mode Huey executes tasks synchronously in
the enqueuing process — this is the recommended setting for integration tests
that exercise task logic without a Huey worker.

Boundary constraints (import-linter enforced):
    - This module must NOT import from ``modules/`` or ``bootstrapper/``.

Task: P4-T4.2c — Huey Task Wiring & Checkpointing
T2.1 context: Huey was specified as the task queue in the Phase 2 bootstrapper
spec.  This module provides the singleton Huey instance that T4.2c tasks use.
Task: T36.1 — Centralize Configuration Into Pydantic Settings Model
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse, urlunparse

from huey import (  # huey: import suppressed via pyproject.toml [[tool.mypy.overrides]]
    Huey,
)

from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)


def _mask_redis_url(redis_url: str) -> str:
    """Return a safe version of ``redis_url`` with auth material removed.

    Strips the ``username:password@`` authority component so that embedded
    credentials are never emitted to log files.

    Args:
        redis_url: A Redis connection URL that may contain embedded credentials,
            e.g. ``redis://:password@redis:6379/0``.

    Returns:
        A URL with only ``hostname:port`` in the netloc, e.g.
        ``redis://redis:6379/0``.
    """
    parsed = urlparse(redis_url)
    safe_netloc = f"{parsed.hostname}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=safe_netloc))


def _build_huey() -> Huey:
    """Build and return the shared Huey instance.

    Reads configuration from :func:`get_settings` at module import time.
    The resulting instance is bound to the module-level ``huey`` name.

    Backend selection:
      - ``HUEY_BACKEND=redis`` (default): ``RedisHuey`` connected to
        ``REDIS_URL`` (default: ``redis://redis:6379/0``).
      - ``HUEY_BACKEND=memory``: ``MemoryHuey`` — no Redis required.

    Immediate mode:
      - ``HUEY_IMMEDIATE=true``: Tasks execute synchronously in the calling
        process.  Recommended for integration tests.

    Returns:
        A configured Huey instance (``RedisHuey`` or ``MemoryHuey``).
    """
    settings = get_settings()
    backend = settings.huey_backend.lower().strip()
    immediate = settings.huey_immediate

    if backend == "memory":
        from huey import MemoryHuey

        _logger.info("Huey: using MemoryHuey (HUEY_BACKEND=memory).")
        return MemoryHuey(name="conclave-engine", immediate=immediate)

    # Default: Redis backend
    from huey import RedisHuey

    redis_url = settings.redis_url
    safe_url = _mask_redis_url(redis_url)
    _logger.info(
        "Huey: using RedisHuey (url=%s, immediate=%s).",
        safe_url,
        immediate,
    )
    return RedisHuey(
        name="conclave-engine",
        url=redis_url,
        immediate=immediate,
    )


#: Shared Huey instance.  Import this name in task modules:
#:
#:     from synth_engine.shared.task_queue import huey
#:
#:     @huey.task()
#:     def my_task(...) -> None:
#:         ...
huey = _build_huey()
