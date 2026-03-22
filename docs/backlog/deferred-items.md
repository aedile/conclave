# Deferred Items — ADR-0029 Gap Analysis

**Source**: ADR-0029 — Architectural Requirements Gap Analysis
**Added**: P20-T20.4 — Architecture Tightening (AC2)
**Purpose**: Track the five ADR-0029 "Deferred" dispositions as explicit backlog
items so they are not silently lost. Each item is tagged `Phase: TBD` and must
be assigned to a concrete phase before the first production deployment that
triggers the deferred requirement.

---

## TBD-01 — Webhook Callbacks for Task Completion ~~DELIVERED (Phase 45)~~

**Source**: ADR-0029 Gap 2
**Phase**: 45 — Webhook Callbacks, Idempotency Middleware & Orphan Task Reaper (T45.3)
**Priority**: P1
**Status**: DELIVERED — `POST /webhooks`, `GET /webhooks`, `DELETE /webhooks/{id}`
endpoints in `bootstrapper/routers/webhooks.py`; delivery engine in
`modules/synthesizer/webhook_delivery.py`; HMAC-SHA256 signing via
`X-Conclave-Signature`; 3-retry exponential backoff; all deliveries logged to
the WORM audit trail. ADR-0044 documents the architecture.

### Context

The specification requires webhook push for long-running task completion.
The current implementation uses Server-Sent Events (SSE) via
`GET /tasks/{id}/status`, which fully satisfies the React SPA dashboard
consumer. Webhooks are a push-to-external-endpoint pattern for integrations
where an external system (CI pipeline, data platform, ETL orchestrator) needs
notification of synthesis job completion without holding an open connection.

No external integration exists in the current single-tenant air-gapped
deployment. This item is deferred until external integration requirements are
formally defined.

### Acceptance Criteria (when scheduled)

1. `POST /webhooks` endpoint to register callback URLs with HMAC signing key.
2. Webhook delivery on synthesis job state transitions (COMPLETED, FAILED).
3. HMAC-SHA256 signature in `X-Conclave-Signature` header for callback verification.
4. At least 3 retry attempts with exponential backoff on delivery failure.
5. Webhook delivery log persisted to the audit ledger.
6. Integration tests verifying delivery and HMAC verification.

### Blocking Prerequisite

Must be assigned to a phase only when at least one external integration
consumer is confirmed. Premature implementation without a consumer wastes
delivery budget and adds operational surface area.

---

## TBD-02 — Rate Limiting and Circuit Breakers ~~DELIVERED (Phase 39)~~

**Source**: ADR-0029 Gap 6
**Phase**: 39 — Authentication, Authorization & Rate Limiting (T39.3)
**Priority**: P0
**Status**: DELIVERED — `RateLimitGateMiddleware` in
`shared/middleware/rate_limit.py`; Redis sorted-set sliding window with
in-process deque fallback; per-operator configurable limits; `429` responses
with `Retry-After` header. Confirmed in RETRO_LOG Phase 39 entry.

### Context

The specification requires rate limiting and circuit breakers for agentic
DDoS protection. The current single-tenant, air-gapped deployment is protected
by request body size limiting (`RequestBodyLimitMiddleware`) and JSON depth
limiting. No per-IP rate limiting, token-bucket limiting, or circuit breaker
pattern exists.

Rate limiting is deferred to the first multi-tenant or network-exposed
deployment topology where multiple external clients can issue requests at
high frequency.

### Acceptance Criteria (when scheduled)

1. Per-IP rate limiting with configurable burst and sustained rates.
2. Token-bucket implementation backed by Redis (reusing existing Redis
   infrastructure from the task queue).
3. Circuit breaker for external service calls (currently none, but future
   LDAP or SSO integrations would require this).
4. `429 Too Many Requests` responses with `Retry-After` header.
5. Rate limit metrics exposed via `/metrics` Prometheus endpoint.
6. Unit and integration tests covering burst, sustained, and circuit breaker
   open/half-open/closed transitions.

---

## TBD-03 — mTLS Inter-Container Communication ~~DELIVERED (Phase 46)~~

