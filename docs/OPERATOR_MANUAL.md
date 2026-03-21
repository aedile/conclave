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
| `FORCE_CPU` | Force CPU-only synthesis — set to `true` in environments without a compatible NVIDIA GPU | `false` (auto-detect GPU) |
| `ARTIFACT_SIGNING_KEY` | Hex-encoded 32-byte HMAC-SHA256 key for model artifact signing. **Required in production mode (`ENV=production`); optional in development.** | Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"` |

### 2.4 Build the Application Image

```bash
make build
# or: docker compose build app
```

---

## 3. Starting the Platform

### 3.1 Run Database Migrations

Before starting the platform for the first time, and after every update that
includes new migrations, apply Alembic migrations to bring the database schema
up to date:

```bash
# Provision the required environment variables for Alembic
export DB_USER=conclave
export DB_PASSWORD=$(cat secrets/postgres_password.txt)
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=conclave

# Apply all pending migrations
poetry run alembic upgrade head
```

The `alembic upgrade head` command is idempotent — it is safe to run it on each
deployment. Alembic tracks which migrations have already been applied in the
`alembic_version` table and skips them.

**Update workflow**: After pulling a new release, always run
`alembic upgrade head` before restarting the `app` service. Running the app
against a schema that is behind the current revision will result in startup
errors or undefined behaviour.

### 3.2 Start All Services

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

### 3.3 Verify Services Are Healthy

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

# Apply any pending schema migrations before restarting
export DB_USER=conclave
export DB_PASSWORD=$(cat secrets/postgres_password.txt)
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=conclave
poetry run alembic upgrade head

