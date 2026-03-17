# Conclave Engine — Troubleshooting Guide

This guide provides diagnostic flowcharts and resolution steps for the most
common operational failures. For hardware sizing and capacity limits, see
`docs/SCALABILITY.md`. For recovery from data loss or corruption, see
`docs/DISASTER_RECOVERY.md`.

**Audience:** Operators with access to the Docker host and application logs.

---

## How to Read This Guide

Each section follows a consistent pattern:

1. **Symptoms** — what you observe (log messages, API errors, service state)
2. **Likely causes** — in order of probability
3. **Diagnostic steps** — commands to run to isolate the cause
4. **Resolution** — specific fix for each cause

---

## 1. Huey Worker — Task Stuck in QUEUED

### Symptoms

- `GET /jobs/<id>` returns `"status": "QUEUED"` and has not changed for more
  than 60 seconds after calling `POST /jobs/<id>/start`
- No `"TRAINING"` log entries appear in `docker compose logs app`

### Diagnostic Steps

```bash
# Step 1: Verify the Huey worker is running
docker compose ps
# The 'app' service must show 'running' — Huey worker runs inside the app container

# Step 2: Check Huey worker logs for errors
docker compose logs app | grep -i "huey\|worker\|task"

# Step 3: Verify Redis is healthy and the task is in the queue
docker compose exec redis redis-cli llen conclave:default
# Non-zero = tasks are in the queue but not being consumed

# Step 4: Verify the app can connect to Redis
docker compose exec app env | grep REDIS_URL
docker compose exec app python3 -c "
import redis, os
r = redis.from_url(os.environ.get('REDIS_URL', 'redis://redis:6379/0'))
print('Redis ping:', r.ping())
"
```

### Resolution

| Cause | Resolution |
|-------|------------|
| Huey backend set to `memory` | Set `HUEY_BACKEND=redis` in `.env` and restart the `app` service. In-memory Huey does not have a background worker thread by default — tasks only run synchronously when `HUEY_IMMEDIATE=true`. |
| Redis is not running | Run `docker compose up -d redis` and then restart `app`. |
| Redis connection refused | Check `REDIS_URL` in `.env`. In Docker Compose, use `redis://redis:6379/0` (service name `redis`, not `localhost`). |
| Worker process crashed during startup | Check `docker compose logs app` for `ImportError` or startup exceptions. Fix the root cause (often a missing environment variable — see Section 6). |
| Task is orphaned after worker crash | Run `docker compose exec postgres psql -U conclave -c "UPDATE synthesisjob SET status='FAILED' WHERE status='QUEUED';"` to clean up, then resubmit. |

---

## 2. Huey Worker — Task Crashes or Fails Immediately

### Symptoms

- Job transitions from `QUEUED` to `FAILED` within seconds
- SSE stream emits an `error` event with a detail message
- `docker compose logs app` shows `FAILED` with an exception traceback

### Diagnostic Steps

```bash
# Get the error detail from the API
curl http://localhost:8000/jobs/<job-id>
# Check "error_msg" field

# Check application logs for the full traceback
docker compose logs app | grep -A 20 "job_id=<job-id>\|ERROR\|Exception"
```

### Common Failure Patterns and Resolutions

**`OOMGuardrailError: Estimated memory requirement exceeds available RAM`**

The pre-flight memory check failed. The dataset is too large for the available
RAM. Options:

1. Reduce `num_rows` in the job request.
2. Increase Docker memory limit in `docker-compose.override.yml` (see
   `docs/DISASTER_RECOVERY.md` Section 2.3).

**`FileNotFoundError: /data/...parquet`**

The Parquet file path does not exist inside the container.

1. Verify the file is in `data/` on the host (which is mounted into the container).
2. Check the `parquet_path` in the job request — it must be an **absolute path
   inside the container** (e.g., `/data/customers.parquet`), not a host path.

**`BudgetExhaustionError`**

The global privacy budget (`PRIVACY_BUDGET_EPSILON` in `.env`) has been exhausted.

