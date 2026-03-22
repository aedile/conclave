# Conclave Engine — Disaster Recovery

This document describes recovery procedures for failure scenarios.
For day-to-day operations, see [docs/OPERATOR_MANUAL.md](OPERATOR_MANUAL.md).

---

## 1. Failed Subsetting Job Recovery (Saga Rollback)

### 1.1 What the System Does Automatically

The subsetting pipeline uses a Saga-pattern egress writer
(`modules/subsetting/egress.py`, per ADR-0015). The Saga invariant is:

> If **any** write fails, **all** previously written data is TRUNCATEd from the
> target database so it is left in a clean, empty state.

The `EgressWriter` context manager handles this automatically:

1. Each `write()` call commits one batch of rows immediately to the target DB.
2. On any exception — whether a database error, a network timeout, or a
   Python exception propagating out of the `with EgressWriter():` block —
   the context manager calls `rollback()`.
3. `rollback()` issues `TRUNCATE ... CASCADE` on every table that received
   data, in reverse write order (to satisfy FK constraints).
4. After rollback, the target database is guaranteed to be empty. No partial
   data remains.

The Huey task runner marks the job `FAILED` in the database. The `app` service
SSE stream (`.../jobs/<id>/stream`) emits an `error` event with the failure
detail.

### 1.2 When Manual Intervention Is Needed

Manual intervention is required when:

1. **The Huey worker process crashed mid-rollback** — the target database may
   contain partial data. The Orphan Task Reaper (T2.1) marks orphaned DB
   records `FAILED` when the worker heartbeat times out, but it cannot
   undo already-committed writes.

2. **The target database connection was lost during rollback** — the
   compensating TRUNCATEs could not execute.

3. **A job is stuck in `QUEUED` or `TRAINING` after a worker restart** — the
   Orphan Task Reaper resolves this automatically on its next sweep. If it does
   not, mark the job manually (see below).

### 1.3 Manual Recovery Steps

#### Step 1: Identify the failed job

```bash
curl http://<host>:8000/jobs?status=FAILED
curl http://<host>:8000/jobs?status=TRAINING
curl http://<host>:8000/jobs?status=QUEUED
```

#### Step 2: Check application logs for the failure detail

```bash
docker compose logs app | grep -i "saga\|rollback\|egress\|job_id=<id>"
```

Look for log lines containing `Saga rollback: truncating` or
`EgressWriter rollback failed`.

#### Step 3: If the target database contains partial data

Connect to the target PostgreSQL instance and manually truncate the affected
tables:

```sql
-- Example: reset target tables after a failed subsetting run
TRUNCATE TABLE employees, departments CASCADE;
```

Identify which tables received data by checking the application logs for
`Wrote N rows to table <name>` entries before the failure.

#### Step 4: Reset the job status if stuck

If the Orphan Task Reaper has not fired (within its configured sweep interval),
you can reset a stuck job via the API:

```bash
# Delete and recreate the job, or update status directly in the database
docker compose exec postgres psql -U conclave -c \
  "UPDATE synthesisjob SET status='FAILED' WHERE id='<job-id>';"
```

#### Step 5: Retry the job

After the target database is clean, create a new job and start it:

```bash
curl -X POST http://<host>:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"name": "...", "parquet_path": "...", ...}'

curl -X POST http://<host>:8000/jobs/<new-job-id>/start
```

---

## 2. OOM Event Recovery

### 2.1 What Happens During an OOM Event

The synthesis training task (`modules/synthesizer/tasks.py`) includes an OOM
pre-flight check before training begins:

1. `check_memory_feasibility()` estimates the memory required based on the
   Parquet file dimensions, number of rows, and a 6x overhead factor for
   gradient buffers and optimizer state.
2. If estimated memory exceeds available system RAM, the task raises
   `OOMGuardrailError` and the job transitions to `FAILED` without starting
   training. No partial state is written.
3. If the guardrail passes but the system runs out of memory during training
   (e.g., because other processes consumed RAM after the pre-flight), the
   Python process may be killed by the kernel OOM killer.

### 2.2 Diagnosing an OOM Event

Signs of OOM guardrail rejection (clean failure):