# Rolling restart (app service only)
docker compose up -d --no-deps app
```

Always run `alembic upgrade head` before `docker compose up` when a release
contains new migrations. The migration step must complete successfully before
the application process starts — the app will fail on startup if the schema is
behind the current revision.

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

#### `app` service exits immediately after start — missing required configuration

The application runs a startup configuration validation check (`validate_config()`)
at boot. If any required environment variable or secret file is absent, the
process exits immediately with a clear error message rather than starting in a
partially configured state. This is intentional — the engine refuses to run
misconfigured.

To diagnose:

```bash
docker compose logs app | grep -i "error\|secret\|missing\|config"
```

Common causes:
- Missing secrets files: verify all files in `secrets/` exist and have correct
  permissions (`600`).
- Missing required environment variables: `validate_config()` checks
  `DATABASE_URL` and `AUDIT_KEY` in all deployment modes. In production mode
  (`ENV=production` or `CONCLAVE_ENV=production`), `ARTIFACT_SIGNING_KEY` is
  also required. Confirm these are set in `.env`.
- Other variables (`ALE_KEY`, `VAULT_SEAL_SALT`, `LICENSE_PUBLIC_KEY`) are not
  startup-validated; they fail at first use, not at boot.

#### `app` service exits immediately after start — schema mismatch

If the application was upgraded but `alembic upgrade head` was not run, the app
may exit with a schema version error. Run the migration step documented in
Section 3.1 and Section 6.3 before restarting.

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

For comprehensive guidance on CORS policy, DDoS mitigation, TLS hardening,
vault passphrase management, and key rotation procedures, see
[docs/SECURITY_HARDENING.md](SECURITY_HARDENING.md).

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

### 8.7 Model Artifact Signing

Conclave signs model artifacts with an HMAC-SHA256 key (`ARTIFACT_SIGNING_KEY`)
to detect tampering between training and inference. When signing is enabled:

- `ModelArtifact.save()` appends an HMAC-SHA256 signature derived from
  `ARTIFACT_SIGNING_KEY` to the artifact file.
- `ModelArtifact.load()` verifies the signature before returning the artifact.
  If the signature does not match, `load()` raises `SecurityError` and the
  artifact is rejected.

**Generating the signing key:**

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Set the result as `ARTIFACT_SIGNING_KEY` in `.env` (local development) or
inject it via Docker secrets or a secrets manager in production.

**Rotation procedure:**

1. Generate a new key with the command above.
2. Update `ARTIFACT_SIGNING_KEY` in your secrets store.
3. Restart the `app` service. New artifacts will be signed with the new key.
4. Existing artifacts signed with the old key will fail signature verification
   after rotation. Re-run any synthesis jobs whose artifacts must remain
   accessible after the rotation.

**Production injection:** Never embed `ARTIFACT_SIGNING_KEY` in plaintext in
`docker-compose.yml` or commit it to version control. Inject it via:
- Docker secrets: `docker secret create artifact_signing_key <(python3 -c "import secrets; print(secrets.token_hex(32))")`
- HashiCorp Vault dynamic secrets
- Kubernetes `Secret` mounted as a file

If `ARTIFACT_SIGNING_KEY` is not set, artifacts are saved without a signature
and loaded without verification. This is acceptable for development but must
not be used in production deployments where artifact integrity is a compliance
requirement.

---

### 8.8 Reverse Proxy and X-Forwarded-For Trust

> **WARNING — Security Requirement**: Conclave's FastAPI application does **not**
> validate that `X-Forwarded-For` headers originate from a trusted reverse proxy.
> An attacker who can reach the application directly can spoof their IP address
> by including an arbitrary `X-Forwarded-For` header in their request.

**Mandatory deployment requirement:** In production, Conclave **MUST** be deployed
behind a trusted reverse proxy (nginx, Caddy, HAProxy, AWS ALB, etc.). The proxy
must:

1. **Strip** any `X-Forwarded-For` header sent by the client.
2. **Re-set** `X-Forwarded-For` to the actual connecting client IP.
3. **Not** be directly reachable from the public internet by untrusted clients.

Never expose the Conclave application port (default: 8000) directly to the internet.
Bind it to a loopback or internal interface and let the reverse proxy handle
public-facing TLS termination and header management.

**Sample nginx configuration:**

```nginx
server {
    listen 443 ssl;
    server_name conclave.example.com;

    # TLS termination (see section 8.1)
    ssl_certificate     /etc/ssl/certs/conclave.crt;
    ssl_certificate_key /etc/ssl/private/conclave.key;

    location / {
        proxy_pass http://127.0.0.1:8000;

        # Strip any X-Forwarded-For the client may have injected,
        # then set it to the real remote address.
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Real-IP       $remote_addr;
        proxy_set_header Host            $host;

        # Do not pass the original Forwarded header.
        proxy_set_header Forwarded "";
    }
}
```

**Risk if omitted:** Without a trusted reverse proxy, any client may set
`X-Forwarded-For: 127.0.0.1` to appear as a loopback address, potentially
bypassing IP-based access controls or audit log accuracy.


## 9. Differential Privacy (DP-SGD) Configuration

Conclave's DP synthesis pipeline uses Opacus DP-SGD to inject calibrated
Gaussian noise into the CTGAN training process, providing formal
(epsilon, delta)-differential privacy guarantees on the synthetic output.

### 9.1 DP Parameters

Four parameters govern the privacy-utility tradeoff:

| Parameter | Description | Typical Range |
|-----------|-------------|---------------|
| `epsilon` | Privacy budget (lower = stronger privacy, more noise, less utility) | 1–20 |
| `delta` | Failure probability — the probability that the (epsilon, delta)-DP guarantee fails | 1e-5 (fixed) |
| `noise_multiplier` | Ratio of Gaussian noise std to `max_grad_norm`. Higher = more noise = stronger privacy = lower epsilon. | 0.5–2.0 |
| `max_grad_norm` | Maximum L2 norm for per-sample gradient clipping. Controls sensitivity of gradient updates. | 1.0 (canonical) |

**Relationship**: epsilon is not directly set. Instead, `noise_multiplier` and
`max_grad_norm` are configured; the resulting epsilon is computed by Opacus's
RDP accountant after training and can be queried via `epsilon_spent(delta=1e-5)`.

**Important**: epsilon depends on dataset size, batch size, and number of
training epochs. A given `noise_multiplier` will produce different epsilon
values on datasets of different sizes. Always verify the actual epsilon reported
after training.

### 9.2 Recommended Epsilon Ranges by Use Case

The following ranges are based on the empirical benchmark in
`docs/DP_QUALITY_REPORT.md` (500-row dataset, 10 epochs). Recalibrate for
production datasets by running the benchmark script.

| Use Case | Recommended Epsilon | Rationale |
|----------|--------------------|-----------|
| External publication / regulatory compliance | 1–2 | Strong privacy guarantee required; quality loss is acceptable. |
| Internal analytics on sensitive PII | 5–8 | Balanced tradeoff; distributions are broadly preserved. |
| Non-sensitive internal testing / ML training | 10–20 | Utility is primary concern; privacy is best-effort. |
| Production ML training (quality-first) | No DP (vanilla) | Use only when data sensitivity and regulatory requirements allow. |

### 9.3 Calibrated Noise Multiplier Values

The following values were empirically calibrated on a 500-row dataset with
10 training epochs. They are a starting point — recalibrate for your dataset.

| Target Epsilon | noise_multiplier |
|---------------|-----------------|
| ~1 | 2.00 |
| ~5 | 0.75 |
| ~10 | 0.55 |

### 9.4 Creating a DP-Enabled Synthesis Job

DP is enabled by passing `dp_wrapper` to `SynthesisEngine.train()`. The
`build_dp_wrapper()` bootstrapper factory is the canonical construction point.

**Programmatic usage (Python):**

```python
from synth_engine.bootstrapper.main import build_dp_wrapper, build_synthesis_engine

