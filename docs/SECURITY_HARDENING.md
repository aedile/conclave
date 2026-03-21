# Security Hardening Guide

This guide covers the security hardening posture for production deployments of
the Conclave Engine. It documents the CORS policy, DDoS mitigation stack, TLS
configuration, vault passphrase management, and key rotation procedures.

Cross-reference with [docs/infrastructure_security.md](infrastructure_security.md)
for host-level controls (disk encryption, Linux capabilities, secrets management,
network isolation).

---

## 1. CORS Policy

### 1.1 Default Posture — Same-Origin Only

The Conclave Engine does **not** emit CORS headers by default. This is correct
and intentional for air-gapped and same-origin deployments.

The `CSPMiddleware` (`src/synth_engine/bootstrapper/dependencies/csp.py`)
enforces a strict Content-Security-Policy that limits all resource loading to
the same origin:

```text
default-src 'self'
script-src 'self'
style-src 'self' 'unsafe-inline'
font-src 'self'
img-src 'self' data:
connect-src 'self'
frame-ancestors 'none'
base-uri 'self'
form-action 'self'
```

The `connect-src 'self'` directive means the browser's `fetch`/`XHR` will only
send requests to the same origin as the page. This is the primary defense
against cross-origin data exfiltration. No `Access-Control-Allow-Origin` header
is emitted because no cross-origin requests are expected.

### 1.2 When CORS Must Be Configured

CORS headers are only required when the frontend (React SPA) is served from a
**different domain** than the Conclave API. In standard deployments, the frontend
is served by the `app` service on the same host and port (`:8000`), so CORS is
not needed.

CORS must be explicitly enabled if you deploy:

- A frontend on a CDN (e.g., `https://app.example.com`) pointing to an API on
  `https://api.example.com`.
- A native desktop or mobile client calling the API from a different origin.
- An integration test runner on a different host calling the API directly.

### 1.3 How to Enable CORS

FastAPI supports CORS via Starlette's `CORSMiddleware`. To enable it, add the
middleware in `src/synth_engine/bootstrapper/middleware.py` before calling
`setup_middleware()`, or add it to the `create_app()` factory in
`src/synth_engine/bootstrapper/main.py`:

```python
from starlette.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com"],  # Never use ["*"] in production
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=True,
    max_age=600,  # seconds the browser may cache the preflight response
)
```

**Production rules for CORS configuration:**

| Rule | Rationale |
|------|-----------|
| Never set `allow_origins=["*"]` | Wildcard origin combined with `allow_credentials=True` is rejected by browsers and exposes the API to any site |
| Enumerate allowed origins explicitly | Allowlist specific domains; reject all others |
| Do not allow unnecessary HTTP methods | Only allow `GET`, `POST`, `DELETE` — never `TRACE` or `CONNECT` |
| Keep `max_age` short | 600 seconds (10 minutes) balances preflight overhead against the ability to revoke CORS quickly |
| Re-test after proxy changes | Reverse proxies (nginx, Caddy) may strip or add headers that interfere with CORS preflight responses |

### 1.4 CORS and the Reverse Proxy

If a reverse proxy (nginx, Caddy) sits in front of the engine, ensure the proxy
does not inject `Access-Control-Allow-Origin: *` unconditionally. Proxy-level
CORS headers override application-level headers and can silently widen the
allowed-origins set.

The sample nginx configuration in
[docs/OPERATOR_MANUAL.md Section 8.8](OPERATOR_MANUAL.md) strips the
`Forwarded` header and re-sets `X-Forwarded-For`. That configuration must also
**not** add CORS headers unless cross-origin access is required.

---

## 2. DDoS Mitigation Stack

The engine implements a layered DDoS mitigation strategy across the application
and infrastructure layers.

### 2.1 Application Layer — Request Body Limits

`RequestBodyLimitMiddleware`
(`src/synth_engine/bootstrapper/dependencies/request_limits.py`) rejects
requests before they reach route handlers:

| Limit | Value | HTTP Response | Purpose |
|-------|-------|---------------|---------|
| Body size | 1 MiB (`MAX_BODY_BYTES`) | 413 Payload Too Large | Prevent memory exhaustion from large payloads |
| JSON nesting depth | 100 levels (`MAX_JSON_DEPTH`) | 400 Bad Request | Prevent recursive-parser stack overflows (CVE-2020-36327-style) |

