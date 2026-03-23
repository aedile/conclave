# Conclave Engine — Troubleshooting Guide

Diagnostic flowcharts and resolution steps for common operational failures. For hardware sizing, see `docs/SCALABILITY.md`. For data loss/corruption recovery, see `docs/DISASTER_RECOVERY.md`.

**Audience:** Operators with access to the Docker host and application logs.

Each section follows: **Symptoms → Diagnostic Steps → Resolution**.

---

## 1. Huey Worker — Task Stuck in QUEUED

### Symptoms

- `GET /jobs/<id>` returns `"status": "QUEUED"` for more than 60 seconds after `POST /jobs/<id>/start`
- No `"TRAINING"` log entries in `docker compose logs app`

### Diagnostic Steps

```bash
# 1. Verify Huey worker is running (runs inside the app container)
docker compose ps

# 2. Check Huey worker logs
docker compose logs app | grep -i "huey\|worker\|task"

# 3. Verify tasks are in the queue but not consumed
docker compose exec redis redis-cli llen conclave:default
# Non-zero = tasks queued but not consumed

# 4. Verify app can reach Redis
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
| Huey backend set to `memory` | Set `HUEY_BACKEND=redis` in `.env` and restart `app`. In-memory Huey has no background worker by default. |
| Redis not running | `docker compose up -d redis` then restart `app`. |
| Redis connection refused | Check `REDIS_URL` in `.env`. Use `redis://redis:6379/0` (service name, not `localhost`). |
| Worker crashed during startup | Check `docker compose logs app` for `ImportError` or startup exceptions (often a missing env variable — see Section 6). |
| Task orphaned after worker crash | `docker compose exec postgres psql -U conclave -c "UPDATE synthesisjob SET status='FAILED' WHERE status='QUEUED';"` then resubmit. |

---

## 2. Huey Worker — Task Crashes or Fails Immediately

### Symptoms

- Job transitions `QUEUED` → `FAILED` within seconds
- SSE stream emits `error` event
- `docker compose logs app` shows exception traceback

### Diagnostic Steps

```bash
curl http://localhost:8000/jobs/<job-id>
# Check "error_msg" field

docker compose logs app | grep -A 20 "job_id=<job-id>\|ERROR\|Exception"
```

### Common Failure Patterns

**`OOMGuardrailError: Estimated memory requirement exceeds available RAM`**

Pre-flight memory check failed. Options:
1. Reduce `num_rows` in the job request.
2. Increase Docker memory limit (`docs/DISASTER_RECOVERY.md` Section 2.3).

**`FileNotFoundError: /data/...parquet`**

1. Verify the file is in `data/` on the host (mounted into the container).
2. `parquet_path` must be an **absolute path inside the container** (e.g., `/data/customers.parquet`).

**`BudgetExhaustionError`**

Privacy budget exhausted. Check: `curl http://localhost:8000/privacy/budget`. Reset via Privacy Accountant API (`docs/OPERATOR_MANUAL.md` Section 9.5).

**`ImportError: The 'sdv' package is required`**

Run `poetry install --with synthesizer` and rebuild the Docker image.

---

## 3. MinIO / S3 Storage Failures

### Symptoms

- Job fails with `ConnectionError` or `S3Error`
- Job is `COMPLETE` but download endpoint returns 404

### Diagnostic Steps

```bash
docker compose ps minio-ephemeral
docker compose logs minio-ephemeral
docker compose exec app curl -s http://minio-ephemeral:9000/minio/health/live  # Expect 200
docker compose exec app env | grep MINIO
```

### Resolution

| Cause | Resolution |
|-------|------------|
| MinIO not running | `docker compose up -d minio-ephemeral` |
| Wrong endpoint | Use `http://minio-ephemeral:9000` in Docker Compose. Check `.env`. |
| Credentials mismatch | Compare `MINIO_EPHEMERAL_ACCESS_KEY` in `.env` with `secrets/minio_ephemeral_access_key.txt`. |
| tmpfs full | Increase memory limits in `docker-compose.override.yml`. |
| Output file expired | MinIO uses `tmpfs` — artifacts are discarded on container stop. Rerun the synthesis job. |

---

## 4. Connection Pool Exhaustion

### Symptoms

- API requests hang 5–30 seconds then time out
- Logs show: `TimeoutError: QueuePool limit of size 5 overflow 10 reached`
- Grafana shows elevated latency

### Diagnostic Steps

```bash
# PgBouncer pool stats
docker compose exec pgbouncer psql -U pgbouncer -p 6432 pgbouncer \
  -c "SHOW POOLS;" 2>/dev/null || echo "psql not available"

# Active DB connections
docker compose exec postgres psql -U conclave -c \
  "SELECT count(*) FROM pg_stat_activity WHERE state = 'active';"

# Long-running queries
docker compose exec postgres psql -U conclave -c \
  "SELECT pid, state, query, now() - query_start AS duration FROM pg_stat_activity ORDER BY duration DESC NULLS LAST LIMIT 10;"
```

### Resolution

| Cause | Resolution |
|-------|------------|
| Leaked session | Restart `app` to release all connections. |
| Too many concurrent SSE clients | 100+ SSE clients each poll DB every second. Reduce client count or increase PgBouncer `pool_size` (see `docs/SCALABILITY.md` Section 3). |
| PgBouncer misconfigured | Verify `PGBOUNCER_URL` points to port 6432 (not 5432 directly). |
| Application connection leak | Check logs for unclosed sessions; file a bug with the specific code path. |

---

## 5. Vault Sealed Unexpectedly

### Symptoms

- All routes (except `/health`, `/unseal`, `/metrics`) return `HTTP 423 Locked`
- Logs show: `SealGateMiddleware: vault is sealed`
- React UI redirects to `/unseal`

