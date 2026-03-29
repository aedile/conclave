# Operations Runbook — Conclave Synthetic Data Engine

This runbook covers deployment, startup troubleshooting, vault operations,
incident response, rollback procedures, and key rotation for the Conclave
Synthetic Data Engine.

For API reference see `docs/OPERATOR_MANUAL.md`.
For production deployment configuration see `docs/PRODUCTION_DEPLOYMENT.md`.
For disaster recovery see `docs/DISASTER_RECOVERY.md`.

---

## 1. Deployment

### 1.1 Pre-conditions

- Docker Engine >= 24 and Docker Compose >= 2.20 (or Kubernetes >= 1.28)
- All required environment variables set (see Section 2 — Startup Failures)
- `VAULT_SEAL_SALT` generated and stored out-of-band:
  ```
  python3 -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(16)).decode())"
  ```
- PostgreSQL database created and accessible from the application container

### 1.2 Docker Compose Deployment

```bash
# 1. Copy and populate the environment file
cp .env.example .env
# Edit .env with production values

# 2. Generate secrets if not already done
python3 -c "import secrets; print(secrets.token_hex(32))"  # for AUDIT_KEY
python3 -c "import secrets; print(secrets.token_hex(32))"  # for JWT_SECRET_KEY

# 3. Start all services
docker compose up -d

# 4. Verify all containers are healthy
docker compose ps
docker compose logs conclave-app --tail=50

# 5. Unseal the vault (required before any operation)
curl -X POST http://localhost:8000/unseal \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "<your-vault-passphrase>"}'
# Expected: {"status": "unsealed"}

# 6. Verify readiness
curl http://localhost:8000/ready
# Expected: {"status": "ready"} with HTTP 200
```

### 1.3 Kubernetes Deployment

```bash
# 1. Create namespace and secrets
kubectl create namespace conclave
kubectl create secret generic conclave-secrets \
  --from-literal=AUDIT_KEY=<hex-key> \
  --from-literal=JWT_SECRET_KEY=<secret> \
  --from-literal=MASKING_SALT=<salt> \
  --from-literal=OPERATOR_CREDENTIALS_HASH=<bcrypt-hash> \
  -n conclave

# 2. Apply manifests
kubectl apply -f k8s/ -n conclave

# 3. Wait for rollout
kubectl rollout status deployment/conclave -n conclave

# 4. Unseal the vault via a port-forward
kubectl port-forward svc/conclave 8000:8000 -n conclave &
curl -X POST http://localhost:8000/unseal \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "<your-vault-passphrase>"}'
```

### 1.4 Verification After Deployment

```bash
curl http://localhost:8000/health   # Basic liveness (should return 200)
curl http://localhost:8000/ready    # Full readiness (should return 200)
```

---

## 2. Startup Failures

The following table covers every `ConfigurationError` that can prevent startup.

| Error message | Required env var | Remediation |
|---|---|---|
| `database_url must not be empty` | `DATABASE_URL` or `CONCLAVE_DATABASE_URL` | Set to a valid PostgreSQL DSN: `postgresql://USER:PASSWORD@HOST:5432/DBNAME`  <!-- pragma: allowlist secret --> |
| `audit_key must not be empty` | `AUDIT_KEY` or `CONCLAVE_AUDIT_KEY` | Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `artifact_signing_key ... must not be empty` | `ARTIFACT_SIGNING_KEY` | Generate as above; or use `ARTIFACT_SIGNING_KEYS` JSON map |
| `masking_salt must not be empty` | `MASKING_SALT` or `CONCLAVE_MASKING_SALT` | Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `jwt_secret_key must not be empty` | `JWT_SECRET_KEY` or `CONCLAVE_JWT_SECRET_KEY` | Generate a cryptographically random string of at least 32 chars |
| `operator_credentials_hash must not be empty` | `OPERATOR_CREDENTIALS_HASH` | Generate bcrypt hash: `python3 -c "import bcrypt; print(bcrypt.hashpw(b'<pass>', bcrypt.gensalt()).decode())"` |
| `OPERATOR_CREDENTIALS_HASH has an invalid format` | `OPERATOR_CREDENTIALS_HASH` | Hash must start with `$2b$` and be at least 59 characters |
| `artifact_signing_key_active ... not present` | `ARTIFACT_SIGNING_KEY_ACTIVE` | Set to the hex key ID that matches a key in `ARTIFACT_SIGNING_KEYS` |
| `VAULT_SEAL_SALT environment variable is not set` | `VAULT_SEAL_SALT` | Generate: `python3 -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(16)).decode())"` |
| `VAULT_SEAL_SALT must decode to at least 16 bytes` | `VAULT_SEAL_SALT` | Re-generate using the command above (ensure 16+ byte source) |
| `CONCLAVE_DATA_DIR must not be the filesystem root '/'` | `CONCLAVE_DATA_DIR` | Set to a specific directory, e.g. `/app/data` |
| `CONCLAVE_DATA_DIR ... does not exist` | `CONCLAVE_DATA_DIR` | Create the directory: `mkdir -p /app/data` |

