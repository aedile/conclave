# Conclave Engine — Production Deployment Playbook

This playbook covers the full procedure for deploying the Conclave Engine in a
production air-gapped environment. It supplements the day-to-day operational
guidance in `docs/OPERATOR_MANUAL.md`. If a section here conflicts with the
Operator Manual, this playbook takes precedence for initial deployment.

**Audience:** System administrators and security operators performing first-time
installation or major upgrades.

---

## Prerequisites

Before starting, verify you have:

- [ ] A target host meeting at minimum the Tier 2 hardware requirements in
  `docs/SCALABILITY.md` (8 cores, 16–32 GB RAM, 100 GB SSD)
- [ ] Docker 24.0+ and Docker Compose 2.20+ installed on the target host
- [ ] Disk encryption active on the target host (LUKS on Linux, FileVault on macOS)
- [ ] The Conclave release bundle (`conclave-bundle-<version>.tar.gz`) transferred
  to the air-gapped host via physical media if the network is isolated
- [ ] The operator passphrase chosen and stored in a password manager or printed
  and secured in a physical safe

---

## Step 1 — Transfer the Release Bundle

On a connected host, build the air-gap bundle:

```bash
make build-airgap-bundle
```

This produces `conclave-bundle-<version>.tar.gz` containing all Docker images
and source artefacts. Transfer via USB or optical media to the air-gapped host.

On the air-gapped host:

```bash
tar -xzf conclave-bundle-<version>.tar.gz
cd conclave-bundle-<version>
make load-images   # loads Docker images into the local daemon
```

---

## Step 2 — Configure TLS

### 2.1 TLS for the Public Endpoint (Nginx Reverse Proxy)

The Conclave Engine's `app` service does not terminate TLS directly. All
production deployments **must** place a reverse proxy in front of port 8000.

**Obtain a TLS certificate.** In an air-gapped environment, use an internal
Certificate Authority (CA):

```bash
# Generate a self-signed CA (if no internal CA exists)
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt \
  -subj "/CN=Conclave Internal CA"

# Generate the server certificate for the Conclave host
openssl genrsa -out conclave.key 4096
openssl req -new -key conclave.key -out conclave.csr \
  -subj "/CN=conclave.internal"
openssl x509 -req -days 825 -in conclave.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out conclave.crt
```

**Configure Nginx** (`/etc/nginx/sites-available/conclave`):

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

        # Strip any X-Forwarded-For the client may inject, then set to real IP.
        # See OPERATOR_MANUAL.md Section 8.8 — this is a security requirement.
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Real-IP       $remote_addr;
        proxy_set_header Host            $host;
        proxy_set_header Forwarded       "";
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

In air-gapped environments where all services run on the same Docker host,
PostgreSQL traffic is confined to the internal Docker bridge network (`internal`
in `docker-compose.yml`). Network-level encryption via TLS is optional within
the Docker bridge but recommended for multi-host deployments.

To enable TLS on the PostgreSQL container:

1. Generate a self-signed certificate for the postgres container (use the same
   internal CA from Step 2.1 or a separate one):

   ```bash
   openssl genrsa -out postgres.key 4096
   openssl req -new -x509 -days 3650 -key postgres.key -out postgres.crt \
     -subj "/CN=postgres.internal"
   chmod 600 postgres.key
   ```

2. Mount the certificate and key into the container via
   `docker-compose.override.yml`:

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

3. Update `DATABASE_URL` and `PGBOUNCER_URL` in `.env` to add `?sslmode=require`.

### 2.3 TLS for Redis

Redis in the default Conclave configuration does not carry sensitive data —
it holds only Huey task queue messages (no PII, no keys). TLS for Redis is
optional within a single-host Docker deployment. If a multi-host deployment
requires Redis-over-TLS, configure `stunnel` in front of the Redis container.

### 2.4 TLS for MinIO

MinIO (`minio-ephemeral`) stores synthesis artefacts in `tmpfs` (discarded on
container stop). Within the Docker `internal` bridge network, TLS is optional.
If MinIO is exposed outside the Docker host for any reason (this is not the
default and not recommended), enable MinIO TLS via:

```bash
# Place certs in MinIO's default cert location
mkdir -p certs/CAs
cp conclave.crt certs/public.crt
cp conclave.key certs/private.key
```

Mount into the container and set `MINIO_VOLUMES` appropriately.

---

## Step 3 — Firewall Rules

### 3.1 Inbound Rules (Air-Gapped Host)

The following ports should be open on the host firewall. All others should be
blocked by default:

| Port | Protocol | Purpose | Source |
|------|----------|---------|--------|
| 443 | TCP | Nginx (HTTPS) — operator UI and API | LAN / operator workstations |
| 80 | TCP | Nginx HTTP → HTTPS redirect | LAN (optional; redirect only) |
| 22 | TCP | SSH admin access | Admin network only |

### 3.2 Ports That Must NOT Be Exposed