**Source**: ADR-0029 Gap 7
**Phase**: 46 — mTLS Inter-Container Communication
**Priority**: P1
**Status**: DELIVERED — ECDSA P-256 internal CA and per-service leaf certs in
`scripts/generate-mtls-certs.sh`; mTLS wired on all data-plane connections
(`shared/db.py`, `bootstrapper/dependencies/redis.py`,
`shared/task_queue.py`); Docker Compose overlay in
`docker-compose.mtls.yml`; cert expiry Prometheus metric; Kubernetes
NetworkPolicy manifests in `k8s/network-policies/`. ADR-0045 documents the
architecture and threat model. All five original ACs met.

### Context

The specification requires all inter-container communication over mTLS. The
current Docker Compose deployment uses plain TCP on the Docker bridge network.
Traffic between containers on the same bridge network never leaves the host
OS's kernel networking stack, making mTLS equivalent to kernel-level containment
for single-host deployments.

mTLS adds meaningful security when containers run on different physical hosts
(Kubernetes multi-node, Docker Swarm) where traffic traverses shared
infrastructure. This item is deferred until the deployment topology migrates
to multi-host.

### Acceptance Criteria (when scheduled)

1. Internal CA or cert-manager integration for certificate issuance.
2. All container-to-container connections (API → PostgreSQL, API → Redis,
   API → Huey worker) use mutual TLS.
3. Certificate rotation without service downtime (rolling restart or dynamic
   reload).
4. Kubernetes `NetworkPolicy` or Cilium policy enforcing mTLS-only paths.
5. Smoke test verifying plaintext connections are rejected.

---

## TBD-04 — Custom Prometheus Business Metrics ~~DELIVERED (Phase 25)~~

**Source**: ADR-0029 Gap 8
**Phase**: 25 — Observability
**Status**: DELIVERED — `SYNTHESIS_MS_PER_ROW` Histogram in `engine.py`, `EPSILON_SPENT_TOTAL` Counter in `accountant.py`, Grafana dashboard in `grafana/provisioning/dashboards/`. Confirmed in RETRO_LOG Phase 25 entry.

### Context

The specification requires custom Prometheus metrics including "Milliseconds
per Synthesized Row" and "Epsilon Spent per Request". The `/metrics` endpoint
infrastructure exists (ADR-0011) but only exposes standard `prometheus-client`
auto-instrumentation metrics. No custom `Counter`, `Histogram`, or `Gauge`
instruments exist for synthesis-specific KPIs.

This item is deferred to a future observability phase alongside TBD-05
(OTEL propagation), which shares the same instrumentation entry points.

### Acceptance Criteria (when scheduled)

1. `synthesis_ms_per_row` Histogram instrument at `SynthesisEngine.train()`
   call site (labels: model type, row count bucket).
2. `epsilon_spent_total` Counter instrument at `EpsilonAccountant.record()`
   (labels: job ID, dataset ID).
3. Both metrics exposed via `/metrics` and scrapeable by Prometheus.
4. Grafana dashboard JSON (or Prometheus recording rules) for the two KPIs.
5. Unit tests verifying metric increments on synthesis completion.

**Coordination note**: The epsilon metric must be coordinated with the
Privacy module's accounting ledger to avoid double-counting across requests
(see ADR-0029 §Gap 8 rationale).

---

## TBD-05 — OTEL Trace Context Propagation into Huey Workers ~~DELIVERED (Phase 25)~~

**Source**: ADR-0029 Gap 9
**Phase**: 25 — Observability (batched with TBD-04)
**Status**: DELIVERED — `inject_trace_context()` / `extract_trace_context()` in `telemetry.py`, wired at dispatch (`jobs.py`) and worker entry (`tasks.py`). Confirmed in RETRO_LOG Phase 25 entry.

### Context

The specification requires explicit OTEL trace ID injection into Huey async
task arguments for distributed trace continuity. The current implementation
has `shared/telemetry.py` with a `TracerProvider` and FastAPI route tracing,
but Huey workers do not receive trace context — FastAPI spans and Huey worker
spans appear as disconnected traces.

This item is deferred alongside TBD-04 (custom metrics) as both are
observability improvements that share the same implementation phase.

### Acceptance Criteria (when scheduled)

1. `TraceContextTextMapPropagator.inject()` called at Huey task dispatch site
   (`shared/task_queue.py` or the dispatching router) to serialize the current
   span context into a carrier dict.
