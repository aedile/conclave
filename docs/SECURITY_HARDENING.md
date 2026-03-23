# Security Hardening Guide

Security hardening for production deployments: CORS, DDoS mitigation, TLS, vault passphrase management, and key rotation.

Cross-reference: [docs/infrastructure_security.md](infrastructure_security.md) for host-level controls (disk encryption, Linux capabilities, secrets management, network isolation).

---

## 1. CORS Policy

### 1.1 Default Posture — Same-Origin Only

The engine emits **no** CORS headers by default. `CSPMiddleware` (`bootstrapper/dependencies/csp.py`) enforces:

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

`connect-src 'self'` blocks cross-origin fetch/XHR. No `Access-Control-Allow-Origin` header is emitted because no cross-origin requests are expected.

### 1.2 When CORS Must Be Configured

CORS is only needed when the frontend is served from a **different domain** than the API. Standard deployments serve both on the same host/port (`:8000`).

Enable CORS for:
- Frontend on a CDN (e.g., `https://app.example.com`) vs. API on `https://api.example.com`
- Native desktop/mobile clients on a different origin
- Integration test runners on a different host

### 1.3 How to Enable CORS

Add `CORSMiddleware` in `bootstrapper/main.py`:

```python
from starlette.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com"],  # Never use ["*"] in production
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=True,
    max_age=600,
)
```

**Production rules:**

| Rule | Rationale |
|------|-----------|
| Never `allow_origins=["*"]` | Wildcard + `allow_credentials=True` is rejected by browsers and exposes the API |
| Enumerate origins explicitly | Allowlist specific domains only |
| Omit `TRACE`/`CONNECT` methods | Only `GET`, `POST`, `DELETE` |
| Keep `max_age=600` | 10 minutes balances preflight overhead vs. revocation speed |
| Re-test after proxy changes | Reverse proxies may strip/add headers that interfere with preflight |

### 1.4 CORS and the Reverse Proxy

Ensure nginx/Caddy does **not** inject `Access-Control-Allow-Origin: *` unconditionally — proxy-level headers override application-level headers and silently widen the allowed-origins set. See [docs/OPERATOR_MANUAL.md Section 8.8](OPERATOR_MANUAL.md).

---

## 2. DDoS Mitigation Stack

### 2.1 Application Layer — Request Body Limits

`RequestBodyLimitMiddleware` (`bootstrapper/dependencies/request_limits.py`) rejects before route handlers:

| Limit | Value | HTTP Response |
|-------|-------|---------------|
| Body size | 1 MiB | 413 Payload Too Large |
| JSON nesting depth | 100 levels | 400 Bad Request |

### 2.2 Application Layer — Rate Limiting

`RateLimitGateMiddleware` (`bootstrapper/dependencies/rate_limit.py`) is the **outermost** layer — fires before any other check. Uses `FixedWindowRateLimiter` with in-memory storage (no external dependencies; safe for air-gapped deployments).

Default limits (configurable via `ConclaveSettings`):

| Endpoint | Rate Limit | Key | Environment Variable |
|----------|-----------|-----|----------------------|
| `POST /unseal` | 5 req/min | Client IP | `RATE_LIMIT_UNSEAL_PER_MINUTE` |
| `POST /auth/token` | 10 req/min | Client IP | `RATE_LIMIT_AUTH_PER_MINUTE` |
| `GET /jobs/{id}/download` | 10 req/min | JWT `sub` | `RATE_LIMIT_DOWNLOAD_PER_MINUTE` |
| All other endpoints | 60 req/min | JWT `sub` | `RATE_LIMIT_GENERAL_PER_MINUTE` |

Exceeded limits return HTTP 429 with `Retry-After` and RFC 7807 body.

**Tuning:** In-process limits are per-uvicorn-worker and reset on restart. They supplement — not replace — infrastructure limits. Tighten beyond defaults only if your threat model requires it.

```bash
# .env
RATE_LIMIT_UNSEAL_PER_MINUTE=3
RATE_LIMIT_AUTH_PER_MINUTE=5
RATE_LIMIT_GENERAL_PER_MINUTE=30
RATE_LIMIT_DOWNLOAD_PER_MINUTE=5
```

### 2.3 Infrastructure Layer — nginx Rate Limiting

nginx applies rate limiting at the kernel accept level, before any Python code runs.

