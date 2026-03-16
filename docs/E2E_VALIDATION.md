# End-to-End Validation Guide

**Task**: P18-T18.3 — End-to-End Validation with Sample Data
**Status**: Infrastructure complete. Live run pending Docker Compose environment.

This document describes the step-by-step process for running the full Conclave Engine
pipeline against the fictional sample dataset committed to `sample_data/`.

The pipeline exercises: source DB population, schema reflection, FK graph traversal,
deterministic masking, CTGAN training (or FORCE_CPU=true fallback), and egress to
target database.

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Docker Desktop | 4.x+ | With Compose V2 plugin |
| Poetry | 1.8+ | `pip install poetry` |
| 8 GB RAM | — | Minimum; 16 GB recommended for CTGAN |
| GPU (optional) | CUDA 11.8+ | Set `FORCE_CPU=true` to skip |

---

## Step 1 — Start the Docker Compose Stack

```bash
# Start all services in the background
docker-compose up -d

# Verify all services are healthy (wait ~30 seconds on first run)
docker-compose ps
```

Expected output: all services in `healthy` state.

| Service | Port | Role |
|---------|------|------|
| `conclave_api` | 8000 | FastAPI application |
| `conclave_db` | 5432 | Source PostgreSQL |
| `conclave_target_db` | 5433 | Target PostgreSQL (egress destination) |
| `conclave_redis` | 6379 | Task queue (Huey) |
| `conclave_vault` | 8200 | HashiCorp Vault (key management) |
| `pgbouncer` | 6432 | Connection pooler |
| `prometheus` | 9090 | Metrics scraper |
| `grafana` | 3000 | Metrics dashboard |

```bash
# Check API health endpoint
curl -s http://localhost:8000/healthz | jq .
```

Expected: `{"status": "ok"}`

TODO: capture terminal recording of docker-compose up and health check during live validation run

---

## Step 2 — Unseal Vault and Obtain API Token

The API requires a valid JWT token. On first startup, Vault must be unsealed:

```bash
# Unseal Vault (use the unseal key from secrets/ — see OPERATOR_MANUAL.md)
curl -s -X POST http://localhost:8200/v1/sys/unseal \
    -H "Content-Type: application/json" \
    -d '{"key": "<UNSEAL_KEY>"}'

# Obtain a short-lived API token
curl -s -X POST http://localhost:8000/auth/token \
    -H "Content-Type: application/json" \
    -d '{"username": "admin", "password": "<ADMIN_PASSWORD>"}' | jq .access_token
```

Store the token in an environment variable for subsequent steps:

```bash
export CONCLAVE_TOKEN="<token_from_above>"
```

TODO: capture screenshot of successful Vault unseal and token issuance during live validation run

---

## Step 3 — Seed the Source Database with Sample Data

The seeding script uses Faker (seed=42) to generate deterministic fictional data.

```bash
# Export CSV files and execute DDL+INSERTs against the source database
poetry run python3 scripts/seed_sample_data.py \
    --output-dir sample_data \
    --customers 100 \
    --orders 250 \
    --seed 42 \
    --dsn "postgresql://conclave:conclave@localhost:5432/conclave_source"
```

Expected output:

```
[INFO] Generating 100 customers (seed=42)...
[INFO] Generating 250 orders...
[INFO] Generating order items (3 items/order avg)...
[INFO] Generating payments (1 per order)...
[INFO] Exported 100 rows to sample_data/customers.csv
[INFO] Exported 250 rows to sample_data/orders.csv
[INFO] Exported ~888 rows to sample_data/order_items.csv
[INFO] Exported 250 rows to sample_data/payments.csv
[INFO] Transaction committed.
```

Verify data in the source database:

```bash
psql postgresql://conclave:conclave@localhost:5432/conclave_source \
    -c "SELECT COUNT(*) FROM customers; SELECT COUNT(*) FROM orders;"
```

Expected: 100 customers, 250 orders.

TODO: capture terminal recording of seeding and database verification during live validation run

---

## Step 4 — Configure a Subsetting Connection

Register the source and target database connections via the API:

```bash
# Register source connection
curl -s -X POST http://localhost:8000/connections \
    -H "Authorization: Bearer $CONCLAVE_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
        "name": "sample_source",
        "dsn": "postgresql://conclave:conclave@conclave_db:5432/conclave_source",
        "role": "source"
    }' | jq .

# Register target connection
curl -s -X POST http://localhost:8000/connections \
    -H "Authorization: Bearer $CONCLAVE_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
        "name": "sample_target",
        "dsn": "postgresql://conclave:conclave@conclave_target_db:5433/conclave_target",
        "role": "target"
    }' | jq .
```

TODO: capture API response screenshots during live validation run

---

## Step 5 — Run the conclave-subset CLI

The `conclave-subset` CLI performs schema reflection, FK traversal, deterministic
masking, and egress to the target database:

```bash
conclave-subset \
    --source-dsn "postgresql://conclave:conclave@localhost:5432/conclave_source" \
    --target-dsn "postgresql://conclave:conclave@localhost:5433/conclave_target" \
    --root-table customers \
    --where "id <= 50" \
    --mask-columns "email,ssn,phone,address,first_name,last_name"
```

What to look for:
- Schema reflection logs: `Reflected N tables, M foreign keys`
- FK traversal: `Traversing customers -> orders -> order_items (depth 2)`
- Masking: `Masking column 'email' via deterministic substitution`
- Egress: `Inserted N rows into target.customers`
- Exit code 0 (no error)

Expected row counts in target database:
- `customers`: 50 rows (matching `id <= 50` predicate)
- `orders`: All orders belonging to those 50 customers
- `order_items`: All items belonging to those orders
- `payments`: All payments belonging to those orders