This middleware is the **outermost** layer in the ASGI stack (registered last in
LIFO order), so size and depth checks fire before the vault gate, license gate,
or any route handler.

### 2.2 Application Layer — Rate Limiting

`RateLimitGateMiddleware`
(`src/synth_engine/bootstrapper/dependencies/rate_limit.py`) enforces
per-endpoint, per-identity rate limits using a `FixedWindowRateLimiter` backed
by in-memory storage (the `limits` library). This requires no external
dependencies and is safe for air-gapped deployments.

Default limits (configurable via `ConclaveSettings` in
`src/synth_engine/shared/settings.py`):

| Endpoint | Rate Limit | Key | Environment Variable |
|----------|-----------|-----|----------------------|
| `POST /unseal` | 5 req/min | Client IP | `RATE_LIMIT_UNSEAL_PER_MINUTE` |
| `POST /auth/token` | 10 req/min | Client IP | `RATE_LIMIT_AUTH_PER_MINUTE` |
| `GET /jobs/{id}/download` | 10 req/min | Operator JWT `sub` | `RATE_LIMIT_DOWNLOAD_PER_MINUTE` |
| All other endpoints | 60 req/min | Operator JWT `sub` | `RATE_LIMIT_GENERAL_PER_MINUTE` |

Clients that exceed a limit receive HTTP 429 with a `Retry-After` header and an
RFC 7807 Problem Details body.

**Tuning rate limits for production:**

In-process rate limits are per-uvicorn-worker and reset on process restart. They
are additive to—not a replacement for—infrastructure-layer limits. Tighten
application-layer limits beyond the defaults only if your threat model requires
it. Excessive tightening will block legitimate operators.

```bash
# .env
RATE_LIMIT_UNSEAL_PER_MINUTE=3          # Tighter brute-force protection
RATE_LIMIT_AUTH_PER_MINUTE=5            # Tighter credential-stuffing protection
RATE_LIMIT_GENERAL_PER_MINUTE=30        # Reduce for high-value deployments
RATE_LIMIT_DOWNLOAD_PER_MINUTE=5        # Reduce bandwidth exposure
```

### 2.3 Infrastructure Layer — nginx Rate Limiting

Place an nginx reverse proxy in front of the engine (see Section 3 for TLS
configuration). nginx applies rate limiting at the kernel accept level — before
any Python code runs — making it the most cost-effective DDoS mitigation layer.

**Recommended nginx `nginx.conf` additions:**

```nginx
# Define a shared memory zone for rate limiting.
# 10m ~ 160,000 IP addresses; adjust for your expected client population.
limit_req_zone $binary_remote_addr zone=conclave_api:10m rate=30r/m;
limit_req_zone $binary_remote_addr zone=conclave_unseal:10m rate=3r/m;
limit_conn_zone $binary_remote_addr zone=conclave_conn:10m;

server {
    listen 443 ssl;
    server_name conclave.example.com;

    # --- TLS (see Section 3) ---
    ssl_certificate     /etc/ssl/certs/conclave.crt;
    ssl_certificate_key /etc/ssl/private/conclave.key;

    # --- Connection limits ---
    limit_conn conclave_conn 20;          # Max 20 concurrent connections per IP
    client_max_body_size 1m;              # Mirror the application-layer 1 MiB limit
    client_body_timeout 10s;             # Drop slow-read attacks
    client_header_timeout 10s;
    keepalive_timeout 15s;               # Close idle connections promptly
    send_timeout 10s;

    # --- General API rate limit ---
    location / {
        limit_req zone=conclave_api burst=10 nodelay;
        limit_req_status 429;

        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Real-IP       $remote_addr;
        proxy_set_header Host            $host;
        proxy_set_header Forwarded       "";
    }

    # --- Tighter limit for the unseal endpoint ---
    location = /unseal {
        limit_req zone=conclave_unseal burst=2 nodelay;
        limit_req_status 429;

        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Real-IP       $remote_addr;
        proxy_set_header Host            $host;
        proxy_set_header Forwarded       "";
    }
}
```

### 2.4 Infrastructure Layer — Slow-Read Attack Mitigation (uvicorn)

Slow-read (Slowloris-style) attacks hold HTTP connections open by sending
request headers or body data at a very low rate, eventually exhausting the
server's connection pool.