# Configure DP parameters
wrapper = build_dp_wrapper(
    max_grad_norm=1.0,      # canonical gradient clipping bound
    noise_multiplier=1.1,   # moderate noise — epsilon ~3-8 on typical datasets
)

engine = build_synthesis_engine(epochs=300)  # 300 = production quality

artifact = engine.train(
    table_name="customers",
    parquet_path="/data/customers.parquet",
    dp_wrapper=wrapper,
)

# Query the actual epsilon after training
epsilon = wrapper.epsilon_spent(delta=1e-5)
print(f"Actual epsilon: {epsilon:.4f}")  # Log this for compliance records

# Check budget before proceeding
wrapper.check_budget(allocated_epsilon=8.0, delta=1e-5)  # raises BudgetExhaustionError if exceeded

# Generate synthetic data
synthetic_df = artifact.model.sample(n_rows=1000)
```

### 9.5 Privacy Budget Accounting

The `PrivacyLedger` table tracks cumulative epsilon spend per synthesis job.
Use `spend_budget()` from `modules/privacy/accountant.py` to deduct epsilon
atomically after each training run:

```python
from synth_engine.modules.privacy.accountant import spend_budget

async with get_async_session(engine) as session:
    await spend_budget(
        amount=epsilon,          # from wrapper.epsilon_spent(delta=1e-5)
        job_id=job.id,
        ledger_id=1,             # the global privacy ledger row id
        session=session,
        note="customer-data-synthetic run 2026-03-15",
    )
