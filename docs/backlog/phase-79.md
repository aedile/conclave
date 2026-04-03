# Phase 79 — Multi-Tenancy Foundation

**Tier**: 8 (Enterprise Scale)
**Goal**: Transform the single-operator model (ADR-0062) into a multi-tenant system with
full data isolation between organizations.

**Dependencies**: None (foundational for all Tier 8 work)

---

## Context & Constraints

- ADR-0062 explicitly documents the single-operator assumption. This phase supersedes it.
- The privacy ledger (`modules/privacy/ledger.py`) currently has no `org_id` filtering.
- All DB queries in routers filter by `owner_id` (JWT `sub` claim). This must become
  `org_id + user_id` to support multiple users within one organization.
- Connection pooling must be tenant-aware to prevent connection exhaustion by one tenant
  affecting others.
- Migration must be backward-compatible: existing single-operator deployments must continue
  to work without reconfiguration (default org).

---

## Tasks

### T79.1 — Tenant & Organization Model

**User Story**: As a platform operator, I need to create organizations so that multiple
teams can use the system with isolated data.

**Files to create/modify**:
- `src/synth_engine/shared/models/organization.py` (new)
- `src/synth_engine/shared/models/user.py` (new or modify existing)
- Alembic migration for `organizations` and `users` tables

**Acceptance Criteria**:
- [ ] `Organization` model: `id`, `name`, `created_at`, `settings` (JSON)
- [ ] `User` model: `id`, `org_id` (FK to Organization), `email`, `role`, `created_at`
- [ ] Default organization created on first boot (backward compatibility)
- [ ] Migration is reversible
- [ ] Existing data migrated to default org

### T79.2 — Tenant-Scoped Queries

**User Story**: As a user in Organization A, I must never see data belonging to Organization B.

**Files to modify**:
- All router files in `bootstrapper/routers/`
- `bootstrapper/dependencies/auth.py`
- `shared/db.py`

**Acceptance Criteria**:
- [ ] `get_current_operator` replaced with `get_current_user` returning `(org_id, user_id)`
- [ ] Every DB query in every router filters by `org_id`
- [ ] Connection model gains `org_id` FK
- [ ] Job model gains `org_id` FK
- [ ] No query anywhere returns data across org boundaries

### T79.3 — Tenant Isolation Tests

**User Story**: As a security auditor, I need proof that tenant isolation holds under adversarial conditions.

**Files to create**:
- `tests/integration/test_tenant_isolation.py`

**Acceptance Criteria**:
- [ ] Test: User in Org A creates a connection; User in Org B cannot see it (404, not 403)
- [ ] Test: User in Org A creates a job; User in Org B cannot see, cancel, or download it
- [ ] Test: User in Org A cannot access Org B's privacy ledger or epsilon budget
- [ ] Test: User in Org A cannot enumerate Org B's connections via pagination
- [ ] Test: SQL injection attempt in org_id path parameter is rejected
- [ ] All isolation tests are integration tests against real PostgreSQL (not mocks)

### T79.4 — Per-Tenant Privacy Ledger

**User Story**: As a privacy officer, each organization needs its own epsilon budget so
one team's synthesis work doesn't consume another team's privacy allocation.

**Files to modify**:
- `modules/privacy/ledger.py`
- `modules/privacy/accountant.py`
- `bootstrapper/routers/privacy.py`

**Acceptance Criteria**:
- [ ] `PrivacyLedger` model gains `org_id` FK
- [ ] `spend_budget()` and `reset_budget()` filter by `org_id`
- [ ] Each org has independent epsilon allocation
- [ ] `GET /api/v1/privacy/budget` returns only the requesting org's budget
- [ ] ADR-0062 amended to document the multi-tenant model

---

## Testing & Quality Gates

- Integration tests against real PostgreSQL with pytest-postgresql
- Tenant isolation tests are BLOCKER — no merge without them
- Migration tested forward and backward
- Existing single-operator tests must continue to pass (backward compatibility)
