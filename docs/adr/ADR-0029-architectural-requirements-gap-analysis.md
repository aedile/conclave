# ADR-0029 — Architectural Requirements Gap Analysis

**Date:** 2026-03-16
**Status:** Accepted
**Deciders:** PM + Architecture Reviewer
**Task:** P11-T11.3
**Resolves:** Phase 10 roast findings — delta between `docs/ARCHITECTURAL_REQUIREMENTS.md` and the implemented system

---

## Context

The Phase 10 architectural roast identified nine requirements stated in
`docs/ARCHITECTURAL_REQUIREMENTS.md` that are either not implemented, implemented
differently, or not applicable to the current deployment stage. CLAUDE.md Rule 6
mandates that technology or design deviations from the specification have documented
ADR decisions. This ADR documents each gap with a formal disposition: **Implemented
Differently**, **Deferred**, or **Descoped**.

---

## Gap Analysis

### Gap 1 — Internal Event Bus / Pub-Sub

**Specification:** "Internal In-Memory Event Bus (Publisher/Subscriber)" for cross-module
communication.

**Actual Implementation:** IoC callbacks injected by the bootstrapper. The `cli.py`
bootstrapper assembles a `row_transformer` callback (a `Callable[[str, dict[str, Any]],
dict[str, Any]]`) from the masking registry and passes it into the subsetting engine at
construction time. The `factories.py` bootstrapper provides `build_dp_wrapper()` which
injects a `DPTrainingWrapper` instance into `SynthesisEngine.train()`. All cross-module
communication is via explicit Python function signatures — no message bus.

**Disposition: Implemented Differently.**

**Rationale:** The Conclave Engine is a single-process modular monolith (ADR-0001).
Within a single process, an in-memory event bus is a structural overhead that provides no
distribution benefit: there is no physical boundary between modules that would require
asynchronous decoupling. IoC callbacks are simpler, type-safe, easier to test (inject a
mock), and fully deterministic. An event bus would be appropriate if modules needed to be
physically separated into microservices — a topology that is explicitly out of scope for
the air-gapped BYOC deployment model. The bootstrapper-injection pattern is the correct
decoupling mechanism for an in-process modular monolith.

---

### Gap 2 — Webhook Callbacks for Task Completion

**Specification:** Webhook push for long-running task completion.

**Actual Implementation:** Server-Sent Events (SSE) via `GET /tasks/{id}/status`. The
`bootstrapper/sse.py` module implements an async generator that polls the database for
`SynthesisJob` status changes and yields SSE event dicts consumed by
`sse_starlette.EventSourceResponse`. This is a pull-from-push pattern: the client opens a
persistent connection and the server streams updates as they occur.

**Disposition: Deferred.**

**Rationale:** SSE fully satisfies the current client — the React SPA dashboard
(`ADR-0023`). The SPA opens an `EventSource` connection and receives real-time progress
events without polling. Webhooks are a push-to-external-endpoint pattern suited to
integrations where an external system (CI pipeline, data platform, ETL orchestrator) needs
to be notified when a synthesis job completes without holding an open connection. No such
external integration exists in the current single-tenant air-gapped deployment. Webhook
support is deferred to a future phase when external integration requirements are defined.

---

### Gap 3 — `llms.txt` for Agentic AI Integration

**Specification:** Serve an `llms.txt` file to enable agentic AI integration.

**Actual Implementation:** No `llms.txt` endpoint or static file.

**Disposition: Descoped.**

**Rationale:** `llms.txt` is a convention for exposing machine-readable documentation to
external AI agents that crawl or interact with the service over the public internet. The
Conclave Engine's Prime Directive is air-gapped operation — the deployment model
explicitly prohibits external network access (CONSTITUTION.md Priority 0, BYOC mandate in
`docs/ARCHITECTURAL_REQUIREMENTS.md` §1). No external AI agent can reach a service
running inside an air-gapped environment. An `llms.txt` file in this context has no
consumers and serves no purpose. This requirement is incompatible with the air-gap mandate
and is permanently descoped.

---

### Gap 4 — Model Context Protocol (MCP) Support

**Specification:** Native MCP (Model Context Protocol) support.

**Actual Implementation:** No MCP server or protocol adapter.

**Disposition: Descoped.**

**Rationale:** MCP requires an external AI agent (e.g., Claude, a Copilot environment)
to establish a bidirectional connection with the engine to invoke tools and retrieve
context. This connectivity model is fundamentally incompatible with the air-gap mandate:
the engine cannot accept inbound connections from external AI providers, and the
air-gapped host cannot reach external AI orchestration infrastructure. The same reasoning
that eliminates `llms.txt` (Gap 3) eliminates MCP. This requirement is permanently
descoped for air-gapped deployments. If a future on-premise LLM integration (e.g., a
locally-hosted Ollama instance) requires MCP-style tool calling, that scope can be
addressed in a dedicated ADR at that time.

