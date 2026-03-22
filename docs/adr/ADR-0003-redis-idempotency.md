# ADR-0003: Redis SET NX EX for Atomic Idempotency Key Management

**Status:** Accepted — Implemented (T45.1, Phase 45)
**Date:** 2026-03-13
**Deciders:** Project team

## Context

The Conclave Engine API must protect mutating endpoints (POST, PATCH, PUT) from
duplicate submissions — a common failure mode in unreliable networks or when
clients retry after a timeout.  The idempotency mechanism requires that the
first request for a given key is processed exactly once; subsequent requests
with the same key within the TTL window are rejected with HTTP 409 Conflict.

A naive implementation would perform two separate Redis operations:
1. `GET key` — check whether the key exists.
2. `SETEX key ttl value` — store the key if it did not exist.

This two-step pattern introduces a Time-Of-Check / Time-Of-Use (TOCTOU) race
condition: two concurrent requests with the same key can both observe the
key as absent in step 1, then both proceed to step 2, resulting in duplicate
processing.

## Decision

Use a single atomic `SET key value NX EX ttl` call for all idempotency key
operations:

- `NX` — only set the key if it does **not** already exist.
- `EX ttl` — set the key's expiry atomically in the same command.
- Return value: `True` if the key was freshly set (new request); `None` if
  the key already existed (duplicate request).

Additional constraints applied to the implementation:

- **Async-only:** The middleware uses `redis.asyncio.Redis` (aioredis) so that
  all Redis I/O is non-blocking inside FastAPI's async event loop.
- **Error degradation:** If Redis raises `RedisError` (connection lost, timeout),
  the middleware logs a warning and passes the request through rather than
  returning 500 or blocking service.
- **Key length cap:** Keys exceeding 128 characters are rejected with HTTP 400
  before any Redis interaction to prevent memory bloat.
- **Key namespace:** All keys are stored under the `idempotency:` prefix to
  prevent collisions with other Redis consumers on the same instance.
- **Retry safety:** If the downstream handler raises an exception, the middleware
  deletes the key (best-effort) so the caller can retry with the same key.

## Consequences

- **Positive:** Eliminates the TOCTOU race condition inherent in check-then-set
  patterns without requiring Lua scripts or distributed locks.
- **Positive:** A single round-trip to Redis per request reduces latency compared
  to the two-call pattern.
- **Positive:** Degraded-mode pass-through keeps the API available when Redis is
  temporarily unreachable, consistent with the air-gapped BYOC deployment model
  where Redis may be cold-started.
- **Negative:** If Redis is unavailable, idempotency is not enforced during the
  outage window.  Operators must monitor Redis health to detect this condition.
- **Negative:** The 128-character key length cap may require client adjustment if
  existing keys are longer (unlikely for UUIDs and short tokens).

---

## Amendment — T32.1 (Phase 32)

Implementation removed in T32.1 as unwired scaffolding — the module was defined but never
wired into the application (zero call sites in `bootstrapper/`). The design decision remains
sound and will be re-implemented when the trigger condition is met.

See `docs/backlog/deferred-items.md` TBD-07 for acceptance criteria and trigger condition.

---

## Amendment — T45.1 (Phase 45)

Idempotency middleware re-implemented in T45.1 (`shared/middleware/idempotency.py`)
with the following enhancements over the original T32.1-deferred spec:

- **Per-operator key scoping**: Redis key format is
  `idempotency:{operator_id}:{user_key}`.  The operator ID is extracted from
  the JWT `sub` claim (without signature verification — auth middleware performs
  the authoritative check upstream).  When no JWT is present, defaults to
  `"anonymous"`.
- **Sync Redis client**: Uses the synchronous `redis.Redis` (not `redis.asyncio`)
  because `BaseHTTPMiddleware` runs in a thread pool context.  The ADR-0003
  "Async-only" constraint was superseded by this architecture constraint.
- **Status**: The "Deferred" qualifier is removed — the implementation is now
  active and wired in `bootstrapper/middleware.py`.
- **Settings**: `ConclaveSettings.idempotency_ttl_seconds` (default 300) controls
  the Redis key TTL.  See `.env.example` for documentation.
