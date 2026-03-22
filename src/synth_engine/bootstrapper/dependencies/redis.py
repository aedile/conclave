"""Shared Redis client singleton for the Conclave Engine bootstrapper.

Provides a single ``redis.Redis`` client instance constructed from
``ConclaveSettings.redis_url``.  The same Redis connection is reused by:
- Huey task queue (manages its own connection pool via ``huey.backends.RedisHuey``)
- Idempotency middleware (``shared/middleware/idempotency.py``)

Key isolation
-------------
Different consumers use distinct key prefixes to avoid collisions:
- Huey: ``huey.*`` prefix (managed internally by Huey)
- Idempotency: ``idempotency:{operator_id}:{user_key}`` prefix

Connection error handling
--------------------------
``get_redis_client()`` does NOT verify connectivity at construction time
(no ``PING`` call).  This avoids blocking startup if Redis is temporarily
unavailable; the idempotency middleware already degrades gracefully on
``redis.RedisError``.

Thread safety
-------------
``redis.Redis`` is thread-safe.  The singleton pattern (module-level
``_client`` caching) is safe in a single-process deployment.  Do NOT use
the async ``redis.asyncio`` client from this module — ``BaseHTTPMiddleware``
runs in a thread pool and requires the sync client.

CONSTITUTION Priority 0: Security — no credentials logged
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T45.1 — Reintroduce Idempotency Middleware (TBD-07)
"""

from __future__ import annotations

import redis as redis_lib

from synth_engine.shared.settings import get_settings

#: Module-level singleton.  Initialized on first call to ``get_redis_client()``.
#: redis.Redis is typed without type parameters as the installed stubs do not
#: support generic subscripting for this version (redis-py 5.x).
_client: redis_lib.Redis | None = None


def get_redis_client() -> redis_lib.Redis:
    """Return the singleton sync Redis client.

    Constructs the client from ``ConclaveSettings.redis_url`` on first call
    and caches it for subsequent calls.  Connection errors surface at
    operation time (not here) so that the application can start even when
    Redis is temporarily unavailable.

    Returns:
        A ``redis.Redis`` client connected to the configured Redis URL.
    """
    global _client
    if _client is None:
        settings = get_settings()
        _client = redis_lib.Redis.from_url(settings.redis_url)
    return _client
