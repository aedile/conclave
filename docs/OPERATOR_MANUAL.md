# Conclave Engine — Operator Manual

## Security Audit Status

OWASP ZAP baseline scan (`zap-baseline`) runs on every CI build. Current findings are CI-environment artefacts, not real vulnerabilities:

| Rule | Status | Justification |
|------|--------|---------------|
| 10035 — HSTS Header Not Set | IGNORED | HSTS is set by the TLS-terminating reverse proxy. `HTTPSEnforcementMiddleware` enforces application-layer HTTP rejection (T42.2). |
| 10038 — CSP Header Not Found | WARN | `CSPMiddleware` is implemented and tested; ZAP may flag non-HTML API responses where CSP is optional. |
| 10096 — Timestamp Disclosure | IGNORED | Timestamps in JSON responses are intentional (`created_at`, `updated_at`). |
| 10054 — Cookie Without SameSite | IGNORED | Token-based auth — cookies are not used. |

No code changes required. All active security controls (`HTTPSEnforcementMiddleware`, `CSPMiddleware`, `SealGateMiddleware`, `LicenseGateMiddleware`, `RequestBodyLimitMiddleware`, `RateLimitGateMiddleware`) are verified by unit and integration tests.

**HTTPS (T42.2):** In production (`CONCLAVE_ENV=production`), all plain HTTP requests are rejected with 421 before any other processing. A TLS-terminating reverse proxy setting `X-Forwarded-Proto: https` is required. See `docs/PRODUCTION_DEPLOYMENT.md` Appendix A.

---

## 1. System Requirements

### Hardware

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 4 cores | 8+ cores |
| RAM | 8 GB | 32 GB |
| Disk | 50 GB | 200 GB SSD |
| GPU | None (CPU-only) | NVIDIA with CUDA (for DP-SGD) |

### Software

- **Docker** 24.0+
- **Docker Compose** 2.20+ (`docker compose` plugin, not standalone v1)
- **OS**: Linux (recommended) or macOS with Apple Silicon
- **Disk encryption**: LUKS (Linux) or FileVault (macOS) required in production — PostgreSQL data volumes must reside on an encrypted volume

### Network

Air-gapped by design. Inter-service traffic uses an isolated Docker bridge (`internal`). Host-exposed ports:
- `8000` — Conclave Engine API (`app`)
- `3000` — Grafana dashboard

---

## 2. Initial Setup

### 2.1 Clone the Repository

Air-gapped host: transfer `conclave-bundle-<version>.tar.gz` from `make build-airgap-bundle`. Connected host:

```bash
git clone <repository-url> conclave && cd conclave
```

### 2.2 Provision Secrets

```bash
mkdir -p secrets && chmod 700 secrets

openssl rand -hex 32 > secrets/app_secret_key.txt
openssl rand -hex 32 > secrets/postgres_password.txt
openssl rand -hex 32 > secrets/grafana_admin_password.txt
echo "conclave-admin" > secrets/grafana_admin_user.txt
openssl rand -hex 16 > secrets/minio_ephemeral_access_key.txt
openssl rand -hex 32 > secrets/minio_ephemeral_secret_key.txt

chmod 600 secrets/*.txt
```

### 2.3 Configure the Environment

```bash
cp .env.example .env
```

**Required variables:**

| Variable | Description | Example |
|----------|-------------|---------|
| `ALE_KEY` | Fernet key for Application-Level Encryption | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DATABASE_URL` | PostgreSQL URL for migrations | `postgresql+psycopg2://conclave:<pw>@localhost:5432/conclave` |
| `VAULT_SEAL_SALT` | Base64url-encoded 16-byte PBKDF2 salt | `python3 -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(16)).decode())"` |
| `AUDIT_KEY` | Hex-encoded 32-byte HMAC key for audit log signing | `python3 -c "import os; print(os.urandom(32).hex())"` |
| `LICENSE_PUBLIC_KEY` | PEM-encoded RSA public key from licensing server | See [docs/LICENSING.md](LICENSING.md) |

**Optional variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `HUEY_BACKEND` | `redis` | Task queue backend (`redis` or `memory`) |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection URL |
| `FORCE_CPU` | `false` | Force CPU-only synthesis (set `true` without a compatible NVIDIA GPU) |
| `ARTIFACT_SIGNING_KEY` | — | Hex 32-byte HMAC-SHA256 key for artifact signing. **Required in production.** `python3 -c "import secrets; print(secrets.token_hex(32))"` |

### 2.4 Build the Application Image

```bash
make build
```

---

## 3. Starting the Platform

### 3.1 Run Database Migrations

Run before first start and after every update that includes new migrations:

```bash
export DB_USER=conclave
export DB_PASSWORD=$(cat secrets/postgres_password.txt)
export DB_HOST=localhost DB_PORT=5432 DB_NAME=conclave

poetry run alembic upgrade head
```

