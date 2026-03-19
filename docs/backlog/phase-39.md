# Phase 39 — Authentication, Authorization & Rate Limiting (P0 Security)

**Goal**: Close the three critical production blockers identified in the 2026-03-19
Security Audit: no authentication layer, no authorization/IDOR protection, and no
rate limiting. These are non-negotiable before any deployment beyond localhost.

**Prerequisite**: Phase 38 merged. Zero open advisories.

**ADR**: ADR-0039 — Authentication & Authorization Architecture (new, required).
Must document: chosen auth mechanism (JWT vs mTLS vs API key), tenant model,
token lifecycle, and middleware ordering relative to vault/license gates.

**Source**: Production Readiness Audit, 2026-03-19 — Critical Issues C1, C2, C3.

---

## T39.1 — Add Authentication Middleware (JWT Bearer Token)

**Priority**: P0 — Security. No endpoint currently requires identity verification.
Any network-adjacent client can access all resources after vault unseal + license gate.

### Context & Constraints

1. The application has two existing gate middlewares: vault-sealed gate (423) and
   license gate (402). Authentication must slot AFTER these gates but BEFORE any
   resource access. Middleware ordering in `bootstrapper/middleware.py` must be
   updated to reflect: RequestBodyLimit → CSP → VaultSealGate → LicenseGate →
   **AuthenticationGate** → routes.

2. JWT is the recommended mechanism because:
   - The system already depends on `PyJWT >=2.10.0,<3.0.0`
   - Operator-facing API (not end-user-facing) — short-lived tokens are appropriate
   - Vault KEK can derive a JWT signing key, keeping key management centralized

3. The `/unseal` endpoint and `/health` endpoint MUST remain unauthenticated (they
   are pre-auth by definition). All other endpoints require a valid JWT.

4. Token claims must include at minimum: `sub` (operator ID), `exp` (expiry),
   `iat` (issued-at), and `scope` (permissions list).

5. A `/auth/token` endpoint must be created to issue tokens. For MVP, this can
   accept operator credentials (username + passphrase) validated against a
   configurable operator registry (environment variable or settings model).

6. Algorithm confusion attacks must be prevented: the JWT decoder must enforce
   a specific algorithm (HS256 with vault-derived key, or RS256 with operator-
   provided public key). Never accept `alg: none`.

### Acceptance Criteria

1. All endpoints except `/unseal`, `/health`, and `/auth/token` require a valid
   JWT bearer token in the `Authorization` header.
2. Requests without a token receive 401 Unauthorized (RFC 7807 format).
3. Requests with an expired or malformed token receive 401 Unauthorized.
4. JWT algorithm is pinned — `alg: none` and algorithm confusion are rejected.
5. New `ConclaveSettings` fields: `jwt_algorithm`, `jwt_expiry_seconds`,
   `operator_credentials_hash` (bcrypt hash of operator passphrase).
6. ADR-0039 documents the authentication architecture.
7. New tests: unauthenticated request → 401, expired token → 401, valid token → 200,
   algorithm confusion → 401, `/unseal` without token → allowed.
8. Full gate suite passes.

### Files to Create/Modify

- Create: `src/synth_engine/bootstrapper/dependencies/auth.py`
- Create: `src/synth_engine/bootstrapper/routers/auth.py`
- Modify: `src/synth_engine/bootstrapper/middleware.py` (add auth gate)
- Modify: `src/synth_engine/bootstrapper/router_registry.py` (register auth router)
- Modify: `src/synth_engine/shared/settings.py` (add JWT settings)
- Create: `docs/adr/ADR-0039-authentication-authorization.md`
- Create: `tests/unit/test_auth.py`
- Create: `tests/integration/test_auth_middleware.py`

---

## T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints

**Priority**: P0 — Security. `session.get(SynthesisJob, job_id)` with no ownership
check allows horizontal privilege escalation.

### Context & Constraints

1. Every resource endpoint currently retrieves objects by primary key with no
   tenant/owner filtering:
   - `routers/jobs.py:134` — `GET /jobs/{job_id}`
   - `routers/jobs.py:162` — `POST /jobs/{job_id}/start`
   - `routers/jobs.py:205` — `POST /jobs/{job_id}/shred`
   - `routers/connections.py:80-133` — `GET/DELETE /connections/{connection_id}`
   - `routers/jobs_streaming.py` — artifact download

2. Fix: Extract the authenticated operator's `sub` claim from the JWT (via a
   FastAPI `Depends` on the auth dependency from T39.1). Add an `owner_id`
   column to `SynthesisJob` and `Connection` models. All queries must filter
   by `owner_id == current_operator.sub`.

3. For the MVP, if the system is single-operator (one set of credentials),
   the `owner_id` can be a fixed value from the JWT `sub` claim. The important
   thing is that the authorization check EXISTS and is wired, so multi-tenant
   support is a configuration change, not a code change.

4. Requests accessing resources owned by a different operator must receive
   404 Not Found (not 403 Forbidden — to prevent enumeration).

5. Database migration required: add `owner_id VARCHAR` column to `synthesis_job`
   and `connection` tables.

### Acceptance Criteria