---

### Gap 5 — `datamodel-code-generator` in CI

**Specification:** Pydantic models must be generated from the OpenAPI specification in CI
using `datamodel-code-generator`.

**Actual Implementation:** Hand-written Pydantic models in `bootstrapper/schemas/`. The
`datamodel-code-generator` package is pinned in `pyproject.toml`
(`>=0.50.0,<1.0.0`) but it is a dev dependency and is not invoked in any CI job (verified
by inspection of `.github/workflows/ci.yml` — no `datamodel-codegen` step exists).

**Disposition: Implemented Differently.**

**Rationale:** The `datamodel-code-generator` workflow generates models from an OpenAPI
spec and is designed for API *consumers* — clients that need to stay in sync with a
changing contract they do not control. The Conclave Engine is the API *producer*: it owns
and defines the OpenAPI spec. Hand-written Pydantic models provide three advantages over
auto-generation for a server-side producer:

1. **Custom validators.** Hand-written models use `@field_validator` and `@model_validator`
   decorators to enforce business rules (e.g., epsilon bounds, row count limits). These
   cannot be expressed in an OpenAPI schema and would be stripped by code generation.
2. **Domain-specific typing.** Models reference internal domain types (e.g.,
   `ColumnProfile`, `MaskingRule`) that have no OpenAPI representation. Auto-generated
   models would flatten these to primitives.
3. **Stability.** Auto-generation introduces a two-step chain where an OpenAPI spec change
   can silently break model generation. Direct Pydantic authoring keeps the contract and
   its enforcement co-located in the same code.

The `datamodel-code-generator` dependency is retained in `pyproject.toml` for ad-hoc
client SDK generation (e.g., generating a typed Python client for integration test
scaffolding), but it is not part of the server-side CI pipeline.

---

### Gap 6 — Rate Limiting and Circuit Breakers

**Specification:** Rate limiting and circuit breakers required for agentic DDoS
protection.

**Actual Implementation:** Request body size limiting (`RequestBodyLimitMiddleware` in
`bootstrapper/dependencies/request_limits.py`) and JSON depth limiting are implemented.
No per-IP rate limiting, token-bucket limiting, or circuit breaker pattern exists.

**Disposition: Deferred.**

**Rationale:** Rate limiting defends against a specific threat model: multiple external
clients making requests at high frequency to exhaust server resources. The current
deployment is single-tenant and air-gapped — there is one authenticated user (the operator
behind a hardware perimeter) and no external API consumers. The request body size and
depth limits (`RequestBodyLimitMiddleware`) address the OWASP concern of malformed or
oversized request payloads without introducing the operational complexity of a rate
limiting backend (Redis counters, sliding window state). Circuit breakers protect against
cascading failures from external service dependencies — the Conclave Engine makes no
external service calls. Both features are deferred to a future phase scoped to multi-tenant
or network-exposed deployment topologies.

---

### Gap 7 — mTLS Inter-Container Communication

**Specification:** All inter-container communication over mTLS.

**Actual Implementation:** Plain TCP on the Docker bridge network (Docker Compose
`docker-compose.yml`). Containers communicate over the default bridge network with no
TLS.

**Disposition: Deferred.**

**Rationale:** The Docker bridge network (`docker-compose.yml`) creates a private virtual
network namespace on the host machine. Traffic between containers on the same bridge
network never leaves the host OS's kernel networking stack — it is not transmitted over
any physical or wireless medium. An attacker who has compromised the host to the level of
intercepting bridge network traffic already has root and has already compromised the
system at a more fundamental level than mTLS would protect. mTLS between containers adds
meaningful security when containers run on different physical hosts (Kubernetes, Docker
Swarm with multi-node networking) where traffic traverses shared infrastructure. For the
current single-host Docker Compose deployment, the bridge network isolation provides
equivalent containment. mTLS hardening is deferred to a future phase scoped to
multi-host Kubernetes deployment.

---

### Gap 8 — Custom Prometheus Business Metrics

**Specification:** Custom Prometheus metrics including "Milliseconds per Synthesized Row"
and "Epsilon Spent per Request".

