# ADR-0004: OpenTelemetry + FastAPIInstrumentor for Distributed Tracing

**Status:** Accepted
**Date:** 2026-03-13
**Deciders:** Project team

## Context

The Conclave Engine must provide observability into request latency, error
rates, and cross-service call chains without depending on any cloud-hosted
telemetry backend.  In production the engine runs in air-gapped environments
where outbound network calls to SaaS observability platforms (Datadog, New
Relic, Honeycomb) are prohibited by the security perimeter.

Two requirements must be balanced:

1. **Production:** Traces must be exportable to an on-premises OpenTelemetry
   Collector (e.g. Jaeger) via the OTLP gRPC protocol when the operator has
   provisioned one.
2. **Development and testing:** The application must start cleanly when no
   OTLP endpoint is configured, with zero external network calls.

## Decision

Use the OpenTelemetry Python SDK with `FastAPIInstrumentor` for automatic HTTP
span generation, and implement a two-path exporter selection strategy:

- **OTLP path (production):** When the `OTEL_EXPORTER_OTLP_ENDPOINT` environment
  variable is set and `opentelemetry-exporter-otlp` is installed, an
  `OTLPSpanExporter` (gRPC) is constructed and wired into a `BatchSpanProcessor`.
- **InMemory path (dev/test):** When `OTEL_EXPORTER_OTLP_ENDPOINT` is absent, or
  when the OTLP exporter package is not installed, an `InMemorySpanExporter` is
  used.  This exporter accumulates spans in memory and does not forward them to
  any backend.  It is suitable for development and unit testing only — in
  production with no OTLP endpoint, spans are silently discarded when the
  process exits.

The OTLP exporter is imported lazily so that `opentelemetry-exporter-otlp` is
an optional dependency — the application starts without it in air-gapped
environments where the wheel cannot be fetched.

Endpoint URLs are redacted before being written to logs: only scheme, host,
and port are logged; any credentials embedded in the URL are stripped.

## Consequences

- **Positive:** Automatic HTTP instrumentation via `FastAPIInstrumentor` requires
  no per-route boilerplate.
- **Positive:** The two-path strategy provides a clean on-ramp for operators —
  set one environment variable to enable full tracing; omit it for zero-config
  startup.
- **Positive:** Lazy OTLP import keeps the base Docker image smaller for
  deployments that do not need OTLP export.
- **Negative:** `InMemorySpanExporter` accumulates spans in the process heap.
  Long-running dev or test processes that generate many spans will consume
  increasing memory.  Operators running extended integration tests should be
  aware of this limit.
- **Negative:** The `BatchSpanProcessor` introduces a small background thread
  for span export.  This is acceptable for production but adds minor overhead
  in unit tests (mitigated by the in-memory path which never blocks on I/O).