```nginx
limit_req_zone $binary_remote_addr zone=conclave_api:10m rate=30r/m;
limit_req_zone $binary_remote_addr zone=conclave_unseal:10m rate=3r/m;
limit_conn_zone $binary_remote_addr zone=conclave_conn:10m;

server {
    listen 443 ssl;
    server_name conclave.example.com;

    ssl_certificate     /etc/ssl/certs/conclave.crt;
    ssl_certificate_key /etc/ssl/private/conclave.key;

    limit_conn conclave_conn 20;
    client_max_body_size 1m;
    client_body_timeout 10s;
    client_header_timeout 10s;
    keepalive_timeout 15s;
    send_timeout 10s;

    location / {
        limit_req zone=conclave_api burst=10 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Real-IP       $remote_addr;
        proxy_set_header Host            $host;
        proxy_set_header Forwarded       "";
    }

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

uvicorn defaults to `--timeout-keep-alive=5s`, which is a safe production value. To set explicitly:

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
      - "1"   # Required for in-memory rate limiter correctness
```

**Important:** `RateLimitGateMiddleware` uses `MemoryStorage` (per-process). Multiple workers maintain separate buckets. Keep `--workers 1` or switch to a Redis-backed rate limit store before scaling out.

### 2.5 Infrastructure Layer — Cloud WAF (Optional)

| Cloud | Service | Recommended Rules |
|-------|---------|-------------------|
| AWS | AWS WAF + Shield Standard | IP rate-based rules, SQL injection, XSS managed rule groups |
| GCP | Cloud Armor | Rate limiting, OWASP top-10 preconfigured rules |
| Azure | Azure Front Door + WAF | Managed ruleset, custom rate limit rules |

For air-gapped deployments, rely on nginx + application layers above.

---

## 3. TLS Configuration

### 3.1 TLS Termination Architecture

The `app` service does **not** terminate TLS. Uvicorn listens on port 8000 over plain HTTP on the internal Docker bridge. TLS is terminated by nginx or Caddy.

**Never expose port 8000 to the internet.** Bind it to `127.0.0.1` or an internal Docker network.

### 3.2 Recommended TLS Configuration (nginx)

Use TLS 1.2 and 1.3 only (Mozilla "Intermediate" profile):

```nginx
ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;
ssl_prefer_server_ciphers off;

# HSTS — omit `preload` for air-gapped/internal deployments
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

ssl_stapling on;
ssl_stapling_verify on;
ssl_trusted_certificate /etc/ssl/certs/chain.pem;
resolver 127.0.0.1;  # Use local resolver for air-gapped deployments

ssl_session_tickets off;      # Preserve forward secrecy across restarts
ssl_session_cache shared:SSL:10m;
ssl_session_timeout 1d;

ssl_dhparam /etc/nginx/dhparam.pem;  # Generate: openssl dhparam -out dhparam.pem 4096
```

Generate DH parameters before deploying:

```bash
openssl dhparam -out /etc/nginx/dhparam.pem 4096
```

### 3.3 Certificate Provisioning

Internet-facing: use Let's Encrypt (Certbot) or your org's PKI.

Air-gapped (internal CA):

```bash
openssl genrsa -out conclave.key 4096
openssl req -new -key conclave.key -out conclave.csr \
  -subj "/CN=conclave.internal/O=Conclave Engine"
openssl x509 -req -in conclave.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out conclave.crt -days 825 -sha256
```

### 3.4 PostgreSQL TLS

SSL is enforced by default (`CONCLAVE_SSL_REQUIRED=true`). On Docker bridge networks where app and postgres are co-located, it may be relaxed:

```bash
# .env — Docker bridge only; never for cross-host
CONCLAVE_SSL_REQUIRED=false
```

For cross-host deployments, leave `CONCLAVE_SSL_REQUIRED=true` and provision PostgreSQL TLS:

```bash
# postgresql.conf
ssl = on
ssl_cert_file = '/etc/ssl/certs/server.crt'
ssl_key_file  = '/etc/ssl/private/server.key'
```

---

## 4. Vault Passphrase Management

The engine boots **sealed**. All non-exempt endpoints return `423 Locked` until unsealed. Implementation: `shared/security/vault.py`.

### 4.1 KEK Derivation

The operator passphrase is never stored. `VaultState.unseal()` runs PBKDF2-HMAC-SHA256 (600,000 iterations) to derive a 32-byte KEK held in process memory as a zeroed-on-seal `bytearray`.

