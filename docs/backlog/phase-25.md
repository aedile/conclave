# Phase 25 — Observability

**Historical summary.** This file is a backfill record, not a planning document.
Phase 25 was executed on 2026-03-17 and merged as a single PR.

---

## PRs Merged

| PR | Title | Merged |
|----|-------|--------|
| [#119](../../pull/119) | feat(P25): Observability — Custom Prometheus Metrics + OTEL Trace Propagation | 2026-03-17 |

---

## Key Deliverables

- **Custom Prometheus metrics**: Added four domain-specific gauges/histograms beyond the
  default FastAPI instrumentation:
  - `conclave_job_queue_depth`: number of jobs in QUEUED or PROCESSING state.
  - `conclave_synthesis_duration_seconds`: histogram of synthesis job wall time.
  - `conclave_epsilon_spent_total`: gauge of total epsilon consumed across all jobs.
  - `conclave_privacy_budget_remaining`: gauge of epsilon budget remaining.

- **OTEL trace propagation**: Wired OpenTelemetry trace context from FastAPI HTTP routes
  through to Huey background worker tasks. Synthesis jobs now appear as child spans of
  the originating HTTP request in Jaeger, enabling end-to-end trace visibility across
  the sync/async boundary.

---

## Retrospective Notes

- Trace propagation across the HTTP → Huey boundary requires explicit trace context
  serialization into the Huey task arguments. OTEL's automatic propagation does not
  cross process boundaries.
- Custom metrics must be registered at application startup, not per-request, to avoid
  duplicate registration errors under hot-reload.