2. Carrier dict passed as a Huey task argument (not a side-channel).
3. `TraceContextTextMapPropagator.extract()` called at Huey worker entry point
   to re-attach the context and create a linked child span.
4. End-to-end trace test verifying that an API request span and its Huey
   worker child span share the same trace ID in the OTEL exporter output.
5. No performance regression: carrier serialization is O(1) and adds < 1 ms
   overhead per task dispatch.

---

## TBD-06 — JWT Authentication & Route-Level Authorization ~~DELIVERED (Phase 39)~~

**Source**: P32-T32.1 — Dead Module Cleanup (unwired scaffolding removal)
**Phase**: 39 — Authentication, Authorization & Rate Limiting (T39.1, T39.2)
**Priority**: P0
**Status**: DELIVERED — `shared/auth/jwt.py` with `create_access_token()` /
`verify_token()`; `shared/auth/scopes.py` with `Scope` StrEnum;
`bootstrapper/dependencies/auth.py` with `get_current_operator()`;
`Depends()` wired on all mutating routes; `/auth/token` OAuth2 endpoint;
IDOR ownership scoping via ADR-0040. ADR-0039 documents the JWT decision.
Confirmed in RETRO_LOG Phase 39 entry.

### Context

`shared/auth/jwt.py` and `shared/auth/scopes.py` were scaffolded in Phase 2
(P2-T2.3) for zero-trust JWT authentication with mTLS client binding. The
implementation was complete and well-tested but never wired to any FastAPI
route via `Depends(get_current_user(...))`. The single-tenant, single-operator
air-gapped deployment does not require per-user authentication today.

The module was removed in Phase 32 (T32.1) as unwired dead code to bring
coverage reporting and static analysis back to a clean baseline.

### Acceptance Criteria (when scheduled)

1. Reintroduce `shared/auth/jwt.py` with `create_access_token()`, `verify_token()`,
   and `JWTConfig`. Client binding (mTLS SAN or IP hash) must be preserved.
2. Reintroduce `shared/auth/scopes.py` with `Scope` StrEnum and `has_required_scope()`.
3. Reintroduce `bootstrapper/dependencies/auth.py` with `get_current_user()` factory.
4. Wire `Depends(get_current_user(Scope.X))` on all mutating routes (jobs, connections,
   settings, vault, licensing).
5. Add a `/auth/token` route for OAuth2 password-flow token issuance.
6. Implement refresh token rotation.
7. Unit tests: token issuance, expiry, client binding mismatch, scope enforcement.
8. Integration test: full round-trip from `/auth/token` to a protected route.

### Blocking Prerequisite

Requires a multi-user or multi-tenant deployment topology where caller
identity matters for access control. Must not be scheduled for single-operator
air-gapped deployments without a concrete user-story trigger.

---

## TBD-07 — Idempotency Middleware ~~DELIVERED (Phase 45)~~

**Source**: P32-T32.1 — Dead Module Cleanup (unwired scaffolding removal)
**Phase**: 45 — Webhook Callbacks, Idempotency Middleware & Orphan Task Reaper (T45.1)
**Priority**: P1
**Status**: DELIVERED — `shared/middleware/idempotency.py` with
`IdempotencyMiddleware`; atomic Redis `SET NX EX` deduplication; wired in
`bootstrapper/middleware.py`; configurable TTL via `ConclaveSettings`;
HTTP 409 on duplicate, HTTP 400 on key length violation; graceful Redis-down
pass-through; key release on handler exception. ADR-0044 documents the
architecture.

### Context

`shared/middleware/idempotency.py` was scaffolded in Phase 2 (P2-T2.1) as a
Redis-backed deduplication middleware using atomic `SET NX EX`. The
implementation was complete (atomic TOCTOU-safe, degraded-mode pass-through,
key release on handler exception) but was never added to the ASGI middleware
stack in `bootstrapper/main.py`.

The module was removed in Phase 32 (T32.1) as unwired dead code.

### Acceptance Criteria (when scheduled)

1. Reintroduce `shared/middleware/idempotency.py` with `IdempotencyMiddleware`.
2. Wire `app.add_middleware(IdempotencyMiddleware, redis_client=..., ttl_seconds=...)`
   in `bootstrapper/main.py` using the existing Redis client from the task queue.
