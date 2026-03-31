"""Rate limiting backend: Redis counter and in-memory fallback implementations.

Provides the low-level counting primitives used by
:class:`~synth_engine.bootstrapper.dependencies.rate_limit_middleware.RateLimitGateMiddleware`.
Both functions are pure (no global state) and accept injected dependencies so
they are trivially testable in isolation.

Redis key format: ``ratelimit:{window_seconds}:{identity_key}``
The ``ratelimit:`` prefix isolates rate-limit keys from:
- Idempotency middleware (``idempotency:`` prefix)
- Huey task queue (``huey.`` prefix)

CONSTITUTION Priority 0: Security — Redis backend is a DoS mitigation primitive.
Task: T48.1 — Redis-Backed Rate Limiting
Task: T63.3 — Rate Limiter Fail-Closed on Redis Failure
Task: T64.3 — Decompose rate_limit.py
"""

from __future__ import annotations

import redis as redis_lib
from limits import RateLimitItem
from limits.strategies import FixedWindowRateLimiter
from prometheus_client import Counter

#: Redis key prefix that isolates rate limit keys from other middleware keys.
#: - Idempotency middleware uses 'idempotency:' prefix
#: - Huey task queue uses 'huey.' prefix
#: This ensures no collision between middleware namespaces (T48.1 attack mitigation).
_REDIS_KEY_PREFIX: str = "ratelimit:"

# Window duration is externalized to ConclaveSettings.conclave_rate_limit_window_seconds (T74.2).
# Read via get_settings() inside _redis_hit() to allow runtime configuration via env vars.
# IMPORTANT: changing CONCLAVE_RATE_LIMIT_WINDOW_SECONDS requires an application restart —
# the window is baked into the Redis key format at startup.

#: Prometheus counter for Redis fallback events.
#: Label 'tier' identifies which rate limit tier triggered the fallback.
#: Values: 'unseal', 'auth', 'download', 'general' (NOT raw request path).
#: This prevents unbounded label cardinality from path parameters.
RATE_LIMIT_REDIS_FALLBACK_TOTAL: Counter = Counter(
    "rate_limit_redis_fallback_total",
    "Total number of rate limit requests that fell back to in-memory counting "
    "due to Redis unavailability (T63.3).",
    ["tier"],
)

# ADV-P63-04 — Pre-initialize all known tier labels so Prometheus exports zero-
# valued time series from the first scrape, enabling threshold alerting without
# waiting for the first fallback event to occur.
for _tier in ("unseal", "auth", "download", "general"):
    RATE_LIMIT_REDIS_FALLBACK_TOTAL.labels(tier=_tier)

__all__ = [
    "RATE_LIMIT_REDIS_FALLBACK_TOTAL",
    "_REDIS_KEY_PREFIX",
    "_memory_hit",
    "_redis_hit",
]


def _redis_hit(
    redis_client: redis_lib.Redis,
    limit_str: str,
    identity_key: str,
) -> tuple[int, bool]:
    """Atomically increment the Redis counter and check the limit.

    Uses a Redis pipeline to issue ``INCR`` and ``EXPIRE`` as a single
    atomic batch, preventing the scenario where a key exists without a TTL
    (which would permanently block the identity).  Any ``redis.RedisError``
    from the pipeline propagates to the caller for graceful degradation
    handling in the middleware dispatch.

    Redis key format: ``ratelimit:{window_seconds}:{identity_key}``

    Args:
        redis_client: Synchronous Redis client to use for the pipeline.
        limit_str: Rate limit string in ``N/period`` format (e.g.
            ``"5/minute"``).  Used to derive the limit count and key.
        identity_key: The identity bucket (e.g. ``"ip:10.0.0.1"`` or
            ``"op:operator-123"``).

    Returns:
        A tuple of ``(count, allowed)`` where ``count`` is the current
        request count in the window and ``allowed`` is ``True`` when
        ``count <= limit``.
    """
    # Parse limit count from "N/period" format (e.g. "5/minute" -> limit=5)
    limit_count = int(limit_str.split("/")[0])
    from synth_engine.shared.settings import get_settings

    window_seconds = get_settings().conclave_rate_limit_window_seconds
    redis_key = f"{_REDIS_KEY_PREFIX}{window_seconds}:{identity_key}"

    with redis_client.pipeline() as pipe:
        pipe.incr(redis_key)
        pipe.expire(redis_key, window_seconds)
        results = pipe.execute()

    count: int = int(results[0])
    allowed: bool = count <= limit_count
    return count, allowed


def _memory_hit(
    fallback_limiter: FixedWindowRateLimiter,
    limit: RateLimitItem,
    key: str,
) -> tuple[int, bool]:
    """Increment the in-memory fallback counter and check the limit.

    Used when Redis is unavailable (grace period or fail-open mode).
    The ``limits`` library's ``FixedWindowRateLimiter.hit()`` increments
    the in-memory counter atomically and returns whether the request is
    within the configured limit.

    Args:
        fallback_limiter: In-memory fixed-window rate limiter instance.
        limit: The rate limit item (``RateLimitItem``) whose window to
            increment.
        key: The rate limit bucket key (e.g. ``"ip:10.0.0.1"``).

    Returns:
        A tuple of ``(count, allowed)`` where ``count`` is a proxy value
        (``limit.amount`` when denied, ``0`` otherwise — exact in-memory
        counts are not exposed by the limits library) and ``allowed`` is
        ``True`` when the request is within the in-memory limit.
    """
    allowed: bool = fallback_limiter.hit(limit, key)
    count: int = limit.amount if not allowed else 0
    return count, allowed