```bash
docker compose logs app | grep "OOMGuardrailError\|memory requirement exceeds"
```

The job will show `FAILED` status with a detail message.

Signs of kernel OOM kill (container crash):

```bash
docker compose ps  # app service shows 'exited'
docker compose logs app | tail -20
# Check host dmesg for OOM killer entries:
dmesg | grep -i "out of memory\|oom_kill"
```

### 2.3 Recovery Steps

#### If the OOM guardrail rejected the job

Reduce the job size or increase the memory available to Docker.

Option A — Reduce `num_rows`:

```bash
curl -X POST http://<host>:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"name": "...", "parquet_path": "...", "num_rows": 500, ...}'
```

Option B — Increase the Docker memory limit for the `app` service in
`docker-compose.override.yml`:

```yaml
services:
  app:
    deploy:
      resources:
        limits:
          memory: 16g
```

Then restart the service:

```bash
docker compose up -d --no-deps app
```

#### If the container was killed by the kernel OOM killer

1. Check that `postgres`, `redis`, and `pgbouncer` are still healthy:

   ```bash
   docker compose ps
   ```

2. Restart the `app` service:

   ```bash
   docker compose up -d --no-deps app
   ```

3. Unseal the vault (the KEK is lost when the container crashes):

   ```bash
   curl -X POST http://<host>:8000/unseal \
     -H "Content-Type: application/json" \
     -d '{"passphrase": "<operator-passphrase>"}'
   ```

4. Check whether the failed job left partial MinIO artifacts. Because
   `minio-ephemeral` uses `tmpfs`, all artifacts are discarded when the
   container stops — no cleanup is required.

5. Mark any stuck jobs as `FAILED` (see Section 1.3, Step 4) and retry.

---

## 3. Cryptographic Key Recovery

### 3.1 If the Vault KEK Is Lost

The KEK is never persisted to disk. It exists only in process memory for
the lifetime of the container. If the container stops, restarts, or crashes,
the KEK is gone. Recovery is simple: unseal the vault again with the operator
passphrase (Section 4 of the Operator Manual).

The KEK is derived from the passphrase and the `VAULT_SEAL_SALT` value every
time the vault is unsealed. As long as the operator passphrase and
`VAULT_SEAL_SALT` are known, the KEK can always be reproduced.

### 3.2 If the Operator Passphrase Is Lost

If the operator passphrase is lost and the vault cannot be unsealed:

1. All data encrypted with Application-Level Encryption (ALE) — stored in
   `EncryptedString` columns in PostgreSQL — is **unrecoverable**. This is
   by design, per NIST SP 800-88 (cryptographic erasure).
2. The PostgreSQL database can be restored from a backup (see Section 4), but
   the ALE-encrypted columns will remain ciphertext that cannot be decrypted.
3. The recommended recovery path is to restore from a pre-encryption backup
   (before ALE columns were populated), provision a new passphrase, and
   re-run data ingestion.

### 3.3 If the ALE Key (Fernet KEK) Is Shredded

`POST /security/shred` zeroizes the in-memory KEK (fills the `bytearray` with
`0x00` bytes). After shredding:

1. All `EncryptedString` columns in PostgreSQL contain valid-looking
   ciphertext that can no longer be decrypted.
2. This is an intentional, irreversible operation per NIST SP 800-88 §2.4.
3. The application remains functional for new data, but all previously
   encrypted data is permanently unrecoverable.

To recover: restore the database from a pre-shred backup (Section 4) and
unseal with the original passphrase using the same `VAULT_SEAL_SALT`.

### 3.4 Key Rotation Recovery

`POST /security/keys/rotate` re-encrypts all ALE columns with a new Fernet key.
This is a Huey background task. If the rotation task fails mid-stream:

1. Check the job status:

   ```bash
   docker compose logs app | grep -i "rotate\|re-encrypt\|rotation"
   ```

2. If some columns were re-encrypted with the new key and others retain the
   old key, the database is in an inconsistent state. Restore from a
   pre-rotation backup (Section 4) and retry the rotation.

3. The old key is passed (KEK-wrapped) through Redis to the Huey worker. If
   Redis is unavailable during rotation, the task will fail cleanly without
   partial re-encryption.

---