### 2.1 Checking Startup Logs

```bash
# Docker Compose
docker compose logs conclave-app --since=2m --follow

# Kubernetes
kubectl logs -l app=conclave -n conclave --since=2m --follow
```

---

## 3. Vault Operations

### 3.1 Unseal

**Pre-conditions**: Application is running, `VAULT_SEAL_SALT` is set.

```bash
curl -X POST http://localhost:8000/unseal \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "<passphrase>"}'
# Expected: {"status": "unsealed"}
```

**Verification**: `curl http://localhost:8000/ready` returns HTTP 200.

### 3.2 Seal (Administrative)

Sealing revokes the in-memory KEK. All subsequent operations requiring
the KEK will return 423 Locked until the vault is unsealed again.

```bash
curl -X POST http://localhost:8000/vault/seal \
  -H "Authorization: Bearer <operator-jwt>"
# Expected: {"status": "sealed"}
```

**Verification**: `curl http://localhost:8000/ready` returns HTTP 503.

### 3.3 Key Rotation (Vault Passphrase)

The vault passphrase derives the KEK via PBKDF2. Rotating it requires:

1. **Seal** the vault (POST /vault/seal with operator JWT).
2. **Rotate** the stored passphrase (update in your secrets manager).
3. **Unseal** with the new passphrase (POST /unseal).
4. **Verify** readiness (GET /ready).

The `VAULT_SEAL_SALT` must NOT change when rotating the passphrase.
The salt is not secret — it is an anti-rainbow-table measure that must
remain consistent so PBKDF2 derivation produces the expected KEK.

### 3.4 Recovery from Lost Passphrase

If the vault passphrase is irretrievably lost:

1. All encrypted data objects are unrecoverable (by design — no key escrow).
2. Redeploy the application with a new `VAULT_SEAL_SALT` and new passphrase.
3. Re-ingest all source data and re-run synthesis jobs.
4. Document the recovery in the audit trail (`VAULT_RECOVERY_REQUIRED` event).

There is no backdoor. This is a design guarantee, not a limitation.

---

## 4. Incident Response

### 4.1 PII Exposure Incident

**Indicators**: Logs show PII field values in audit events or API responses.

**Steps**:
1. Immediately rotate the `MASKING_SALT` (see Section 5.4).
2. Identify the window of exposure from audit logs.
3. Revoke all operator JWTs (rotate `JWT_SECRET_KEY`).
4. Notify affected parties per your data breach notification policy.
5. File a `PII_EXPOSURE_INCIDENT` audit event via the CLI:
   ```
   conclave audit log-event --type PII_EXPOSURE_INCIDENT \
     --actor security-team --resource all --action investigate \
     --details '{"severity": "high", "ticket": "INC-XXXX"}'
   ```
6. Preserve all logs before rotation — do NOT overwrite audit logs.

### 4.2 Audit Chain Break

**Indicators**: `AUDIT_CHAIN_RESUME_FAILURE_TOTAL` counter is non-zero;
logs show "Starting from genesis" after restart.

**Steps**:
1. Do NOT delete or overwrite the anchor file at `ANCHOR_FILE_PATH`.
2. Identify the last valid anchor record:
   ```bash
   tail -n 1 logs/audit_anchors.jsonl
   ```
3. Run the signature migration tool to verify chain integrity:
   ```bash
   conclave audit migrate-signatures \
     --input logs/audit.jsonl \
     --output logs/audit_verified.jsonl
   ```
4. If chain cannot be verified, preserve original files for forensic review.
5. Document the break in a new audit event and escalate to your compliance team.

### 4.3 Compromised Signing Key

**Indicators**: Artifact signatures fail verification; unauthorized access detected.

**Steps**:
1. **Immediately** add a new key to `ARTIFACT_SIGNING_KEYS` and set
   `ARTIFACT_SIGNING_KEY_ACTIVE` to the new key ID.
2. Rotate `JWT_SECRET_KEY` to revoke all current sessions.
3. Re-sign existing artifacts if required by your compliance policy:
   ```bash
   conclave artifacts re-sign --key-id <new-key-id>
   ```
4. Remove the compromised key from `ARTIFACT_SIGNING_KEYS` after all
   artifacts signed with it have been re-signed or discarded.
5. Emit an audit event: `SIGNING_KEY_COMPROMISED`.

