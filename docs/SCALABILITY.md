# Conclave Engine — Scalability Reference

Capacity limits, resource constraints, and hardware sizing. All figures are derived from source code configuration and empirical benchmarks.

---

## 1. Database Connection Pool

### Pool Configuration

The SQLAlchemy engine factory (`src/synth_engine/shared/db.py`) uses a `QueuePool`:

| Parameter | Value | Description |
|-----------|-------|-------------|
| `_POOL_SIZE` | `5` | Persistent connections kept open at all times |
| `_MAX_OVERFLOW` | `10` | Additional connections allowed above `POOL_SIZE` |
| **Peak connections** | **15** | Maximum simultaneous DB connections per engine instance |

### PgBouncer Dependency

Production connects through PgBouncer (transaction-mode pooler), not directly to PostgreSQL. The pool values are intentionally modest because PgBouncer handles external multiplexing.

- The engine cache (`_engine_cache`, `_async_engine_cache`) returns the same engine for the same URL, preventing redundant pool allocation.
- **Without PgBouncer**, each concurrent request may hold one of the 15 connections. At 150+ concurrent requests with slow queries, pool exhaustion is likely.

### Connection Pool Exhaustion

Symptoms:
- Requests hang with no response.
- Logs show: `TimeoutError: QueuePool limit of size 5 overflow 10 reached`.
- Grafana shows elevated `db_connection_wait_seconds`.

Mitigation: increase PgBouncer's `pool_size` before adjusting application-level `_POOL_SIZE`.

---

## 2. Synthesis Job Concurrency

### Single Huey Worker Serializes Jobs

Conclave runs **one Huey worker process** — synthesis tasks execute sequentially. Multiple jobs queue in Redis and run one after another. This is deliberate: parallel CTGAN training jobs would compete for RAM and CPU, degrading all jobs.

| Scenario | Behavior |
|----------|----------|
| 1 job running | Normal operation |
| 2–10 jobs queued | Jobs execute in order; queue visible via `GET /jobs` |
| 100+ jobs queued | Queue grows; Redis memory increases ~KB per queued task |

Multiple Huey workers would enable concurrency but is untested. Contact the development team if parallel synthesis is required.

### Job Timeout

The SSE stream times out after 3600 poll cycles at 1 second each — **1 hour maximum stream lifetime per job**. If a job exceeds 1 hour, the SSE connection closes with a timeout error. The job continues running in Huey; reconnect the stream or poll `GET /jobs/<id>` for status.

---

## 3. SSE Client Limits

### Polling Architecture

The SSE endpoint (`/jobs/<job-id>/stream`) uses database polling:

| Parameter | Value | Source |
|-----------|-------|--------|
| Polling interval | **1 second** | `_POLL_INTERVAL_S = 1.0` in `bootstrapper/sse.py` |
| Max stream lifetime | **3600 cycles** (1 hour) | `_MAX_POLL_CYCLES = 3600` |

### Concurrent Client Scaling

Each SSE client issues one SELECT-by-primary-key per second via `asyncio.to_thread`.

| Concurrent SSE clients | Estimated DB queries/sec | Notes |
|------------------------|--------------------------|-------|
| 10 | ~10 queries/sec | Negligible load |
| 50 | ~50 queries/sec | Normal operation |
| 100 | ~100 queries/sec | Moderate; monitor PgBouncer queue depth |
| 500 | ~500 queries/sec | Heavy; verify PgBouncer and PostgreSQL can sustain |

A well-tuned PostgreSQL instance handles 5,000–50,000 simple SELECTs per second. **Recommended production maximum without dedicated tuning: 100 concurrent SSE clients.** Beyond this:

1. Reduce polling frequency by adjusting `_POLL_INTERVAL_S` (code change required).
2. Add a caching layer in front of the job status query.
3. Scale PgBouncer's connection pool.

---

## 4. CTGAN Training — Memory Constraints

### Memory Model

CTGAN loads the **entire training DataFrame into memory** before training. No streaming or chunked training path exists.

The synthesis engine includes an OOM pre-flight guardrail using a **6x overhead factor**:

```
estimated_ram_bytes = df_rows * df_columns * bytes_per_float32 * 6
```

The 6x covers: 1x raw DataFrame + ~5x gradient buffers, optimizer state, VGM normalization buffers, and intermediate tensors. If `estimated_ram_bytes > available_system_ram`, the job raises `OOMGuardrailError` before training starts.

### Memory by Dataset Size

