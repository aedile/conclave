# OIDC Runbook: IdP Unavailable

**Phase**: 81 — SSO/OIDC Integration
**ADR**: ADR-0067

---

## Overview

Conclave Engine fetches the OIDC discovery document and JWKS at application
startup. If the identity provider (IdP) is unavailable at startup, the
application fails to start (fail-closed behavior). This runbook covers
diagnosis and recovery procedures.

---

## Symptoms of IdP Unavailability

### At Startup

- Application fails to start with an error like:
  ```
  RuntimeError: OIDC discovery document fetch failed for 'http://idp.internal:9999/.well-known/openid-configuration': ...
  ```
- Container exits immediately (non-zero exit code).
- Health endpoint `/health` unreachable (application never started).

### During Operation (after successful startup)

- `GET /auth/oidc/authorize` returns `200 OK` with a `redirect_url` (OIDC state is pre-loaded from boot).
- `GET /auth/oidc/callback` returns `503 Service Unavailable` if the IdP is down
  when the code exchange is attempted (the token endpoint call fails).
- Existing JWT tokens remain valid (they do not depend on IdP availability after issuance).
- Existing Redis sessions remain valid.

---

## Diagnosis

### Step 1: Check IdP reachability

From the application host or container:

```bash
# Check discovery document endpoint
curl -v http://<OIDC_ISSUER_URL>/.well-known/openid-configuration

# Check token endpoint (from discovery doc)
curl -v http://<OIDC_ISSUER_URL>/token

# Check JWKS endpoint (from discovery doc)
curl -v http://<OIDC_ISSUER_URL>/certs
```

### Step 2: Check application logs

```bash
# Startup failure
docker logs <container> 2>&1 | grep -i "OIDC\|oidc\|discovery\|JWKS"

# Runtime 503 errors
docker logs <container> 2>&1 | grep "503\|idp\|token_exchange"
```

### Step 3: Check OIDC configuration

Verify environment variables are set correctly:

```bash
env | grep OIDC
# Expected: OIDC_ENABLED, OIDC_ISSUER_URL, OIDC_CLIENT_ID
# OIDC_CLIENT_SECRET is a SecretStr — should not be visible in clear text logs
```

---

## Recovery Procedures

### Scenario A: IdP was temporarily down during startup

1. Confirm IdP is now reachable (Step 1 above).
2. Restart the application container:
   ```bash
   docker restart <container>
   # or in Kubernetes
   kubectl rollout restart deployment/conclave-engine
   ```
3. Monitor startup logs to confirm successful OIDC initialization.

### Scenario B: IdP is down during operation

- Existing JWT-authenticated users are unaffected.
- New OIDC logins will fail at the callback step (503).
- **Fallback option**: Use passphrase auth while IdP recovers.

#### Enabling passphrase auth fallback

Passphrase auth (`POST /auth/token`) is always available when the application
is running, even when OIDC is configured. Users with a passphrase can continue
to authenticate. To provision users for passphrase auth:

```bash
# Provision or update operator credentials
# (requires OPERATOR_CREDENTIALS_HASH to be set to a valid bcrypt hash)
export OPERATOR_CREDENTIALS_HASH=$(python3 -c "import bcrypt; print(bcrypt.hashpw(b'<passphrase>', bcrypt.gensalt()).decode())")
```

### Scenario C: JWKS key rotation at IdP

The application caches JWKS at boot time. If the IdP rotates its signing keys:

1. Existing tokens signed with the OLD key will fail verification (401).
2. **Resolution**: Restart the application to fetch the new JWKS:
   ```bash
   docker restart <container>
   ```
3. After restart, new OIDC logins will work with the new signing key.
4. Users with old tokens must re-authenticate via OIDC.

**Note**: There is no hot JWKS reload — this is an accepted limitation at
Tier 8 (ADR-0067). Key rotation in production must be coordinated with a
maintenance window or rolling restart.

### Scenario D: Manual user provisioning when IdP is down

If a user needs access and the IdP is unavailable:

1. Connect to the database.
2. Insert a user record manually:
   ```sql
   INSERT INTO users (id, org_id, email, role, created_at, updated_at)
   VALUES (
     gen_random_uuid(),
     '<org_uuid>',
     'user@example.com',
     'operator',
     NOW(),
     NOW()
   );
   ```
3. Issue a passphrase token via `POST /auth/token` (if operator credentials are configured).

---

## Key Rotation Procedure

Key rotation at the IdP invalidates all currently cached JWKS and all
outstanding tokens signed with the old key.

**Steps**:
1. Notify users that active sessions will be invalidated.
2. Rotate the signing key at the IdP.
3. Restart Conclave Engine to fetch the new JWKS:
   ```bash
   kubectl rollout restart deployment/conclave-engine
   ```
4. Users must re-authenticate via OIDC after restart.

**Optional**: Before rotating, flush all active Redis sessions to force
re-authentication:
```bash
redis-cli -u $REDIS_URL SCAN 0 MATCH "conclave:session:*" COUNT 1000
# Delete all returned keys
```

---

## Configuration Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `OIDC_ENABLED` | When using SSO | `false` | Enable OIDC authentication |
| `OIDC_ISSUER_URL` | When OIDC enabled | — | IdP issuer URL |
| `OIDC_CLIENT_ID` | When OIDC enabled | — | Registered client ID |
| `OIDC_CLIENT_SECRET` | When OIDC enabled | — | Registered client secret |
| `OIDC_STATE_TTL_SECONDS` | No | `600` | State/PKCE Redis TTL (60-3600s) |
| `SESSION_TTL_SECONDS` | No | `28800` | Session Redis TTL (min 60s) |
| `CONCURRENT_SESSION_LIMIT` | No | `3` | Max sessions per user (min 1) |
| `OIDC_DEFAULT_ORG_ID` | Multi-tenant + OIDC | — | Default org for new users |
| `REDIS_URL` | When OIDC enabled | `redis://redis:6379/0` | Redis connection URL |

---

## Security Notes

- The OIDC issuer URL is validated against SSRF rules at startup.
  Cloud metadata endpoints (169.254.169.254, etc.) are always blocked.
  RFC-1918 addresses are allowed for air-gap IdPs.
- Role claims from the IdP are ALWAYS ignored. User roles come from the
  local database only (ADR-0067 Decision 10).
- A new user's default role is `operator` (lowest privilege). Admins must
  explicitly elevate users who need higher permissions.
