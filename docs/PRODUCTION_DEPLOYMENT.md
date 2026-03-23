# Conclave Engine — Production Deployment Playbook

Full deployment procedure for production air-gapped environments. Supplements
`docs/OPERATOR_MANUAL.md`; this playbook takes precedence for initial deployment.

**Audience:** System administrators performing first-time installation or major upgrades.

---

## Prerequisites

- [ ] Target host meets Tier 2 minimums (`docs/SCALABILITY.md`): 8 cores, 16–32 GB RAM, 100 GB SSD
- [ ] Docker 24.0+ and Docker Compose 2.20+ installed
- [ ] Disk encryption active (LUKS on Linux, FileVault on macOS)
- [ ] Release bundle (`conclave-bundle-<version>.tar.gz`) transferred via physical media
- [ ] Operator passphrase stored in a password manager or physical safe

---

## Step 1 — Transfer the Release Bundle

On a connected host:

```bash
make build-airgap-bundle
```

Produces `conclave-bundle-<version>.tar.gz` (Docker images + source artefacts). Transfer
via USB or optical media to the air-gapped host.

On the air-gapped host:

```bash
tar -xzf conclave-bundle-<version>.tar.gz
cd conclave-bundle-<version>
make load-images   # loads Docker images into the local daemon
```

---

## Step 2 — Configure TLS

### 2.1 TLS for the Public Endpoint (Nginx)

The `app` service does not terminate TLS. All production deployments **must** use a
reverse proxy in front of port 8000.

Generate certificates using an internal CA:

```bash
# CA (if none exists)
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt \
  -subj "/CN=Conclave Internal CA"

# Server certificate
openssl genrsa -out conclave.key 4096
openssl req -new -key conclave.key -out conclave.csr \
  -subj "/CN=conclave.internal"
openssl x509 -req -days 825 -in conclave.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out conclave.crt
```

**Nginx config** (`/etc/nginx/sites-available/conclave`):

```nginx
server {
    listen 443 ssl http2;
    server_name conclave.internal;

    ssl_certificate     /etc/ssl/conclave/conclave.crt;
    ssl_certificate_key /etc/ssl/conclave/conclave.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # HSTS — required for production
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

    location / {
        proxy_pass http://127.0.0.1:8000;

        # Overwrite client-supplied X-Forwarded-For with the real TCP IP.
        # Required for rate limiting integrity — see Appendix B and §8.8.
        proxy_set_header X-Forwarded-For   $remote_addr;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header Host              $host;
        proxy_set_header Forwarded         "";
        # Tells HTTPSEnforcementMiddleware the real client scheme (T42.2).
        # Proxy strips any client-supplied X-Forwarded-Proto before setting this.
        proxy_set_header X-Forwarded-Proto https;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name conclave.internal;
    return 301 https://$host$request_uri;
}
```

### 2.2 TLS for PostgreSQL

PostgreSQL traffic is confined to the Docker `internal` bridge. TLS is optional for
single-host deployments; required for multi-host.

To enable:

1. Generate a certificate:

   ```bash
   openssl genrsa -out postgres.key 4096
   openssl req -new -x509 -days 3650 -key postgres.key -out postgres.crt \
     -subj "/CN=postgres.internal"
   chmod 600 postgres.key
   ```

2. Mount via `docker-compose.override.yml`:

   ```yaml
   services:
     postgres:
       volumes:
         - ./certs/postgres.key:/var/lib/postgresql/server.key:ro
         - ./certs/postgres.crt:/var/lib/postgresql/server.crt:ro
       command: >
         postgres
         -c ssl=on
         -c ssl_cert_file=/var/lib/postgresql/server.crt
         -c ssl_key_file=/var/lib/postgresql/server.key
   ```

3. Add `?sslmode=require` to `DATABASE_URL` in `.env`.

### 2.3 TLS for Redis

Redis holds only Huey task queue messages (no PII, no keys). TLS is optional for
single-host; for multi-host configure `stunnel` in front of the Redis container.

**Redis authentication:** Enable for any deployment where Redis is reachable by more
than one host:

```bash
requirepass <strong-random-password>
```

Set the matching password via `REDIS_URL`:

```
REDIS_URL=redis://:strongpassword@redis:6379/0
```

Single-host deployments on an isolated bridge may omit `requirepass`, but it is
recommended as defence-in-depth.

