# Conclave Engine — Operator Manual

## Security Audit Status

The OWASP ZAP baseline scan (CI job `zap-baseline`) runs against the engine on
every CI build. All findings from the most recent scan are informational
artefacts of the CI environment, not real vulnerabilities:

| Rule | Status | Justification |
|------|--------|---------------|
| 10035 — HSTS Header Not Set | IGNORED | Production is served over TLS via a reverse proxy (nginx/Caddy). Raw uvicorn in CI does not terminate TLS; HSTS is set by the proxy. |
| 10038 — CSP Header Not Found | WARN | `CSPMiddleware` is implemented and tested; ZAP may flag non-HTML API responses where CSP is optional. |
| 10096 — Timestamp Disclosure | IGNORED | Timestamps in JSON API responses are intentional (`created_at`, `updated_at` fields). |
| 10054 — Cookie Without SameSite | IGNORED | The engine uses token-based authentication. Cookies are not part of the auth flow. |

No code changes were required as a result of the ZAP scan findings.
All active security controls (`CSPMiddleware`, `SealGateMiddleware`,
`LicenseGateMiddleware`, `RequestBodyLimitMiddleware`) are verified by
unit and integration tests.

---

## 1. System Requirements

### Hardware

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 4 cores | 8+ cores |
| RAM | 8 GB | 32 GB (for GPU-accelerated synthesis) |
| Disk | 50 GB | 200 GB (SSD, for PostgreSQL data and synthesis artifacts) |
| GPU | None (CPU-only mode) | NVIDIA GPU with CUDA support (for DP-SGD training) |

### Software

- **Docker**: version 24.0 or later
- **Docker Compose**: version 2.20 or later (the `docker compose` plugin, not the
  standalone `docker-compose` v1 binary)
- **Operating System**: Linux (recommended) or macOS with Apple Silicon
- **Disk encryption**: LUKS (Linux) or FileVault (macOS) required for production
  deployments — PostgreSQL data volumes must reside on an encrypted volume

### Network

The engine is designed for air-gapped deployments. All inter-service
communication uses an isolated Docker bridge network (`internal`). Only two
ports are exposed to the host:

- `8000` — Conclave Engine API (the `app` service)
- `3000` — Grafana dashboard (the `grafana` service)

---

## 2. Initial Setup

### 2.1 Clone the Repository

On an air-gapped host, transfer the `conclave-bundle-<version>.tar.gz` produced
by `make build-airgap-bundle`. On a connected host:

```bash
git clone <repository-url> conclave
cd conclave
```

### 2.2 Provision Secrets

Secret files are **never** committed to version control. Create them before
starting the platform:

```bash
mkdir -p secrets
chmod 700 secrets

# Application signing key
openssl rand -hex 32 > secrets/app_secret_key.txt

# PostgreSQL password
openssl rand -hex 32 > secrets/postgres_password.txt

# Grafana credentials
openssl rand -hex 32 > secrets/grafana_admin_password.txt
echo "conclave-admin" > secrets/grafana_admin_user.txt

# MinIO ephemeral storage credentials
openssl rand -hex 16 > secrets/minio_ephemeral_access_key.txt
openssl rand -hex 32 > secrets/minio_ephemeral_secret_key.txt

# Lock down permissions
chmod 600 secrets/*.txt
```

### 2.3 Configure the Environment

Copy the template and fill in values:

```bash
cp .env.example .env
```

Required environment variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `ALE_KEY` | Fernet key for Application-Level Encryption | Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DATABASE_URL` | Direct PostgreSQL URL (for migrations) | `postgresql+psycopg2://conclave:<password>@localhost:5432/conclave` |
| `VAULT_SEAL_SALT` | Base64url-encoded 16-byte PBKDF2 salt | Generate with `python3 -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(16)).decode())"` |
| `AUDIT_KEY` | Hex-encoded 32-byte HMAC key for audit log signing | Generate with `python3 -c "import os; print(os.urandom(32).hex())"` |
| `LICENSE_PUBLIC_KEY` | PEM-encoded RSA public key from the licensing server | See [docs/LICENSING.md](LICENSING.md) |

Optional variables (uncomment in `.env` as needed):

| Variable | Description | Default |
|----------|-------------|---------|
| `HUEY_BACKEND` | Task queue backend (`redis` or `memory`) | `redis` |
| `REDIS_URL` | Redis connection URL | `redis://redis:6379/0` |
| `MINIO_ENDPOINT` | MinIO server URL | `http://minio-ephemeral:9000` |

### 2.4 Build the Application Image

```bash
make build
# or: docker compose build app
```

