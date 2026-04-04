# Phase 79 — Multi-Tenancy Foundation

**Tier**: 8 (Enterprise Scale)
**Goal**: Transform the single-operator model (ADR-0062) into a multi-tenant system with
full data isolation between organizations.

**Dependencies**: None (foundational for all Tier 8 work)

---

## Prerequisites (must complete before T79.1)

### T79.0 — JWT Identity Architecture ADR

The multi-tenancy design requires a decision that propagates to Phases 80-82 and 86:
**How does the system resolve `(org_id, user_id, role)` from a JWT?**

Option A: Embed `org_id`, `user_id`, and `role` in JWT claims. Pros: no DB round-trip per
request. Cons: org/role changes require token revocation and reissue.

Option B: JWT contains only `sub` (user_id); `get_current_user` does a DB lookup per request
to resolve `(org_id, role)`. Pros: role/org changes take effect immediately. Cons: DB
round-trip on every authenticated request; requires caching strategy.

This ADR MUST be written and approved before any implementation begins. It affects P80
(role encoding), P81 (OIDC token mapping), P82 (API key auth context), and P86 (multi-pod
token validation).

### T79.0b — Create `shared/models/` subpackage

The `shared/models/` directory does not exist. P79, P80, and P82 all place table models there.

- [ ] Create `src/synth_engine/shared/models/__init__.py`
- [ ] Update `alembic/env.py` to discover models from `shared/models/` in addition to
      existing locations (`bootstrapper/schemas/`, `modules/synthesizer/jobs/`)
- [ ] Verify `alembic autogenerate` detects new models in `shared/models/`

---

## Context & Constraints

- ADR-0062 explicitly documents the single-operator assumption. This phase supersedes it.
- ADR-0040 (IDOR ownership model) defines `owner_id` as a JWT `sub` string, not a FK.
  This phase changes the identity model fundamentally — ADR-0040 must be superseded.
- The privacy ledger (`modules/privacy/ledger.py`) currently has no `org_id` filtering.
- All DB queries in routers filter by `owner_id` (JWT `sub` claim). This must become
  `org_id + user_id` to support multiple users within one organization.
- Connection pooling must be tenant-aware to prevent connection exhaustion by one tenant
  affecting others.
- Migration must be backward-compatible: existing single-operator deployments must continue
  to work without reconfiguration (default org).
- Tenant isolation is application-level (`org_id` FK filtering), not database-level
  (PostgreSQL RLS). Register this as assumption A-014 in `docs/ASSUMPTIONS.md`.
- The `EPSILON_SPENT_TOTAL` Prometheus counter currently uses `(job_id, dataset_id)` labels.
  After T79.4, this counter needs `org_id` or it provides no per-org observability. Address
  cardinality impact in T79.4.

---

## Tasks

### T79.1 — Tenant & Organization Model

**User Story**: As a platform operator, I need to create organizations so that multiple
teams can use the system with isolated data.

**Files to create/modify**:
- `src/synth_engine/shared/models/organization.py` (new)
- `src/synth_engine/shared/models/user.py` (new)
- Alembic migration for `organizations` and `users` tables

**Acceptance Criteria**:
- [ ] `Organization` model: `id`, `name`, `created_at`, `settings` (JSON)
- [ ] `User` model: `id`, `org_id` (FK to Organization), `email`, `role`, `created_at`
- [ ] Both models extend `BaseModel` (UUID PK, auto-discovered by Alembic via `BaseModel.metadata`)
- [ ] Default organization created idempotently in migration (upsert, not insert)
- [ ] Default org uses reserved UUID `00000000-0000-0000-0000-000000000000`; default user
      uses reserved UUID `00000000-0000-0000-0000-000000000001` — cannot collide with UUIDv4
- [ ] Migration is structurally reversible but acknowledged as data-lossy on downgrade
      (original `owner_id` varchar values are not preserved in shadow columns)
- [ ] Existing data migrated to default org: each distinct `owner_id` value creates a
      default user in the default org; existing `Connection` and `SynthesisJob` rows
      get the default org's `org_id`