### 4.4 Database Corruption

**Indicators**: SQLAlchemy errors in logs; synthesis jobs failing with DB errors.

**Steps**:
1. Stop the application immediately (`docker compose stop conclave-app`).
2. Restore from the most recent backup.
3. Verify audit log continuity — if audit events are missing from the
   restored DB, the anchor chain will show gaps.
4. Resume from the anchor file (the anchor file is independent of the DB).
5. Restart the application and verify readiness.

---

## 5. Rollback

### 5.1 Docker Compose Rollback

```bash
# Roll back to the previous image tag
docker compose down
IMAGE_TAG=<previous-tag> docker compose up -d

# Verify
docker compose ps
curl http://localhost:8000/health
```

### 5.2 Kubernetes Rollback

```bash
# Roll back the deployment to the previous revision
kubectl rollout undo deployment/conclave -n conclave

# Wait for rollout
kubectl rollout status deployment/conclave -n conclave

# Verify
kubectl get pods -n conclave
curl http://localhost:8000/health
```

### 5.3 Rollback-of-the-Rollback

If a rollback itself causes issues:

1. Identify the stable revision: `kubectl rollout history deployment/conclave -n conclave`
2. Roll back to a specific revision: `kubectl rollout undo deployment/conclave --to-revision=<N> -n conclave`
3. For Docker Compose, pin to a known-good image tag in `docker-compose.yml`.

---

## 6. Key Rotation

All key rotations follow the same safe pattern: **add the new key first,
then remove the old key**. Never delete a key before adding its replacement.

### 6.1 Audit Key Rotation (`AUDIT_KEY`)

Zero-downtime: audit logging continues throughout.

1. Generate a new hex-encoded 32-byte key:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```
2. Update `AUDIT_KEY` in your secrets manager.
3. Perform a rolling restart of the application pods/containers to pick up the new key.
4. After restart, new audit events are signed with the new key.
5. **Important**: audit events signed with the OLD key cannot be verified
   after rotation. Archive the old key alongside the log files it signed.
   Run `conclave audit migrate-signatures` to re-sign archived events.

### 6.2 Artifact Signing Key Rotation (`ARTIFACT_SIGNING_KEY`)

Zero-downtime using the multi-key map:

1. Generate a new key ID and hex key:
   ```bash
   KEY_ID=$(python3 -c "import secrets; print(secrets.token_hex(4))")
   KEY_HEX=$(python3 -c "import secrets; print(secrets.token_hex(32))")
   echo "Key ID: $KEY_ID, Key: $KEY_HEX"
   ```
2. Add the new key to `ARTIFACT_SIGNING_KEYS` JSON (keep the old key):
   ```json
   {"<old-key-id>": "<old-hex>", "<new-key-id>": "<new-hex>"}
   ```
3. Set `ARTIFACT_SIGNING_KEY_ACTIVE=<new-key-id>`.
4. Rolling restart — new artifacts are signed with the new key; old
   artifacts still verify using the old key (still in the map).
5. After all old artifacts are expired or re-signed, remove the old key
   from `ARTIFACT_SIGNING_KEYS`.

### 6.3 JWT Secret Key Rotation (`JWT_SECRET_KEY`)

**All existing JWTs are immediately invalidated** — operators must re-authenticate.

1. Generate a new secret key (>= 32 chars).
2. Update `JWT_SECRET_KEY` in your secrets manager.
3. Perform a rolling restart.
4. Notify all operators that they must re-authenticate.

### 6.4 Masking Salt Rotation (`MASKING_SALT`)

**Warning**: rotating the masking salt changes all deterministic pseudonyms.
Two records that previously hashed to the same pseudonym will now hash
differently — referential integrity of masked data is broken.

1. Generate a new masking salt.
2. Update `MASKING_SALT` in your secrets manager.
3. Rolling restart.
4. Re-mask all existing pseudonymized datasets if referential integrity matters.
5. Document the rotation date — any cross-dataset joins that relied on
   consistent pseudonyms are now broken.

---

## 7. CLI Reference for Runbook Operations

```bash
# Audit chain migration (v1/v2 -> v3 signatures)
poetry run conclave audit migrate-signatures \
  --input logs/audit.jsonl \
  --output logs/audit_migrated.jsonl

# Log a custom audit event
poetry run conclave audit log-event \
  --type INCIDENT_DECLARED \
  --actor operator \
  --resource system \
  --action declare_incident \
  --details '{"ticket": "INC-001"}'
```

---

*Last updated: Phase 70 — T70.5*
*See also: `docs/OPERATOR_MANUAL.md`, `docs/PRODUCTION_DEPLOYMENT.md`, `docs/DISASTER_RECOVERY.md`*