## 4. PostgreSQL Backup and Restore

### 4.1 Creating a Backup

Use `pg_dump` to create a consistent logical backup:

```bash
docker compose exec postgres pg_dump \
  -U conclave \
  -F c \
  -f /tmp/conclave_backup_$(date +%Y%m%d_%H%M%S).dump \
  conclave
```

Copy the dump file out of the container:

```bash
docker compose cp postgres:/tmp/conclave_backup_<timestamp>.dump ./backups/
```

For production deployments, schedule this as a cron job and transfer the
output to offline media.

### 4.2 Restoring from Backup

#### Step 1: Stop the application

```bash
docker compose stop app
```

#### Step 2: Drop and recreate the database

```bash
docker compose exec postgres psql -U conclave postgres \
  -c "DROP DATABASE IF EXISTS conclave;" \
  -c "CREATE DATABASE conclave OWNER conclave;"
```

#### Step 3: Restore the dump

```bash
docker compose cp ./backups/conclave_backup_<timestamp>.dump postgres:/tmp/

docker compose exec postgres pg_restore \
  -U conclave \
  -d conclave \
  /tmp/conclave_backup_<timestamp>.dump
```

#### Step 4: Restart the application

```bash
docker compose start app
```

#### Step 5: Unseal the vault

See Section 3.1. The KEK is never persisted; every restart requires a manual
unseal with the operator passphrase.

---

## 5. Redis Failure Recovery

### 5.1 What Redis Holds

Redis is used exclusively as the Huey task queue backing store. It holds:

- Queued synthesis jobs (pending Huey task messages)
- In-flight task payloads (including KEK-wrapped Fernet keys for key rotation)
- No persistent PII or application data

Redis persistence is intentionally disabled (`--save "" --appendonly no`).
All Redis data is ephemeral.

### 5.2 Recovery Steps

If Redis crashes:

1. Any jobs that were `QUEUED` and not yet picked up by the Huey worker are
   lost from the queue. The database records remain in `QUEUED` state.
2. Restart Redis:

   ```bash
   docker compose up -d --no-deps redis
   ```

3. Re-submit any jobs whose status is `QUEUED` in the database but which are
   no longer present in the Redis queue:

   ```bash
   curl http://<host>:8000/jobs?status=QUEUED
   # For each stuck job:
   curl -X POST http://<host>:8000/jobs/<job-id>/start
   ```

4. If a key rotation task was in-flight when Redis crashed, the Fernet keys
   in transit are lost. Retry the rotation:

   ```bash
   curl -X POST http://<host>:8000/security/keys/rotate \
     -H "Content-Type: application/json" \
     -d '{"new_passphrase": "<new-passphrase>"}'
   ```

   If the rotation was partially complete, restore from a pre-rotation backup
   first (Section 4).

---

## 6. Container Crash Recovery

### 6.1 General Recovery Sequence

For any container crash, follow this sequence:

1. Assess the damage:

   ```bash
   docker compose ps
   docker compose logs <service>
   ```

2. Restart the crashed service:

   ```bash
   docker compose up -d --no-deps <service>
   ```

3. If the `app` service crashed, unseal the vault before making API
   requests (the KEK is always lost on crash — see Section 3.1).

4. Check for stuck jobs and reset them as described in Section 1.3.

5. Check for partial operations:
   - Key rotation in progress: see Section 3.4
   - Subsetting job in progress: see Section 1.2

### 6.2 Service Dependency Order

When starting from a fully stopped state, services must come up in this order:

```text
postgres (healthy) → pgbouncer → app + redis + minio-ephemeral
```

`docker-compose.yml` enforces this via `depends_on: condition: service_healthy`.
Running `docker compose up -d` handles the ordering automatically.

### 6.3 Persistent Volume Inspection

If a named volume may be corrupt:

```bash
# List volumes
docker volume ls | grep conclave

# Inspect a volume
docker run --rm -v conclave_postgres_data:/data alpine ls /data/

# Remove a corrupt volume (destructive — data is lost)
docker volume rm conclave_postgres_data
```

Do not remove `postgres_data` without first attempting a `pg_dump` backup.
If the PostgreSQL data files are corrupt, `pg_dump` may fail — in that case,
restore from an external backup (Section 4).