| Port | Service | Why Blocked |
|------|---------|-------------|
| 8000 | Conclave app | Direct app access bypasses nginx TLS; never expose publicly |
| 3000 | Grafana | Operator-only; expose only via VPN or SSH tunnel if needed |
| 5432 | PostgreSQL | Internal only; never expose outside Docker bridge |
| 6432 | PgBouncer | Internal only |
| 6379 | Redis | Internal only; no auth on Redis by default |
| 9000 | MinIO | Internal only; tmpfs data must not be accessible externally |

### 3.3 Outbound Rules (Air-Gapped Environment)

A fully air-gapped deployment requires no outbound connectivity from the
Conclave host. Block all outbound traffic except:

- DNS queries to your internal resolver (if required for hostname resolution
  within the Docker network)

No part of the Conclave Engine makes outbound HTTP or API calls at runtime.
All license validation, synthesis, and storage operations are fully local.

---

## Step 4 — Vault Initialization and Operator Passphrase Ceremony

### 4.1 Before First Startup

The vault is **not** initialized on first boot — it simply starts in a SEALED
state. The "initialization" is the first unseal, which establishes the
relationship between the operator passphrase, the `VAULT_SEAL_SALT`, and the
derived KEK.

**Before starting the stack, ensure:**

1. `VAULT_SEAL_SALT` is set in `.env`:

   ```bash
   python3 -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(16)).decode())"
   ```

   Copy the output into `.env` as `VAULT_SEAL_SALT=<value>`. This value is
   **not a secret** (it is a PBKDF2 salt, not a key), but it must remain
   **stable for the lifetime of the deployment**. Changing it means the
   same passphrase will derive a different KEK, making all ALE-encrypted data
   unreadable.

2. The operator passphrase is chosen. Requirements:
   - Minimum 20 characters
   - Mixed case, numbers, symbols recommended
   - Stored in a hardware password manager or printed and stored physically
   - Known by at least two operators (key escrow principle)

### 4.2 Operator Passphrase Ceremony

Perform this ceremony with at least two operators present:

1. Operator A generates the `VAULT_SEAL_SALT` and writes it to `.env`.
2. Operator A chooses the passphrase (does not share it verbally — writes it
   down on paper or enters it directly into the password manager).
3. Operator B confirms that the sealed copy of the passphrase is in escrow
   (physical safe, password manager with separate credentials, etc.).
4. Both operators sign a log entry noting the date, the fact that the ceremony
   was performed, and that the passphrase is in escrow. This log entry is
   retained for compliance records.
5. Operator A performs the first unseal (see Step 7 or `OPERATOR_MANUAL.md`
   Section 4). If it succeeds, the vault configuration is correct.

**Recovery note:** If the passphrase is ever lost, all ALE-encrypted data
becomes permanently unrecoverable. The `VAULT_SEAL_SALT` alone is not enough
to unseal. See `docs/DISASTER_RECOVERY.md` Section 3.2.

---

## Step 5 — Secret Provisioning

All secrets are injected via files in the `secrets/` directory. Create each
file with restricted permissions:

```bash
mkdir -p secrets
chmod 700 secrets
```

### 5.1 Application Signing Key

Required in production mode (`ENV=production`):

```bash
python3 -c "import secrets; print(secrets.token_hex(32))" > secrets/app_secret_key.txt
chmod 600 secrets/app_secret_key.txt
```

### 5.2 Artifact Signing Key

Required to enable model artifact integrity verification:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))" > secrets/artifact_signing_key.txt
chmod 600 secrets/artifact_signing_key.txt
```

Set `ARTIFACT_SIGNING_KEY` in `.env` to read from this file, or reference it
via Docker secrets in `docker-compose.yml`.

### 5.3 PostgreSQL Password

```bash
openssl rand -hex 32 > secrets/postgres_password.txt
chmod 600 secrets/postgres_password.txt
```

Update `DATABASE_URL` and `PGBOUNCER_URL` in `.env` to use this password.

### 5.4 ALE Key (Application-Level Encryption)

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
  > secrets/ale_key.txt
chmod 600 secrets/ale_key.txt
```

Set `ALE_KEY` in `.env` to the contents of this file.

### 5.5 Audit Log Signing Key

```bash
python3 -c "import os; print(os.urandom(32).hex())" > secrets/audit_key.txt
chmod 600 secrets/audit_key.txt
```

Set `AUDIT_KEY` in `.env`.

### 5.6 Masking Salt

```bash
python3 -c "import os; print(os.urandom(32).hex())" > secrets/masking_salt.txt
chmod 600 secrets/masking_salt.txt
```

Set `MASKING_SALT` in `.env`.

### 5.7 MinIO Credentials

```bash
openssl rand -hex 16 > secrets/minio_ephemeral_access_key.txt
openssl rand -hex 32 > secrets/minio_ephemeral_secret_key.txt
chmod 600 secrets/minio_ephemeral_access_key.txt secrets/minio_ephemeral_secret_key.txt
```

### 5.8 Grafana Credentials

```bash
openssl rand -hex 32 > secrets/grafana_admin_password.txt
echo "conclave-admin" > secrets/grafana_admin_user.txt
chmod 600 secrets/grafana_admin_password.txt secrets/grafana_admin_user.txt
```

### 5.9 License Public Key