1. Check remaining budget: `curl http://localhost:8000/privacy/budget`.
2. If the budget was consumed by failed or test jobs, the operator can reset it
   via the Privacy Accountant API (see `docs/OPERATOR_MANUAL.md` Section 9.5).

**`ImportError: The 'sdv' package is required`**

The synthesizer dependency group is not installed.

1. Run `poetry install --with synthesizer` and rebuild the Docker image.

---

## 3. MinIO / S3 Storage Failures

### Symptoms

- Job fails with `ConnectionError` or `S3Error` in the logs
- `docker compose logs app` shows `minio\|S3\|bucket\|storage` errors
- Job is `COMPLETE` but the download endpoint returns 404

### Diagnostic Steps

```bash
# Step 1: Verify MinIO is running
docker compose ps minio-ephemeral
# Should show 'running'

# Step 2: Check MinIO logs
docker compose logs minio-ephemeral

# Step 3: Verify MinIO is accessible from the app container
docker compose exec app curl -s http://minio-ephemeral:9000/minio/health/live
# Expected: 200 OK

# Step 4: Check credentials are set
docker compose exec app env | grep MINIO
```

### Resolution

| Cause | Resolution |
|-------|------------|
| MinIO container is not running | `docker compose up -d minio-ephemeral` |
| `MINIO_ENDPOINT` points to wrong host | In Docker Compose, the endpoint is `http://minio-ephemeral:9000`. Check `.env`. |
| Credentials mismatch | Compare `MINIO_EPHEMERAL_ACCESS_KEY` in `.env` with the value in `secrets/minio_ephemeral_access_key.txt`. They must match. |
| tmpfs is full (unlikely) | The ephemeral tmpfs is sized to available container memory. If training produces very large artefacts and memory is constrained, increase `docker-compose.override.yml` memory limits. |
| Output file expired | MinIO uses `tmpfs` — artefacts are discarded when the container stops. If the container was restarted after the job completed, the output file is gone. Rerun the synthesis job. |

---

## 4. Connection Pool Exhaustion

### Symptoms

- API requests hang for 5–30 seconds and eventually time out
- Application logs show: `TimeoutError: QueuePool limit of size 5 overflow 10 reached`
- Grafana shows elevated request latency

### Diagnostic Steps

```bash
# Step 1: Check PgBouncer connection pool stats
docker compose exec pgbouncer psql -U pgbouncer -p 6432 pgbouncer \
  -c "SHOW POOLS;" 2>/dev/null || echo "psql not available"

# Alternative: check PgBouncer via TCP
docker compose exec app python3 -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['PGBOUNCER_URL'])
cur = conn.cursor()
cur.execute('SHOW POOLS;')
print(cur.fetchall())
"

# Step 2: Count active DB connections
docker compose exec postgres psql -U conclave -c \
  "SELECT count(*) FROM pg_stat_activity WHERE state = 'active';"

# Step 3: Check for long-running queries
docker compose exec postgres psql -U conclave -c \
  "SELECT pid, state, query, now() - query_start AS duration FROM pg_stat_activity ORDER BY duration DESC NULLS LAST LIMIT 10;"
```

### Resolution

| Cause | Resolution |
|-------|------------|
| Long-running synthesis query holding connections | Synthesis tasks use their own session scope. If a session is leaked (connection not returned to pool), restart the `app` service to release all connections. |
| Too many concurrent SSE clients | 100+ concurrent SSE clients each poll the DB every second. Reduce SSE client count or increase PgBouncer `pool_size`. See `docs/SCALABILITY.md` Section 3. |
| PgBouncer misconfigured | Verify PgBouncer is running and the `PGBOUNCER_URL` in `.env` points to it (port 6432), not directly to PostgreSQL (port 5432). |
| Application-level connection leak | Check for unclosed sessions in application logs. File a bug if you identify a specific code path that fails to close its session. |

---

## 5. Vault Sealed Unexpectedly

### Symptoms

