# ADR-0011: Prometheus Metrics via prometheus-client

**Date:** 2026-03-13
**Status:** Accepted
**Task:** P2-T2.4 — Vault Observability
**Deciders:** Engineering Team

---

## Context

The Conclave Engine needs an observability layer to expose runtime metrics
(request rate, latency, error rate, seal state) without requiring external
network access.  The deployment model is an internal Docker bridge network
where Prometheus scrapes the app container directly.

---

## Decision

### Library: prometheus-client

`prometheus-client` (the official Python client maintained by the Prometheus
project) is added as a production dependency:

```toml
prometheus-client = ">=0.21.0,<1.0.0"
```

### Alternatives Considered

| Option | Verdict |
|--------|---------|
| `opentelemetry-exporter-prometheus` | Rejected — still requires `prometheus-client` as a transitive dep; adds `opentelemetry-exporter-prometheus` + `opentelemetry-sdk` configuration overhead for no benefit. |
| `starlette-prometheus` | Rejected — third-party wrapper; no significant advantage over direct `prometheus-client` usage. |
| Custom `/metrics` handler | Rejected — reinventing exposition format is error-prone and non-standard. |

### Mounting Strategy: make_asgi_app()

```python
from prometheus_client import make_asgi_app
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
```

`make_asgi_app()` returns a standards-compliant ASGI app that serves the
Prometheus text exposition format.  Mounting it at `/metrics` integrates
cleanly with FastAPI's router without requiring a dedicated thread or process.

### No Auth on /metrics

`/metrics` is in the `EXEMPT_PATHS` frozenset (bypasses `SealGateMiddleware`)
and has no authentication.  This is deliberate: Prometheus scraping from
within the Docker internal bridge network does not cross a trust boundary.
Exposing `/metrics` on a public network would require HTTP Basic Auth or
network-level ACLs — that is an operational concern for the deployment team.

### Grafana + Alertmanager

`grafana/provisioning/` and `alertmanager/alertmanager.yml` are provisioned
alongside Prometheus to form a complete local observability stack.  In
development, Alertmanager uses a null receiver (no external notifications).

---

## Consequences

**Positive:**
- Single new production dependency (`prometheus-client`); well-maintained,
  zero transitive production dependencies.
- `/metrics` endpoint is available immediately with default process and Python
  runtime metrics from `prometheus_client`.
- Custom metrics (vault seal state, generation job counts) can be added by
  importing `prometheus_client.Gauge` / `Counter` in any module.

**Negative / Mitigations:**
- Default metrics expose Python internals (GC counts, memory usage).  This is
  acceptable on an internal network; scrape access must be restricted at the
  network layer for public-facing deployments.
- `prometheus_data` volume retains TSDB samples between restarts.  For
  production, this volume SHOULD reside on a LUKS-encrypted host volume
  consistent with the rest of the data plane.