Assumes 10 columns of mixed numeric/categorical data. Actual consumption depends on column count, data types, and epoch count.

| Dataset Rows | Approx Raw DataFrame Size | Estimated Training RAM (6x) |
|-------------|--------------------------|------------------------------|
| 10,000 | ~1.6 MB | ~10 MB |
| 100,000 | ~16 MB | ~100 MB |
| 500,000 | ~80 MB | ~480 MB |
| 1,000,000 | ~160 MB | ~960 MB |
| 5,000,000 | ~800 MB | ~4.8 GB |
| 10,000,000 | ~1.6 GB | ~9.6 GB |

Add 512 MB–1 GB for the FastAPI process, Python interpreter, and other co-located services.

### GPU Memory (DP-SGD Training)

Opacus DP-SGD (Phase 30+) is applied directly to `OpacusCompatibleDiscriminator`. For typical datasets with fewer than 100 features, DP-SGD GPU overhead is under 200 MB above base CTGAN memory. See ADR-0036.

---

## 5. Recommended Hardware by Dataset Size

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

Suitable for: production deployments with customer or transaction data. GPU reduces training time from hours to minutes at 300 epochs.

### Tier 3: Large Datasets (1,000,000 – 10,000,000 rows)

| Component | Recommended |
|-----------|-------------|
| CPU | 16+ cores |
| RAM | 64 GB |
| Disk | 200 GB+ SSD (NVMe preferred) |
| GPU | Required (NVIDIA A100 or equivalent with ≥40 GB VRAM) |
| PgBouncer pool_size | 50–100 server connections |

Suitable for: large-scale PII masking, data lake synthesis pipelines. Training 10M rows at 300 epochs takes multiple hours even on high-end GPU. Consider reducing epoch count or using `num_rows` sampling.

### Maximum Tested Dataset Size

**1,000,000 rows with 20 columns** on 32 GB RAM + NVIDIA A10 GPU. Experimentally tested to 5M rows with 64 GB RAM but not part of the automated test suite.

---

## 6. Expected Synthesis Latency

Latency is dominated by the CTGAN training loop. Sampling after training is fast (<5 seconds for up to 100,000 synthetic rows).

### Training Latency Ranges (300 epochs)

| Dataset Size | CPU Only | NVIDIA GPU (A10/A100) |
|-------------|----------|-----------------------|
| 10,000 rows | 2–5 minutes | 30–90 seconds |
| 100,000 rows | 20–60 minutes | 5–15 minutes |
| 500,000 rows | 3–8 hours | 30–90 minutes |
| 1,000,000 rows | 8–20+ hours | 2–5 hours |

Reducing epoch count trades synthesis quality for speed. For exploratory work, 10–50 epochs often produces acceptable distributions. For compliance-grade synthesis, 300+ epochs is recommended.

### DP-SGD Overhead

Opacus DP-SGD adds per-batch overhead for per-sample gradient clipping and noise injection. Empirically, **2–4x slower** than vanilla CTGAN on the same hardware and dataset.

---

## 7. Memory Footprint Per 1M Rows

| Measurement | Value |
|-------------|-------|
| Raw 1M-row DataFrame (10 columns, float32) | ~160 MB |
| CTGAN training peak RAM (6x overhead) | ~960 MB |
| CTGAN model artifact size (serialized) | 5–50 MB (depends on hidden layer config) |
| MinIO ephemeral storage for 1M-row Parquet | ~200–400 MB (depends on compression) |
| Total system RAM requirement | ~1.5–2 GB for training alone |

For multi-table synthesis, tables are trained sequentially — peak RAM is the maximum of any single table's requirement, not the sum.

---

## 8. References

- `src/synth_engine/shared/db.py` — `_POOL_SIZE`, `_MAX_OVERFLOW` constants
- `src/synth_engine/bootstrapper/sse.py` — `_POLL_INTERVAL_S`, `_MAX_POLL_CYCLES` constants
- `src/synth_engine/modules/synthesizer/dp_training.py` — Discriminator-level DP-SGD training (ADR-0036)
- `docs/DISASTER_RECOVERY.md` Section 2 — OOM event recovery procedures
- `docs/OPERATOR_MANUAL.md` Section 1 — Hardware requirements table
- `docs/DP_QUALITY_REPORT.md` — Empirical epsilon benchmarks on 500-row dataset
- `docs/adr/ADR-0017-synthesizer-dp-library-selection.md` — CTGAN/Opacus selection rationale