`VAULT_SEAL_SALT` (16-byte base64url, not secret) prevents rainbow-table attacks and must remain stable across restarts so the same passphrase always produces the same KEK.

### 4.2 Passphrase Requirements

| Requirement | Rationale |
|-------------|-----------|
| Minimum 20 characters | 600,000 PBKDF2 iterations makes brute-force infeasible against strong passphrases |
| High entropy — not a dictionary word | PBKDF2 cost is tuned for strong passphrases |
| Unique per deployment | Never reuse across environments |
| Stored out-of-band | HSM, password manager, or printed card in a physical safe — never in `.env` |
| Known to at least two operators | Prevents permanent data loss if sole operator is unavailable |

Generate a passphrase:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 4.3 Timing Side-Channel Mitigation

`VaultState.unseal()` runs PBKDF2 derivation **before** checking for an empty passphrase (T38.2). Both empty and wrong passphrases incur the full ~100 ms cost, eliminating the timing oracle.

### 4.4 Seal Before Maintenance

> **WARNING: `POST /security/shred` is destructive.** It zeroizes the in-memory KEK, rendering all ALE-encrypted data permanently unrecoverable until re-unsealed with the original passphrase.

```bash
curl -X POST http://localhost:8000/security/shred \
  -H "Authorization: Bearer ${TOKEN}"
```

Re-unseal after maintenance:

```bash
curl -X POST http://localhost:8000/unseal \
  -H "Content-Type: application/json" \
  -d "{\"passphrase\": \"<original-passphrase>\"}"
```

### 4.5 Emergency Re-Seal

If KEK compromise is suspected:

1. Seal immediately (`POST /security/shred`).
2. Restart the `app` container (in-memory KEK is zeroed; never written to disk).
3. Rotate the ALE key (Section 5).
4. Generate a new `VAULT_SEAL_SALT` and update `.env`.
5. Change the passphrase.
6. Re-unseal with the new passphrase.

---

## 5. Key Rotation Procedures

### 5.1 ALE Key Rotation

The ALE key encrypts sensitive DB columns (Fernet: AES-128-CBC + HMAC-SHA256). Implementation: `shared/security/rotation.py`. Endpoint: `POST /security/keys/rotate`.

**When to rotate:** suspected compromise, scheduled policy (every 90 days), or staff change involving key material access.

**How it works:** The operator provides `new_passphrase` (recorded in audit trail for intent). The server generates the new Fernet key internally:

1. Server calls `Fernet.generate_key()`.
2. New key is KEK-wrapped (never stored in plaintext in Redis).
3. Huey task `rotate_ale_keys_task` is enqueued — returns `202 Accepted`.
4. Worker re-encrypts all ALE columns: discovers via SQLModel metadata, re-encrypts in batches of 1,000, all-or-nothing transaction.

```bash
# 1. Ensure vault is unsealed
curl http://localhost:8000/health

# 2. Trigger rotation
curl -X POST http://localhost:8000/security/keys/rotate \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"new_passphrase\": \"<new-operator-passphrase>\"}"
```

**After rotation:**

1. The new ALE key is never written to any log or external store. **The vault passphrase is the sole recovery mechanism.** If lost, all ALE-encrypted data is permanently unrecoverable.
2. Restart `app` and Huey worker services.
3. Verify `KEY_ROTATION_REQUESTED` in the audit log.

### 5.2 Model Artifact Signing Key Rotation

Rotating `ARTIFACT_SIGNING_KEY` does **not** re-sign existing artifacts — they will fail verification.

1. Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"`
2. Update `ARTIFACT_SIGNING_KEY` in your secrets store.
3. Restart `app`. New artifacts use the new key.
4. Re-run synthesis jobs whose artifacts must remain accessible.

### 5.3 JWT Secret Key Rotation

Rotating `JWT_SECRET_KEY` immediately invalidates all existing tokens.

1. Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"`
2. Update `JWT_SECRET_KEY` in your secrets store.
3. **Notify operators** of forced logout before rotating in production.
4. Restart `app`.

### 5.4 Audit Key Rotation

`AUDIT_KEY` HMAC-signs audit log entries. Rotation does not invalidate existing entries — verify historical entries with the key that was active at write time.

1. Archive the current `AUDIT_KEY` alongside the signed entries (needed for historical verification).
2. Generate: `python3 -c "import os; print(os.urandom(32).hex())"`
3. Update `AUDIT_KEY` in your secrets store.
4. Restart `app`.

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
