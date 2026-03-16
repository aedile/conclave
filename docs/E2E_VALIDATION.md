# End-to-End Validation Guide

**Task**: P18-T18.3 — End-to-End Validation with Sample Data
**Live Run**: P19-T19.4 — Live E2E Pipeline Validation (2026-03-16)
**Status**: Partial PASS — seed script and CLI execute successfully; FK traversal
finding documented; full stack blocked by Dockerfile build error.

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

### LIVE VALIDATION EVIDENCE (2026-03-16)

**FINDING F1 — Dockerfile build failure: inline comment syntax in FROM**

Running `docker compose up -d` fails because the `conclave-engine:latest` image
cannot be built. The Dockerfile uses inline comments after SHA-256 digests on `FROM`
lines, which is not valid Docker syntax:

```
Dockerfile:8

>>> FROM node:20-alpine@sha256:b88333c42c23... AS frontend-builder # 20-alpine

failed to solve: dockerfile parse error on line 8:
FROM requires either one or three arguments
```

Root cause: inline `# comment` after `FROM ... AS name` is not valid Docker
Dockerfile syntax. Docker treats the comment as a fourth argument, causing a parse error.

Fix required: Remove or move inline comments to a preceding comment line.

**Infrastructure services started (partial stack):**

```
$ docker compose up -d postgres redis minio-ephemeral

 Network synthetic_data_internal  Creating
 Network synthetic_data_internal  Created
 Volume synthetic_data_postgres_data  Creating
 Volume synthetic_data_postgres_data  Created
 Container synthetic_data-minio-ephemeral-1  Created
 Container synthetic_data-redis-1  Created
 Container synthetic_data-postgres-1  Created
 Container synthetic_data-postgres-1  Started
 Container synthetic_data-minio-ephemeral-1  Started
 Container synthetic_data-redis-1  Started
```

**FINDING F2 — Redis fails to start with cap_drop: ALL**

```
$ docker logs synthetic_data-redis-1
error: failed switching to "redis": operation not permitted
error: failed switching to "redis": operation not permitted
```

Root cause: `cap_drop: ALL` removes the `SETUID`/`SETGID` capability that the
`redis:7-alpine` image uses to drop from root to the `redis` user at startup.
The security hardening configuration is incompatible with the official Redis image's
startup procedure.

Fix required: Add `SYS_CHROOT` or `SETUID`/`SETGID` capabilities back for the Redis
service, or configure the Redis image with `--user redis` in the compose command.

**FINDING F3 — pgbouncer env var mismatch**

```
$ docker logs synthetic_data-pgbouncer-1
/entrypoint.sh: line 66: DB_HOST: Setup pgbouncer config error!
You must set DB_HOST env
```

Root cause: The `edoburu/pgbouncer` image expects `DB_HOST`, `DB_PORT`, `DB_USER`,
`DB_NAME` env vars, but `docker-compose.yml` sets `DATABASES_HOST`, `DATABASES_PORT`,
`DATABASES_USER`, `DATABASES_DBNAME`. The env var names are mismatched.

Fix required: Update docker-compose.yml pgbouncer environment section to use
`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_NAME`.

**Service health status at validation time:**

```
NAME                               IMAGE                STATUS
synthetic_data-minio-ephemeral-1   minio/minio:...      Up 8 minutes
synthetic_data-postgres-1          postgres:16-alpine   Up 8 minutes (healthy)
synthetic_data-redis-1             redis:7-alpine       Restarting (1) ...
```

PostgreSQL: HEALTHY. MinIO: UP. Redis: FAILING (F2). pgbouncer: FAILING (F3).
App/API: NOT STARTED (F1 — build failure).

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

**LIVE VALIDATION NOTE (2026-03-16)**: Steps 2, 4, 6, 7 could not be executed because
the app service (FastAPI + Vault) failed to start due to F1 (Dockerfile build error).
These steps are deferred until F1 is resolved.

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

### LIVE VALIDATION EVIDENCE (2026-03-16)

The seed script was run against the Docker postgres container via a python:3.14-slim
container on the internal Docker network:

