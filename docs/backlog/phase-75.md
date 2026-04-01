# Phase 75 — Multi-Worker Safety & Observability

**Goal**: Close the three multi-worker deployment gaps identified in the audit
and tracked as open advisories: process-local circuit breaker (ADV-P62-03),
process-local grace period (ADV-P63-01), and Prometheus multiprocess mode
(ADV-P71-01).

**Source**: Production Audit 2026-03-29, Findings C10, C14 + ADV-P62-03,
ADV-P63-01, ADV-P71-01

---

## Tasks

### T75.1 — Redis-Backed Circuit Breaker for Webhook Delivery

**Files**: `modules/synthesizer/jobs/webhook_delivery.py` (circuit breaker
class), `bootstrapper/wiring.py` (CB wiring)

Replace the process-local circuit breaker with a Redis-backed implementation
that shares state across N workers. Use atomic Redis operations (INCR + EXPIRE)
for failure counting.

**ACs**:
1. Circuit breaker state stored in Redis, shared across all workers.
2. Threshold trips after N failures globally, not N * workers.
3. Half-open → closed transition coordinated via Redis.
4. Graceful degradation: if Redis is unavailable, falls back to process-local
   CB (existing behavior) with WARNING log.
5. ADV-P62-03 closed in RETRO_LOG.

### T75.2 — Redis-Backed Grace Period Clock

**Files**: Rate limiter fail-closed grace period implementation.

Replace the process-local grace period clock with a Redis-backed timestamp
so all workers share the same fail-closed timer.

**ACs**:
1. Grace period start time stored in Redis.
2. All workers transition to fail-closed simultaneously.
3. Graceful degradation: Redis-down falls back to process-local (existing
   behavior) with WARNING log.
4. ADV-P63-01 closed in RETRO_LOG.

### T75.3 — Configure Prometheus Multiprocess Mode

**Files**: `bootstrapper/main.py`, `shared/observability.py`, new
`scripts/prometheus_multiproc_setup.sh`

Configure `PROMETHEUS_MULTIPROC_DIR` and use `prometheus_client.multiprocess`
collector so per-worker counters are aggregated in `/metrics` responses.

**ACs**:
1. `PROMETHEUS_MULTIPROC_DIR` documented in operator manual and `.env.example`.
2. `/metrics` endpoint returns aggregated counters across all workers.
3. Existing single-worker deployments work unchanged (dir not required when
   workers=1).
4. Docker Compose and k8s manifests updated.
5. ADV-P71-01 closed in RETRO_LOG.

### T75.4 — Factory Injection Synchronization

**File**: `bootstrapper/wiring.py`

Add a `threading.Lock` around module-scope factory setters to prevent race
conditions during multi-worker uvicorn startup.

**ACs**:
1. All `set_*_factory()` calls are serialized via lock.
2. Double-set detection: WARNING if a factory is set twice.
3. No performance impact on normal request path (lock only at startup).
