# Phase 80 — Developer Brief: Role-Based Access Control (RBAC)

**Branch**: `feat/P80-rbac-role-based-access-control`
**Spec**: `docs/backlog/phase-80.md` (amended with spec-challenger findings)
**Spec Challenge Results**: `docs/backlog/phase-80-spec-challenge.md`

---

## PM Architectural Decisions

These decisions are final. Do NOT substitute without an ADR.

### 1. Permission Matrix (expanded)

20 permissions across 4 roles. The matrix is a **static frozen data structure** in
`bootstrapper/dependencies/permissions.py`. No runtime override mechanism at Tier 8.

New permissions added per spec-challenger: `jobs:shred`, `jobs:legal-hold`, `webhooks:write`,
`webhooks:read`, `security:admin`. `jobs:start` maps to `jobs:create` (same permission).

### 2. require_permission() Design

`require_permission("jobs:create")` is a FastAPI dependency factory that:
1. Calls `get_current_user()` → `TenantContext(org_id, user_id, role)`
2. Looks up `role` in the static permission matrix
3. If permission not granted → 403 with `"Insufficient permissions"`
4. Org-level IDOR checks happen at the query level (`WHERE org_id = :org_id` → 404)
5. Ordering: org check (implicit in query) runs before permission check (dependency)
   — but `require_permission` fires before the query. So: 401 (no auth) → 403 (wrong role)
   → 404 (wrong org, at query time). This is correct: we don't leak resource existence to
   wrong-role users (they get 403 before the query runs).

### 3. Security Endpoints Migration

`require_scope("security:admin")` → `require_permission("security:admin")`.
The `require_scope` function is NOT removed (backward compat for any external consumers),
but all internal usage is replaced.

### 4. Erasure Semantics Change

Admin can erase any subject within their org:
- `DELETE /compliance/erasure` requires `compliance:erasure` permission (admin only)
- Admin passes `subject_id` in request body (any user in their org)
- The IDOR guard changes: instead of `subject_id == current_user.user_id`, check
  that the subject exists in the admin's org
- Non-admin → 403. Cross-org subject → 404.

### 5. Token Issuance

`POST /auth/token` resolves role from DB `User` record:
- After verifying credentials, look up user in `users` table by some identifier
- Embed the DB-stored `role` in the JWT — client cannot specify role
- In single-tenant mode (`conclave_multi_tenant_enabled=False`): issue `role="admin"`
  for backward compatibility (existing single-operator gets full access)

### 6. Default User Role

The P79 default user (`00000000-0000-0000-0000-000000000001`) must have `role="admin"`.
If migration 009 set it to `"operator"`, add a data migration in P80 to update it.
Check current state before deciding.

### 7. Last-Admin Guard

Both deactivation (`DELETE /admin/users/{id}`) and demotion (`PATCH /admin/users/{id}`
with role != admin) must check: `SELECT COUNT(*) FROM users WHERE org_id = :org_id AND
role = 'admin' FOR UPDATE`. If count == 1 and target is the last admin, return 409.

### 8. Audit Log Endpoint

`GET /compliance/audit-log` returns paginated audit events:
- Fields: `id`, `actor`, `action`, `resource`, `details`, `timestamp`
- Pagination: cursor-based (same pattern as jobs list)
- Scoped to requesting user's org
- No PII scrubbing (auditors have enumeration capability by design — documented)
- Auditor access is itself logged as an audit event

### 9. Webhook Delivery

NOT re-checked at delivery time. Registration-time permission is sufficient.
Document as accepted design decision in ADR.

### 10. Background Tasks

Explicitly exempt from RBAC. Audit events use `actor="system/huey"` or
`actor="system/reaper"`. Already the case — just document explicitly in ADR.

### 11. Stale JWT Window

Accepted risk: up to 15 minutes (jwt_expiry_seconds ≤ 900). Role change emits audit event.
ADR must document this accepted risk with mitigation (short token lifetime).

---

## Task Execution Order

### T80.0 — RBAC ADR (supersedes ADR-0049)

**Files to create**:
- `docs/adr/ADR-0066-rbac-permission-model.md`

Documents: 4-role model, 20-permission matrix, 403/404 ordering, last-admin guard,
stale JWT accepted risk, webhook delivery non-re-check, background task RBAC exemption,
static matrix rationale.

### T80.1 — Role Model & Permission Matrix

**Files to create**:
- `src/synth_engine/bootstrapper/dependencies/permissions.py` (NEW)
  - `Role` enum: `admin`, `operator`, `viewer`, `auditor`
  - `PERMISSION_MATRIX: dict[str, frozenset[Role]]` — maps permission to allowed roles
  - `require_permission(permission: str)` — FastAPI dependency factory
  - `has_permission(role: str, permission: str) -> bool` — pure function for testing

**Files to modify**:
- `src/synth_engine/shared/models/user.py` — ensure `role` field uses Role enum values

### T80.2 — Permission Middleware on All Endpoints