### 2.4 TLS for MinIO

MinIO (`minio-ephemeral`) uses `tmpfs` (discarded on container stop). TLS within the
`internal` bridge is optional. If MinIO is ever exposed outside the Docker host
(non-default, not recommended), enable TLS:

```bash
mkdir -p certs/CAs
cp conclave.crt certs/public.crt
cp conclave.key certs/private.key
```

Mount into the container and set `MINIO_VOLUMES` appropriately.

### 2.5 mTLS Inter-Container Certificates (Multi-Host Only)

> **Single-host Docker Compose:** mTLS is **optional** when all services share an
> isolated `internal` bridge. Skip this section for single-host deployments.
>
> **Multi-host (Kubernetes, Docker Swarm):** mTLS is **required**. See ADR-0029 Gap 7.

**Services covered:**

| Service | Container Hostname | Purpose |
|---------|-------------------|---------|
| `app` | `app` | API server + Huey workers |
| `postgres` | `postgres` | PostgreSQL |
| `pgbouncer` | `pgbouncer` | Connection pooler |
| `redis` | `redis` | Task queue |

Prometheus, Alertmanager, Grafana, and MinIO are exempt (ADR-0029 Gap 7).

**Certificate generation** — run on the operator host only (`openssl` 1.1.1+ required):

```bash
# Defaults: CA 3650 days, leaf 90 days
./scripts/generate-mtls-certs.sh

# Custom validity
./scripts/generate-mtls-certs.sh --ca-days 1825 --leaf-days 30

# Force CA regeneration (WARNING: invalidates all existing leaf certs)
./scripts/generate-mtls-certs.sh --force
```

Output:

```
secrets/mtls/
├── ca.crt          — CA root (trust anchor)
├── ca.key          — CA private key (0400 — NEVER mount into containers)
├── app.crt / app.key
├── postgres.crt / postgres.key
├── pgbouncer.crt / pgbouncer.key
└── redis.crt / redis.key
```

**Security constraints:**

- `ca.key` stays on the operator host (`0400`). If compromised, regenerate with
  `--force` and redeploy all leaf certs.
- Leaf keys are `0600`. Mount only the service-specific `.crt`, `.key`, and `ca.crt`.
- Re-running without `--force` is idempotent — skips CA generation if `ca.key` exists.
- SANs include Docker Compose short hostnames and Kubernetes FQDNs
  (`<service>.synth-engine.svc.cluster.local`).

**Certificate rotation** — leaf certs default to 90-day validity:

```bash
openssl x509 -noout -enddate -in secrets/mtls/app.crt  # check expiry
./scripts/generate-mtls-certs.sh                        # rotate leaves (CA preserved)
docker compose restart app postgres pgbouncer redis     # pick up new certs
```

Python helper:

```python
from pathlib import Path
from synth_engine.shared.tls import TLSConfig

days = TLSConfig.days_until_expiry(Path("secrets/mtls/app.crt"))
print(f"app certificate expires in {days} days")
```

---

## Step 3 — Firewall Rules

### 3.1 Inbound Rules

| Port | Protocol | Purpose | Source |
|------|----------|---------|--------|
| 443 | TCP | Nginx HTTPS — UI and API | LAN / operator workstations |
| 80 | TCP | HTTP → HTTPS redirect | LAN (optional) |
| 22 | TCP | SSH admin | Admin network only |

All other inbound ports: blocked.

### 3.2 Ports That Must NOT Be Exposed

| Port | Service | Reason |
|------|---------|--------|
| 8000 | Conclave app | Direct access bypasses nginx TLS |
| 3000 | Grafana | Operator-only; use VPN or SSH tunnel |
| 5432 | PostgreSQL | Internal Docker bridge only |
| 6432 | PgBouncer | Internal only |
| 6379 | Redis | Internal only |
| 9000 | MinIO | tmpfs data must not be externally accessible |

### 3.3 Outbound Rules

Block all outbound except DNS queries to your internal resolver (if needed for Docker
hostname resolution). Conclave makes no outbound HTTP or API calls at runtime.

---

## Step 4 — Vault Initialization and Operator Passphrase Ceremony

### 4.1 Before First Startup

The vault starts **SEALED**. The first unseal derives the KEK from the operator
passphrase and `VAULT_SEAL_SALT`.