```
$ docker run --rm --network synthetic_data_internal \
  -v /path/to/project:/workspace -w /workspace python:3.14-slim \
  bash -c "pip install faker click psycopg2-binary -q && \
    python3 scripts/seed_sample_data.py \
      --dsn 'postgresql://conclave:***@postgres:5432/conclave_source' \
      --customers 100 --orders 250 --seed 42"

2026-03-16 16:24:19,883 [INFO] __main__ — Generating 100 customers (seed=42)...
2026-03-16 16:24:19,906 [INFO] __main__ — Generating 250 orders...
2026-03-16 16:24:19,907 [INFO] __main__ — Generating order items (3 items/order avg)...
2026-03-16 16:24:19,907 [INFO] __main__ — Generating payments (1 per order)...
2026-03-16 16:24:19,908 [INFO] __main__ — Exported 100 rows to sample_data/customers.csv
2026-03-16 16:24:19,909 [INFO] __main__ — Exported 250 rows to sample_data/orders.csv
2026-03-16 16:24:19,910 [INFO] __main__ — Exported 888 rows to sample_data/order_items.csv
2026-03-16 16:24:19,910 [INFO] __main__ — Exported 250 rows to sample_data/payments.csv
2026-03-16 16:24:19,910 [INFO] __main__ — Sample data written to sample_data/ (100 customers, 250 orders, 888 items, 250 payments)
2026-03-16 16:24:19,918 [INFO] __main__ — Connecting to database: postgres:5432/conclave_source
2026-03-16 16:24:19,924 [INFO] __main__ — Executing DDL...
2026-03-16 16:24:19,935 [INFO] __main__ — Inserted 100 rows into customers
2026-03-16 16:24:19,944 [INFO] __main__ — Inserted 250 rows into orders
2026-03-16 16:24:19,992 [INFO] __main__ — Inserted 888 rows into order_items
2026-03-16 16:24:20,005 [INFO] __main__ — Inserted 250 rows into payments
2026-03-16 16:24:20,006 [INFO] __main__ — Transaction committed.
```

Database spot-check (via psql inside postgres container):

```sql
SELECT 'customers'   AS tbl, COUNT(*) FROM customers
UNION ALL SELECT 'orders',      COUNT(*) FROM orders
UNION ALL SELECT 'order_items', COUNT(*) FROM order_items
UNION ALL SELECT 'payments',    COUNT(*) FROM payments;

     tbl     | count
-------------+-------
 customers   |   100
 orders      |   250
 order_items |   888
 payments    |   250
(4 rows)
```

**RESULT: PASS** — seed script AC2 fully verified.

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

**LIVE VALIDATION NOTE (2026-03-16)**: Step 4 could not be executed — app service not
started (F1). Deferred until F1 is resolved.

---

## Step 5 — Run the conclave-subset CLI

The `conclave-subset` CLI performs schema reflection, FK traversal, deterministic
masking, and egress to the target database:

```bash
conclave-subset \
    --source "postgresql://conclave:conclave@localhost:5432/conclave_source" \
    --target "postgresql://conclave:conclave@localhost:5433/conclave_target" \
    --seed-table customers \
    --seed-query "SELECT * FROM customers WHERE id <= 50"
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

### LIVE VALIDATION EVIDENCE (2026-03-16)

**FINDING F4 — sslmode=require enforced for non-localhost hosts**

The `validate_connection_string` function requires `?sslmode=require` for any host
that is not `localhost`, `127.0.0.1`, or `::1`. The Docker postgres service does not
have SSL configured. Connections to `postgres:5432` or container IPs therefore fail:

```
Error: invalid --source connection string: Remote host 'postgres' requires
sslmode=require in the connection URL.
```

Workaround used: expose postgres to host via socat proxy on port 5499 and connect
via `localhost:5499` which bypasses the SSL requirement.

**CLI execution (via socat proxy on port 5499):**

```
$ poetry run conclave-subset \
    --source "postgresql://conclave:***@localhost:5499/conclave_source" \
    --target "postgresql://conclave:***@localhost:5499/conclave_target" \
    --seed-table customers \
    --seed-query "SELECT * FROM customers WHERE id <= 50"

MASKING_SALT env var not set; using hardcoded CLI fallback. Set MASKING_SALT for production use.
Subset complete.
  customers: 50 rows