3. Configurable TTL via environment variable (e.g., `IDEMPOTENCY_TTL_SECONDS`,
   default 300).
4. HTTP 409 response with `{"detail": "Duplicate request", "idempotency_key": "..."}`.
5. HTTP 400 rejection for keys exceeding 128 characters.
6. Graceful Redis-down degradation: log warning and pass through (no service block).
7. Unit tests: duplicate detection, pass-through, key length cap, Redis-down mode,
   key release on handler exception.
8. Integration test: duplicate POST with a real Redis connection returns 409.

### Blocking Prerequisite

Requires clients that issue retryable mutating requests (e.g., mobile clients
with unreliable connections, CI pipelines retrying on transient failures).
Must not be scheduled without a concrete client use-case.

---

## TBD-08 — Orphan Task Reaper ~~DELIVERED (Phase 45)~~

**Source**: P32-T32.1 — Dead Module Cleanup (unwired scaffolding removal)
**Phase**: 45 — Webhook Callbacks, Idempotency Middleware & Orphan Task Reaper (T45.2)
**Priority**: P1
**Status**: DELIVERED — `shared/tasks/reaper.py` with `OrphanTaskReaper` and
`TaskRepository` ABC; `shared/tasks/repository.py` with
`SQLAlchemyTaskRepository`; registered as Huey `@periodic_task` (every 15
minutes); configurable staleness threshold via `ConclaveSettings`; audit log
entry for each reaped task; per-task error isolation. ADR-0044 documents the
architecture.

### Context

`shared/tasks/reaper.py` was scaffolded in Phase 2 (P2-T2.1) as an
`OrphanTaskReaper` that detects IN_PROGRESS tasks exceeding a staleness
threshold and marks them FAILED. The implementation was complete with a clean
abstract `TaskRepository` interface, but was never registered as a Huey
periodic task.

In single-worker deployments, Huey's own crash recovery handles stale tasks.
The reaper becomes necessary in multi-node deployments where a crashed worker
releases its process but the task record remains IN_PROGRESS in the database.

The module was removed in Phase 32 (T32.1) as unwired dead code.

### Acceptance Criteria (when scheduled)

1. Reintroduce `shared/tasks/reaper.py` with `OrphanTaskReaper`, `TaskRepository`
   ABC, and `Task` dataclass.
2. Implement a concrete `SQLAlchemyTaskRepository` in `modules/ingestion/` (or
   `shared/tasks/`) using the existing async SQLAlchemy session.
3. Register `OrphanTaskReaper.reap()` as a Huey periodic task with configurable
   schedule (e.g., `@huey.periodic_task(crontab(minute='*/15'))`).
4. Configurable staleness threshold via environment variable (e.g.,
   `REAPER_STALE_THRESHOLD_MINUTES`, default 60).
5. Audit log entry for each reaped task (using `shared/security/audit.py`).
6. Unit tests: stale detection, skip-recent, per-task error isolation, INFO log.
7. Integration test: inject an artificial stale task into the DB, run the reaper,
   assert the task transitions to FAILED.

### Blocking Prerequisite

Requires multi-node Huey worker deployment (e.g., Kubernetes Deployment with
`replicas > 1`). Single-worker deployments do not accumulate orphaned tasks
in a way that requires periodic sweeping.

---

## Triage Notes

- ~~Items TBD-04 and TBD-05 should be implemented in the same phase~~ — DONE (Phase 25).
- ~~Items TBD-01 through TBD-03 each require a distinct deployment trigger~~ —
  DELIVERED: TBD-01 → Phase 45 (T45.3), TBD-02 → Phase 39 (T39.3),
  TBD-03 → Phase 46 (T46.1–T46.4).
- ~~Items TBD-06 through TBD-08 were removed as unwired scaffolding in Phase 32~~ —
  DELIVERED: TBD-06 → Phase 39 (T39.1/T39.2), TBD-07 → Phase 45 (T45.1),
  TBD-08 → Phase 45 (T45.2).
- ADR-0029 summary table updated in T45.4 to reflect Phase 39 and Phase 45
  assignments for all previously-TBD items.
- ADR-0044 documents the Phase 45 webhook, idempotency, and reaper architecture.
- ADR-0045 documents the Phase 46 mTLS inter-container communication architecture.
