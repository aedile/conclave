# ADR-0044 — Webhook, Idempotency & Reaper Architecture

> **Amendment (Phase 56):** File paths updated to reflect synthesizer sub-package decomposition.

**Status:** Accepted
**Date:** 2026-03-21
**Deciders:** PM + Architecture Reviewer
**Task:** P45-T45.3 / P45-T45.4

---

## Context

Phase 45 delivers three features that were scaffolded in Phase 2, removed as
unwired dead code in Phase 32, and tracked as deferred items (TBD-01, TBD-07,
TBD-08) in `docs/backlog/deferred-items.md`:

1. **Idempotency Middleware** (TBD-07) — Redis-backed deduplication for
   retryable mutating requests.
2. **Orphan Task Reaper** (TBD-08) — Periodic sweeper that marks stale
   IN\_PROGRESS synthesis jobs as FAILED after a configurable staleness
   threshold.
3. **Webhook Callbacks** (TBD-01) — Push-to-external-endpoint notifications
   on synthesis job state transitions (COMPLETED, FAILED) with HMAC-SHA256
   signing and exponential-backoff retry.

All three features share the existing Redis and Huey infrastructure introduced
in Phases 2 and 20. This ADR documents the design decisions made to integrate
them cohesively while maintaining the modular monolith boundary constraints
(ADR-0001).

---

## Decision

### 1. Idempotency Middleware

**Key semantics**: The `Idempotency-Key` header value is used as-is as the
Redis key, prefixed with `idempotency:` to avoid namespace collisions with
other Redis keys. Only mutating HTTP methods (`POST`, `PUT`, `DELETE`) trigger
deduplication. `GET` and `HEAD` requests are always passed through.

**Atomic deduplication**: Redis `SET NX EX` provides atomic test-and-set
semantics. If the key does not exist, it is created and the request proceeds.
If it already exists, HTTP 409 is returned immediately without invoking the
handler. This is TOCTOU-safe: there is no window between the existence check
and the key creation.

**TTL**: Defaults to 300 seconds, configurable via
`ConclaveSettings.idempotency_ttl_seconds`. The TTL is set at key creation
time and is not renewed on read. After the TTL expires, the key is removed
and the same `Idempotency-Key` value can be used for a new request.

**Key length cap**: Keys exceeding 128 characters are rejected with HTTP 400
before any Redis interaction. This prevents Redis key size abuse and guards
against accidental hash collisions from truncation.

**Key release on handler exception**: If the request handler raises an
unhandled exception, the middleware deletes the idempotency key before
re-raising. This makes the request retryable: the client can retry with the
same `Idempotency-Key` value and the request will be treated as new.

**Graceful Redis-down degradation**: If Redis is unavailable at the time of
the deduplication check, the middleware logs a WARNING and passes the request
through without deduplication. The service remains available; only the
idempotency guarantee is suspended. This is the correct trade-off for a
feature that is defensive (not correctness-critical) infrastructure.

**Wiring**: `IdempotencyMiddleware` is registered in
`bootstrapper/middleware.py` using the shared Redis client from
`bootstrapper/dependencies/redis.py`. The shared Redis dependency reads
`ConclaveSettings.redis_url` so that idempotency and rate-limiting both
use a single configured Redis instance.

---

### 2. Orphan Task Reaper

**Staleness heuristic**: A synthesis job is considered stale if its
`status` is `IN_PROGRESS` and its `updated_at` timestamp is older than
`ConclaveSettings.reaper_stale_threshold_minutes` (default: 60 minutes).
The `updated_at` field is set by the Huey worker on every status transition,
so a job that has not transitioned in 60 minutes is almost certainly orphaned
(worker crashed before writing a terminal status).

**Why 60 minutes**: The longest observed synthesis job (1M rows, 4 tables)
completed in under 20 minutes in Phase 28 load testing. A 60-minute threshold
provides a 3x safety margin while still cleaning up orphans within the hour.
Operators with unusual workloads can increase the threshold via
`REAPER_STALE_THRESHOLD_MINUTES`.

**Repository abstraction**: `OrphanTaskReaper` depends on a `TaskRepository`
ABC rather than a concrete SQLAlchemy session. This keeps the reaper in
`shared/tasks/` (cross-cutting infrastructure) without importing SQLAlchemy
directly. The concrete `SQLAlchemyTaskRepository` in `shared/tasks/repository.py`
provides the database query using the existing async session factory.

**Periodic task registration**: The reaper is registered as
`@huey.periodic_task(crontab(minute='*/15'))` in
`modules/synthesizer/jobs/tasks.py`, co-located with the synthesis task definitions
that share the same Huey instance. Running every 15 minutes is sufficient for
60-minute staleness detection and adds negligible load (a single indexed query
per cycle).

**Per-task error isolation**: If marking one stale task FAILED raises an
exception (e.g., a transient DB connection error), the reaper logs the
exception and continues to the next stale task. This prevents a single
bad task record from blocking the entire sweep.

**Audit trail**: Each reaped task generates a `TASK_REAPED` audit event via
`shared/security/audit.py` with the job ID and reason
("Reaped: exceeded staleness threshold — possible worker crash"). This
satisfies the audit requirements in ADR-0010 (WORM Audit Logger) for all
system-initiated state transitions.

---

### 3. Webhook Callbacks

**Delivery guarantees**: Webhooks provide at-least-once delivery, not
exactly-once. The `X-Conclave-Delivery-Id` header (a UUID generated per
delivery attempt) allows receivers to implement idempotent processing. A
delivery is considered successful when the receiver returns HTTP 2xx within
a 10-second timeout.