1. Generate `VAULT_SEAL_SALT` and add to `.env`:

   ```bash
   python3 -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(16)).decode())"
   ```

   `VAULT_SEAL_SALT` is not secret (PBKDF2 salt, not a key), but **must remain stable
   for the lifetime of the deployment** — changing it makes all ALE-encrypted data
   unreadable.

2. Choose the operator passphrase:
   - Minimum 20 characters; mixed case, numbers, symbols
   - Store in a hardware password manager or physical safe
   - Known by at least two operators (key escrow)

### 4.2 Passphrase Ceremony

Perform with at least two operators present:

1. Operator A generates `VAULT_SEAL_SALT` and writes it to `.env`.
2. Operator A sets the passphrase (writes to password manager or paper — not verbally).
3. Operator B confirms the sealed copy is in escrow.
4. Both operators sign a compliance log entry: date, ceremony performed, passphrase in escrow.
5. Operator A performs the first unseal (Step 8). Success confirms correct vault config.

**Recovery:** Lost passphrase = all ALE-encrypted data permanently unrecoverable.
`VAULT_SEAL_SALT` alone cannot unseal. See `docs/DISASTER_RECOVERY.md` §3.2.

---

## Step 5 — Secret Provisioning

```bash
mkdir -p secrets && chmod 700 secrets
```

Create each file, then set the corresponding `.env` variable. All files must be `chmod 600`.

| Secret file | Generate with | `.env` variable |
|-------------|--------------|-----------------|
| `app_secret_key.txt` | `python3 -c "import secrets; print(secrets.token_hex(32))"` | `APP_SECRET_KEY` |
| `artifact_signing_key.txt` | same as above | `ARTIFACT_SIGNING_KEY` |
| `postgres_password.txt` | `openssl rand -hex 32` | embed in `DATABASE_URL` |
| `ale_key.txt` | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | `ALE_KEY` |
| `audit_key.txt` | `python3 -c "import os; print(os.urandom(32).hex())"` | `AUDIT_KEY` |
| `masking_salt.txt` | same as above | `MASKING_SALT` |
| `minio_ephemeral_access_key.txt` | `openssl rand -hex 16` | MinIO access key |
| `minio_ephemeral_secret_key.txt` | `openssl rand -hex 32` | MinIO secret key |
| `grafana_admin_password.txt` | `openssl rand -hex 32` | Grafana admin password |
| `grafana_admin_user.txt` | `echo "conclave-admin"` | Grafana admin user |
| `license_public_key.pem` | Obtain from licensing server (`docs/LICENSING.md`) | `LICENSE_PUBLIC_KEY` |

After creating all files:

```bash
chmod 600 secrets/*
ls -la secrets/
# All files should show -rw------- (600), owned by the deploy user
```

---

## Step 6 — Database Migration

```bash
docker compose up -d postgres
docker compose ps postgres   # wait for 'healthy'

export DB_USER=conclave
export DB_PASSWORD=$(cat secrets/postgres_password.txt)
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=conclave

poetry run alembic upgrade head
```

Idempotent — safe to run on each deployment; already-applied migrations are skipped.

---

## Step 7 — Start the Full Stack

```bash
docker compose up -d
docker compose ps
docker compose exec app curl -s http://localhost:8000/health
# Expected: {"status": "ok"}
```

---

## Step 8 — Vault Unseal

All non-exempt API routes return `423 Locked` until unsealed:

```bash
curl -X POST http://localhost:8000/unseal \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "<operator-passphrase>"}'
# Expected: {"status": "unsealed"}
```

Browser: navigate to `https://conclave.internal` — the UI redirects to `/unseal`.

---

## Step 9 — License Activation

Full protocol: `docs/LICENSING.md`. Short version:

```bash
curl http://localhost:8000/license/challenge   # get challenge

# Generate license JWT on licensing server (or use pre-issued JWT)

curl -X POST http://localhost:8000/license/activate \
  -H "Content-Type: application/json" \
  -d '{"token": "<license-jwt>"}'
```

---

## Step 10 — First Synthesis Job Walkthrough

### 10.1 — Connect Source Data

The `data/` host directory mounts at `/data/` inside the container (verify in
`docker-compose.yml`):

```bash
cp /path/to/source_customers.parquet data/customers.parquet
```

