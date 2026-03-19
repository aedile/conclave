# Phase 45 — Webhook Callbacks, Idempotency Middleware & Orphan Task Reaper

**Goal**: Deliver the three remaining deferred items (TBD-01, TBD-07, TBD-08)
that were scaffolded in Phase 2, removed as unwired dead code in Phase 32, and
tracked in `docs/backlog/deferred-items.md`. Each task builds its prerequisite
infrastructure first, then implements and wires the feature.

**Prerequisite**: Phase 44 merged. Zero open advisories.

**ADR**: ADR-0041 — Webhook, Idempotency & Reaper Architecture (new, required).
Must document: webhook delivery guarantees, idempotency key semantics, reaper
staleness heuristics, and interaction with existing Huey/Redis infrastructure.

**Source**: Deferred Items (ADR-0029 Gap Analysis) — TBD-01, TBD-07, TBD-08.

---

## T45.1 — Reintroduce Idempotency Middleware (TBD-07)

**Priority**: P1 — Defensive infrastructure. Prevents duplicate job creation
from retried requests, even in single-client deployments.

### Context & Constraints

1. `shared/middleware/idempotency.py` was scaffolded in Phase 2 (P2-T2.1) as a
   Redis-backed deduplication middleware using atomic `SET NX EX`. It was removed
   in Phase 32 (T32.1) as unwired dead code. The original implementation can be
   restored from git history (commit range P2-T2.1) or reimplemented.

2. **Prerequisite task**: Extract a shared Redis client dependency from the Huey
   task queue configuration. Currently, Redis is configured only for Huey in
   `bootstrapper/factories.py`. The idempotency middleware needs its own Redis
   access via `ConclaveSettings` without duplicating connection configuration.

3. The middleware intercepts mutating requests (`POST`, `PUT`, `DELETE`) that
   include an `Idempotency-Key` header. If the key exists in Redis, return the
   cached response (HTTP 409 or the original response). If not, execute the
   handler and cache the response.

4. Configurable TTL via `ConclaveSettings`: `idempotency_ttl_seconds: int = 300`.

5. Graceful Redis-down degradation: log WARNING and pass through (no service
   block). The system must not become unavailable because Redis is unreachable.

6. Key length cap: reject keys exceeding 128 characters with HTTP 400.

7. Key release on handler exception: if the handler raises, delete the Redis key
   so the client can retry.

### Acceptance Criteria

1. `shared/middleware/idempotency.py` reintroduced with `IdempotencyMiddleware`.
2. Wired in `bootstrapper/middleware.py` ASGI stack.
3. Shared Redis client extracted as a `ConclaveSettings`-configured dependency.
4. `Idempotency-Key` header triggers deduplication on mutating requests.
5. HTTP 409 for duplicate requests within TTL window.
6. HTTP 400 for keys exceeding 128 characters.
7. Graceful degradation when Redis is down (WARNING log, pass-through).
8. Key released on handler exception (retryable).
9. TTL configurable via `ConclaveSettings`.
10. Unit tests: duplicate detection, pass-through, key length cap, Redis-down
    mode, key release on exception.
11. Integration test: duplicate POST with real Redis returns 409.
12. Full gate suite passes.

### Files to Create/Modify

- Create: `src/synth_engine/shared/middleware/idempotency.py`
- Create: `src/synth_engine/bootstrapper/dependencies/redis.py` (shared client)
- Modify: `src/synth_engine/bootstrapper/middleware.py` (wire middleware)
- Modify: `src/synth_engine/shared/settings.py` (idempotency + Redis settings)
- Create: `tests/unit/test_idempotency.py`
- Create: `tests/integration/test_idempotency_redis.py`

---

## T45.2 — Reintroduce Orphan Task Reaper (TBD-08)

**Priority**: P1 — Defensive infrastructure. Prevents stale IN_PROGRESS jobs
from accumulating after process crashes (SIGKILL, OOM kill).

### Context & Constraints

1. `shared/tasks/reaper.py` was scaffolded in Phase 2 (P2-T2.1) as an
   `OrphanTaskReaper` that detects IN_PROGRESS tasks exceeding a staleness
   threshold and marks them FAILED. Removed in Phase 32 (T32.1).

2. **Prerequisite task**: Implement `SQLAlchemyTaskRepository` — a concrete
   implementation of the `TaskRepository` ABC that queries `synthesis_job` table
   for stale IN_PROGRESS records using the existing async SQLAlchemy session.

3. The reaper must be registered as a Huey periodic task:
   `@huey.periodic_task(crontab(minute='*/15'))`.

4. Configurable staleness threshold via `ConclaveSettings`:
   `reaper_stale_threshold_minutes: int = 60`.

5. Each reaped task must generate an audit log entry via
   `shared/security/audit.py` (meta-audit: "reaper marked job FAILED").

6. Per-task error isolation: if reaping one task fails, continue to the next.

### Acceptance Criteria

1. `OrphanTaskReaper` reintroduced with `TaskRepository` ABC.
2. `SQLAlchemyTaskRepository` queries `synthesis_job` for stale tasks.
3. Reaper registered as Huey periodic task (every 15 minutes).
4. Stale threshold configurable via `ConclaveSettings`.
5. Reaped tasks marked FAILED with error message "Reaped: exceeded staleness
   threshold — possible worker crash".