Obtain the PEM-encoded RSA public key from the Conclave licensing server (see
`docs/LICENSING.md`). Save to `secrets/license_public_key.pem`:

```bash
chmod 600 secrets/license_public_key.pem
```

Set `LICENSE_PUBLIC_KEY` in `.env` to the PEM content (with `\n` for line breaks
if passing as a single-line environment variable).

### 5.10 Final Permissions Check

```bash
ls -la secrets/
# All files should show -rw------- (600) and be owned by the deploy user
```

---

## Step 6 — Database Migration

Apply Alembic migrations before starting the application stack. This step
requires a running PostgreSQL instance:

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

This is idempotent — safe to run on each deployment. It will skip migrations
that have already been applied.

---

## Step 7 — Start the Full Stack

```bash
docker compose up -d
```

Wait for all services to become healthy:

```bash
docker compose ps
docker compose exec app curl -s http://localhost:8000/health
# Expected: {"status": "ok"}
```

---

## Step 8 — Vault Unseal

The vault starts sealed. All non-exempt API routes return `423 Locked` until
unsealed:

```bash
curl -X POST http://localhost:8000/unseal \
  -H "Content-Type: application/json" \
  -d '{"passphrase": "<operator-passphrase>"}'
# Expected: {"status": "unsealed"}
```

For subsequent operator access via the browser, navigate to
`https://conclave.internal` — the UI will redirect to `/unseal` automatically.

---

## Step 9 — License Activation

Before synthesis jobs can run, activate the license. Follow the complete
protocol in `docs/LICENSING.md`. The short version:

```bash
# Obtain a challenge from the engine
curl http://localhost:8000/license/challenge

# Use the challenge to generate a license JWT on the licensing server
# (This step requires connectivity to the licensing server or a pre-issued JWT)

# Activate
curl -X POST http://localhost:8000/license/activate \
  -H "Content-Type: application/json" \
  -d '{"token": "<license-jwt>"}'
```

---

## Step 10 — First Synthesis Job Walkthrough

This section walks through a complete end-to-end synthesis job after the stack
is up, the vault is unsealed, and the license is active.

### Step 10.1 — Connect Source Data

Place your source Parquet file in a location accessible to the `app` container.
By default, the `data/` directory on the host is mounted at `/data/` inside
the container (verify in `docker-compose.yml` or `docker-compose.override.yml`).

```bash
# Copy your data file into the mounted data directory
cp /path/to/source_customers.parquet data/customers.parquet
```

### Step 10.2 — Configure Masking (Optional)

If you want to apply deterministic masking to specific columns before synthesis,
configure the masking engine via the API. See `docs/OPERATOR_MANUAL.md` for
the masking API reference.

### Step 10.3 — Create the Synthesis Job

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

Note the `id` field in the response (a UUID).

### Step 10.4 — Start the Job

```bash
curl -X POST http://localhost:8000/jobs/<job-id>/start
# Response: HTTP 202 Accepted
```

The job is now queued in Redis. The Huey worker will pick it up within a few
seconds.

### Step 10.5 — Monitor Progress

```bash
# Stream live SSE events
curl -N http://localhost:8000/jobs/<job-id>/stream
```

Or poll for status:

```bash
curl http://localhost:8000/jobs/<job-id>
```

The job transitions through: `QUEUED` → `TRAINING` → `COMPLETE`.

### Step 10.6 — Verify the Output

When the job reaches `COMPLETE` status, the synthetic Parquet file is available
in the ephemeral MinIO storage:

```bash
curl http://localhost:8000/jobs/<job-id>
# Check "output_path" in the response for the MinIO object path
```

Download the output via the MinIO API or the job download endpoint:

```bash
curl -o synthetic_customers.parquet \
  http://localhost:8000/jobs/<job-id>/download
```

**Important:** MinIO uses `tmpfs` storage. The output Parquet file is discarded
when the `minio-ephemeral` container stops. Download and archive the output
before stopping or restarting the stack.

### Step 10.7 — Verify DP Budget (if DP-enabled)

If the job used DP-SGD synthesis, check the epsilon spent:

```bash
curl http://localhost:8000/jobs/<job-id>
# Check "epsilon_spent" field in the response
```

Compare this to the global privacy budget:

```bash
curl http://localhost:8000/privacy/budget
```

---

## Step 11 — Post-Deployment Verification Checklist

After completing the walkthrough, verify:

- [ ] Vault unseals successfully with the operator passphrase
- [ ] `GET /health` returns `{"status": "ok"}`
- [ ] A test synthesis job completes without errors
- [ ] Grafana dashboard (`https://conclave.internal:3000`) shows metrics
- [ ] Application logs show no error-level entries
- [ ] Audit log (`docker compose exec app cat /tmp/audit.log`) contains the
  unseal event
- [ ] All `secrets/` files have `600` permissions

---

## References

- `docs/OPERATOR_MANUAL.md` — Day-to-day operational procedures
- `docs/SCALABILITY.md` — Hardware sizing and capacity limits
- `docs/DISASTER_RECOVERY.md` — Recovery procedures for failure scenarios
- `docs/LICENSING.md` — License activation protocol
- `.env.example` — All supported environment variables with descriptions