### 10.2 — Configure Masking (Optional)

Apply deterministic masking to specific columns before synthesis via the masking API.
See `docs/OPERATOR_MANUAL.md` for the API reference.

### 10.3 — Create the Synthesis Job

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "customers-synthetic-v1",
    "parquet_path": "/data/customers.parquet",
    "num_rows": 1000,
    "num_epochs": 50,
    "checkpoint_every_n": 10
  }'
```

Note the `id` (UUID) in the response.

### 10.4 — Start the Job

```bash
curl -X POST http://localhost:8000/jobs/<job-id>/start
# Response: HTTP 202 Accepted
```

The Huey worker picks it up within seconds.

### 10.5 — Monitor Progress

```bash
curl -N http://localhost:8000/jobs/<job-id>/stream  # live SSE
curl http://localhost:8000/jobs/<job-id>            # or poll
```

States: `QUEUED` → `TRAINING` → `COMPLETE`.

### 10.6 — Download Output

```bash
curl -o synthetic_customers.parquet http://localhost:8000/jobs/<job-id>/download
```

**Important:** MinIO uses `tmpfs`. Output is discarded when `minio-ephemeral` stops.
Download before stopping or restarting the stack.

### 10.7 — Verify DP Budget (DP-enabled jobs only)

```bash
curl http://localhost:8000/jobs/<job-id>       # check "epsilon_spent"
curl http://localhost:8000/privacy/budget      # check global budget remaining
```

---

## Step 11 — Post-Deployment Verification Checklist

- [ ] Vault unseals with the operator passphrase
- [ ] `GET /health` → `{"status": "ok"}`
- [ ] `GET /ready` → `{"status": "ok"}` with all dependency checks passing
- [ ] Test synthesis job completes without errors
- [ ] Grafana (`https://conclave.internal:3000`) shows metrics
- [ ] Application logs show no ERROR-level entries
- [ ] Audit log (`docker compose exec app cat /tmp/audit.log`) contains the unseal event
- [ ] All `secrets/` files have `600` permissions

---

## Appendix A — HTTPS Enforcement (T42.2)

### A.1 How It Works

`HTTPSEnforcementMiddleware` inspects `X-Forwarded-Proto` on every inbound request.
Any request arriving over plain `http` in production mode is rejected with **HTTP 421
Misdirected Request** (RFC 7231 §6.5.11) and an RFC 7807 Problem Details body.

### A.2 Why 421 Instead of 301

A 301/302 redirect transmits the request line, headers, and body in cleartext before
redirecting — a classic SSL-stripping surface. 421 forces operators to fix the
deployment rather than silently degrading to plain HTTP.

### A.3 Reverse Proxy Requirements

All production deployments must front `app` with a TLS-terminating proxy (nginx, Caddy,
or HAProxy) that:

1. Terminates TLS on port 443.
2. Sets `X-Forwarded-Proto: https` on every forwarded request.
3. Strips any client-supplied `X-Forwarded-Proto` before setting its own.

The nginx config in §2.1 satisfies all three requirements.

### A.4 Development Mode

When `CONCLAVE_ENV` is anything other than `"production"`, the middleware passes all
requests through unchanged. Plain HTTP works during local development.

### A.5 Startup Misconfiguration Warning

When `CONCLAVE_SSL_REQUIRED=true` (default) but `CONCLAVE_TLS_CERT_PATH` is unset:

```
WARNING  synth_engine.bootstrapper.dependencies.https_enforcement:
CONCLAVE_SSL_REQUIRED=true but no TLS certificate is configured. Ensure a
TLS-terminating reverse proxy is in place and sets X-Forwarded-Proto: https.
```

Advisory only — the application starts. Review this warning in production logs.

---

## Appendix B — X-Forwarded-For Trust Model and Rate Limiting (ADV-P48-01)

### B.1 Security Requirement

The rate limiter trusts the **first (leftmost)** `X-Forwarded-For` entry as the real
client IP for pre-authentication endpoints (`/unseal`, `/auth/token`).

**Without a correctly configured reverse proxy, rate limiting is bypassable.** A client
sending `X-Forwarded-For: 1.2.3.4` would consume the rate limit quota of `1.2.3.4`,
allowing unlimited brute-force attempts from a single host.

### B.2 Required Reverse Proxy Configuration