- All API routes (except `/health`, `/unseal`, `/metrics`) return `HTTP 423 Locked`
- Logs show: `SealGateMiddleware: vault is sealed`
- The React UI redirects to `/unseal`

### Diagnostic Steps

```bash
# Step 1: Confirm the vault is sealed
curl http://localhost:8000/health
# If sealed, response is still 200 (health check is exempt)

curl -X GET http://localhost:8000/jobs
# Returns 423 if sealed

# Step 2: Check if the app container was restarted
docker compose logs app | head -20
# Look for startup timestamps — a recent restart means the KEK was lost

# Step 3: Check if seal was triggered deliberately
docker compose exec app cat /tmp/audit.log | grep -i "seal\|shred"
```

### Why Vaults Re-Seal

The KEK (Key Encryption Key) exists **only in process memory**. It is never
written to disk. Any of the following events cause the vault to re-seal:

| Event | Result |
|-------|--------|
| Container restart | KEK is lost; must unseal again |
| Container crash (OOM kill, etc.) | KEK is lost |
| Docker host reboot | KEK is lost |
| Operator calls `POST /unseal/seal` | Intentional re-seal |
| Operator calls `POST /security/shred` | KEK is zeroized (irreversible) |

### Resolution

Unseal the vault using the operator passphrase:

```bash
curl -X POST http://localhost:8000/unseal \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "<operator-passphrase>"}'
```

If you receive `CONFIG_ERROR`, check that `VAULT_SEAL_SALT` is set in `.env`
and has not changed since the last successful unseal.

**If the passphrase was lost:** See `docs/DISASTER_RECOVERY.md` Section 3.2.
Data encrypted with ALE is unrecoverable without the passphrase.

---

## 6. Application Fails to Start

### Symptoms

- `docker compose up` shows `app` exiting with code 1 immediately
- `docker compose logs app` shows a configuration error

### Diagnostic Steps

```bash
docker compose logs app | head -50
# Look for "ConfigurationError", "missing", "required", "ARTIFACT_SIGNING_KEY"
```

### Common Startup Errors

**`ConfigurationError: DATABASE_URL is required`**

`DATABASE_URL` is not set in `.env`. Add it:

```bash
DATABASE_URL=postgresql+psycopg2://conclave:<password>@pgbouncer:6432/conclave
```

**`ConfigurationError: ARTIFACT_SIGNING_KEY is required in production mode`**

The application is running with `ENV=production` but `ARTIFACT_SIGNING_KEY` is
not set. Generate and set it:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
# Add as ARTIFACT_SIGNING_KEY=<value> in .env
```

**`ConfigurationError: AUDIT_KEY is required`**

Generate and set `AUDIT_KEY` in `.env`:

```bash
python3 -c "import os; print(os.urandom(32).hex())"
```

**Schema migration missing**

If the database schema is behind the current revision, the app exits with a
schema version error. Run:

```bash
export DB_USER=conclave
export DB_PASSWORD=$(cat secrets/postgres_password.txt)
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=conclave
poetry run alembic upgrade head
```

---

## 7. OOM During Synthesis — Guardrail vs. Actual OOM

Understanding the difference matters because the recovery steps differ.

### OOM Guardrail Rejection (Clean Failure)

The pre-flight memory check runs before training starts. The job fails cleanly
with `OOMGuardrailError` and the status transitions to `FAILED`. No crash occurs.

**Identifying guardrail rejection:**

```bash
docker compose logs app | grep "OOMGuardrailError\|memory requirement exceeds"
```

The job record will show `FAILED` with an `error_msg` containing "Estimated
memory requirement".

**Resolution:** Reduce `num_rows`, or increase the Docker memory limit for the
`app` service. See `docs/DISASTER_RECOVERY.md` Section 2.3.

### Actual OOM Kill (Container Crash)

If the kernel OOM killer terminates the `app` process (RAM exhaustion during
training despite the pre-flight check passing):

**Identifying kernel OOM kill:**

```bash
docker compose ps
# app shows 'exited'

