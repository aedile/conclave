# Conclave Engine — Disaster Recovery

Recovery procedures for failure scenarios. For day-to-day operations, see [docs/OPERATOR_MANUAL.md](OPERATOR_MANUAL.md).

---

## 1. Failed Subsetting Job Recovery (Saga Rollback)

### 1.1 What the System Does Automatically

The subsetting pipeline uses a Saga-pattern egress writer (`modules/subsetting/egress.py`, ADR-0015):

- Each `write()` commits one batch immediately to the target DB.
- On any exception, the context manager calls `rollback()`.
- `rollback()` issues `TRUNCATE ... CASCADE` on every written table in reverse order (FK-safe).
- The target DB is left empty — no partial data remains.

The Huey task is marked `FAILED`. The SSE stream emits an `error` event.

### 1.2 When Manual Intervention Is Needed

1. **Worker crashed mid-rollback** — target DB may contain partial data. The Orphan Task Reaper marks orphaned DB records `FAILED` on heartbeat timeout, but cannot undo committed writes.
2. **Target DB connection lost during rollback** — compensating TRUNCATEs could not execute.
3. **Job stuck in `QUEUED`/`TRAINING` after restart** — Orphan Task Reaper resolves this on its next sweep; if it does not, mark manually (see below).

### 1.3 Manual Recovery Steps

```bash
# 1. Identify the failed job
curl http://<host>:8000/jobs?status=FAILED
curl http://<host>:8000/jobs?status=TRAINING
curl http://<host>:8000/jobs?status=QUEUED

# 2. Check logs for failure detail
docker compose logs app | grep -i "saga\|rollback\|egress\|job_id=<id>"
# Look for: "Saga rollback: truncating" or "EgressWriter rollback failed"

# 3. If target DB has partial data — truncate manually
# (Identify tables from "Wrote N rows to table <name>" in logs before failure)
# TRUNCATE TABLE employees, departments CASCADE;

# 4. Reset stuck job status
docker compose exec postgres psql -U conclave -c \
  "UPDATE synthesisjob SET status='FAILED' WHERE id='<job-id>';"

# 5. Retry: create and start a new job
curl -X POST http://<host>:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"name": "...", "parquet_path": "...", ...}'
curl -X POST http://<host>:8000/jobs/<new-job-id>/start
```

---

## 2. OOM Event Recovery

### 2.1 What Happens During an OOM Event

`check_memory_feasibility()` runs before training. It estimates memory using Parquet dimensions and a 6x overhead factor:

- **Guardrail rejects**: raises `OOMGuardrailError` → job transitions to `FAILED` cleanly. No partial state written.
- **Kernel OOM kill**: if RAM is exhausted during training after the pre-flight passes, the kernel may kill the process and crash the container.

### 2.2 Diagnosing an OOM Event

```bash
# Guardrail rejection (clean failure)
docker compose logs app | grep "OOMGuardrailError\|memory requirement exceeds"

# Kernel OOM kill (container crash)
docker compose ps  # app shows 'exited'
dmesg | grep -i "out of memory\|oom_kill"
```

### 2.3 Recovery Steps

**Guardrail rejection** — reduce dataset or increase memory:

```bash
# Option A: reduce num_rows in the job request
# Option B: increase Docker memory limit in docker-compose.override.yml:
# services:
#   app:
#     deploy:
#       resources:
#         limits:
#           memory: 16g
docker compose up -d --no-deps app
```

**Kernel OOM kill:**

```bash
# 1. Verify postgres, redis, pgbouncer are healthy
docker compose ps

# 2. Restart app
docker compose up -d --no-deps app

# 3. Unseal vault (KEK is lost on container crash)
curl -X POST http://<host>:8000/unseal \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "<operator-passphrase>"}'

# 4. No MinIO cleanup needed — minio-ephemeral uses tmpfs, discarded on container stop

# 5. Mark stuck jobs FAILED (see Section 1.3, Step 4) and retry
```

---

## 3. Cryptographic Key Recovery

### 3.1 If the Vault KEK Is Lost

The KEK exists only in process memory. Container stop/restart/crash destroys it. Recovery: unseal again with the operator passphrase (Operator Manual Section 4). The KEK is re-derived from the passphrase + `VAULT_SEAL_SALT` on every unseal.

### 3.2 If the Operator Passphrase Is Lost