The proxy MUST overwrite any client-supplied `X-Forwarded-For` with the real TCP
connection IP. The nginx template in §2.1 does this:

```nginx
proxy_set_header X-Forwarded-For $remote_addr;
```

`$remote_addr` is set from the TCP connection — it cannot be spoofed.

**Equivalent config for other proxies:**

| Proxy | Directive |
|-------|-----------|
| nginx | `proxy_set_header X-Forwarded-For $remote_addr;` |
| HAProxy | `http-request set-header X-Forwarded-For %[src]` |
| Traefik | Strip `X-Forwarded-For` in middleware; Traefik sets from real connection |
| Caddy | `header_up X-Forwarded-For {remote_host}` |

The proxy must **replace**, not append to, any existing `X-Forwarded-For`.

### B.3 Affected Rate Limit Tiers

| Endpoint | Limit | Key source |
|----------|-------|------------|
| `/unseal` | 5 req/min | Client IP from `X-Forwarded-For` (first entry) |
| `/auth/token` | 10 req/min | Client IP from `X-Forwarded-For` (first entry) |

All other endpoints key on the JWT `sub` claim — `X-Forwarded-For` does not affect them.

### B.4 Fallback Behaviour

Without a reverse proxy, the rate limiter falls back to `request.client.host`. This is
accurate for direct connections, but direct exposure of port 8000 is prohibited by §3.2
(no TLS). Do not rely on this fallback.

### B.5 Deployment Verification

```bash
# Spoof test — proxy must overwrite this before it reaches the app
curl -H "X-Forwarded-For: 10.0.0.1" http://localhost:8000/health

# Confirm nginx config
nginx -T | grep "X-Forwarded-For"
# Expected: proxy_set_header X-Forwarded-For $remote_addr;
```

If `X-Forwarded-For: 10.0.0.1` appears unchanged in application access logs, the proxy
is not stripping client headers and rate limiting is bypassable.

---

## Appendix C — Kubernetes Readiness & Liveness Probes (T48.3)

### C.1 Probe Endpoints

| Endpoint | Probe type | What it checks |
|----------|-----------|----------------|
| `GET /health` | Liveness | Process alive — always returns `{"status": "ok"}` |
| `GET /ready` | Readiness | Live checks — PostgreSQL, Redis, MinIO (optional) |

Use `/ready` as the readiness probe (gates traffic) and `/health` as the liveness probe
(triggers container restart on hang).

### C.2 Security Properties

- Both endpoints exempt from `SealGateMiddleware` and `AuthenticationGateMiddleware`.
- Error responses use generic names only (`database`, `cache`, `object_store`).
  Internal hostnames, ports, and connection strings are never exposed.

### C.3 Kubernetes Deployment Manifest

```yaml
spec:
  containers:
    - name: conclave-engine
      image: conclave-engine:latest
      ports:
        - containerPort: 8000

      livenessProbe:
        httpGet:
          path: /health
          port: 8000
        initialDelaySeconds: 15
        periodSeconds: 20
        timeoutSeconds: 5
        failureThreshold: 3

      readinessProbe:
        httpGet:
          path: /ready
          port: 8000
        initialDelaySeconds: 10
        periodSeconds: 10
        timeoutSeconds: 5
        failureThreshold: 3
        successThreshold: 1
```

### C.4 Response Schema

**200 OK:**
```json
{
  "status": "ok",
  "checks": { "database": "ok", "cache": "ok", "object_store": "skipped" }
}
```

**503 Service Unavailable:**
```json
{
  "status": "degraded",
  "checks": { "database": "error", "cache": "ok", "object_store": "skipped" }
}
```

`object_store` is `"skipped"` when MinIO is not configured. MinIO unavailability does
not cause a 503 — it is treated as optional storage.

### C.5 Per-Check Timeout

Each dependency check is bounded to **3 seconds** via `asyncio.wait_for`. A timed-out
check returns `"error"` and the probe returns 503.

---

## References

- `docs/OPERATOR_MANUAL.md` — Day-to-day operational procedures
- `docs/SCALABILITY.md` — Hardware sizing and capacity limits
- `docs/DISASTER_RECOVERY.md` — Recovery procedures
- `docs/LICENSING.md` — License activation protocol
- `.env.example` — All supported environment variables