`alembic upgrade head` is idempotent. Always run it before restarting `app` after a release — a schema mismatch causes startup failure.

### 3.2 Start All Services

```bash
docker compose up -d
```

| Service | Role | Internal Port |
|---------|------|---------------|
| `app` | Conclave Engine API (FastAPI/uvicorn) | 8000 |
| `redis` | Huey task queue | 6379 |
| `postgres` | Primary store (PostgreSQL 16) | 5432 |
| `pgbouncer` | Connection pooler (transaction mode) | 6432 |
| `prometheus` | Metrics scraper | 9090 |
| `alertmanager` | Alert routing | 9093 |
| `grafana` | Dashboard | 3000 |
| `minio-ephemeral` | Ephemeral artifact store (tmpfs) | 9000 |

Development-only services (Jaeger, hot-reload uvicorn) start automatically when `docker-compose.override.yml` is present.

### 3.3 Verify Health

```bash
docker compose ps
docker compose exec app curl -s http://localhost:8000/health
# Expected: {"status": "ok"}
```

`postgres` must be `healthy` before `app` starts (enforced by `depends_on`).

---

## 4. Vault Unseal Procedure

The engine boots **SEALED**. All non-exempt routes return `423 Locked` until unsealed. Unsealing derives the KEK from the operator passphrase via PBKDF2-HMAC-SHA256 (600,000 iterations). The KEK is held in process memory only — never written to disk.

### 4.1 Unseal via the React UI

1. Open `http://<host>:8000`.
2. The RouterGuard detects 423 and redirects to `/unseal`.
3. Enter the operator passphrase and click **Unseal**.

Error codes:

| `error_code` | Cause | Resolution |
|-------------|-------|------------|
| `EMPTY_PASSPHRASE` | Empty string submitted | Enter a non-empty passphrase |
| `ALREADY_UNSEALED` | Already unsealed | No action needed |
| `CONFIG_ERROR` | `VAULT_SEAL_SALT` missing or too short | Check `.env`; regenerate if missing |

### 4.2 Unseal via the API

```bash
curl -X POST http://<host>:8000/unseal \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "<operator-passphrase>"}'
# Response: {"status": "unsealed"}
```

### 4.3 Seal the Vault

**Option 1 — Container restart (non-destructive)**

Clears the in-memory KEK; encrypted data on disk is preserved. Routes return 423 until next unseal.

```bash
docker compose restart synth-engine
```

**Option 2 — `POST /security/shred` (DESTRUCTIVE — irreversible)**

Performs NIST SP 800-88 key zeroization. All keying material is permanently destroyed. No recovery without full re-initialisation.

```bash
curl -X POST http://<host>:8000/security/shred \
  -H "Authorization: Bearer ${TOKEN}"
```

> **Warning**: For decommissioning and incident response only. Unlike a restart, shred cannot be reversed.

### 4.4 Multi-Worker Unseal Procedure

In multi-worker deployments (e.g., Gunicorn with multiple Uvicorn worker processes), each worker
process has an **independent** in-memory `VaultState`. A vault unseal command unseals only the
worker process that receives the HTTP request — other workers remain sealed.

**Why this matters**: After a deployment or rolling restart, some workers may be unsealed while
others are not. Sealed workers return `503` on `/ready` and are excluded from the load-balancer
pool, but they are still running. You must unseal each worker individually.

#### Checking seal status per worker

```bash
# /health/vault reports this specific worker's seal status and its PID
curl http://<host>:8000/health/vault
# Response: {"vault_sealed": true, "worker_pid": 12345}
```

The `worker_pid` field identifies which OS process responded. Use this to confirm which workers
have been unsealed after each unseal command.

#### Unsealing all workers

Because HTTP load balancers route requests to different workers, send multiple unseal requests
until all workers confirm `"vault_sealed": false` on `/health/vault`:

```bash
# Repeat until all workers are unsealed (typically num_workers + 1 times to be safe)
for i in $(seq 1 8); do
  curl -X POST http://<host>:8000/unseal \
    -H "Content-Type: application/json" \
    -d '{"passphrase": "<operator-passphrase>"}' \
    2>/dev/null || true
  sleep 0.1
done
```

Alternatively, use the `/ready` probe with the `vault_sealed` field to detect sealed workers:

```bash
# A sealed worker will return 503 with vault_sealed: true
curl -s http://<host>:8000/ready | python3 -m json.tool
```

#### Automated unseal in orchestrated environments

For Kubernetes or Docker Swarm deployments:

1. Store the passphrase in a Kubernetes `Secret` or Docker secret.
2. Use an `initContainer` or startup hook that calls `POST /unseal` on container startup.
3. Configure the readiness probe to check `GET /ready` — sealed workers are automatically
   excluded from the pod's ready state.