1. All ALE-encrypted data (`EncryptedString` columns in PostgreSQL) is **unrecoverable** — by design, per NIST SP 800-88.
2. A backup can be restored, but ALE columns remain undecryptable ciphertext.
3. Recommended path: restore from a pre-encryption backup, provision a new passphrase, re-run data ingestion.

### 3.3 If the ALE Key Is Shredded

`POST /security/shred` zeroizes the in-memory KEK. After shredding, all `EncryptedString` columns are permanently unrecoverable. This is intentional per NIST SP 800-88 §2.4.

Recovery: restore from a pre-shred backup (Section 4) and unseal with the original passphrase + `VAULT_SEAL_SALT`.

### 3.4 Key Rotation Recovery

If `POST /security/keys/rotate` fails mid-stream:

```bash
# Check rotation status
docker compose logs app | grep -i "rotate\|re-encrypt\|rotation"
```

If some columns were re-encrypted with the new key and others retain the old key, the DB is in an inconsistent state. Restore from a pre-rotation backup (Section 4) and retry.

If Redis was unavailable during rotation, the task fails cleanly without partial re-encryption — retry is safe.

---

## 4. PostgreSQL Backup and Restore

### 4.1 Creating a Backup

```bash
docker compose exec postgres pg_dump \
  -U conclave \
  -F c \
  -f /tmp/conclave_backup_$(date +%Y%m%d_%H%M%S).dump \
  conclave

docker compose cp postgres:/tmp/conclave_backup_<timestamp>.dump ./backups/
```

Schedule as a cron job and transfer output to offline media for production.

### 4.2 Restoring from Backup

```bash
# 1. Stop application
docker compose stop app

# 2. Drop and recreate database
docker compose exec postgres psql -U conclave postgres \
  -c "DROP DATABASE IF EXISTS conclave;" \
  -c "CREATE DATABASE conclave OWNER conclave;"

# 3. Restore dump
docker compose cp ./backups/conclave_backup_<timestamp>.dump postgres:/tmp/
docker compose exec postgres pg_restore \
  -U conclave -d conclave /tmp/conclave_backup_<timestamp>.dump

# 4. Restart and unseal
docker compose start app
# Then unseal — see Section 3.1
```

---

## 5. Redis Failure Recovery

### 5.1 What Redis Holds

Redis is the Huey task queue only. It holds queued synthesis jobs and in-flight task payloads (including KEK-wrapped Fernet keys during rotation). No persistent PII or application data. Persistence is intentionally disabled (`--save "" --appendonly no`).

### 5.2 Recovery Steps

```bash
# 1. Restart Redis
docker compose up -d --no-deps redis

# 2. Re-submit any jobs stuck in QUEUED (they lost their queue entry)
curl http://<host>:8000/jobs?status=QUEUED
# For each stuck job:
curl -X POST http://<host>:8000/jobs/<job-id>/start

# 3. If key rotation was in-flight, retry (keys in transit are lost):
curl -X POST http://<host>:8000/security/keys/rotate \
  -H "Content-Type: application/json" \
  -d '{"new_passphrase": "<new-passphrase>"}'
# If rotation was partially complete, restore from pre-rotation backup first (Section 4)
```

---

## 6. Container Crash Recovery

### 6.1 General Recovery Sequence

```bash
# 1. Assess
docker compose ps
docker compose logs <service>

# 2. Restart crashed service
docker compose up -d --no-deps <service>

# 3. If app crashed, unseal vault (KEK always lost on crash — see Section 3.1)

# 4. Reset stuck jobs (Section 1.3)

# 5. Check for partial operations
#    Key rotation in progress: Section 3.4
#    Subsetting job in progress: Section 1.2
```

### 6.2 Service Dependency Order

```text
postgres (healthy) → pgbouncer → app + redis + minio-ephemeral
```

`docker-compose.yml` enforces this via `depends_on: condition: service_healthy`. `docker compose up -d` handles ordering automatically.

### 6.3 Persistent Volume Inspection

```bash
docker volume ls | grep conclave
docker run --rm -v conclave_postgres_data:/data alpine ls /data/

# Remove corrupt volume (destructive — data lost)
docker volume rm conclave_postgres_data
```

Attempt `pg_dump` before removing `postgres_data`. If PostgreSQL files are corrupt and `pg_dump` fails, restore from external backup (Section 4).

---

## 7. mTLS Certificate Loss Recovery (T46.3)