---

## 3. Starting the Platform

### 3.1 Start All Services

```bash
docker compose up -d
```

This starts the following services defined in `docker-compose.yml`:

| Service | Role | Internal Port |
|---------|------|---------------|
| `app` | Conclave Engine API (FastAPI/uvicorn) | 8000 |
| `redis` | Ephemeral Huey task queue backing store | 6379 (internal only) |
| `postgres` | Primary relational store (PostgreSQL 16) | 5432 (internal only) |
| `pgbouncer` | Connection pooler (transaction mode) | 6432 (internal only) |
| `prometheus` | Metrics scraper | 9090 (internal only) |
| `alertmanager` | Alert routing | 9093 (internal only) |
| `grafana` | Dashboard visualisation | 3000 |
| `minio-ephemeral` | Ephemeral synthesis artifact store (tmpfs) | 9000 (internal only) |

Development-only services (Jaeger, hot-reload uvicorn) are defined in
`docker-compose.override.yml` and start automatically when
`docker-compose.override.yml` is present.

### 3.2 Verify Services Are Healthy

```bash
docker compose ps
```

The `app` service health check polls `GET /health`. Wait until its status shows
`healthy` before proceeding:

```bash
docker compose exec app curl -s http://localhost:8000/health
```

Expected response:

```json
{"status": "ok"}
```

The `postgres` service health check runs `pg_isready`. It must be `healthy`
before `app` starts (enforced by `depends_on` in `docker-compose.yml`).

---

## 4. Vault Unseal Procedure

The engine boots into a **SEALED** state. All non-exempt API routes return
`423 Locked` until the vault is unsealed. Unsealing derives the Key Encryption
Key (KEK) from the operator's passphrase using PBKDF2-HMAC-SHA256 (600,000
iterations). The KEK is held **only in process memory** — it is never written
to disk.

### 4.1 Unseal via the React UI

1. Open `http://<host>:8000` in a browser.
2. The RouterGuard detects the sealed state (HTTP 423) and redirects to the
   `/unseal` page.
3. Enter the operator passphrase in the form and click **Unseal**.
4. On success, the UI redirects to the main dashboard.

Error codes returned by the `/unseal` endpoint:

| `error_code` | Cause | Resolution |
|-------------|-------|------------|
| `EMPTY_PASSPHRASE` | Empty string submitted | Enter a non-empty passphrase |
| `ALREADY_UNSEALED` | Vault is already unsealed | No action needed; the UI redirects automatically |
| `CONFIG_ERROR` | `VAULT_SEAL_SALT` is missing or too short | Check the `.env` file; regenerate the salt if missing |

### 4.2 Unseal via the API

```bash
curl -X POST http://<host>:8000/unseal \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "<operator-passphrase>"}'
```

Successful response (HTTP 200):

```json
{"status": "unsealed"}
```

### 4.3 Seal the Vault

To re-seal the vault (zeroizes the in-memory KEK):

```bash
curl -X POST http://<host>:8000/unseal/seal
```

After sealing, all non-exempt routes return `423 Locked` again. The passphrase
is **not** required to re-seal; any authenticated operator can seal the vault.

---

## 5. Creating and Monitoring Synthesis Jobs

### 5.1 License Activation

Before creating jobs, the software must be licensed. See
[docs/LICENSING.md](LICENSING.md) for the full activation protocol.

### 5.2 Create a Job via the API

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

The response includes the job `id` for subsequent operations.

### 5.3 Start a Job

```bash
curl -X POST http://<host>:8000/jobs/<job-id>/start
```

Response: HTTP 202 Accepted. The job is enqueued in the Huey task queue backed
by Redis. A Huey worker process must be running to execute the task.

### 5.4 Monitor Progress via SSE

```bash
curl -N http://<host>:8000/jobs/<job-id>/stream
```

The server sends `text/event-stream` events:

| Event type | Payload | Description |
|------------|---------|-------------|
| `progress` | `{"percent": 42, "epoch": 21, "total_epochs": 50}` | Training progress update |
| `complete` | `{"status": "COMPLETE"}` | Job finished successfully |
| `error` | `{"status": "FAILED", "detail": "..."}` | Job failed |

### 5.5 Monitor via the Dashboard

The React dashboard at `http://<host>:8000` displays active jobs with real-time
progress bars. Jobs are loaded from `GET /jobs` with cursor-based pagination.
Progress updates via EventSource (SSE). The dashboard rehydrates the active job
ID from `localStorage` on page refresh.

---

## 6. Stopping and Restarting the Platform

### 6.1 Graceful Stop