**Actual Implementation:** The `/metrics` endpoint is mounted and serves standard
`prometheus-client` auto-instrumentation metrics (HTTP request counts, latency histograms
via `prometheus-client`'s default collectors). No custom `Counter`, `Histogram`, or
`Gauge` instruments exist for synthesis-specific KPIs (verified by searching
`src/synth_engine/` — no `prometheus_client.Counter(` or `prometheus_client.Histogram(`
calls outside of `main.py`'s `make_asgi_app()` mount).

**Disposition: Deferred.**

**Rationale:** The `/metrics` endpoint infrastructure exists (`ADR-0011`). Adding custom
business metrics requires instrumentation at the synthesis pipeline call sites
(`SynthesisEngine.train()`, `DPCompatibleCTGAN.fit()`, `EpsilonAccountant.record()`).
This is incremental work that is not blocked by any architectural dependency — the
scaffolding is in place. The two specified KPIs ("Milliseconds per Synthesized Row" and
"Epsilon Spent per Request") are deferred to a future observability phase. The epsilon
metric in particular must be coordinated with the Privacy module's accounting ledger to
avoid double-counting across requests.

---

### Gap 9 — OTEL Trace Context Propagation into Huey Workers

**Specification:** Explicit OTEL trace ID injection into Huey async task arguments for
distributed trace continuity.

**Actual Implementation:** OTEL is configured in `shared/telemetry.py` with a
`TracerProvider` backed by either OTLP gRPC (when `OTEL_EXPORTER_OTLP_ENDPOINT` is set)
or `InMemorySpanExporter` (dev/test). FastAPI routes are traced via the provider. Huey
workers (`shared/task_queue.py`) do not receive trace context — there is no
`trace_id`/`span_context` argument injected into Huey task payloads, and no
`TraceContextTextMapPropagator.inject()` call at the task dispatch site.

**Disposition: Deferred.**

**Rationale:** OTEL context propagation into worker processes requires three steps:
(1) serialize the current span context at the `huey.task()` call site (inject into a
carrier dict using `TraceContextTextMapPropagator.inject()`), (2) pass the carrier as a
task argument, and (3) extract and re-attach the context at the worker entry point using
`TraceContextTextMapPropagator.extract()` to create a linked child span. This is an
observability improvement — its absence does not cause incorrect behavior, only trace
fragmentation (FastAPI spans and Huey worker spans appear as disconnected traces). The
improvement is deferred to a future observability phase alongside Gap 8's custom metrics.
The `shared/telemetry.py` infrastructure requires no changes; only the task dispatch and
worker entry points need modification.

---

## Summary Table

| # | Requirement | Disposition | Target Phase |
|---|-------------|-------------|--------------|
| 1 | Internal Event Bus / Pub-Sub | Implemented Differently (IoC callbacks) | N/A — permanent |
| 2 | Webhook callbacks | Deferred | Future integration phase |
| 3 | `llms.txt` | Descoped — incompatible with air-gap mandate | Permanent |
| 4 | Model Context Protocol (MCP) | Descoped — incompatible with air-gap mandate | Permanent |
| 5 | `datamodel-code-generator` in CI | Implemented Differently (hand-written models) | N/A — permanent |
| 6 | Rate limiting & circuit breakers | Deferred | Future multi-tenant phase |
| 7 | mTLS inter-container | Deferred | Future multi-host/K8s phase |
| 8 | Custom Prometheus business metrics | Implemented | Phase 25 (complete) |
| 9 | OTEL trace context into Huey workers | Implemented | Phase 25 (complete) |

---

## Consequences

**Positive:**
- All nine gaps are now formally documented with written rationale. CLAUDE.md Rule 6
  compliance is satisfied for each deviation.
- The two permanently descoped items (llms.txt, MCP) are closed with written justification
  and will not re-enter the backlog unless the deployment model changes to permit external
  AI agent connectivity.
- The two "Implemented Differently" items are documented as intentional design choices,
  not omissions.

**Negative / Constraints:**
- The five deferred items remain open as technical debt. They must be addressed before
  any multi-tenant, multi-host, or externally-connected deployment goes to production.
- Deferred items 8 and 9 (custom metrics and OTEL propagation) share a natural
  implementation phase and should be batched together.
- Deferred item 7 (mTLS) requires a Kubernetes deployment topology to provide full value
  and cannot be meaningfully implemented in a single-host Docker Compose environment.

---

## References

- `docs/ARCHITECTURAL_REQUIREMENTS.md` — source specification document
- ADR-0001: Modular Monolith Topology — foundational constraint driving Gap 1 decision
- ADR-0011: Prometheus Metrics — Gap 8 infrastructure already in place
- ADR-0020: Huey Task Queue Singleton — Gap 9 worker entry points
- ADR-0021: SSE and Bootstrapper-Owned Tables — Gap 2 current SSE implementation
- ADR-0023: Frontend React/Vite SPA — Gap 2 current SSE consumer
- CONSTITUTION.md Priority 0 — air-gap mandate driving Gaps 3 and 4 descoping
- `src/synth_engine/bootstrapper/cli.py` — Gap 1 `row_transformer` IoC wiring
- `src/synth_engine/bootstrapper/factories.py` — Gap 1 `build_dp_wrapper` IoC wiring
- `src/synth_engine/bootstrapper/sse.py` — Gap 2 current SSE implementation
- `src/synth_engine/shared/telemetry.py` — Gap 9 OTEL TracerProvider configuration
- `src/synth_engine/shared/task_queue.py` — Gap 9 Huey instance (no trace propagation)
- `.github/workflows/ci.yml` — Gap 5 CI pipeline (no `datamodel-codegen` step)