```bash
# Verify target database contents
psql postgresql://conclave:conclave@localhost:5433/conclave_target \
    -c "SELECT COUNT(*) FROM customers; SELECT COUNT(*) FROM orders;"
```

TODO: capture terminal recording of conclave-subset execution and target DB verification during live validation run

---

## Step 6 — Run API-Driven Synthesis Job

Submit a synthesis job via the SSE endpoint (CTGAN with CPU fallback):

```bash
# Submit synthesis job
curl -s -X POST http://localhost:8000/tasks/synthesize \
    -H "Authorization: Bearer $CONCLAVE_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
        "table_name": "customers",
        "parquet_path": "/data/customers.parquet",
        "total_epochs": 5,
        "checkpoint_every_n": 1,
        "force_cpu": true
    }' | jq .task_id
```

Store the task ID and stream progress via SSE:

```bash
export TASK_ID="<task_id_from_above>"
curl -s -N http://localhost:8000/tasks/$TASK_ID/stream \
    -H "Authorization: Bearer $CONCLAVE_TOKEN"
```

Expected SSE events:

```
data: {"event": "progress", "epoch": 1, "loss": 2.34}
data: {"event": "progress", "epoch": 2, "loss": 1.98}
...
data: {"event": "progress", "epoch": 5, "loss": 0.87}
data: {"event": "complete", "artifact_path": "/output/customers_synthetic.parquet"}
```

TODO: capture terminal recording of SSE stream during live validation run

---

## Step 7 — Verify Synthetic Output Quality

After the synthesis job completes, verify statistical similarity between the
original and synthetic data using the StatisticalProfiler:

```bash
# Profile original data
poetry run python3 -c "
from synth_engine.modules.profiler.profiler import StatisticalProfiler
import pandas as pd
orig = pd.read_parquet('/data/customers.parquet')
synth = pd.read_parquet('/output/customers_synthetic.parquet')
profiler = StatisticalProfiler()
orig_profile = profiler.profile(orig)
synth_profile = profiler.profile(synth)
print('Original shape:', orig.shape)
print('Synthetic shape:', synth.shape)
print('Column drift report:')
for col in orig.columns:
    print(f'  {col}: orig_mean={orig_profile[col].get(\"mean\", \"N/A\"):.4f}, '
          f'synth_mean={synth_profile[col].get(\"mean\", \"N/A\"):.4f}')
"
```

Acceptable thresholds (from DP_QUALITY_REPORT.md):
- Column means within 10% of original for numeric columns
- Categorical distribution KL-divergence < 0.1

TODO: capture profiler output and quality metrics during live validation run

---

## Step 8 — Teardown

```bash
# Stop all services and remove containers (preserves named volumes)
docker-compose down

# Full teardown including volumes (resets database state)
docker-compose down -v
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Service not healthy | Docker resource limits | Increase Docker memory to 8+ GB |
| Vault unseal fails | Wrong unseal key | Check `secrets/` — see OPERATOR_MANUAL.md |
| DB connection refused | Services still starting | Wait 30s, retry `docker-compose ps` |
| CTGAN OOM | Insufficient RAM | Set `--total-epochs 2`, or use `--force-cpu true` |
| SSE stream times out | Redis unavailable | Check `conclave_redis` health |
| `conclave-subset` not found | Poetry env not active | Run `poetry install` first |

---

## Sample Data Schema Reference

All tables are generated by `scripts/seed_sample_data.py` using Faker (seed=42).

### customers

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | Sequential integer |
| first_name | VARCHAR(100) | Faker: `first_name()` |
| last_name | VARCHAR(100) | Faker: `last_name()` |
| email | VARCHAR(255) UNIQUE | Faker: `email()` — PII-like |
| ssn | VARCHAR(11) | Faker: `ssn()` — PII-like, e.g. `XXX-XX-XXXX` |
| phone | VARCHAR(30) | Faker: `phone_number()` — PII-like |
| address | TEXT | Faker: `address()` — PII-like |
| created_at | TIMESTAMP | Random within last 3 years |

### orders

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | Sequential integer |
| customer_id | INTEGER FK→customers | FK validated |
| order_date | TIMESTAMP | Random within last 2 years |
| total_amount | NUMERIC(10,2) | Between 9.99 and 2499.99 |
| status | VARCHAR(20) | One of: pending, processing, shipped, delivered, cancelled |

### order_items

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | Sequential integer |
| order_id | INTEGER FK→orders | FK validated |
| product_name | VARCHAR(200) | From 20-item product catalogue |
| quantity | INTEGER | Between 1 and 10 |
| unit_price | NUMERIC(10,2) | Between 0.99 and 499.99 |

### payments

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | Sequential integer |
| order_id | INTEGER FK→orders | FK validated |
| payment_date | TIMESTAMP | Within 30 days of order |
| amount | NUMERIC(10,2) | Matches order total_amount |
| payment_method | VARCHAR(30) | One of: credit_card, debit_card, bank_transfer, paypal, cash |

---

## Acceptance Criteria Mapping

| AC | Status | Evidence |
|----|--------|---------|
| AC1: seed script creates `sample_data/` | PASS | `scripts/seed_sample_data.py` + committed CSVs |
| AC2: `sample_data/` populated with CSVs | PASS | 4 CSV files committed |
| AC3: `docker-compose up` starts all services | TODO: capture during live validation run | Steps 1+2 above |
| AC4: pipeline documented step-by-step | PASS | This document |
| AC5: `conclave-subset` CLI completes | TODO: capture during live validation run | Step 5 above |
| AC6: API synthesis job completes | TODO: capture during live validation run | Step 6 above |
| AC7: screenshots/recordings included | TODO: capture during live validation run | Placeholder sections above |
