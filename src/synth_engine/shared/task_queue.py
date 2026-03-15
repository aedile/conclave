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
"""

from __future__ import annotations

import logging
import os

from huey import Huey  # type: ignore[import-untyped]

_logger = logging.getLogger(__name__)

#: Environment variable that selects the Huey backend.
_HUEY_BACKEND_ENV: str = "HUEY_BACKEND"

#: Environment variable for the Redis connection URL.
_REDIS_URL_ENV: str = "REDIS_URL"

#: Default Redis URL used when REDIS_URL is not set.
_DEFAULT_REDIS_URL: str = "redis://redis:6379/0"

#: Environment variable to enable Huey immediate mode (synchronous execution).
_HUEY_IMMEDIATE_ENV: str = "HUEY_IMMEDIATE"


def _build_huey() -> Huey:
    """Build and return the shared Huey instance.

    Reads configuration from the environment at module import time.  The
    resulting instance is bound to the module-level ``huey`` name.

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
    backend = os.environ.get(_HUEY_BACKEND_ENV, "redis").lower().strip()
    immediate_raw = os.environ.get(_HUEY_IMMEDIATE_ENV, "").strip().lower()
    immediate = immediate_raw in {"1", "true", "yes"}

    if backend == "memory":
        from huey import MemoryHuey

        _logger.info("Huey: using MemoryHuey (HUEY_BACKEND=memory).")
        return MemoryHuey(name="conclave-engine", immediate=immediate)

    # Default: Redis backend
    from huey import RedisHuey

    redis_url = os.environ.get(_REDIS_URL_ENV, _DEFAULT_REDIS_URL)
    _logger.info(
        "Huey: using RedisHuey (url=%s, immediate=%s).",
        redis_url,
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