```

`spend_budget()` uses `SELECT ... FOR UPDATE` pessimistic locking to prevent
concurrent synthesis jobs from overrunning the global privacy budget. If the
requested amount would exhaust the budget, `BudgetExhaustionError` is raised
and the ledger row is left unchanged.

### 9.6 DP Mode Limitations and Operational Notes

- **Proxy model architecture**: Opacus is activated on a lightweight proxy
  linear model (not the CTGAN Discriminator directly) to work around CTGAN's
  internal optimizer lifecycle. The epsilon accounting reflects real gradient
  steps on this proxy model. See ADR-0025 for the full rationale.
- **Epoch count**: DP epsilon accumulates with each training epoch. More epochs
  = more epsilon spent. At 300 epochs, epsilon is significantly higher than at
  10 epochs for the same `noise_multiplier`.
- **Dataset size**: Larger datasets give stronger DP guarantees (lower epsilon)
  for the same noise configuration, because each sample contributes less to
  the overall gradient signal.
- **CPU vs. GPU**: Opacus DP-SGD works on both CPU and GPU. Training is
  substantially faster on GPU for production epoch counts (300+). Set
  `FORCE_CPU=true` in `.env` to force CPU-only mode (see Section 2.3).
- **Single-use wrapper**: Each `DPTrainingWrapper` instance is single-use —
  calling `wrap()` twice raises `RuntimeError`. Create a new wrapper per
  training run.
- **Opacus `secure_mode` deferred**: Opacus 1.5.x emits
  `UserWarning: Secure RNG turned off` at `PrivacyEngine()` instantiation
  because the default engine uses PyTorch's standard PRNG rather than a
  CSPRNG-backed one. `secure_mode=True` (which uses `torchcsprng` for a
  cryptographically-secure RNG) is not enabled because `torchcsprng` has no
  published wheels for Python 3.14 and is unmaintained upstream. The practical
  attack path `secure_mode` mitigates — PRNG state reconstruction from
  observable gradient noise — is not present in Conclave's air-gapped threat
  model. The warning is suppressed in `pyproject.toml`'s `filterwarnings`
  configuration; this suppression is explicitly justified by ADR-0017a.

---

## 10. Development and CI Reference

### 10.1 Pytest Marker Routing

The test suite uses pytest markers to route tests to the correct CI gate.
Use markers when running subsets of the test suite locally:

| Marker | Description | Typical usage |
|--------|-------------|---------------|
| `unit` | Fast, isolated unit tests with no external dependencies | `pytest -m unit` |
| `integration` | Tests requiring live databases or external services | `pytest -m integration` |
| `synthesizer` | Synthesizer integration tests requiring SDV, PyTorch, and Opacus | `pytest -m synthesizer` |

**CI gate mapping:**
- `pytest tests/unit/ -W error` — runs all unit tests; zero warnings tolerated
- `pytest tests/integration/ -v --no-cov` — integration gate (separate from unit coverage)
- `pytest -m synthesizer` — synthesizer gate; requires the `synthesizer` Poetry
  dependency group (`poetry install --with synthesizer`)

### 10.2 Zero-Warning Policy

`pyproject.toml` configures `filterwarnings = ["error", ...]`. The baseline
`"error"` entry promotes all Python warnings to test failures. Specific
suppressions for third-party packages (Opacus, SDV, pytest-asyncio) are listed
with inline justifications in `pyproject.toml`.

This policy means: **any new unhandled `DeprecationWarning` or `UserWarning`
from the application code will fail the unit test suite**. If you add a new
dependency that emits warnings, add a scoped suppression entry to
`pyproject.toml` with a written justification comment.

---

## 11. Data Retention and Compliance Configuration

Conclave enforces configurable data retention TTLs across three data categories.
Configure retention periods before going to production to satisfy your
regulatory obligations. The full compliance policy — including GDPR/CCPA/HIPAA
guidance, erasure procedures, and audit trail guarantees — is in
[docs/DATA_COMPLIANCE.md](DATA_COMPLIANCE.md).

### 11.1 Retention Environment Variables

> **Planned — T41.1**: The retention environment variables in this section
> (`JOB_RETENTION_DAYS`, `AUDIT_RETENTION_DAYS`, `ARTIFACT_RETENTION_DAYS`)
> and the corresponding `ConclaveSettings` fields are not yet implemented.
> They are scheduled for delivery in task T41.1. Do not set these variables
> in `.env` on the current release — they will have no effect. The table
> below documents the intended interface once T41.1 is merged.

Add these to `.env` to override the defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `JOB_RETENTION_DAYS` | `90` | Days before completed/failed synthesis jobs are eligible for purge. Jobs on legal hold are exempt. |
| `AUDIT_RETENTION_DAYS` | `1095` | Days audit events are retained. Audit events are never deleted within this period. |
| `ARTIFACT_RETENTION_DAYS` | `30` | Days Parquet output files and model checkpoints are retained on MinIO before purge. |

**Example: financial-sector configuration (7-year audit retention):**

```bash
# .env
JOB_RETENTION_DAYS=180
AUDIT_RETENTION_DAYS=2555
ARTIFACT_RETENTION_DAYS=14
```

**Example: GDPR minimum configuration (3-year audit retention):**

```bash
# .env
JOB_RETENTION_DAYS=90
AUDIT_RETENTION_DAYS=1095
ARTIFACT_RETENTION_DAYS=30
```

### 11.2 Legal Hold

> **Planned — T41.1**: The `legal_hold` field on `SynthesisJob` records and
> the admin API endpoints below are not yet implemented. They are scheduled
> for delivery in task T41.1.

Place a synthesis job on legal hold to prevent it from being deleted regardless
of TTL. This satisfies e-discovery obligations and regulatory hold requirements.

```bash
# Authenticate (required)
TOKEN=$(curl -s -X POST http://<host>:8000/auth/token \
  -H "Content-Type: application/json" \
  -d '{"password": "<operator-passphrase>"}' | jq -r .access_token)