**Files to modify** (ALL routers):
- `bootstrapper/routers/connections.py` — `connections:create`, `connections:read`, `connections:delete`
- `bootstrapper/routers/jobs.py` — `jobs:create`, `jobs:read`, `jobs:cancel`, `jobs:shred`
- `bootstrapper/routers/jobs_streaming.py` — `jobs:read`
- `bootstrapper/routers/privacy.py` — `privacy:read`, `privacy:reset`
- `bootstrapper/routers/compliance.py` — `compliance:erasure`, `compliance:audit-read`
- `bootstrapper/routers/webhooks.py` — `webhooks:write`, `webhooks:read`
- `bootstrapper/routers/settings.py` — `settings:read`, `settings:write`
- `bootstrapper/routers/admin.py` — `jobs:legal-hold`
- `bootstrapper/routers/security.py` — `security:admin`
- `bootstrapper/routers/auth.py` — update token issuance to embed DB role

### T80.3 — Admin User Management Endpoints

**Files to create**:
- `bootstrapper/routers/admin_users.py` (NEW)
- `bootstrapper/schemas/admin_users.py` (NEW)

Endpoints:
- `POST /api/v1/admin/users` — create user in org (admin only)
- `GET /api/v1/admin/users` — list users in org (admin only)
- `PATCH /api/v1/admin/users/{user_id}` — update role (admin only)
- `DELETE /api/v1/admin/users/{user_id}` — deactivate user (admin only)

### T80.4 — Auditor Role & Audit Log Endpoint

**Files to create/modify**:
- `bootstrapper/routers/compliance.py` — add `GET /compliance/audit-log`

### T80.5 — Erasure Semantics Update

**Files to modify**:
- `bootstrapper/routers/compliance.py` — admin can erase any subject in org
- `modules/synthesizer/lifecycle/erasure.py` — if needed

---

## Negative Test Requirements (from spec-challenger)

**MANDATORY**: Written in ATTACK RED phase, BEFORE feature tests.

### Permission Enforcement (tests 1-3)
1. `test_require_permission_unauthenticated_returns_401`
2. `test_require_permission_wrong_org_returns_404`
3. `test_require_permission_insufficient_role_returns_403`

### Viewer Restrictions (tests 4-8)
4. `test_viewer_job_create_returns_403`
5. `test_viewer_job_cancel_returns_403`
6. `test_viewer_connection_create_returns_403`
7. `test_viewer_connection_delete_returns_403`
8. `test_viewer_settings_write_returns_403`

### Auditor Restrictions (tests 9-14)
9. `test_auditor_connection_read_returns_403`
10. `test_auditor_job_read_returns_403`
11. `test_auditor_job_download_returns_403`
12. `test_auditor_settings_read_returns_403`
13. `test_auditor_webhook_create_returns_403`
14. `test_auditor_privacy_reset_returns_403`

### Operator Restrictions (tests 15-19)
15. `test_operator_privacy_reset_returns_403`
16. `test_operator_compliance_erasure_returns_403`
17. `test_operator_audit_log_read_returns_403`
18. `test_operator_admin_users_returns_403`
19. `test_operator_settings_write_returns_403`

### Admin Boundaries (tests 20-23)
20. `test_admin_org_a_cannot_manage_users_org_b_returns_404`
21. `test_admin_cannot_escalate_role_to_superadmin`
22. `test_admin_cannot_deactivate_self_if_last_admin_returns_409`
23. `test_admin_cannot_demote_self_if_last_admin_returns_409`

### Auditor Capabilities (tests 24-26)
24. `test_auditor_audit_log_access_is_itself_logged`
25. `test_auditor_compliance_report_read_returns_200`
26. `test_auditor_compliance_erasure_returns_403`

### Spec-Challenger Gaps (tests 27-36)
27. `test_legal_hold_viewer_returns_403`
28. `test_security_shred_operator_returns_403`
29. `test_security_shred_auditor_returns_403`
30. `test_privacy_refresh_operator_returns_403`
31. `test_erasure_operator_returns_403`
32. `test_erasure_admin_can_erase_other_subject_in_org`
33. `test_erasure_admin_cannot_erase_subject_other_org_returns_404`
34. `test_settings_read_auditor_returns_403`
35. `test_job_shred_viewer_returns_403`
36. `test_job_shred_auditor_returns_403`

### Exhaustive Matrix (test 37)
37. `test_permission_matrix_parametrized_all_role_endpoint_combinations`

---

## Inline Advisory Resolution

### ADV-P79-01: Test setup duplication
Extract shared IDOR test fixture into `tests/unit/conftest.py` or a shared helper.
Do this during REFACTOR phase.

### ADV-P79-02: ADR-0049 stale section 4
Supersede ADR-0049 entirely with the new RBAC ADR (ADR-0066). Section 4 becomes moot.

---

## Commit Plan (expected ~8-10 commits)

1. `test: add negative/attack tests for RBAC` (ATTACK RED)
2. `test: add failing tests for RBAC feature` (RED)
3. `docs: add ADR-0066 RBAC permission model` (T80.0)
4. `feat: implement permission matrix and require_permission` (GREEN for T80.1 + T80.2)
5. `feat: implement admin user management endpoints` (GREEN for T80.3)
6. `feat: implement auditor role and audit log endpoint` (GREEN for T80.4)
7. `feat: update erasure semantics for admin-delegated erasure` (GREEN for T80.5)
8. `refactor: clean up RBAC implementation + resolve ADV-P79-01` (REFACTOR)
9. `review: address reviewer findings` (REVIEW)
10. `docs: update documentation for RBAC` (DOCS)

---

## Quality Gates

All gates per CLAUDE.md. Two-gate policy applies.

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