4. The `vault_sealed` field in `/ready` allows orchestrators to distinguish a sealed-worker
   `503` from a dependency-failure `503`.

> **Security note**: The passphrase is never persisted — it is used only to derive the
> in-memory KEK. Each worker derives the same KEK from the same passphrase, so any worker
> can be unsealed with the same passphrase. The passphrase itself must be stored in a
> secrets manager (Vault, AWS Secrets Manager, Kubernetes Secrets) — never in plain text.


---

## 5. Creating and Monitoring Synthesis Jobs

### 5.1 License Activation

The software must be licensed before creating jobs. See [docs/LICENSING.md](LICENSING.md).

### 5.2 Create a Job

```bash
curl -X POST http://<host>:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "customer-data-synthetic",
    "parquet_path": "/data/customers.parquet",
    "num_rows": 1000,
    "num_epochs": 50,
    "checkpoint_every_n": 10
  }'
```

Response includes the job `id`.

### 5.3 Start a Job

```bash
curl -X POST http://<host>:8000/jobs/<job-id>/start
# Response: HTTP 202 Accepted
```

A Huey worker must be running to execute the task.

### 5.4 Monitor Progress via SSE

```bash
curl -N http://<host>:8000/jobs/<job-id>/stream
```

| Event | Payload | Meaning |
|-------|---------|---------|
| `progress` | `{"percent": 42, "epoch": 21, "total_epochs": 50}` | Training update |
| `complete` | `{"status": "COMPLETE"}` | Job finished |
| `error` | `{"status": "FAILED", "detail": "..."}` | Job failed |

### 5.5 Monitor via the Dashboard

The React dashboard at `http://<host>:8000` shows active jobs with real-time progress. Jobs load from `GET /jobs` (cursor-based pagination); progress updates via EventSource.

---

## 6. Stopping, Restarting, and Updating

### 6.1 Graceful Stop

```bash
docker compose down
```

Named volumes (`postgres_data`, `grafana_data`, `prometheus_data`) are **preserved**. `minio-ephemeral` tmpfs data is **discarded** (by design — privacy mandate).

### 6.2 Restart

```bash
docker compose up -d
```

The vault is **sealed** after every restart. Unseal again (see Section 4).

### 6.3 Update

```bash
docker compose build app

export DB_USER=conclave DB_PASSWORD=$(cat secrets/postgres_password.txt) \
       DB_HOST=localhost DB_PORT=5432 DB_NAME=conclave
poetry run alembic upgrade head

docker compose up -d --no-deps app
```

Always run `alembic upgrade head` before the app starts. Schema mismatch causes startup failure.

---

## 7. Logs and Troubleshooting

### 7.1 Application Logs

```bash
docker compose logs -f app   # tail app
docker compose logs -f       # tail all services
```

Log rotation: `app` rotates at 50 MB (3 files retained); other services at 10–20 MB.

### 7.2 Audit Log

WORM (append-only) audit events for unseal, shred, key rotation, and license activation:

```bash
docker compose exec app cat /tmp/audit.log
```

Each event is HMAC-signed with `AUDIT_KEY`. Tampering is detectable.

### 7.3 Multi-Worker Audit Chain Semantics

Each Uvicorn worker process maintains an **independent** audit hash chain in memory.
The chain links events within a single worker: each event's `prev_hash` points to the
hash of the previous event logged by that same worker.

In multi-worker deployments (e.g., `uvicorn --workers 4`), all workers append to the
same anchor file (e.g., `/tmp/audit_anchor.jsonl`). Appends from different workers are
interleaved on disk — the file order does not reflect a single global chain.

**What this means for compliance:**

| Requirement | Behaviour |
|-------------|-----------|
| Per-worker chain integrity | Guaranteed — each worker's chain is independently verifiable via `GET /audit/verify` |
| Cross-worker chain contiguity | Not guaranteed — anchor file entries are interleaved |
| Single global chain | Not provided in multi-worker mode |

**Compliance recommendation:** If your audit policy requires a single unbroken chain
(e.g., ISO 27001 log integrity controls), deploy with a single worker:

```bash
uvicorn src.synth_engine.bootstrapper.main:create_app --workers 1
```

Or, for production throughput with chain compliance, use an external chain-coordinator
service (e.g., a dedicated audit service that receives events via a queue and maintains
the chain itself) and configure `AUDIT_ANCHOR_BACKEND` accordingly.

**Verifying a single worker's chain:**

```bash
# All events from a specific worker can be extracted and verified independently
curl -H "Authorization: Bearer ${TOKEN}" http://<host>:8000/audit/verify
# Response: {"chain_valid": true, "event_count": 42}
```

The `/audit/verify` endpoint verifies the chain for the worker that handles the
request. In multi-worker mode, send the request to each worker in turn (using the
`worker_pid` from `/health/vault` to confirm which worker responded).

---

### 7.4 Common Issues