uvicorn exposes two keep-alive timeout settings:

| Flag | Default | Recommended (Production) | Purpose |
|------|---------|--------------------------|---------|
| `--timeout-keep-alive` | 5 s | 5 s (keep default) | Maximum idle time between keep-alive requests before closing the connection |
| `--timeout-graceful-shutdown` | 30 s | 30 s (keep default) | Time uvicorn waits for in-flight requests to finish during shutdown |

The current `Dockerfile` `CMD` does not pass `--timeout-keep-alive` explicitly;
uvicorn uses its built-in default of 5 seconds, which is a reasonable production
value. To harden further, set it explicitly in `docker-compose.yml` or a
`docker-compose.override.yml`:

```yaml
services:
  app:
    command:
      - uvicorn
      - synth_engine.bootstrapper.main:app
      - --host
      - "0.0.0.0"
      - --port
      - "8000"
      - --timeout-keep-alive
      - "5"
      - --workers
      - "1"   # Single worker — required for in-memory rate limiter correctness
```

**Important:** The in-process `RateLimitGateMiddleware` uses `MemoryStorage`,
which is per-process. Running multiple uvicorn workers means each worker
maintains a separate rate-limit bucket. Keep `--workers 1` (the default when
`--workers` is not specified) or switch to a Redis-backed rate limit store
before scaling out.

### 2.5 Infrastructure Layer — Cloud WAF (Optional)

If the engine is deployed in a cloud environment (AWS, GCP, Azure), a WAF
(Web Application Firewall) provides a fourth layer of DDoS mitigation:

| Cloud | Service | Recommended Rules |
|-------|---------|-------------------|
| AWS | AWS WAF + Shield Standard | IP rate-based rules, SQL injection, XSS managed rule groups |
| GCP | Cloud Armor | Rate limiting policies, OWASP top-10 preconfigured rules |
| Azure | Azure Front Door + WAF | Managed ruleset, custom rate limit rules |

For air-gapped deployments without cloud access, rely on the nginx + application
layers described above.

---

## 3. TLS Configuration

### 3.1 TLS Termination Architecture

The engine's `app` service does **not** terminate TLS. Uvicorn listens on port
8000 over plain HTTP on the internal Docker bridge network. TLS is terminated by
a reverse proxy (nginx or Caddy) before traffic reaches the engine.

This architecture is intentional:

- The Python application process is not responsible for key material rotation.
- TLS configuration changes (cipher suites, certificates) do not require
  application restarts.
- The nginx/Caddy process runs with minimal privileges and a smaller attack
  surface than the Python application.

**Never expose port 8000 to the internet.** Bind it to `127.0.0.1` or an
internal Docker network interface and let the reverse proxy handle public TLS.

### 3.2 Recommended TLS Configuration (nginx)

Use TLS 1.2 and 1.3 only. Disable older protocol versions and weak cipher
suites. The following nginx configuration follows Mozilla's "Intermediate"
compatibility profile, which balances modern security with broad client support:

```nginx
# TLS protocol versions
ssl_protocols TLSv1.2 TLSv1.3;

# Cipher suite order: prefer AEAD ciphers; disable 3DES, RC4, export ciphers
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;
ssl_prefer_server_ciphers off;  # Let TLS 1.3 negotiate freely

# HSTS — instruct browsers to always use HTTPS for this domain
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

# OCSP stapling — cache certificate status checks at the server
ssl_stapling on;
ssl_stapling_verify on;
ssl_trusted_certificate /etc/ssl/certs/chain.pem;
resolver 127.0.0.1;  # Use a local resolver for air-gapped deployments

# Session tickets — disabled to preserve forward secrecy across restarts
ssl_session_tickets off;
ssl_session_cache shared:SSL:10m;
ssl_session_timeout 1d;

# DH params for DHE cipher suites
ssl_dhparam /etc/nginx/dhparam.pem;  # Generate: openssl dhparam -out dhparam.pem 4096
```

Generate a new 4096-bit DH parameter file before deploying:

```bash
openssl dhparam -out /etc/nginx/dhparam.pem 4096
```

### 3.3 Certificate Provisioning

For internet-facing deployments, use Let's Encrypt (Certbot) or your
organization's PKI to provision certificates. For air-gapped environments,
issue certificates from an internal CA:

```bash
# Generate a private key
openssl genrsa -out conclave.key 4096

# Generate a CSR
openssl req -new -key conclave.key -out conclave.csr \
  -subj "/CN=conclave.internal/O=Conclave Engine"

# Issue from internal CA (on the CA host)
openssl x509 -req -in conclave.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out conclave.crt -days 825 -sha256
```

### 3.4 PostgreSQL TLS

The engine enforces SSL for PostgreSQL connections by default
(`CONCLAVE_SSL_REQUIRED=true` in `ConclaveSettings`). In Docker bridge network
deployments, internal PostgreSQL traffic flows over an isolated network that
does not reach the host interface, and SSL may be relaxed to `false`:

```bash
# .env — Docker bridge network (internal traffic only)
CONCLAVE_SSL_REQUIRED=false
```

For deployments where the `app` service and `postgres` service are on separate
hosts, leave `CONCLAVE_SSL_REQUIRED=true` and provision a TLS certificate for
PostgreSQL:

```bash
# postgresql.conf
ssl = on
ssl_cert_file = '/etc/ssl/certs/server.crt'
ssl_key_file  = '/etc/ssl/private/server.key'
```

---

## 4. Vault Passphrase Management

The engine boots **sealed**. All non-exempt API endpoints return `423 Locked`
until the vault is unsealed. The vault implementation is in
`src/synth_engine/shared/security/vault.py`.

### 4.1 KEK Derivation

The operator passphrase is never stored. `VaultState.unseal()` runs
PBKDF2-HMAC-SHA256 with 600,000 iterations to derive a 32-byte Key Encryption
Key (KEK) held exclusively in process memory as a zeroed-on-seal `bytearray`.

The `VAULT_SEAL_SALT` environment variable provides a 16-byte base64url-encoded
PBKDF2 salt. The salt is **not secret** — it prevents rainbow-table attacks and
must remain stable across restarts so the same passphrase always produces the
same KEK.

### 4.2 Passphrase Selection Requirements

| Requirement | Rationale |
|-------------|-----------|
| Minimum 20 characters | With 600,000 PBKDF2 iterations, a 20+ character passphrase raises brute-force cost to infeasible levels |
| High entropy — not a dictionary word | PBKDF2 does not resist low-entropy passphrases; the algorithm's cost is tuned for strong passphrases |
| Unique per deployment | Never reuse passphrases across environments (development, staging, production) |
| Stored out-of-band | Store in a hardware security module (HSM), password manager, or on a printed card in a physical safe — never in `.env` |
| Known to at least two operators | A single point of failure risks permanent data loss if the sole operator is unavailable |

Generate a cryptographically random passphrase:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 4.3 Timing Side-Channel Mitigation

`VaultState.unseal()` performs the PBKDF2 derivation **before** checking for an
empty passphrase. This was introduced in T38.2 to eliminate a timing oracle: an
attacker cannot distinguish an empty passphrase (previously ~µs) from a wrong
passphrase (~100 ms at 600,000 iterations) by measuring response time. Both
paths now incur the full PBKDF2 cost before any error is raised.

### 4.4 Seal Before Maintenance

Always re-seal the vault before performing maintenance operations that require
elevated system access:

```bash
curl -X POST http://localhost:8000/unseal/seal
```

After sealing, all non-exempt routes return `423 Locked`. The passphrase is
**not** required to re-seal; any authenticated operator can seal the vault.

### 4.5 Emergency Re-Seal

If an operator suspects the KEK has been compromised:

1. Seal the vault immediately (`POST /unseal/seal`).
2. Restart the `app` container to force a fresh process (the KEK is never
   written to disk; the in-memory copy is zeroed by `VaultState.seal()`).
3. Rotate the ALE encryption key (see Section 5).
4. Generate a new `VAULT_SEAL_SALT` and update `.env` — this changes the KEK
   derived from the same passphrase.
5. Change the passphrase.
6. Re-unseal with the new passphrase.

---

## 5. Key Rotation Procedures

### 5.1 Application-Level Encryption (ALE) Key Rotation

The ALE key encrypts sensitive database columns using Fernet (AES-128-CBC +
HMAC-SHA256). Key rotation is implemented in
`src/synth_engine/shared/security/rotation.py` and exposed via the
`POST /security/keys/rotate` API endpoint.