### Diagnostic Steps

```bash
curl http://localhost:8000/health         # Still 200 (health is exempt)
curl -X GET http://localhost:8000/jobs    # 423 if sealed
docker compose logs app | head -20        # Recent restart = KEK was lost
docker compose exec app cat /tmp/audit.log | grep -i "seal\|shred"
```

### Why Vaults Re-Seal

The KEK exists **only in process memory** — never written to disk.

| Event | Result |
|-------|--------|
| Container restart | KEK lost; must unseal again |
| Container crash (OOM kill, etc.) | KEK lost |
| Docker host reboot | KEK lost |
| `POST /unseal/seal` | Intentional re-seal |
| `POST /security/shred` | KEK zeroized (irreversible) |

### Resolution

```bash
curl -X POST http://localhost:8000/unseal \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "<operator-passphrase>"}'
```

`CONFIG_ERROR` response: check `VAULT_SEAL_SALT` in `.env` has not changed since last unseal.

**Passphrase lost:** See `docs/DISASTER_RECOVERY.md` Section 3.2. ALE-encrypted data is unrecoverable.

---

## 6. Application Fails to Start

### Symptoms

- `docker compose up` shows `app` exiting with code 1
- `docker compose logs app` shows a configuration error

### Diagnostic Steps

```bash
docker compose logs app | head -50
# Look for: ConfigurationError, missing, required, ARTIFACT_SIGNING_KEY
```

### Common Startup Errors

**`ConfigurationError: DATABASE_URL is required`**

```bash
DATABASE_URL=postgresql+psycopg2://conclave:<password>@pgbouncer:6432/conclave
```

**`ConfigurationError: ARTIFACT_SIGNING_KEY is required in production mode`**

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
# Add as ARTIFACT_SIGNING_KEY=<value> in .env
```

**`ConfigurationError: AUDIT_KEY is required`**

```bash
python3 -c "import os; print(os.urandom(32).hex())"
# Add as AUDIT_KEY=<value> in .env
```

**Schema migration missing**

```bash
export DB_USER=conclave
export DB_PASSWORD=$(cat secrets/postgres_password.txt)
export DB_HOST=localhost DB_PORT=5432 DB_NAME=conclave
poetry run alembic upgrade head
```

---

## 7. OOM During Synthesis — Guardrail vs. Actual OOM

### OOM Guardrail Rejection (Clean Failure)

Pre-flight check runs before training. Job fails with `OOMGuardrailError` → `FAILED` status. No crash.

```bash
docker compose logs app | grep "OOMGuardrailError\|memory requirement exceeds"
```

**Resolution:** Reduce `num_rows` or increase Docker memory limit. See `docs/DISASTER_RECOVERY.md` Section 2.3.

### Actual OOM Kill (Container Crash)

Kernel OOM killer terminates the `app` process during training.

```bash
docker compose ps          # app shows 'exited'
dmesg | grep -i "out of memory\|oom_kill"
```

**Recovery:**

```bash
# 1. Restart app
docker compose up -d --no-deps app

# 2. Unseal vault (KEK lost on crash)
curl -X POST http://localhost:8000/unseal \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "<operator-passphrase>"}'

# 3. Mark stuck job FAILED
docker compose exec postgres psql -U conclave -c \
  "UPDATE synthesisjob SET status='FAILED', error_msg='Killed by kernel OOM' \
   WHERE status IN ('TRAINING', 'QUEUED') AND id='<job-id>';"

# 4. Resubmit with reduced num_rows or increased memory limit
```

---

## 8. Privacy Budget Exhaustion

### Symptoms

- Jobs fail with `BudgetExhaustionError`
- `GET /privacy/budget` shows `remaining_epsilon` near zero or negative
- HTTP 409 when starting a DP synthesis job

### Diagnostic Steps

```bash
curl http://localhost:8000/privacy/budget
curl http://localhost:8000/jobs?status=COMPLETE | python3 -m json.tool | grep epsilon
```

### Resolution

Budget exhaustion is intentional — a compliance instrument, not a bug.

1. **Review consumption**: identify whether test/failed jobs incorrectly consumed budget.
2. **Erroneous transactions**: the Privacy Ledger admin API can restore budget (operator-level access required; leaves audit trail).
3. **Legitimately exhausted**: decide whether to allocate additional epsilon (compliance decision). Update `PRIVACY_BUDGET_EPSILON` in `.env` and re-seed via Alembic migration.
4. **Non-DP synthesis**: jobs without `dp_wrapper` never consume the budget and remain available regardless of budget state.

---

## 9. License Issues

### Symptoms

- HTTP 402 on all routes except `/health`
- Logs show `LicenseGateMiddleware: no valid license`

### Resolution

See `docs/LICENSING.md` for the full activation protocol.

```bash
# Get challenge
CHALLENGE=$(curl -s http://localhost:8000/license/challenge | python3 -c "
import sys, json; print(json.load(sys.stdin)['challenge'])
")

# Activate
curl -X POST http://localhost:8000/license/activate \
  -H "Content-Type: application/json" \
  -d "{\"token\": \"<license-jwt>\"}"
```

Air-gapped: obtain the license JWT offline using the QR code challenge workflow.

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

1. Data-loss recovery: `docs/DISASTER_RECOVERY.md`.
2. Additional common issues: `docs/OPERATOR_MANUAL.md` Section 7.
3. Enable debug logging: set `LOG_LEVEL=DEBUG` in `.env` and restart.
4. File a bug report with:

   ```bash
   docker compose logs app > app_logs.txt
   docker compose ps > services.txt
   curl http://localhost:8000/health > health.json
   ```