dmesg | grep -i "out of memory\|oom_kill"
# Shows python or app process was killed
```

**Recovery sequence:**

```bash
# 1. Restart the app service
docker compose up -d --no-deps app

# 2. Unseal the vault (KEK was lost when the container crashed)
curl -X POST http://localhost:8000/unseal \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "<operator-passphrase>"}'

# 3. Mark the stuck job as FAILED (Orphan Reaper will do this automatically,
#    but you can do it immediately)
docker compose exec postgres psql -U conclave -c \
  "UPDATE synthesisjob SET status='FAILED', error_msg='Killed by kernel OOM' \
   WHERE status IN ('TRAINING', 'QUEUED') AND id='<job-id>';"

# 4. Resubmit with reduced num_rows or increased memory limit
```

---

## 8. Privacy Budget Exhaustion

### Symptoms

- Jobs fail with `BudgetExhaustionError` in the logs
- `GET /privacy/budget` shows `remaining_epsilon` near zero or negative
- API returns `HTTP 409 Conflict` when attempting to start a new DP synthesis job

### Diagnostic Steps

```bash
# Check the budget state
curl http://localhost:8000/privacy/budget

# Check which jobs consumed the budget
curl http://localhost:8000/jobs?status=COMPLETE | python3 -m json.tool | grep epsilon
```

### Resolution

The privacy budget is a compliance instrument — exhaustion is intentional
behavior, not a bug. When the budget is exhausted:

1. **Review consumption**: identify which jobs consumed the budget and whether
   any were test runs or failed jobs that should not have deducted.

2. **If test jobs incorrectly consumed budget**: the Privacy Ledger admin API
   can restore budget for identifiably erroneous transactions. This requires
   operator-level access and leaves an audit trail.

3. **If budget is legitimately exhausted**: the operator must decide whether to
   allocate additional epsilon (a compliance decision, not a technical one).
   Update `PRIVACY_BUDGET_EPSILON` in `.env` and re-seed the ledger via the
   Alembic migration.

4. **For non-DP synthesis**: jobs without `dp_wrapper` do not consume the privacy
   budget. Non-DP synthesis is always available regardless of budget state.

---

## 9. License Issues

### Symptoms

- API returns `HTTP 402 Payment Required` for all routes except `/health`
- Logs show `LicenseGateMiddleware: no valid license`

### Resolution

See `docs/LICENSING.md` for the full activation protocol. The short version:

```bash
# Step 1: Get challenge
CHALLENGE=$(curl -s http://localhost:8000/license/challenge | python3 -c "
import sys, json; print(json.load(sys.stdin)['challenge'])
")

# Step 2: Activate with a pre-issued JWT
curl -X POST http://localhost:8000/license/activate \
  -H "Content-Type: application/json" \
  -d "{\"token\": \"<license-jwt>\"}"
```

In air-gapped environments, the license JWT is obtained offline from the
licensing server using the QR code challenge workflow.

---

## 10. Log Locations Reference

| What you need | Command |
|---------------|---------|
| Application errors | `docker compose logs app \| grep ERROR` |
| Huey task failures | `docker compose logs app \| grep -i "huey\|task\|FAILED"` |
| Database errors | `docker compose logs postgres` |
| Redis errors | `docker compose logs redis` |
| MinIO errors | `docker compose logs minio-ephemeral` |
| PgBouncer stats | `docker compose logs pgbouncer` |
| Audit events | `docker compose exec app cat /tmp/audit.log` |
| Host OOM events | `dmesg \| grep -i "out of memory"` |

---

## 11. Getting More Help

1. Check `docs/DISASTER_RECOVERY.md` for data-loss recovery procedures.
2. Check `docs/OPERATOR_MANUAL.md` Section 7 for additional common issues.
3. Enable `DEBUG` logging by setting `LOG_LEVEL=DEBUG` in `.env` and restarting.
4. File a bug report with the output of:

   ```bash
   docker compose logs app > app_logs.txt
   docker compose ps > services.txt
   curl http://localhost:8000/health > health.json
   ```