**`423 Locked` on all routes** — vault is sealed. Unseal before making API requests (exempt routes: `/health`, `/unseal`, `/metrics`, `/docs`, `/license/challenge`, `/license/activate`).

**`402 Payment Required`** — software is unlicensed. See [docs/LICENSING.md](LICENSING.md).

**`app` exits immediately on start** — `validate_config()` rejects misconfigured environments at boot.

```bash
docker compose logs app | grep -i "error\|secret\|missing\|config"
```

Common causes:
- Missing files in `secrets/` or wrong permissions (must be `600`).
- Missing `DATABASE_URL` or `AUDIT_KEY` (required in all modes). In production, `ARTIFACT_SIGNING_KEY` is also required.
- `ALE_KEY`, `VAULT_SEAL_SALT`, `LICENSE_PUBLIC_KEY` are not startup-validated; they fail at first use.

**Schema mismatch on start** — `alembic upgrade head` was not run. See Sections 3.1 and 6.3.

**PostgreSQL health check fails** — `docker compose logs postgres`. For corrupt volumes see [docs/DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) §4.

**OOM during synthesis**

```text
OOMGuardrailError: Estimated memory requirement exceeds available RAM
```

Reduce `num_rows` or increase the Docker memory limit in `docker-compose.override.yml`. See [docs/DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) §2.

**`413 Request Entity Too Large`** — `RequestBodyLimitMiddleware` enforces 1 MiB. Reduce payload size.

**`400 Bad Request` (JSON nesting)** — max nesting depth is 100 levels. Flatten the payload.

---

## 8. Security

For CORS policy, DDoS mitigation, TLS hardening, passphrase management, and key rotation, see [docs/SECURITY_HARDENING.md](SECURITY_HARDENING.md).

### 8.1 TLS Termination

The `app` service does not terminate TLS. Place nginx or Caddy in front of port 8000. The proxy must set:

```
Strict-Transport-Security: max-age=63072000; includeSubDomains
```

### 8.2 Network Isolation

All inter-service traffic is confined to the `internal` Docker bridge. Only `app` (8000) and `grafana` (3000) are reachable from the host. Do not bind to public interfaces in production.

### 8.3 mTLS

Native mTLS for inter-container connections is an opt-in Compose overlay. Setup in Section 12; certificate rotation in Section 13.

### 8.4 Secret Management

Secrets are injected via Docker secrets files (`secrets/`). Never embed secret values in `docker-compose.yml` or version control. Rotate by replacing the file and restarting the affected service.

### 8.5 Capability Model

The `app` container drops all Linux capabilities except `IPC_LOCK` (required to `mlock` the KEK — prevents swap). Filesystem is `read_only: true`; writable scratch uses tmpfs only.

### 8.6 Synthesis Artifact Privacy

Training artifacts are stored in `minio-ephemeral` on tmpfs. All artifacts are discarded on container stop — no training data survives a restart.

### 8.7 Model Artifact Signing

`ARTIFACT_SIGNING_KEY` (HMAC-SHA256) signs artifacts at save time and verifies at load time. Signature mismatch raises `SecurityError`.

**Generate key:** `python3 -c "import secrets; print(secrets.token_hex(32))"`

**Rotation:**
1. Generate a new key.
2. Update `ARTIFACT_SIGNING_KEY` in your secrets store.
3. Restart `app`. New artifacts are signed with the new key.
4. Existing artifacts signed with the old key fail verification after rotation — re-run any jobs whose artifacts must remain accessible.

In production, inject via Docker secrets, HashiCorp Vault, or Kubernetes `Secret`. Never embed plaintext in `docker-compose.yml`. Without the key, artifacts are saved and loaded without signatures (development only).

### 8.8 Reverse Proxy and X-Forwarded-For Trust

> **WARNING:** Conclave does not validate that `X-Forwarded-For` originates from a trusted proxy. A direct-access attacker can spoof their IP by injecting this header.

**Requirement:** Conclave MUST be deployed behind a trusted reverse proxy that:
1. Strips any `X-Forwarded-For` set by the client.
2. Re-sets `X-Forwarded-For` to the real connecting IP.
3. Is not directly reachable by untrusted clients.

Never expose port 8000 directly to the internet.

**Sample nginx configuration:**

```nginx
server {
    listen 443 ssl;
    server_name conclave.example.com;

    ssl_certificate     /etc/ssl/certs/conclave.crt;
    ssl_certificate_key /etc/ssl/private/conclave.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Real-IP       $remote_addr;
        proxy_set_header Host            $host;
        proxy_set_header Forwarded       "";
    }
}
```

**Risk if omitted:** Any client can set `X-Forwarded-For: 127.0.0.1` to appear as loopback, potentially bypassing IP-based access controls or poisoning audit logs.

### 8.9 HTTPS Enforcement (T42.2)