**Retry policy**: Up to 3 delivery attempts per event. Backoff delays between
attempts are exponential: 1 second before the second attempt, 4 seconds before
the third. After 3 failures, the delivery record is marked FAILED in the
`webhook_delivery_log` table. No further retries occur; the operator can
inspect the delivery log and re-trigger manually if needed.

**Why 3 retries with 1s/4s/16s backoff**: The retry budget (total: ~21
seconds) is chosen to recover from transient receiver unavailability (brief
network blip, container restart) without indefinitely queuing deliveries or
blocking the Huey worker pool. Permanent failures (misconfigured URL, receiver
returning 4xx) surface within seconds rather than accumulating silently.

**HMAC signing**: Every delivery includes an `X-Conclave-Signature` header
containing `sha256=<hex>` where `<hex>` is the HMAC-SHA256 of the raw request
body using the registration's per-webhook signing key. Receivers MUST verify
this signature before processing the payload. The signing key is provided by
the operator at registration time and is stored encrypted (ALE) in the
`webhook_registration` table.

**Interaction with Huey/Redis**: Webhook delivery is executed as a Huey
background task (`@huey.task()`) dispatched immediately after a job state
transition to COMPLETED or FAILED in `jobs/job_orchestration.py`. This decouples
delivery latency from the synthesis job lifecycle: a slow or unavailable
receiver cannot block the synthesis worker. The Huey task is not idempotent
by design — if the worker crashes mid-delivery, the delivery may not be
retried. The 3-retry loop runs synchronously within the Huey task; each
attempt is recorded in `webhook_delivery_log` before the next retry.

**Authentication**: `POST /webhooks`, `GET /webhooks`, and
`DELETE /webhooks/{id}` all require JWT bearer authentication via
`get_current_operator()` (ADR-0039). Webhook registrations are scoped to
the creating operator via `owner_id` (ADR-0040).

**Rate limit**: A maximum of 10 active webhook registrations per operator is
enforced at `POST /webhooks`. This prevents resource exhaustion from unbounded
registration growth. The limit is checked against the `webhook_registration`
table at registration time.

---

## Consequences

**Positive:**
- All three TBD items (TBD-01, TBD-07, TBD-08) are closed. The deferred items
  ledger now has only TBD-03 (mTLS) remaining open.
- Idempotency middleware protects all mutating routes from duplicate request
  processing without requiring per-route logic changes.
- The orphan task reaper provides a safety net for worker crashes in all
  deployment topologies, not just multi-node.
- Webhook callbacks enable external system integration without requiring
  clients to hold long-lived SSE connections.

**Negative / Constraints:**
- Webhooks are at-least-once. Receivers must implement idempotent processing
  using `X-Conclave-Delivery-Id`.
- The 3-retry limit means a receiver that is unavailable for more than ~21
  seconds will miss the delivery. Operators requiring stronger guarantees
  should implement a polling fallback via `GET /jobs/{id}`.
- The reaper's 60-minute staleness threshold means orphaned jobs remain
  visible as IN\_PROGRESS for up to 75 minutes (60-minute threshold +
  15-minute sweep interval) before being marked FAILED.
- Redis-down degradation for idempotency suspends duplicate detection silently
  (logged at WARNING). Operators must monitor Redis availability to ensure
  the guarantee is active.

---

## Alternatives Considered

### Idempotency: Database-backed deduplication

A `idempotency_log` PostgreSQL table could replace the Redis key. This would
survive Redis restarts, providing stronger durability. Rejected because:
(a) idempotency keys have TTLs that PostgreSQL does not enforce natively
(would require periodic cleanup jobs), (b) Redis atomic `SET NX EX` is a
purpose-built primitive for this exact pattern, and (c) adding a DB write
on every mutating request increases write amplification on PostgreSQL. Redis
TTL-based expiry is the industry-standard approach (Stripe, Braintree).

### Reaper: Database-side scheduled trigger (pg\_cron)

PostgreSQL's `pg_cron` extension could run the staleness sweep without a
Huey task. Rejected because: (a) the deployment does not enable `pg_cron`
(it is not in the Docker image), (b) the reaper logic requires calling
`shared/security/audit.py` which is Python application code, and (c)
co-locating the reaper with other Huey periodic tasks keeps all scheduled
work in one observable place.

### Webhooks: Message queue (Redis Streams / Pub/Sub)

Delivering webhooks via Redis Streams would decouple delivery from the Huey
worker and allow replay. Rejected because: (a) the air-gapped deployment
already has Redis as infrastructure — adding a Streams consumer adds topology
complexity without proportional benefit, (b) the 3-retry/at-least-once model
satisfies the current integration requirements, and (c) Huey tasks are already
the project's durable background work primitive (ADR-0020). Promoting to
Redis Streams would be appropriate if webhook volume exceeded Huey's
throughput capacity.

---

## References

- ADR-0001: Modular Monolith Topology — boundary constraints
- ADR-0003: Redis Idempotency — original Phase 2 idempotency design
- ADR-0005: Orphan Task Reaper — original Phase 2 reaper design
- ADR-0010: WORM Audit Logger — audit trail requirements for reaped tasks
- ADR-0020: Huey Task Queue Singleton — periodic task registration
- ADR-0039: JWT Bearer Authentication — webhook endpoint authentication
- ADR-0040: IDOR Protection Ownership Model — webhook owner scoping
- `docs/backlog/deferred-items.md` — TBD-01, TBD-07, TBD-08 deferred item specs
- `src/synth_engine/shared/middleware/idempotency.py` — idempotency implementation
- `src/synth_engine/shared/tasks/reaper.py` — reaper implementation
- `src/synth_engine/shared/tasks/repository.py` — SQLAlchemy task repository
- `src/synth_engine/bootstrapper/routers/webhooks.py` — webhook CRUD endpoints
- `src/synth_engine/modules/synthesizer/jobs/webhook_delivery.py` — delivery engine