```bash
docker compose down
```

This stops and removes containers. Named volumes (`postgres_data`,
`chroma_data`, `grafana_data`, `prometheus_data`) are **preserved** — data
persists across restarts.

The `minio-ephemeral` service stores synthesis artifacts in `tmpfs`; this data
is **discarded** when the container stops. This is by design — see the privacy
mandate in `docker-compose.yml`.

### 6.2 Restart

```bash
docker compose up -d
```

After restart, the vault is **sealed** and must be unsealed again (see
Section 4). The KEK is never persisted to disk; every restart requires a manual
unseal.

### 6.3 Update Deployment

```bash
# Pull latest images (or rebuild)
docker compose build app
# Rolling restart (app service only)
docker compose up -d --no-deps app
```

---

## 7. Log Locations and Troubleshooting

### 7.1 Application Logs

```bash
# Tail live logs from the app service
docker compose logs -f app

# Tail all services
docker compose logs -f
```

Log rotation is configured for all services. The `app` service rotates at
50 MB, retaining 3 files. Other services rotate at 10–20 MB.

### 7.2 Audit Log

The engine writes WORM (append-only) audit events for security-sensitive
operations (unseal, shred, key rotation, license activation). To inspect:

```bash
docker compose exec app cat /tmp/audit.log
```

Each audit event is HMAC-signed with `AUDIT_KEY`. Tampering with log entries
is detectable.

### 7.3 Common Issues

#### Service fails to start — vault is sealed

All API routes (except `/health`, `/unseal`, `/metrics`, `/docs`,
`/license/challenge`, `/license/activate`) return HTTP 423 until the vault is
unsealed. Unseal before making API requests.

#### Service returns 402 Payment Required

The software is not licensed. Complete the license activation protocol in
[docs/LICENSING.md](LICENSING.md).

#### `app` service exits immediately after start

Check for missing secrets files:

```bash
docker compose logs app | grep -i "error\|secret\|missing"
```

Verify all files in `secrets/` exist and have correct permissions (`600`).

#### PostgreSQL fails health check

```bash
docker compose logs postgres
```

If `postgres_data` volume is corrupt, see
[docs/DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) Section 4 for
backup/restore procedures.

#### OOM during synthesis training

The engine includes an OOM pre-flight guardrail that estimates memory
requirements before training begins. If a job is rejected with:

```text
OOMGuardrailError: Estimated memory requirement exceeds available RAM
```

Reduce `num_rows`, or increase the Docker memory limit for the `app` service
in `docker-compose.override.yml`. See
[docs/DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) Section 2 for OOM recovery
procedures.

#### Request rejected with 413 Request Entity Too Large

The `RequestBodyLimitMiddleware` enforces a 1 MiB request body limit. Reduce
the size of the request payload.

#### Request rejected with 400 Bad Request (JSON nesting)

The middleware also enforces a maximum JSON nesting depth of 100 levels.
Flatten the request payload.

---

## 8. Security Considerations

### 8.1 TLS Termination

The engine's `app` service does **not** terminate TLS directly. In production,
place a reverse proxy (nginx or Caddy) in front of port 8000. Configure TLS at
the proxy level. The proxy must set:

```text
Strict-Transport-Security: max-age=63072000; includeSubDomains
```

### 8.2 Network Isolation

All inter-service traffic is confined to the `internal` Docker bridge network.
No service other than `app` (port 8000) and `grafana` (port 3000) is accessible
from outside the Docker host. Do not bind these ports to public interfaces in
production.

### 8.3 mTLS (Future)

The current architecture uses network-level isolation rather than mutual TLS
between services. If inter-service mTLS is required, configure it at the
Docker network layer or use a service mesh (Envoy, Linkerd). No code changes
are required in the engine itself.

### 8.4 Secret Management

All secrets are injected via Docker secrets (files in `secrets/`). Secret
values are **never** embedded in environment variables in the Compose file, and
**never** committed to version control. Rotate secrets by replacing the file
contents and restarting the affected service.

### 8.5 Capability Model

The `app` container drops all Linux capabilities except `IPC_LOCK`, which is
required so the KEK can be memory-locked (`mlock`) and never swapped to disk.
The container filesystem is immutable (`read_only: true`). Writable scratch
space uses `tmpfs` (in-memory only).

### 8.6 Synthesis Artifact Privacy

Training artifacts (Parquet files, model checkpoints) are stored in the
`minio-ephemeral` service, which uses `tmpfs` for its data directory. All
artifacts are discarded when the container stops. This is the privacy mandate:
no training data survives a container restart.