In production (`CONCLAVE_ENV=production` — `ENV=production` is deprecated), all plain HTTP requests are rejected with **421 Misdirected Request** before rate limiting, auth, or business logic runs. A redirect would transmit headers in cleartext before firing — a classic SSL-stripping vector; 421 terminates immediately.

`HTTPSEnforcementMiddleware` checks `X-Forwarded-Proto` first (proxy deployments), then falls back to the raw ASGI scheme.

**Production proxy requirements:**
1. Terminate TLS on port 443.
2. Set `X-Forwarded-Proto: https` on every forwarded request.
3. Strip any `X-Forwarded-Proto` injected by the client.

See `docs/PRODUCTION_DEPLOYMENT.md` §2.1 for nginx/Caddy templates.

At startup, `warn_if_ssl_misconfigured()` emits a `WARNING` log when `CONCLAVE_SSL_REQUIRED=true` but no TLS cert path is configured.

| `CONCLAVE_ENV` / `ENV` | HTTPS enforced? |
|------------------------|-----------------|
| `production` | Yes — HTTP → 421 |
| `development` or absent | No — HTTP allowed |

---

## 9. Differential Privacy (DP-SGD) Configuration

### 9.1 DP Parameters

| Parameter | Description | Typical Range |
|-----------|-------------|---------------|
| `epsilon` | Privacy budget (lower = stronger privacy, less utility) | 1–20 |
| `delta` | Failure probability | 1e-5 (fixed) |
| `noise_multiplier` | Gaussian noise std / `max_grad_norm`. Higher = more noise = lower epsilon. | 0.5–2.0 |
| `max_grad_norm` | Max L2 norm for per-sample gradient clipping | 1.0 (canonical) |

Epsilon is not set directly — configure `noise_multiplier` and `max_grad_norm`; query the resulting epsilon via `epsilon_spent(delta=1e-5)` after training. Epsilon varies with dataset size, batch size, and epoch count.

### 9.2 Recommended Epsilon by Use Case

Based on empirical benchmarks in `docs/archive/DP_QUALITY_REPORT.md` (500-row dataset, 10 epochs). Recalibrate for production datasets.

| Use Case | Epsilon | Rationale |
|----------|---------|-----------|
| External publication / regulatory compliance | 1–2 | Strong guarantee; quality loss acceptable |
| Internal analytics on sensitive PII | 5–8 | Balanced tradeoff |
| Non-sensitive internal testing | 10–20 | Utility-first |
| Production ML (quality-first) | No DP | Only when sensitivity and regulations allow |

### 9.3 Calibrated Noise Multiplier Values

Calibrated on 500 rows, 10 epochs — use as a starting point.

| Target Epsilon | `noise_multiplier` |
|---------------|--------------------|
| ~1 | 2.00 |
| ~5 | 0.75 |
| ~10 | 0.55 |

### 9.4 Creating a DP-Enabled Synthesis Job

```python
from synth_engine.bootstrapper.main import build_dp_wrapper, build_synthesis_engine

wrapper = build_dp_wrapper(max_grad_norm=1.0, noise_multiplier=1.1)
engine = build_synthesis_engine(epochs=300)

artifact = engine.train(
    table_name="customers",
    parquet_path="/data/customers.parquet",
    dp_wrapper=wrapper,
)

epsilon = wrapper.epsilon_spent(delta=1e-5)
print(f"Actual epsilon: {epsilon:.4f}")  # Log for compliance records

wrapper.check_budget(allocated_epsilon=8.0, delta=1e-5)  # raises BudgetExhaustionError if exceeded

synthetic_df = artifact.model.sample(n_rows=1000)
```

### 9.5 Privacy Budget Accounting

`spend_budget()` deducts epsilon atomically using `SELECT ... FOR UPDATE`. Raises `BudgetExhaustionError` if the requested amount would exhaust the budget.

```python
from synth_engine.modules.privacy.accountant import spend_budget

async with get_async_session(engine) as session:
    await spend_budget(
        amount=epsilon,
        job_id=job.id,
        ledger_id=1,
        session=session,
        note="customer-data-synthetic run 2026-03-15",
    )
```

### 9.6 DP Operational Notes