- [ ] ADR-0040 (IDOR ownership) superseded with new ADR documenting `org_id + user_id` model
- [ ] ADR-0062 (single-operator) superseded
- [ ] `alembic autogenerate` detects new models in `shared/models/` (test required)
- [ ] Migration documents maintenance window requirement (ATTACK-07: race window)

**Implementation note**: This is the most complex Alembic migration in the project's history.
It must create tables, backfill data, add FK columns, and set defaults — all reversibly.
T79.2, T79.3, and T79.4 have a strict sequential dependency on T79.1 completing.

### T79.2 — Tenant-Scoped Queries

**User Story**: As a user in Organization A, I must never see data belonging to Organization B.

**Files to modify**:
- All router files in `bootstrapper/routers/`
- `bootstrapper/dependencies/auth.py`
- `shared/db.py` (specify: no per-tenant connection pools or schemas at this stage;
  tenant scoping is query-level `WHERE org_id = :org_id` only)

**Acceptance Criteria**:
- [ ] `get_current_operator` replaced with `get_current_user` returning `(org_id, user_id)`
      (JWT-embedded claims with ≤900s expiry per ADR T79.0 Option C)
- [ ] `get_current_user` pass-through sentinel returns default org UUID + default user UUID
- [ ] Every DB query in every router filters by `org_id`
- [ ] Connection model gains `org_id` FK
- [ ] Job model gains `org_id` FK
- [ ] WebhookRegistration gains `org_id` FK; limit enforcement scoped per-org
- [ ] No query anywhere returns data across org boundaries
- [ ] Huey `run_synthesis_job` resolves org via `SynthesisJob.org_id` FK and passes to
      `spend_budget()` — validates `job.org_id == ledger.org_id` before spending
- [ ] `OrphanTaskReaper` audit events include correct `org_id` from reaped job
- [ ] `DELETE /compliance/erasure` scoped to requesting user's org; cross-org erasure
      returns 404 (not 403)
- [ ] Settings table remains intentionally global (deployment-wide config, not per-tenant
      data) — documented in ADR
- [ ] Tenant-aware connection pooling via application semaphore: per-org connection limits
      to prevent one tenant exhausting the pool (integration test required — not mocks)
- [ ] `X-Org-ID` or similar header spoofing cannot override JWT-derived org_id
- [ ] Pagination cursors scoped by org_id (cursor from Org A replayed under Org B returns
      only Org B data)

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
- [ ] Test: JWT with forged `org_id` claim is rejected (signature verification prevents tampering)
- [ ] Test: HTTP header spoofing attempt (`X-Org-ID` or similar) cannot override JWT-derived org_id
- [ ] Test: Pagination cursor/offset token from Org A's session replayed under Org B's session
      returns only Org B's data (no cross-tenant cursor leakage)
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
- [ ] `PrivacyTransaction` gains `org_id` FK as defense-in-depth (no route may query
      transactions without `ledger_id` filter — AC + test required)
- [ ] `spend_budget()` and `reset_budget()` filter by `org_id`
- [ ] Each org has independent epsilon allocation
- [ ] Migration carries existing ledger allocation to default org (CONFIG-02: zero epsilon)
- [ ] New orgs created with configurable default epsilon allocation (CONFIG-03)
- [ ] `GET /api/v1/privacy/budget` returns only the requesting org's budget
- [ ] `EPSILON_SPENT_TOTAL` Prometheus counter updated with `org_id` label;
      cardinality impact documented (bounded by number of orgs, not unbounded);
      org creation rate-limited to prevent cardinality DoS (ATTACK-05)
- [ ] Update `docs/ASSUMPTIONS.md` with A-014 (application-level tenant isolation assumption)
- [ ] `jwt_expiry_seconds` validated ≤ 900 in multi-tenant mode (CONFIG-01)

---

## Testing & Quality Gates

- Integration tests against real PostgreSQL with pytest-postgresql
- Tenant isolation tests are BLOCKER — no merge without them
- Tenant-aware connection pooling integration test required (not satisfiable by mocks)
- Migration tested forward and backward
- Existing single-operator tests must continue to pass (backward compatibility)
- Integration tests must reset state between tests via fixture scope — no cross-test
  database state leakage