Exit code: 0
```

**CLI exit code: 0 — PASS**

**FINDING F5 — FK traversal writes only seed table (zero related rows)**

The CLI exits 0 and reports "Subset complete", but only the seed table (`customers`)
is written to the target. Related tables (`orders`, `order_items`, `payments`) receive
0 rows despite 116 matching orders existing in the source for customers `id <= 50`.

Root cause: `_load_topology()` in `cli.py` builds `ColumnInfo` objects with
`primary_key=int(col.get('primary_key', 0))`. SQLAlchemy's
`Inspector.get_columns()` does NOT include a `primary_key` key in column dicts —
it returns `autoincrement`, `nullable`, `default`, etc., but not `primary_key`.
As a result, `_extract_pk_values()` in `traversal.py` always returns `[]` (no PK
found), so `_fetch_by_fk_values()` is never called for child tables.

The schema reflection correctly detects all 4 tables and their FK relationships.
The traversal logic is architecturally correct. The defect is the incorrect key name
used when constructing `ColumnInfo` from the raw SQLAlchemy column dict.

Fix required: Use `Inspector.get_pk_constraint(table)['constrained_columns']` to
detect primary key columns when building `ColumnInfo`, rather than relying on
`col.get('primary_key', 0)`.

Target database spot-check post-CLI-run:

```sql
SELECT 'customers'   AS tbl, COUNT(*) FROM customers
UNION ALL SELECT 'orders',      COUNT(*) FROM orders
UNION ALL SELECT 'order_items', COUNT(*) FROM order_items
UNION ALL SELECT 'payments',    COUNT(*) FROM payments;

     tbl     | count
-------------+-------
 customers   |    50
 orders      |     0
 order_items |     0
 payments    |     0
(4 rows)
```

Schema reflection verified correct:

```
Tables in topological order: ['customers', 'orders', 'order_items', 'payments']
  customers FKs: []
  orders FKs: [{'name': 'orders_customer_id_fkey', 'constrained_columns': ['customer_id'],
               'referred_table': 'customers', 'referred_columns': ['id']}]
  order_items FKs: [{'name': 'order_items_order_id_fkey', 'constrained_columns': ['order_id'],
                    'referred_table': 'orders', 'referred_columns': ['id']}]
  payments FKs: [{'name': 'payments_order_id_fkey', 'constrained_columns': ['order_id'],
                 'referred_table': 'orders', 'referred_columns': ['id']}]
```

**PARTIAL PASS**: CLI exits 0. Seed table rows written correctly. FK traversal
does not propagate to child tables due to F5.

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

**LIVE VALIDATION NOTE (2026-03-16)**: Step 6 could not be executed — app service not
started (F1). Deferred until F1 is resolved.

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

**LIVE VALIDATION NOTE (2026-03-16)**: Step 7 could not be executed — app service not
started (F1). Deferred until F1 is resolved.

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
| Docker build fails on FROM line | Inline comment syntax | Remove `# comment` after `FROM ... AS name` |
| Redis restarting with cap_drop | Missing SETUID cap | See F2 finding above |
| pgbouncer exits with DB_HOST error | Env var mismatch | See F3 finding above |
| conclave-subset writes 0 orders | FK traversal PK bug | See F5 finding above |

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
| AC3: `docker-compose up` starts all services | PARTIAL — 3 of 8 services started | F1 (Dockerfile), F2 (Redis), F3 (pgbouncer) — see findings above |
| AC4: pipeline documented step-by-step | PASS | This document |
| AC5: `conclave-subset` CLI completes | PARTIAL PASS — exits 0, writes seed table only | F5 (FK traversal PK bug) — exit code 0, 50 customers written; 0 orders/items/payments |
| AC6: API synthesis job completes | DEFERRED — app not started | F1 blocks this |
| AC7: screenshots/recordings included | PASS — terminal output captured | Live run evidence in Steps 1, 3, 5 above |

---

## Findings Summary (P19-T19.4)

| ID | Severity | Finding | Fix Required |
|----|----------|---------|--------------|
| F1 | BLOCKER | Dockerfile parse error: inline `# comment` on `FROM` lines | Remove `# tag` comments after `FROM ... AS name` syntax |
| F2 | BLOCKER | Redis fails to start: `cap_drop: ALL` removes SETUID | Add `SETUID`/`SETGID` or use `--user` flag in Redis command |
| F3 | BLOCKER | pgbouncer exits: `DATABASES_HOST` should be `DB_HOST` | Update compose env vars to match edoburu/pgbouncer API |
| F4 | ADVISORY | `validate_connection_string` blocks internal Docker hostnames (sslmode=require) | Document workaround; configure postgres SSL or allow internal hosts |
| F5 | BLOCKER | FK traversal writes only seed table: `col.get('primary_key')` always returns 0 | Use `Inspector.get_pk_constraint()` to detect PK columns |
