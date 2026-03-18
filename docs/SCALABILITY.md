# Conclave Engine — Scalability Reference

This document describes the capacity limits, resource constraints, and hardware
sizing recommendations for the Conclave Engine. All figures are derived from the
actual source code configuration and empirical benchmarks. Operators should use
these numbers to plan hardware provisioning and set expectations before deploying.

---

## 1. Database Connection Pool

### Pool Configuration

The SQLAlchemy engine factory (`src/synth_engine/shared/db.py`) configures a
`QueuePool` with the following constants:

| Parameter | Value | Description |
|-----------|-------|-------------|
| `_POOL_SIZE` | `5` | Persistent connections kept open at all times |
| `_MAX_OVERFLOW` | `10` | Additional connections allowed above `POOL_SIZE` |
| **Peak connections** | **15** | Maximum simultaneous DB connections per engine instance |

### PgBouncer Dependency

The production deployment connects the application to PostgreSQL **through
PgBouncer** (transaction-mode pooler), not directly. This is why the
`_POOL_SIZE` and `_MAX_OVERFLOW` values are intentionally modest:

- PgBouncer handles external multiplexing. Many application-side SQLAlchemy
  connections may share a smaller number of actual server-side PostgreSQL backend
  processes.
- Without PgBouncer, each `get_engine()` call (if not cached) would allocate a
  fresh 15-connection pool. The engine cache (`_engine_cache` and
  `_async_engine_cache`) prevents this by returning the same engine instance for
  the same URL, but PgBouncer remains the primary scale-out mechanism.
- **If PgBouncer is removed or misconfigured**, the application will still
  function, but each concurrent request may hold one of the 15 available
  connections. Under load, new requests will queue waiting for a connection. At
  150+ concurrent requests with slow queries, pool exhaustion is likely.

### Connection Pool Exhaustion

Symptoms of pool exhaustion:

- Requests hang with no response for several seconds.
- Application logs show: `TimeoutError: QueuePool limit of size 5 overflow 10 reached`.
- Grafana shows elevated `db_connection_wait_seconds` (if instrumented).

Mitigation: increase PgBouncer's `pool_size` parameter before adjusting
`_POOL_SIZE` in application code. PgBouncer is the correct scaling point.

---

## 2. Synthesis Job Concurrency

### Single Huey Worker Serializes Jobs

The Conclave Engine runs **one Huey worker process** that executes synthesis
tasks sequentially. This is a deliberate architectural constraint:

- Huey's default behavior with a single worker process processes one task at a
  time from the queue.
- Multiple synthesis jobs submitted simultaneously will queue in Redis and execute
  one after another — they do not run in parallel.
- CTGAN training is CPU- and memory-intensive. Parallel training jobs would
  compete for RAM and CPU, degrading all jobs.

**Practical limits:**

| Scenario | Behavior |
|----------|----------|
| 1 job running | Normal operation |
| 2–10 jobs queued | Jobs execute in order; queue visible via `GET /jobs` |
| 100+ jobs queued | Queue grows; Redis memory usage increases ~KB per queued task |

To process multiple jobs concurrently, you would need to run multiple Huey
worker processes. This is not the default configuration and has not been tested.
If you require parallel synthesis, contact the development team.

### Job Timeout

The SSE stream times out after 3600 poll cycles at 1 second per cycle — a
maximum stream lifetime of **1 hour per job**. If a synthesis job takes longer
than 1 hour, the SSE connection will close with a timeout error event. The job
itself continues running in Huey; you can reconnect the stream or poll `GET /jobs/<id>`
for status.

---

## 3. SSE Client Limits

### Polling Architecture

The Server-Sent Events (SSE) endpoint (`/jobs/<job-id>/stream`) uses database
polling rather than WebSockets or persistent pub/sub:

| Parameter | Value | Source |
|-----------|-------|--------|
| Polling interval | **1 second** | `_POLL_INTERVAL_S = 1.0` in `bootstrapper/sse.py` |
| Max stream lifetime | **3600 cycles** (1 hour) | `_MAX_POLL_CYCLES = 3600` |