---

## 7. mTLS Certificate Loss Recovery (T46.3)

### 7.1 Loss Scenarios and Recovery Paths

| Loss scenario | Recoverable? | Recovery action |
|---------------|:------------:|-----------------|
| Leaf cert(s) lost (CA key intact) | Yes | Regenerate leaf certs from existing CA |
| CA cert lost (CA key intact) | Yes | Regenerate CA cert from CA key, then regenerate leaf certs |
| CA key lost (no backup) | **No** | Full CA rebuild — all services must be restarted |
| CA key lost (backup exists) | Yes | Restore from backup, then rotate leaf certs |

### 7.2 Leaf Cert Loss (CA Key Intact)

If one or more leaf `.crt` / `.key` files are lost or corrupted but the CA
key remains intact, re-run the rotation helper:

```bash
./scripts/rotate-mtls-certs.sh
```

Then follow the reload commands printed by the script
(Section 13.2 of `docs/OPERATOR_MANUAL.md`).

If only a single service is affected, you can regenerate just that service
by running `generate-mtls-certs.sh` (it regenerates all leaf certs but
preserves the CA):

```bash
./scripts/generate-mtls-certs.sh
# Then restart only the affected service:
docker compose up -d --no-deps --force-recreate <service>
```

### 7.3 CA Key Loss (Unrecoverable Without Backup)

If `secrets/mtls/ca.key` is lost and no backup exists, the CA cannot be
reconstructed. **All mTLS connections will fail** once the existing leaf
certs expire or are replaced.

**Immediate mitigation:** Disable mTLS to restore connectivity:

```bash
# Set MTLS_ENABLED=false in your .env or docker-compose override
echo "MTLS_ENABLED=false" >> .env
docker compose up -d --force-recreate
```

**Full recovery procedure:**

```bash
# Step 1: Generate a completely new CA and all leaf certs
./scripts/generate-mtls-certs.sh --force

# Step 2: Restart ALL services to pick up the new trust anchor
docker compose up -d --force-recreate

# Step 3: Re-enable mTLS
# Remove the MTLS_ENABLED=false line from .env (or set to true)
docker compose up -d --force-recreate

# Step 4: Verify the new chain
openssl verify -CAfile secrets/mtls/ca.crt secrets/mtls/app.crt
```

This constitutes a hard cutover — all containers must be restarted
simultaneously or they will reject each other's certs (new CA vs old CA).
Schedule this as a maintenance window.

### 7.4 Certificate Backup Strategy

The rotation script (`rotate-mtls-certs.sh`) automatically creates a
timestamped backup before every rotation:

```
secrets/mtls/backup-20260315-143022/
  app.crt  app.key  postgres.crt  postgres.key
  pgbouncer.crt  pgbouncer.key  redis.crt  redis.key  ca.crt
```

The CA private key (`ca.key`) is **NOT** included in rotation backups because
it never changes during leaf-cert rotation. You MUST back up `ca.key`
separately using your organisation's secret management procedure.

**Recommended backup checklist:**

- `secrets/mtls/ca.key` — Back up immediately after first-time generation.
  Store in offline cold storage (encrypted USB, hardware security module, or
  secrets vault).
- `secrets/mtls/ca.crt` — Include in rotation backups (already done by script).
- Rotation backups (`backup-YYYYMMDD-HHMMSS/`) — Retain for at least 7 days
  to cover any delayed rollback scenario.

**Test your backup:**

```bash
# Verify you can reconstruct a leaf cert from the backed-up CA key
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

If certificates have already expired and services are rejecting connections:

```bash
# Fast path: disable mTLS to restore connectivity immediately
echo "MTLS_ENABLED=false" >> .env
docker compose up -d --force-recreate

# Then rotate certs at controlled pace (CA key still intact)
./scripts/rotate-mtls-certs.sh

# Re-enable mTLS and restart
# Remove MTLS_ENABLED=false from .env
docker compose up -d --force-recreate
```

Monitor the `conclave_cert_expiry_days` Prometheus metric and the alert rules
in Section 13.5 of `docs/OPERATOR_MANUAL.md` to receive advance warning
before certs reach zero.