1. All resource endpoints filter by `owner_id` from JWT `sub` claim.
2. Accessing another operator's job returns 404 (not 403).
3. New `owner_id` column on `SynthesisJob` and `Connection` models.
4. Migration script for existing data (backfill `owner_id` from settings).
5. New tests: operator A cannot access operator B's job, operator A can access
   own job, unauthenticated access returns 401.
6. IDOR test: sequential ID enumeration returns 404 for non-owned resources.
7. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/bootstrapper/schemas/connections.py` (add owner_id)
- Modify: `src/synth_engine/modules/synthesizer/job_models.py` (add owner_id)
- Modify: `src/synth_engine/bootstrapper/routers/jobs.py` (filter by owner)
- Modify: `src/synth_engine/bootstrapper/routers/connections.py` (filter by owner)
- Modify: `src/synth_engine/bootstrapper/routers/jobs_streaming.py` (filter by owner)
- Create: `tests/unit/test_authorization.py`
- Create: `tests/integration/test_idor_protection.py`

---

## T39.3 — Add Rate Limiting Middleware

**Priority**: P0 — Security. No request throttling exists. Vault unseal,
job creation, and artifact download are all unbounded.

### Context & Constraints

1. Rate limiting should be implemented at the application layer (not relying
   solely on reverse proxy) because:
   - The application knows the authenticated operator identity (per-user limits)
   - Vault unseal is a high-value target needing per-IP limiting
   - Defense-in-depth: proxy rate limiting is additive, not a substitute

2. Recommended approach: `slowapi` library (built on `limits`, integrates with
   FastAPI). Alternative: custom middleware using `limits` directly.

3. Rate limit tiers:
   - `/unseal`: 5 requests/minute per IP (brute-force protection)
   - `/auth/token`: 10 requests/minute per IP
   - All other endpoints: 60 requests/minute per authenticated operator
   - `/jobs/{id}/download`: 10 requests/minute per operator (bandwidth protection)

4. Rate limit responses must use RFC 7807 format with `Retry-After` header.

5. Rate limit configuration must be in `ConclaveSettings` (not hardcoded).

6. If Rule 6 (technology substitution) applies and `slowapi` is rejected,
   an ADR must document the alternative.

### Acceptance Criteria

1. Rate limiting middleware active on all endpoints.
2. Exceeding rate limit returns 429 Too Many Requests (RFC 7807 format)
   with `Retry-After` header.
3. `/unseal` limited to 5/min per IP.
4. Authenticated endpoints limited to 60/min per operator.
5. Rate limit values configurable via `ConclaveSettings`.
6. New tests: exceed rate limit → 429, within limit → 200, different
   operators have independent limits.
7. Full gate suite passes.

### Files to Create/Modify

- Create: `src/synth_engine/bootstrapper/dependencies/rate_limit.py`
- Modify: `src/synth_engine/bootstrapper/middleware.py` (add rate limit layer)
- Modify: `src/synth_engine/shared/settings.py` (add rate limit settings)
- Modify: `pyproject.toml` (add `slowapi` or chosen dependency)
- Create: `tests/unit/test_rate_limiting.py`
- Create: `tests/integration/test_rate_limit_middleware.py`

---

## T39.4 — Encrypt Connection Metadata with ALE

**Priority**: P1 — Security. `host`, `database`, `schema_name` stored in
plaintext in `connections` table. Database compromise exposes source system topology.

### Context & Constraints

1. `bootstrapper/schemas/connections.py:31-59` stores connection metadata
   without encryption. The ALE (Application-Level Encryption) infrastructure
   already exists in `shared/security/ale.py` with `EncryptedString` TypeDecorator.

2. Fix: Apply `EncryptedString` to `host`, `database`, and `schema_name` columns.

3. Migration required: encrypt existing plaintext values in-place.

4. The `port` field (integer) does not need encryption — it's not sensitive.

5. ALE encryption requires the vault to be unsealed. Connection creation/listing
   while vault is sealed should return 423 (already handled by vault gate middleware).

### Acceptance Criteria

1. `host`, `database`, `schema_name` columns use `EncryptedString` TypeDecorator.
2. Values are encrypted at rest in the database.
3. Migration encrypts existing plaintext values.
4. Connection list/detail endpoints return decrypted values transparently.
5. New test: verify raw database value is encrypted (not plaintext).
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/bootstrapper/schemas/connections.py`
- Create: migration script for encrypting existing data
- Create: `tests/unit/test_connection_encryption.py`

---

## Task Execution Order

```
T39.1 (Authentication) ──────────> sequential (auth before authz)
T39.2 (Authorization/IDOR) ──────> after T39.1 (depends on JWT identity)
T39.3 (Rate Limiting) ───────────> parallel with T39.2 (independent middleware)
T39.4 (Connection Encryption) ───> parallel with T39.2/T39.3 (independent)
```

T39.1 must complete first (T39.2 depends on JWT `sub` claim for ownership).

---

## Phase 39 Exit Criteria

1. All endpoints (except /unseal, /health, /auth/token) require valid JWT.
2. All resource endpoints enforce owner_id filtering — IDOR eliminated.
3. Rate limiting active on all endpoints with configurable thresholds.
4. Connection metadata encrypted at rest via ALE.
5. ADR-0039 documents authentication & authorization architecture.
6. All quality gates pass.
7. Zero open advisories in RETRO_LOG.
8. Review agents pass for all tasks.