### Concurrent Client Scaling

Each connected SSE client issues one database query per second via
`asyncio.to_thread` (to avoid blocking the event loop). This is a
**SELECT by primary key** — the cheapest possible query.

| Concurrent SSE clients | Estimated DB queries/sec | Notes |
|------------------------|--------------------------|-------|
| 10 | ~10 queries/sec | Negligible load |
| 50 | ~50 queries/sec | Normal operation |
| 100 | ~100 queries/sec | Moderate; monitor PgBouncer queue depth |
| 500 | ~500 queries/sec | Heavy; verify PgBouncer and PostgreSQL can sustain |

**PostgreSQL throughput reference:** A well-tuned PostgreSQL instance on
modern hardware handles 5,000–50,000 simple SELECT operations per second,
depending on indexing, connection overhead, and hardware. 500 SSE clients at
100 queries/sec is within range but warrants monitoring.

**Recommended maximum for production without dedicated tuning: 100 concurrent
SSE clients.** Beyond this, operators should:

1. Reduce polling frequency by adjusting `_POLL_INTERVAL_S` (requires code
   change — not an environment variable).
2. Add a caching layer in front of the job status query.
3. Scale PgBouncer's connection pool.

---

## 4. CTGAN Training — Memory Constraints

### Memory Model

CTGAN (via SDV's `CTGANSynthesizer`) loads the **entire training DataFrame into
memory** before training begins. There is no streaming or chunked training path.

The synthesis engine (`modules/synthesizer/`) includes an OOM pre-flight
guardrail that estimates memory requirements before starting. The estimate uses
a **6x overhead factor** over the raw DataFrame byte size:

- 1x for the raw DataFrame itself
- ~5x for gradient buffers, optimizer state, VGM normalization buffers, and
  intermediate tensors during backpropagation

**Formula:**

```
estimated_ram_bytes = df_rows * df_columns * bytes_per_float32 * 6
```

If `estimated_ram_bytes > available_system_ram`, the job raises
`OOMGuardrailError` and fails cleanly before training starts.

### Memory by Dataset Size

The following estimates assume 10 columns of mixed numeric/categorical data.
Actual consumption depends on column count, data types, and epoch count.

| Dataset Rows | Approx Raw DataFrame Size | Estimated Training RAM (6x) |
|-------------|--------------------------|------------------------------|
| 10,000 | ~1.6 MB | ~10 MB |
| 100,000 | ~16 MB | ~100 MB |
| 500,000 | ~80 MB | ~480 MB |
| 1,000,000 | ~160 MB | ~960 MB |
| 5,000,000 | ~800 MB | ~4.8 GB |
| 10,000,000 | ~1.6 GB | ~9.6 GB |

**Note:** These are estimates for the training loop only. The Docker container
also needs RAM for the FastAPI process, Python interpreter, and other services
sharing the host. Add 512 MB–1 GB overhead for the application itself.

### GPU Memory (DP-SGD Training)

When Opacus DP-SGD is enabled, the proxy linear model training also occupies
GPU memory proportional to `n_features` (number of numeric columns after
VGM preprocessing). For typical datasets with fewer than 100 features, GPU
memory usage for the proxy model is under 100 MB. The CTGAN model training
is the primary GPU consumer.

---

## 5. Recommended Hardware by Dataset Size

The following tiers are based on the memory model above and practical
experience running CTGAN training jobs. "Small" datasets train quickly on any
modern server; "Large" datasets require significant resources.

### Tier 1: Small Datasets (up to 100,000 rows)

| Component | Recommended |
|-----------|-------------|
| CPU | 4 cores |
| RAM | 8 GB |
| Disk | 50 GB SSD |
| GPU | Optional |
| PgBouncer pool_size | 10–20 server connections |

Suitable for: development, testing, compliance audits on small PII tables.

### Tier 2: Medium Datasets (100,000 – 1,000,000 rows)

| Component | Recommended |
|-----------|-------------|
| CPU | 8 cores |
| RAM | 16–32 GB |
| Disk | 100 GB SSD |
| GPU | Recommended (NVIDIA GPU with ≥8 GB VRAM) |
| PgBouncer pool_size | 20–50 server connections |

Suitable for: production deployments with customer or transaction data.
GPU significantly reduces training time from hours to minutes at 300 epochs.

### Tier 3: Large Datasets (1,000,000 – 10,000,000 rows)

| Component | Recommended |
|-----------|-------------|
| CPU | 16+ cores |
| RAM | 64 GB |
| Disk | 200 GB+ SSD (NVMe preferred) |
| GPU | Required (NVIDIA A100 or equivalent with ≥40 GB VRAM) |
| PgBouncer pool_size | 50–100 server connections |

Suitable for: large-scale PII masking projects, data lake synthesis pipelines.
Training at 300 epochs on 10M rows will take multiple hours even on high-end
GPU hardware. Consider reducing epoch count or using `num_rows` sampling.

### Maximum Tested Dataset Size

The maximum dataset size validated in the Conclave Engine integration test suite
is **1,000,000 rows with 20 columns** on a machine with 32 GB RAM and an
NVIDIA A10 GPU. Larger datasets have been tested experimentally to 5M rows with
64 GB RAM but are not part of the automated test suite.

---

## 6. Expected Synthesis Latency

Latency is dominated by the CTGAN training loop. Sampling (generation) after
training is fast (<5 seconds for up to 100,000 synthetic rows).

### Training Latency Ranges (300 epochs)

| Dataset Size | CPU Only | NVIDIA GPU (A10/A100) |
|-------------|----------|-----------------------|
| 10,000 rows | 2–5 minutes | 30–90 seconds |
| 100,000 rows | 20–60 minutes | 5–15 minutes |
| 500,000 rows | 3–8 hours | 30–90 minutes |
| 1,000,000 rows | 8–20+ hours | 2–5 hours |

**Reducing epoch count trades synthesis quality for speed.** For exploratory
work or pipeline testing, 10–50 epochs often produces acceptable distributions
in a fraction of the time. For compliance-grade synthesis, 300+ epochs is
recommended.

### DP-SGD Overhead

Enabling Opacus DP-SGD adds a per-batch overhead for per-sample gradient
clipping and noise injection. Empirically, DP-SGD training is approximately
**2–4x slower** than vanilla CTGAN training on the same hardware and dataset.

---

## 7. Memory Footprint Per 1M Rows

Based on the 6x overhead model and empirical observations:

| Measurement | Value |
|-------------|-------|
| Raw 1M-row DataFrame (10 columns, float32) | ~160 MB |
| CTGAN training peak RAM (6x overhead) | ~960 MB |
| CTGAN model artifact size (serialized) | 5–50 MB (depends on hidden layer config) |
| MinIO ephemeral storage for 1M-row Parquet | ~200–400 MB (depends on compression) |
| Total system RAM requirement | ~1.5–2 GB for training alone |

For multi-table synthesis (topological training of FK-linked tables), multiply
the per-table estimate by the number of tables. Tables are trained sequentially,
so peak RAM is the maximum of any single table's requirement — not the sum.

---

## 8. References

- `src/synth_engine/shared/db.py` — `_POOL_SIZE`, `_MAX_OVERFLOW` constants
- `src/synth_engine/bootstrapper/sse.py` — `_POLL_INTERVAL_S`, `_MAX_POLL_CYCLES` constants
- `src/synth_engine/modules/synthesizer/dp_training.py` — Opacus proxy model training pattern
- `docs/DISASTER_RECOVERY.md` Section 2 — OOM event recovery procedures
- `docs/OPERATOR_MANUAL.md` Section 1 — Hardware requirements table
- `docs/DP_QUALITY_REPORT.md` — Empirical epsilon benchmarks on 500-row dataset
- `docs/adr/ADR-0017-synthesizer-dp-library-selection.md` — CTGAN/Opacus selection rationale