6. Audit log entry for each reaped task.
7. Per-task error isolation (one failure doesn't block others).
8. Unit tests: stale detection, skip-recent, per-task error isolation, INFO log.
9. Integration test: inject stale task, run reaper, assert FAILED status.
10. Full gate suite passes.

### Files to Create/Modify

- Create: `src/synth_engine/shared/tasks/reaper.py`
- Create: `src/synth_engine/shared/tasks/repository.py`
- Modify: `src/synth_engine/modules/synthesizer/tasks.py` (register periodic task)
- Modify: `src/synth_engine/shared/settings.py` (reaper settings)
- Create: `tests/unit/test_reaper.py`
- Create: `tests/integration/test_reaper_stale_jobs.py`

---

## T45.3 — Implement Webhook Callbacks for Task Completion (TBD-01)

**Priority**: P1 — Integration infrastructure. Enables external systems to
receive push notifications on synthesis job state transitions.

### Context & Constraints

1. **Prerequisite tasks** (within this task):
   a. Create a `webhook_registration` database table (SQLModel) storing:
      callback URL, HMAC signing key, target events (COMPLETED, FAILED),
      owner_id (from Phase 39 auth), created_at, active flag.
   b. Create a test webhook receiver (small FastAPI app or pytest fixture)
      that captures and validates incoming webhook deliveries.

2. Webhook delivery must occur on synthesis job state transitions:
   - `COMPLETED` — job finished successfully
   - `FAILED` — job failed (any reason)

3. Each delivery includes:
   - JSON payload: `{job_id, status, timestamp, details}`
   - `X-Conclave-Signature` header: HMAC-SHA256 of the payload using the
     registration's signing key
   - `X-Conclave-Event` header: event type (e.g., `job.completed`)
   - `X-Conclave-Delivery-Id` header: unique delivery UUID for deduplication

4. Retry policy: 3 attempts with exponential backoff (1s, 4s, 16s).
   After 3 failures, mark the delivery as FAILED in the delivery log.

5. All delivery attempts (success and failure) logged to the WORM audit trail.

6. Webhook registration requires authentication (Phase 39 JWT).

7. Rate limit: max 10 active webhook registrations per operator.

### Acceptance Criteria

1. `POST /webhooks` endpoint to register callback URLs with HMAC signing key.
2. `GET /webhooks` endpoint to list registrations.
3. `DELETE /webhooks/{id}` endpoint to deactivate a registration.
4. Webhook delivered on job COMPLETED and FAILED transitions.
5. HMAC-SHA256 signature in `X-Conclave-Signature` header.
6. 3 retry attempts with exponential backoff on delivery failure.
7. All deliveries logged to audit trail.
8. Delivery log table tracks attempts, status, response code.
9. Test webhook receiver validates HMAC signature.
10. Unit tests: signature generation, retry logic, delivery log.
11. Integration test: register webhook → complete job → verify delivery received
    with valid HMAC signature.
12. Full gate suite passes.

### Files to Create/Modify

- Create: `src/synth_engine/bootstrapper/schemas/webhooks.py` (registration + delivery log models)
- Create: `src/synth_engine/bootstrapper/routers/webhooks.py` (CRUD endpoints)
- Create: `src/synth_engine/modules/synthesizer/webhook_delivery.py` (delivery engine)
- Modify: `src/synth_engine/modules/synthesizer/job_orchestration.py` (trigger on state transition)
- Modify: `src/synth_engine/bootstrapper/router_registry.py` (register webhook router)
- Modify: `src/synth_engine/shared/settings.py` (webhook settings)
- Create: `docs/adr/ADR-0041-webhook-idempotency-reaper.md`
- Create: `tests/unit/test_webhooks.py`
- Create: `tests/unit/test_webhook_delivery.py`
- Create: `tests/integration/test_webhook_roundtrip.py`

---

## T45.4 — Update Deferred Items & ADR-0029

**Priority**: P2 — Documentation currency.

### Context & Constraints

1. Mark TBD-01, TBD-07, TBD-08 as DELIVERED in `docs/backlog/deferred-items.md`
   with Phase 45 reference.

2. Update ADR-0029 summary table: change `Target Phase` from "TBD" to "45"
   for Gaps 2 (webhooks), 6 (rate limiting → Phase 39), and the items
   corresponding to TBD-07 and TBD-08.

3. Update `docs/BACKLOG.md` — mark Phase 45 tasks.

### Acceptance Criteria

1. `deferred-items.md` TBD-01, TBD-07, TBD-08 marked DELIVERED with Phase 45.
2. TBD-02 marked DELIVERED with Phase 39 reference.
3. TBD-06 marked DELIVERED with Phase 39 reference.
4. ADR-0029 summary table updated.
5. Markdownlint passes.

### Files to Create/Modify

- Modify: `docs/backlog/deferred-items.md`
- Modify: `docs/adr/ADR-0029-architectural-requirements-gap-analysis.md`

---

## Task Execution Order

```
T45.1 (Idempotency) ──────────────> first (extracts shared Redis client used by T45.3)
T45.2 (Orphan Reaper) ────────────> parallel with T45.1 (independent)
T45.3 (Webhooks) ─────────────────> after T45.1 (may use shared Redis for delivery dedup)
T45.4 (Deferred items update) ────> LAST (documents all deliveries)
```

T45.1 and T45.2 are independent and can run in parallel. T45.3 benefits from
the shared Redis client extracted in T45.1. T45.4 runs last.

---

## Phase 45 Exit Criteria

1. Idempotency middleware wired and functional with Redis-backed deduplication.
2. Orphan task reaper registered as Huey periodic task, marks stale jobs FAILED.
3. Webhook callbacks delivered on job state transitions with HMAC signing and retry.
4. All three deferred items (TBD-01, TBD-07, TBD-08) marked DELIVERED.
5. ADR-0041 documents the architecture.
6. ADR-0029 updated with Phase 45 assignments.
7. All quality gates pass.
8. Zero open advisories in RETRO_LOG.
9. Review agents pass for all tasks.
