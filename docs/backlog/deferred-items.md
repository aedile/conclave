# Deferred Items — ADR-0029 Gap Analysis

**Source**: ADR-0029 — Architectural Requirements Gap Analysis
**Added**: P20-T20.4 — Architecture Tightening (AC2)
**Purpose**: Track the five ADR-0029 "Deferred" dispositions as explicit backlog
items so they are not silently lost. Each item is tagged `Phase: TBD` and must
be assigned to a concrete phase before the first production deployment that
triggers the deferred requirement.

---

## TBD-01 — Webhook Callbacks for Task Completion

**Source**: ADR-0029 Gap 2
**Phase**: TBD — Future integration phase
**Priority**: TBD

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

## TBD-02 — Rate Limiting and Circuit Breakers

**Source**: ADR-0029 Gap 6
**Phase**: TBD — Future multi-tenant phase
**Priority**: TBD

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

## TBD-03 — mTLS Inter-Container Communication

**Source**: ADR-0029 Gap 7
**Phase**: TBD — Future multi-host / Kubernetes phase
**Priority**: TBD

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

## TBD-04 — Custom Prometheus Business Metrics

**Source**: ADR-0029 Gap 8
**Phase**: TBD — Future observability phase
**Priority**: TBD

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

## TBD-05 — OTEL Trace Context Propagation into Huey Workers

**Source**: ADR-0029 Gap 9
**Phase**: TBD — Future observability phase (batch with TBD-04)
**Priority**: TBD

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

## Triage Notes

- Items TBD-04 and TBD-05 should be implemented in the same phase (they share
  instrumentation entry points in `SynthesisEngine`, `EpsilonAccountant`, and
  `shared/telemetry.py`).
- Items TBD-01 through TBD-03 each require a distinct deployment trigger
  (external integrations, multi-tenancy, multi-host Kubernetes) — they must
  NOT be batched together without verifying the triggering condition exists.
- A phase assignment for any of these items requires an ADR update to ADR-0029
  changing the `Target Phase` in the summary table from "TBD" to the assigned
  phase number.