**When to rotate:**

- Suspected key compromise.
- Scheduled rotation policy (recommended: every 90 days in production).
- After a staff change that involved access to key material.

**Rotation procedure:**

```bash
# 1. Ensure the vault is unsealed
curl http://localhost:8000/health

# 2. Generate a new Fernet key
NEW_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# 3. Trigger the rotation via the API (the API handler wraps the new key
#    with the vault Fernet before enqueuing it in Huey, so the raw key
#    never appears in Redis in plaintext)
curl -X POST http://localhost:8000/security/keys/rotate \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"new_key\": \"${NEW_KEY}\"}"
```

The rotation task runs asynchronously in the Huey worker process.
`rotate_ale_keys_task()` (in `shared/security/rotation.py`):

1. Discovers all `EncryptedString` columns via SQLModel metadata introspection
   (`find_encrypted_columns()`).
2. Re-encrypts every non-NULL value from the old Fernet key to the new one in
   a single database transaction (`re_encrypt_column_values()`).
3. The entire operation is all-or-nothing: any failure rolls back all changes,
   preventing partial-rotation states.
4. Rows are processed in batches of 1,000 to avoid OOM on large tables.

**After rotation:**

1. Update `ALE_KEY` in your secrets store with the new Fernet key.
2. Restart the `app` and Huey worker services so they pick up the new key.
3. Verify the audit log for the rotation event.

### 5.2 Model Artifact Signing Key Rotation

Model artifacts are signed with an HMAC-SHA256 key (`ARTIFACT_SIGNING_KEY`).
Rotating this key does not re-sign existing artifacts — artifacts signed with
the old key will fail signature verification after rotation.

**Rotation procedure:**

1. Generate a new key:

   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

2. Update `ARTIFACT_SIGNING_KEY` in your secrets store.
3. Restart the `app` service. New artifacts will be signed with the new key.
4. Re-run any synthesis jobs whose artifacts must remain accessible after
   rotation (existing artifacts signed with the old key will be rejected).

### 5.3 JWT Secret Key Rotation

The JWT secret key (`JWT_SECRET_KEY`) signs operator access tokens.

**Rotation procedure:**

1. Generate a new secret:

   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

2. Update `JWT_SECRET_KEY` in your secrets store.
3. Restart the `app` service. All existing tokens signed with the old key are
   immediately invalidated — operators must re-authenticate.
4. Notify operators of the forced logout before rotating in production.

### 5.4 Audit Key Rotation

The `AUDIT_KEY` is used to HMAC-sign audit log entries for tamper detection.
Rotating this key does not invalidate existing audit entries — they retain their
original signatures. Verification of historical entries must use the key that
was active at the time of writing.

**Rotation procedure:**

1. Archive the current `AUDIT_KEY` value alongside the audit log entries it
   signed (e.g., in a secure key escrow). You will need the original key to
   verify historical entries.
2. Generate a new key:

   ```bash
   python3 -c "import os; print(os.urandom(32).hex())"
   ```

3. Update `AUDIT_KEY` in your secrets store.
4. Restart the `app` service. New audit entries will be signed with the new key.

---

## 6. Compliance Cross-References

| Topic | Primary Reference |
|-------|------------------|
| Host-level disk encryption (LUKS) | [docs/infrastructure_security.md Section 1](infrastructure_security.md) |
| IPC_LOCK capability and memory locking | [docs/infrastructure_security.md Section 2](infrastructure_security.md) |
| Non-root container execution | [docs/infrastructure_security.md Section 3](infrastructure_security.md) |
| Secrets management (Docker secrets) | [docs/infrastructure_security.md Section 4](infrastructure_security.md) |
| X-Forwarded-For trust / reverse proxy | [docs/OPERATOR_MANUAL.md Section 8.8](OPERATOR_MANUAL.md) |
| Vault unseal procedure | [docs/OPERATOR_MANUAL.md Section 4](OPERATOR_MANUAL.md) |
| GDPR / CCPA / HIPAA retention | [docs/DATA_COMPLIANCE.md](DATA_COMPLIANCE.md) |
| DP-SGD privacy guarantees | [docs/OPERATOR_MANUAL.md Section 9](OPERATOR_MANUAL.md) |
| Disaster recovery | [docs/DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) |
