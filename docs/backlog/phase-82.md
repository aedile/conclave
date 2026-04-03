# Phase 82 — API Key Management

**Tier**: 8 (Enterprise Scale)
**Goal**: Provide programmatic access for data pipelines, CI/CD integrations, and agent
workflows via scoped, rotatable API keys.

**Dependencies**: Phase 80 (RBAC — API keys inherit permission scopes)

---

## Context & Constraints

- Current auth: JWT via passphrase or OIDC. No programmatic API key option.
- API keys are needed for: automated synthesis pipelines, CI/CD integration, monitoring
  agents, webhook signature verification.
- Keys must be scoped to specific permissions (not full admin access by default).
- Key rotation must support a grace period where both old and new keys work.
- Keys are per-user, per-org. One user can have multiple keys with different scopes.
- Keys must be stored hashed (not plaintext) in the database. Only shown once on creation.

---

## Tasks

### T82.1 — API Key Model

**Files to create/modify**:
- `src/synth_engine/shared/models/api_key.py` (new)
- Alembic migration for `api_keys` table
- `bootstrapper/dependencies/auth.py` (accept API key in `Authorization: Bearer` header)

**Acceptance Criteria**:
- [ ] `ApiKey` model: `id`, `org_id`, `user_id`, `name`, `key_hash` (SHA-256), `prefix` (first 8 chars for identification), `scopes` (list of permission strings), `expires_at`, `created_at`, `last_used_at`, `revoked_at`
- [ ] Key format: `conclave_<random 32 bytes hex>` (recognizable prefix)
- [ ] Key stored as SHA-256 hash only — plaintext returned once on creation, never retrievable
- [ ] Auth middleware accepts API key in same `Authorization: Bearer` header as JWT
- [ ] API key auth populates same `(org_id, user_id, role)` context as JWT auth

### T82.2 — Key Lifecycle Endpoints

**Files to create**:
- `bootstrapper/routers/api_keys.py` (new)
- `bootstrapper/schemas/api_keys.py` (new)

**Acceptance Criteria**:
- [ ] `POST /api/v1/api-keys` — create key (returns plaintext ONCE, operator+admin)
- [ ] `GET /api/v1/api-keys` — list keys (shows prefix, name, scopes, expiry — never plaintext)
- [ ] `DELETE /api/v1/api-keys/{key_id}` — revoke key (immediate, operator+admin)
- [ ] `POST /api/v1/api-keys/{key_id}/rotate` — create new key, old key enters grace period
- [ ] Scopes on creation limited to user's own permissions (cannot escalate)
- [ ] Maximum 10 active keys per user (configurable)

### T82.3 — Key Rotation with Grace Period

**Files to modify**:
- `bootstrapper/routers/api_keys.py`
- `bootstrapper/dependencies/auth.py`

**Acceptance Criteria**:
- [ ] Rotation creates new key and sets `grace_period_end` on old key (default: 24 hours)
- [ ] Both old and new keys work during grace period
- [ ] Old key auto-revokes after grace period (background task or TTL)
- [ ] Audit event logged for rotation, grace period start, and auto-revocation
- [ ] Force-revoke skips grace period (admin only)

### T82.4 — Per-Key Rate Limiting

**Files to modify**:
- `bootstrapper/dependencies/rate_limit.py`
- `shared/settings.py`

**Acceptance Criteria**:
- [ ] Rate limit bucket per API key (separate from per-IP limits)
- [ ] Default: 100 requests/minute per key (configurable per org)
- [ ] Rate limit headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- [ ] Exceeded → 429 with Retry-After header
- [ ] Admin keys exempt from per-key limits (still subject to global limits)

---

## Testing & Quality Gates

- Attack tests: key with `jobs:read` scope attempts `jobs:create` (403)
- Attack tests: revoked key returns 401
- Attack tests: expired key returns 401
- Integration tests: full create → use → rotate → grace period → revoke lifecycle
- Key hash verification: plaintext key works, modified key doesn't