- **Proxy model**: Opacus runs on a lightweight proxy linear model (not CTGAN's Discriminator) to work around CTGAN's optimizer lifecycle. See ADR-0025 (proxy rationale) and ADR-0036 (discriminator-level DP-SGD, Phase 30+).
- **Epoch count**: Epsilon accumulates per epoch. 300 epochs produce significantly higher epsilon than 10 epochs at the same `noise_multiplier`.
- **Dataset size**: Larger datasets yield lower epsilon for the same noise config.
- **CPU vs. GPU**: Opacus works on both; GPU is substantially faster at 300+ epochs. `FORCE_CPU=true` forces CPU mode.
- **Single-use wrapper**: Each `DPTrainingWrapper` is single-use — `wrap()` twice raises `RuntimeError`.
- **`secure_mode` deferred**: Opacus emits `UserWarning: Secure RNG turned off`. `secure_mode=True` requires `torchcsprng`, which has no Python 3.14 wheels and is unmaintained. The PRNG reconstruction attack it mitigates is not present in Conclave's air-gapped threat model. Warning suppressed in `pyproject.toml`; justified by ADR-0017a.

---

## 10. Development and CI Reference

### 10.1 Pytest Marker Routing

| Marker | Description | Usage |
|--------|-------------|-------|
| `unit` | Fast, isolated, no external dependencies | `pytest -m unit` |
| `integration` | Requires live DB or external services | `pytest -m integration` |
| `synthesizer` | Requires SDV, PyTorch, Opacus | `pytest -m synthesizer` |

CI gates:
- `pytest tests/unit/ -W error` — zero warnings tolerated
- `pytest tests/integration/ -v --no-cov` — separate gate
- `pytest -m synthesizer` — requires `poetry install --with synthesizer`

### 10.2 Zero-Warning Policy

`pyproject.toml` sets `filterwarnings = ["error", ...]`. All Python warnings become test failures. If a new dependency emits warnings, add a scoped suppression entry with a written justification comment.

---

## 11. Data Retention and Compliance

Configure retention periods before going to production. Full compliance policy (GDPR/CCPA/HIPAA, erasure, audit guarantees) is in [docs/DATA_COMPLIANCE.md](DATA_COMPLIANCE.md).

### 11.1 Retention Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `JOB_RETENTION_DAYS` | `90` | Days before completed/failed jobs are eligible for purge (legal-hold jobs exempt) |
| `AUDIT_RETENTION_DAYS` | `1095` | Days audit events are retained (never deleted within this period) |
| `ARTIFACT_RETENTION_DAYS` | `30` | Days Parquet files and model checkpoints are retained on MinIO |

**Financial sector (7-year):**
```bash
JOB_RETENTION_DAYS=180
AUDIT_RETENTION_DAYS=2555
ARTIFACT_RETENTION_DAYS=14
```

**GDPR minimum (3-year):**
```bash
JOB_RETENTION_DAYS=90
AUDIT_RETENTION_DAYS=1095
ARTIFACT_RETENTION_DAYS=30
```

### 11.2 Legal Hold

```bash
TOKEN=$(curl -s -X POST http://<host>:8000/auth/token \
  -H "Content-Type: application/json" \
  -d '{"password": "<operator-passphrase>"}' | jq -r .access_token)

# Place hold
curl -X PATCH http://<host>:8000/admin/jobs/<job-id>/legal-hold \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"enable": true}'

# Release hold
curl -X PATCH http://<host>:8000/admin/jobs/<job-id>/legal-hold \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"enable": false}'
```

All holds and releases are logged to the WORM audit trail.

### 11.3 Manual Purge

```bash
curl -X POST http://<host>:8000/admin/retention/purge \
  -H "Authorization: Bearer ${TOKEN}"
```

Response includes counts of jobs deleted and artifacts shredded. All deletions are audited.

### 11.4 GDPR / CCPA Erasure Requests

```bash
curl -X DELETE http://<host>:8000/compliance/erasure \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"subject_id": "<source-record-id-or-email-hash>"}'
```

Response is a compliance receipt documenting every record deleted and preserved. Keep it as compliance evidence.

Notes:
- Vault must be unsealed (returns 423 if sealed).
- Rate-limited to 1 request/minute per operator.
- The erasure request itself is written to the WORM audit trail and cannot be deleted.
- Synthesized output and audit entries are preserved — see [docs/DATA_COMPLIANCE.md §3](DATA_COMPLIANCE.md).

### 11.5 Regulatory Retention Guidance

| Regulatory context | Recommended `AUDIT_RETENTION_DAYS` | Basis |
|--------------------|------------------------------------|-------|
| GDPR (EU) | 1,095 (3 years) | GDPR Article 5(1)(e) |
| CCPA (California) | 1,095 (3 years) | CCPA § 1798.100(e) |
| HIPAA (US healthcare) | 2,190 (6 years) | 45 CFR § 164.530(j) |
| Financial services (SEC Rule 17a-4) | 2,555 (7 years) | SEC Rule 17a-4(b)(1) |

Consult legal counsel for your jurisdiction and data category.

---

## 12. mTLS Inter-Container Communication (T46.1)

### 12.1 Overview

An operator-provisioned internal CA issues leaf certificates for each mTLS-participating service. The CA never leaves the operator host.

**Services with mTLS:** `app`, `postgres`, `pgbouncer`, `redis`

**Services exempt** (ADR-0029 Gap 7): `prometheus`, `alertmanager`, `grafana`, `minio`

### 12.2 Generating Certificates

Requires `openssl` 1.1.1+ on the operator host.

```bash
./scripts/generate-mtls-certs.sh        # First-time: generates CA + all leaf certs
./scripts/generate-mtls-certs.sh --help
```

| Option | Default | Description |
|--------|---------|-------------|
| `--ca-days N` | 3650 | CA cert validity (days) |
| `--leaf-days N` | 90 | Leaf cert validity (days) |
| `--output-dir DIR` | `secrets/mtls` | Output directory |
| `--force` | off | Regenerate CA even if `ca.key` exists |

### 12.3 Certificate Storage

Stored in `secrets/mtls/` (gitignored):

```
secrets/mtls/ca.crt      — CA trust anchor  (distribute to all containers)
secrets/mtls/ca.key      — CA private key   (0400 — operator host ONLY)
secrets/mtls/<svc>.crt   — Leaf certificate (0644)
secrets/mtls/<svc>.key   — Leaf private key (0600)
```

**CRITICAL:** `ca.key` must never be mounted into any container.

### 12.4 Certificate Expiry Monitoring

```bash
openssl x509 -noout -enddate -in secrets/mtls/app.crt
```

```python
from pathlib import Path
from synth_engine.shared.tls import TLSConfig

for service in ("app", "postgres", "pgbouncer", "redis"):
    days = TLSConfig.days_until_expiry(Path(f"secrets/mtls/{service}.crt"))
    status = "OK" if days > 14 else "EXPIRING SOON" if days > 0 else "EXPIRED"
    print(f"{service}: {days} days — {status}")
```

### 12.5 Certificate Rotation

Leaf certs default to 90-day validity. Rotate (CA is preserved unless `--force`):

```bash
./scripts/generate-mtls-certs.sh
docker compose restart app postgres pgbouncer redis

openssl verify -CAfile secrets/mtls/ca.crt secrets/mtls/app.crt  # verify chain
```

See `docs/PRODUCTION_DEPLOYMENT.md` §2.5 for full provisioning details.

---

## 13. Certificate Rotation Procedures (T46.3)

### 13.1 Overview

- **Leaf rotation**: Replace certs using the existing CA. Zero-downtime for PgBouncer/Redis/PostgreSQL; rolling restart required for `app` (uvicorn does not support live TLS reload).
- **CA rotation**: Requires a planned maintenance window. See Section 13.4.
- **Mixed-cert window**: During rotation, old and new leaf certs are both valid (same CA), so PgBouncer/Redis can reload without dropping connections.

### 13.2 Docker Compose Leaf Certificate Rotation

Prerequisites: `openssl` 1.1.1+; CA key at `secrets/mtls/ca.key`.

**Step 1: Run the rotation script**

```bash
./scripts/rotate-mtls-certs.sh
```

| Option | Default | Description |
|--------|---------|-------------|
| `--output-dir DIR` | `secrets/mtls` | Directory with current certs |
| `--leaf-days N` | `90` | New leaf cert validity |

The script backs up existing certs, generates new ones, validates, and prints reload commands. Exits with code 1 if any cert fails chain verification, key-pair match, or minimum expiry check (>30 days) — previous certs are preserved in a timestamped backup.

**Step 2: Reload services (in order)**

```bash
# PgBouncer — live reload, no connection drops
docker compose exec pgbouncer psql -U pgbouncer pgbouncer -c 'RELOAD;'

# Redis — live TLS reload, no connection drops
docker compose exec redis redis-cli CONFIG SET \
    tls-ca-cert-file /run/secrets/mtls/ca.crt \
    tls-cert-file /run/secrets/mtls/redis.crt \
    tls-key-file /run/secrets/mtls/redis.key

# PostgreSQL — reload without dropping connections
docker compose exec postgres psql -U postgres -c 'SELECT pg_reload_conf();'

# App — rolling restart required (brief ~1-5s reconnect window)
docker compose up -d --no-deps --force-recreate app
```

**Step 3: Verify chain**

```bash
openssl verify -CAfile secrets/mtls/ca.crt secrets/mtls/app.crt
openssl verify -CAfile secrets/mtls/ca.crt secrets/mtls/postgres.crt
openssl verify -CAfile secrets/mtls/ca.crt secrets/mtls/pgbouncer.crt
openssl verify -CAfile secrets/mtls/ca.crt secrets/mtls/redis.crt
```

**Step 4: Verify Prometheus expiry metrics**

```bash
curl -s http://localhost:9000/metrics | grep conclave_cert_expiry_days
# Expected: conclave_cert_expiry_days{service="app"} 90.0
```

### 13.3 Kubernetes Leaf Certificate Rotation

**Option A: cert-manager (recommended)**

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: conclave-app-cert
  namespace: synth-engine
spec:
  secretName: conclave-mtls
  duration: 2160h      # 90 days
  renewBefore: 720h    # renew 30 days before expiry
  issuerRef:
    name: conclave-internal-ca
    kind: Issuer
  dnsNames:
    - app
    - app.synth-engine.svc.cluster.local
```

cert-manager automatically rotates certs and triggers pod rollouts.

**Option B: Manual rotation**

```bash
./scripts/rotate-mtls-certs.sh --output-dir /tmp/new-certs

kubectl create secret generic conclave-mtls \
    --from-file=ca.crt=/tmp/new-certs/ca.crt \
    --from-file=app.crt=/tmp/new-certs/app.crt \
    --from-file=app.key=/tmp/new-certs/app.key \
    --from-file=postgres.crt=/tmp/new-certs/postgres.crt \
    --from-file=postgres.key=/tmp/new-certs/postgres.key \
    --from-file=pgbouncer.crt=/tmp/new-certs/pgbouncer.crt \
    --from-file=pgbouncer.key=/tmp/new-certs/pgbouncer.key \
    --from-file=redis.crt=/tmp/new-certs/redis.crt \
    --from-file=redis.key=/tmp/new-certs/redis.key \
    --namespace synth-engine \
    --dry-run=client -o yaml | kubectl apply -f -

kubectl rollout restart deployment/conclave-app -n synth-engine
kubectl rollout restart deployment/conclave-pgbouncer -n synth-engine
kubectl rollout restart statefulset/conclave-postgres -n synth-engine
kubectl rollout restart statefulset/conclave-redis -n synth-engine

kubectl rollout status deployment/conclave-app -n synth-engine
```

### 13.4 CA Rotation (Planned Maintenance Window)

CA rotation invalidates ALL existing leaf certificates. No zero-downtime path exists when the trust root changes.

**Trigger conditions:** CA approaching expiry (default 3650 days), CA key compromise suspected, or security policy mandates shorter CA lifetime.

**Dual-CA trust migration (eliminates hard cutover):**

```
Phase 1 — Introduce new CA into trust bundles
  1. ./scripts/generate-mtls-certs.sh --force
  2. Distribute new ca.crt alongside old ca.crt to all containers.
  3. Configure all services to trust BOTH CAs (concat in ca-bundle.crt).
  4. Reload: RELOAD / pg_reload_conf() / CONFIG SET (per Step 2 above).

Phase 2 — Issue new leaf certs
  5. ./scripts/rotate-mtls-certs.sh (uses new CA from step 1).
  6. Rolling restart all containers.

Phase 3 — Remove old CA
  7. Remove old ca.crt from trust bundles.
  8. Reload all services.
  9. Securely delete old CA key: shred -u old-ca.key
```

**WARNING:** Do not run `generate-mtls-certs.sh --force` without completing Phase 1 first. Skipping to step 5 before distributing the new CA trust anchor will break all existing connections.

### 13.5 Prometheus Certificate Expiry Alerts

`conclave_cert_expiry_days` gauge on `/metrics`:

| Label | Value | Meaning |
|-------|-------|---------|
| `service="ca"` | Days remaining | CA cert expiry |
| `service="app"` | Days remaining | App leaf cert expiry |
| Any | `-1` | Cert file unreadable (WARNING logged) |
| Any | `NaN` | mTLS disabled |

**Sample Prometheus alert rules:**

```yaml
groups:
  - name: conclave-mtls
    rules:
      - alert: ConclaveCertExpiryWarning
        expr: conclave_cert_expiry_days < 30 and conclave_cert_expiry_days >= 0
        for: 1h
        labels:
          severity: warning
        annotations:
          summary: "mTLS certificate expiring soon ({{ $labels.service }})"
          description: >
            {{ $labels.service }} expires in {{ $value | humanizeDuration }} days.
            Run ./scripts/rotate-mtls-certs.sh before it reaches zero.

      - alert: ConclaveCertExpired
        expr: conclave_cert_expiry_days < 0
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "mTLS certificate EXPIRED ({{ $labels.service }})"
          description: >
            {{ $labels.service }} certificate has expired. mTLS connections will fail.
            Rotate immediately with ./scripts/rotate-mtls-certs.sh.

      - alert: ConclaveCertUnreadable
        expr: conclave_cert_expiry_days == -1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "mTLS certificate unreadable ({{ $labels.service }})"
          description: >
            Cannot read {{ $labels.service }} certificate. Check app container logs
            for WARNING from synth_engine.shared.cert_metrics.
```

### 13.6 Reconnection Behaviour During Rotation

| Service | Reload mechanism | Connection impact |
|---------|-----------------|-------------------|
| PgBouncer | `RELOAD` command | Zero-drop |
| Redis | `CONFIG SET` | Zero-drop |
| PostgreSQL | `pg_reload_conf()` | Zero-drop (existing sessions continue with old TLS until next connect) |
| App (uvicorn) | `--force-recreate` restart | ~1-5 second gap; clients with retry logic reconnect automatically |
