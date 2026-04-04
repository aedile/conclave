# Phase 79 — Developer Brief: Multi-Tenancy Foundation

**Branch**: `feat/P79-multi-tenancy-foundation`
**Spec**: `docs/backlog/phase-79.md` (amended with spec-challenger findings)
**Spec Challenge Results**: `docs/backlog/phase-79-spec-challenge.md`

---

## PM Architectural Decisions

These decisions are final. Do NOT substitute technologies or approaches without an ADR.

### ADR T79.0 — JWT Identity Architecture: Option C (Short-Lived Embed)

Embed `org_id`, `user_id`, and `role` in JWT claims with `jwt_expiry_seconds ≤ 900`.

**Rationale**: No DB round-trip per request (Option B's cost), minimal staleness window
(Option A's risk capped at 15 minutes). Token revocation not required at this tier —
revisit in P86 (multi-pod validation).

**Implementation**:
- `get_current_user(request) -> TenantContext` replaces `get_current_operator(request) -> str`
- `TenantContext` is a frozen dataclass: `org_id: str`, `user_id: str`, `role: str`
- JWT payload adds `org_id` and `role` claims alongside existing `sub` (now `user_id`)
- `verify_token()` requires `org_id` claim in multi-tenant mode
- Pydantic validator on `ConclaveSettings.jwt_expiry_seconds`: `le=900` when multi-tenant enabled
- Pass-through (non-production, no JWT secret): returns sentinel `TenantContext` with
  `org_id="00000000-0000-0000-0000-000000000000"`,
  `user_id="00000000-0000-0000-0000-000000000001"`, `role="admin"`

### Default Org Creation: Alembic Migration Seed

Idempotent `INSERT ... ON CONFLICT DO NOTHING` in migration 009's `upgrade()`.
Default org UUID: `00000000-0000-0000-0000-000000000000`.
Default user UUID: `00000000-0000-0000-0000-000000000001`.

### Migration Reversibility: Data-Lossy Downgrade

Down migration drops `org_id` columns and `organizations`/`users` tables.
Original `owner_id` varchar values are NOT preserved in shadow columns.
Document this explicitly in the migration docstring and ADR.

### Settings Table: Intentionally Global

`Setting` model remains global (no `org_id` FK). Settings are deployment-wide configuration.
Document in ADR that per-org settings are a P80+ concern if needed.

### Webhook Limit: Per-Org

`webhook_max_registrations` limit scoped per-org (not per-user). Change
`_count_active_registrations()` to filter by `org_id` instead of `owner_id`.

### Connection Pooling: Application Semaphore

`asyncio.Semaphore` (or threading equivalent) keyed by `org_id`, limiting concurrent
DB connections per org. NOT per-org engine instances, NOT PgBouncer.
Store in a module-level dict: `_org_semaphores: dict[str, asyncio.Semaphore]`.
Default limit from `ConclaveSettings.per_org_max_connections` (default: 5).

---

## Task Execution Order

Strict sequential — each task depends on the previous.

### T79.0b — Create `shared/models/` Subpackage

**Files to create**:
- `src/synth_engine/shared/models/__init__.py`

**Files to modify**:
- `alembic/env.py` — add import for `shared.models` so autogenerate discovers new models

**Tests**:
- `tests/unit/test_alembic_model_discovery.py::test_alembic_autogenerate_detects_shared_models`

### T79.0 — JWT Identity Architecture ADR

**Files to create**:
- `docs/adr/ADR-0065-multi-tenant-jwt-identity.md` — supersedes ADR-0040 and ADR-0062
  - Documents Option C decision, sentinel UUIDs, settings scoping, migration strategy

### T79.1 — Tenant & Organization Model

**Files to create**:
- `src/synth_engine/shared/models/organization.py` — `Organization(BaseModel, table=True)`
  - `id: str` (UUID, PK from BaseModel), `name: str`, `created_at: datetime`, `settings: str` (JSON)
- `src/synth_engine/shared/models/user.py` — `User(BaseModel, table=True)`
  - `id: str` (UUID, PK from BaseModel), `org_id: str` (FK → Organization), `email: str`,
    `role: str`, `created_at: datetime`
- `alembic/versions/009_multi_tenancy_foundation.py`

**Migration 009 must**:
1. Create `organizations` table
2. Create `users` table
3. Seed default org (UUID `00000000-0000-0000-0000-000000000000`) — idempotent
4. Seed default user (UUID `00000000-0000-0000-0000-000000000001`) in default org — idempotent
5. Add `org_id` column to `connection`, `synthesis_job`, `webhook_registration`,
   `privacy_ledger`, `privacy_transaction` — nullable initially
6. Backfill: for each distinct `owner_id`, create a user in default org; set `org_id` on
   all rows to default org
7. Make `org_id` NOT NULL after backfill
8. Add FK constraints and indexes on `org_id`
9. Document maintenance window requirement in docstring (ATTACK-07)
10. Carry existing `privacy_ledger` epsilon allocation to default org (CONFIG-02)

**Down migration**: Drop `org_id` columns, drop `users` table, drop `organizations` table.
Data-lossy — document explicitly.

### T79.2 — Tenant-Scoped Queries

**Files to create**:
- `src/synth_engine/bootstrapper/dependencies/tenant.py` — `TenantContext` dataclass,
  `get_current_user()` dependency

**Files to modify**:
- `src/synth_engine/bootstrapper/dependencies/auth.py` — deprecate `get_current_operator`,
  wire `get_current_user` to use JWT `org_id`/`sub`/`role` claims
- `src/synth_engine/bootstrapper/routers/connections.py` — replace `get_current_operator` →
  `get_current_user`, filter by `org_id`
- `src/synth_engine/bootstrapper/routers/jobs.py` — same
- `src/synth_engine/bootstrapper/routers/jobs_streaming.py` — same
- `src/synth_engine/bootstrapper/routers/privacy.py` — same
- `src/synth_engine/bootstrapper/routers/compliance.py` — erasure scoped to org; self-erasure
  within org only
- `src/synth_engine/bootstrapper/routers/webhooks.py` — limit per-org, filter by `org_id`
- `src/synth_engine/bootstrapper/routers/settings.py` — no `org_id` filter (global), but
  document in ADR
- `src/synth_engine/bootstrapper/routers/admin.py` — if applicable
- `src/synth_engine/modules/synthesizer/jobs/tasks.py` — `run_synthesis_job` resolves org
  via `SynthesisJob.org_id`, validates against `PrivacyLedger.org_id` before `spend_budget()`
- `src/synth_engine/shared/tasks/reaper.py` — audit events include `org_id`
- `src/synth_engine/shared/db.py` — add per-org connection semaphore
- `src/synth_engine/shared/settings_models.py` — add `per_org_max_connections: int = 5`,
  add `jwt_expiry_seconds` ≤ 900 validator

### T79.3 — Tenant Isolation Tests (Integration)

**Files to create**:
- `tests/integration/test_tenant_isolation.py`

All tests hit real PostgreSQL via pytest-postgresql. No mocks.

### T79.4 — Per-Tenant Privacy Ledger

**Files to modify**:
- `src/synth_engine/modules/privacy/ledger.py` — `org_id` FK on both models
- `src/synth_engine/modules/privacy/accountant.py` — `spend_budget()` and `reset_budget()`
  filter by `org_id`; `EPSILON_SPENT_TOTAL` gains `org_id` label
- `src/synth_engine/bootstrapper/routers/privacy.py` — scoped to requesting org
- `docs/ASSUMPTIONS.md` — add A-014

---

## Negative Test Requirements (from spec-challenger)

**MANDATORY**: These tests MUST be written in the ATTACK RED phase, BEFORE feature tests.
Commit separately as `test: add negative/attack tests for multi-tenancy foundation`.

### Authentication & Identity (tests 1-3)
1. `test_get_current_user_rejects_unauthenticated` — 401, no Bearer token
2. `test_get_current_user_rejects_forged_org_id_claim` — 401, JWT signature fails
3. `test_get_current_user_passthrough_returns_default_org_sentinel` — sentinel → default org UUIDs

### IDOR / Cross-Tenant Data Access (tests 4-10)
4. `test_org_a_connection_returns_404_to_org_b` — IDOR on read
5. `test_org_a_job_returns_404_to_org_b` — IDOR on read
6. `test_org_a_cannot_download_org_b_artifact` — IDOR on download
7. `test_org_a_cannot_cancel_org_b_job` — IDOR on mutation
8. `test_org_a_privacy_budget_not_visible_to_org_b` — 404
9. `test_org_a_cannot_reset_org_b_budget` — 403/404
10. `test_org_a_cannot_enumerate_org_b_connections_via_pagination` — cursor scoping

### Input Validation & Spoofing (tests 11-14)
11. `test_sql_injection_in_org_id_path_parameter` — 422
12. `test_http_header_spoofing_x_org_id_ignored` — header cannot override JWT
13. `test_pagination_cursor_from_org_a_under_org_b_returns_only_org_b_data` — cursor leakage
14. `test_jwt_with_forged_org_id_rejected_by_signature` — tampered JWT

### Migration Safety (tests 15-18)
15. `test_migration_009_upgrade_is_idempotent` — double-apply safe
16. `test_migration_009_downgrade_restores_schema` — reversibility (data-lossy acknowledged)
17. `test_default_org_seed_is_idempotent` — no duplicate rows
18. `test_existing_single_operator_data_accessible_after_migration` — backward compat

### Background Task Isolation (tests 19-22)
19. `test_huey_task_spends_correct_org_budget` — correct org ledger
20. `test_huey_task_cannot_spend_cross_org_budget` — no cross-org spend
21. `test_reaper_scoped_audit_event_includes_org_id` — reaper audit context
22. `test_privacy_transaction_audit_scoped_to_org` — transaction isolation

### Feature Scoping (tests 23-27)
23. `test_settings_org_isolation_or_global_documented` — settings remain global (verify no org_id)
24. `test_webhook_limit_scoped_per_org` — per-org limit enforcement
25. `test_tenant_aware_pooling_prevents_pool_exhaustion_by_one_org` — integration test
26. `test_erasure_scoped_to_requesting_user_within_org` — self-erasure boundary
27. `test_org_a_cannot_erase_org_b_user` — cross-org erasure blocked

---

## Attack Vector Mitigations (from spec-challenger)

The developer MUST implement these mitigations. Each has an associated test above.

| ID | Vector | Mitigation | Test |
|----|--------|------------|------|
| ATTACK-01 | JWT claim staleness | `jwt_expiry_seconds ≤ 900` Pydantic validator | #14, config validator test |
| ATTACK-02 | Sentinel org collision | Reserved UUIDs (all-zeros) distinct from UUIDv4 | #3 |
| ATTACK-03 | Cursor cross-tenant leakage | `WHERE org_id = :org_id` BEFORE cursor comparison | #10, #13 |
| ATTACK-04 | Huey task org_id trust | Validate `job.org_id == ledger.org_id` before spend | #19, #20 |
| ATTACK-05 | Prometheus cardinality DoS | Org creation requires admin auth; bounded label set | documented in ADR |
| ATTACK-06 | PrivacyTransaction cross-org | Add `org_id` FK as defense-in-depth | #22 |
| ATTACK-07 | Migration race window | Document maintenance window requirement | migration docstring |

---

## Config Risks (from spec-challenger)

| ID | Risk | Mitigation |
|----|------|------------|
| CONFIG-01 | Unbounded `jwt_expiry_seconds` | Pydantic `le=900` validator |
| CONFIG-02 | Default org epsilon = 0 | Migration carries existing allocation |
| CONFIG-03 | No per-org epsilon mechanism | Configurable default + admin endpoint (P80 scope if admin endpoint needed) |
| CONFIG-04 | Metadata registration ambiguity | Models extend `BaseModel`, tested in T79.0b |

---

## Domain Assumption

Register in `docs/ASSUMPTIONS.md`:

**A-014**: Tenant isolation is application-level (`org_id` FK filtering in every query),
not database-level (PostgreSQL RLS). Source: Phase 79 spec. Confidence: high for current
scale. Verifiable: yes (integration tests in T79.3). Risk: a missed `WHERE org_id =` clause
in any future query creates a cross-tenant data leak. Mitigation: architecture reviewer
checks all new queries; `red-team-reviewer` runs on every phase.

---

## Commit Plan (expected ~8-10 commits)

1. `test: add negative/attack tests for multi-tenancy foundation` (ATTACK RED)
2. `test: add failing tests for shared/models subpackage` (RED)
3. `feat: create shared/models subpackage and alembic discovery` (GREEN for T79.0b)
4. `docs: add ADR-0065 multi-tenant JWT identity architecture` (T79.0)
5. `test: add failing tests for organization and user models` (RED)
6. `feat: implement tenant models and migration 009` (GREEN for T79.1)
7. `feat: implement tenant-scoped queries and get_current_user` (GREEN for T79.2 + T79.3 + T79.4)
8. `refactor: clean up multi-tenancy implementation` (REFACTOR)
9. `review: address reviewer findings` (REVIEW)
10. `docs: update documentation for multi-tenancy foundation` (DOCS)

---

## Quality Gates

All gates per CLAUDE.md. Two-gate policy applies:
- Gate #1 after GREEN (full suite)
- Gate #2 pre-merge (full suite)
- Light gates at all other checkpoints

```bash
poetry run ruff check src/ tests/
poetry run ruff format --check src/ tests/
poetry run mypy src/
poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=95 -W error
poetry run pytest tests/integration/ -v
poetry run bandit -c pyproject.toml -r src/
poetry run vulture src/ .vulture_whitelist.py --min-confidence 60
pre-commit run --all-files
```
