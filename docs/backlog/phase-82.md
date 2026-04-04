# Phase 82 — API Key Management

**Tier**: 8 (Enterprise Scale)
**Goal**: Provide programmatic access for data pipelines, CI/CD integrations, and agent
workflows via scoped, rotatable API keys.

**Dependencies**: Phase 80 (RBAC — API keys inherit permission scopes)

---

## Context & Constraints

- Current auth: JWT via passphrase or OIDC. No programmatic API key option.
- API keys are needed for: automated synthesis pipelines, CI/CD integration, monitoring
  agents, webhook signature verification (outbound webhook signing — the system sends
  webhooks, it does not receive them; API keys are not used for inbound webhook verification).
- Keys must be scoped to specific permissions (not full admin access by default).
- Key rotation must support a grace period where both old and new keys work.
- Keys are per-user, per-org. One user can have multiple keys with different scopes.
- Keys must be stored hashed (SHA-256, no per-key salt — collision resistance is sufficient
  at 256 bits; register as assumption A-016 in `docs/ASSUMPTIONS.md`).
  Only shown once on creation — no recovery path; lost keys require rotation.
- **Auth disambiguation**: The `Authorization: Bearer` header carries either a JWT or an
  API key. Distinguish by format: JWT has three `.`-separated base64url segments; API keys
  use `conclave_<hex>` prefix. Try format detection first (O(1)), not JWT-decode-then-fallback
  (which creates a timing oracle: failed JWT decode is faster than DB key lookup).
- **Grace period mechanism**: Use read-time TTL check (not background Huey task). On each
  API key auth, check `grace_period_end` — if past, treat key as revoked. No background
  job needed, no multi-pod coordination issue. Periodic cleanup job removes expired rows
  from the `api_keys` table (non-critical, cosmetic).
- **Rate limit ordering**: Auth middleware resolves identity first (including role from
  API key scopes). Rate limit middleware runs after auth, using the resolved key ID as
  the bucket identifier. Admin-scoped keys are exempt from per-key limits but subject
  to global IP-based limits.

---

## Tasks

### T82.1 — API Key Model

**Files to create/modify**:
- `src/synth_engine/shared/models/api_key.py` (new — in `shared/models/` per P79.0b;
  consistent with Organization and User model placement)
- Alembic migration for `api_keys` table
- `bootstrapper/dependencies/auth.py` (accept API key in `Authorization: Bearer` header)

**Acceptance Criteria**:
- [ ] `ApiKey` model: `id`, `org_id`, `user_id`, `name`, `key_hash` (SHA-256), `prefix`
      (first 8 chars for identification), `scopes` (list of permission strings),
      `expires_at`, `created_at`, `last_used_at`, `revoked_at`, `grace_period_end`
- [ ] Key format: `conclave_<random 32 bytes hex>` (recognizable prefix)
- [ ] Key stored as SHA-256 hash only — plaintext returned once on creation, never retrievable
- [ ] Auth middleware: detect format first (`conclave_` prefix → key lookup; three-dot
      pattern → JWT decode). No timing oracle from format detection.
- [ ] API key auth populates same `(org_id, user_id, role)` context as JWT auth
- [ ] Update `alembic/env.py` if not already discovering `shared/models/`
- [ ] Update `docs/ASSUMPTIONS.md` with A-016 (SHA-256 no-salt collision resistance)

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
- [ ] Maximum 10 active keys per user (configurable). 11th creation returns 409 Conflict
      with current count and limit in error body. Test required.
- [ ] `.env.example` updated with API key config variables

### T82.3 — Key Rotation with Grace Period

**Files to modify**:
- `bootstrapper/routers/api_keys.py`
- `bootstrapper/dependencies/auth.py`

**Acceptance Criteria**:
- [ ] Rotation creates new key and sets `grace_period_end` on old key (default: 24 hours)
- [ ] Both old and new keys work during grace period
- [ ] Old key auto-revokes after grace period (read-time TTL check, not background task)
- [ ] Audit event logged for rotation, grace period start, and auto-revocation
- [ ] Force-revoke skips grace period (admin only; operator/viewer attempting force-revoke
      returns 403). Attack test required.
- [ ] Auto-revocation failure (if cleanup job fails) increments
      `conclave_api_key_auto_revoke_failures_total` Prometheus counter

### T82.4 — Per-Key Rate Limiting

**Files to modify**:
- `bootstrapper/dependencies/rate_limit.py`
- `shared/settings.py`

**Acceptance Criteria**:
- [ ] Rate limit bucket per API key (separate from per-IP limits)
- [ ] Default: 100 requests/minute per key (configurable per org)
- [ ] Rate limit headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- [ ] Exceeded → 429 with Retry-After header
- [ ] Admin-scoped keys exempt from per-key limits (still subject to global IP limits).
      Exemption based on resolved scope, not on a forgeable claim — verify the key's
      scopes include `admin:*` after auth resolution.
- [ ] `.env.example` updated with rate limit config variables

---

## Testing & Quality Gates

- Attack tests: key with `jobs:read` scope attempts `jobs:create` (403)
- Attack tests: revoked key returns 401
- Attack tests: expired key returns 401
- Attack tests: operator/viewer attempts force-revoke (403)
- Attack tests: 11th key creation at limit (409 with count/limit)
- Integration tests: full create → use → rotate → grace period → revoke lifecycle
  against real Redis (grace period TTL relies on Redis; mocked TTL is not equivalent)
- Key hash verification: plaintext key works, modified key doesn't
- Timing test: format detection path (conclave_ prefix vs JWT three-dot) does not
  create measurable timing difference