# Place a job on legal hold
curl -X POST http://<host>:8000/admin/jobs/<job-id>/legal-hold \
  -H "Authorization: Bearer ${TOKEN}"

# Release a legal hold
curl -X DELETE http://<host>:8000/admin/jobs/<job-id>/legal-hold \
  -H "Authorization: Bearer ${TOKEN}"
```

Every hold and release is logged to the WORM audit trail.

### 11.3 Manual Purge

> **Planned — T41.1**: The manual purge endpoint (`POST /admin/retention/purge`)
> and the automated purge task are not yet implemented. They are scheduled for
> delivery in task T41.1.

The purge task runs automatically on its configured schedule. To trigger a
manual purge immediately:

```bash
curl -X POST http://<host>:8000/admin/retention/purge \
  -H "Authorization: Bearer ${TOKEN}"
```

The response includes counts of jobs deleted and artifacts shredded. All
deletions are logged to the audit trail.

### 11.4 GDPR / CCPA Erasure Requests

> **Planned — T41.2**: The `DELETE /compliance/erasure` endpoint is not yet
> implemented. It is scheduled for delivery in task T41.2.

To process a right-to-erasure request for a specific data subject:

```bash
curl -X DELETE http://<host>:8000/compliance/erasure \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"subject_id": "<source-record-id-or-email-hash>"}'
```

The response is a compliance receipt documenting every record deleted and every
record preserved (with written justification). Preserve this receipt as
compliance evidence.

**Notes:**

- The vault must be unsealed. Erasure returns `423 Locked` if sealed.
- Erasure requests are rate-limited to 1 per minute per operator.
- The erasure request itself is logged to the WORM audit trail and cannot
  be deleted.
- Synthesized output and audit trail entries are preserved — see
  [docs/DATA_COMPLIANCE.md Section 3](DATA_COMPLIANCE.md) for the
  preservation justifications.

### 11.5 Regulatory Retention Guidance

| Regulatory context | Recommended `AUDIT_RETENTION_DAYS` | Basis |
|--------------------|------------------------------------|-------|
| GDPR (EU) | 1,095 (3 years) | GDPR Article 5(1)(e); legal claims limitation period |
| CCPA (California) | 1,095 (3 years) | CCPA § 1798.100(e) |
| HIPAA (US healthcare) | 2,190 (6 years) | 45 CFR § 164.530(j) |
| Financial services (SEC Rule 17a-4) | 2,555 (7 years) | SEC Rule 17a-4(b)(1) |

Consult your legal counsel for your specific jurisdiction and data category.