### 7.1 Loss Scenarios

| Loss scenario | Recoverable? | Recovery action |
|---------------|:------------:|-----------------|
| Leaf cert(s) lost (CA key intact) | Yes | Regenerate leaf certs from existing CA |
| CA cert lost (CA key intact) | Yes | Regenerate CA cert from CA key, then leaf certs |
| CA key lost (no backup) | **No** | Full CA rebuild — all services must restart |
| CA key lost (backup exists) | Yes | Restore from backup, then rotate leaf certs |

### 7.2 Leaf Cert Loss (CA Key Intact)

```bash
./scripts/rotate-mtls-certs.sh
# Follow reload commands printed by the script (OPERATOR_MANUAL.md Section 13.2)
```

To regenerate all leaf certs while preserving the CA:

```bash
./scripts/generate-mtls-certs.sh
docker compose up -d --no-deps --force-recreate <service>
```

### 7.3 CA Key Loss (Unrecoverable Without Backup)

**Immediate mitigation** — disable mTLS to restore connectivity:

```bash
echo "MTLS_ENABLED=false" >> .env
docker compose up -d --force-recreate
```

**Full recovery:**

```bash
# 1. Generate new CA and all leaf certs
./scripts/generate-mtls-certs.sh --force

# 2. Restart ALL services (hard cutover — schedule as maintenance window)
docker compose up -d --force-recreate

# 3. Re-enable mTLS (remove MTLS_ENABLED=false from .env)
docker compose up -d --force-recreate

# 4. Verify
openssl verify -CAfile secrets/mtls/ca.crt secrets/mtls/app.crt
```

### 7.4 Certificate Backup Strategy

The rotation script creates a timestamped backup before every rotation:

```
secrets/mtls/backup-20260315-143022/
  app.crt  app.key  postgres.crt  postgres.key
  pgbouncer.crt  pgbouncer.key  redis.crt  redis.key  ca.crt
```

`ca.key` is **NOT** included in rotation backups (it never changes during leaf rotation). Back it up separately.

**Backup checklist:**
- `secrets/mtls/ca.key` — Back up immediately after first-time generation. Store in offline cold storage.
- `secrets/mtls/ca.crt` — Included in rotation backups automatically.
- Rotation backups — Retain for at least 7 days.

**Verify your backup:**

```bash
openssl x509 -req \
    -in /tmp/test.csr \
    -CA secrets/mtls/ca.crt \
    -CAkey /path/to/backup/ca.key \
    -CAcreateserial \
    -out /tmp/test.crt \
    -days 1
openssl verify -CAfile secrets/mtls/ca.crt /tmp/test.crt
```

### 7.5 Cert Expiry Emergency (Production)

```bash
# Fast path: disable mTLS immediately
echo "MTLS_ENABLED=false" >> .env
docker compose up -d --force-recreate

# Then rotate certs (CA key still intact)
./scripts/rotate-mtls-certs.sh

# Re-enable mTLS (remove MTLS_ENABLED=false from .env)
docker compose up -d --force-recreate
```

Monitor `conclave_cert_expiry_days` (Prometheus) and alert rules in OPERATOR_MANUAL.md Section 13.5 for advance warning.
---

## 8. DR Dry Run Validation (T51.4)

Before an incident, operators should verify their DR procedures work against the local stack.

### 8.1 Running the Dry Run

```bash
# Start the full stack first
docker compose up -d

# Run all three DR scenarios
./scripts/dr_dry_run.sh
```

The script runs three scenarios and prints `[PASS]` or `[FAIL]` for each:

| Scenario | What it validates |
|----------|------------------|
| 1: DB Backup & Restore | `pg_dump` + `pg_restore` round-trip on synthetic data |
| 2: Service Recovery | App container stop/start + `/ready` health poll |
| 3: Redis Recovery | Stop/start + ephemeral key disappears (no persistence) |

The script exits non-zero if any scenario fails. All test data uses a `dr_test_<timestamp>` prefix and is cleaned up on exit (trapped).

### 8.2 Safety Properties

- Test data: all ephemeral `dr_test_` tables and Redis keys — **never real PII**
- Backup files: written only to `/tmp/` — **never to `data/`, `output/`, or committed paths**
- Cleanup: `trap EXIT` ensures all resources are removed even on script failure
- Credentials: accessed via `docker compose exec` inside the postgres container — no hardcoded secrets
