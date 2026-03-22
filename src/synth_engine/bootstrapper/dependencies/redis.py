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

mTLS configuration (T46.2)
---------------------------
When ``MTLS_ENABLED=true``:

- The ``redis://`` URL scheme is promoted to ``rediss://`` (TLS scheme).
- ``ssl_certfile``, ``ssl_keyfile``, ``ssl_ca_certs``, and
  ``ssl_cert_reqs="required"`` are passed to ``redis.Redis.from_url()``
  for mutual TLS authentication.

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
Task: T46.2 — Wire mTLS on All Container-to-Container Connections
"""

from __future__ import annotations

import redis as redis_lib

from synth_engine.shared.settings import get_settings
from synth_engine.shared.task_queue import _promote_redis_url_to_tls

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

    When ``MTLS_ENABLED=true``, the URL scheme is promoted to ``rediss://``
    and TLS client certificate parameters are passed to the constructor.

    Returns:
        A ``redis.Redis`` client connected to the configured Redis URL.
    """
    global _client
    if _client is None:
        settings = get_settings()
        url = settings.redis_url

        tls_kwargs: dict[str, object] = {}
        if settings.mtls_enabled:
            url = _promote_redis_url_to_tls(url)
            tls_kwargs = {
                "ssl_certfile": settings.mtls_client_cert_path,
                "ssl_keyfile": settings.mtls_client_key_path,
                "ssl_ca_certs": settings.mtls_ca_cert_path,
                "ssl_cert_reqs": "required",
            }

        _client = redis_lib.Redis.from_url(url, **tls_kwargs)
    return _client
