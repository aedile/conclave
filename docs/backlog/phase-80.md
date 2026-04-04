# Phase 80 — Role-Based Access Control (RBAC)

**Tier**: 8 (Enterprise Scale)
**Goal**: Replace the single "operator" role with a role hierarchy supporting admin,
operator, viewer, and auditor permissions.

**Dependencies**: Phase 79 (multi-tenancy — users and orgs must exist first)

---

## Context & Constraints

- Currently: one role ("operator"), one permission level, JWT `sub` is the identity.
- ADR-0049 (scope-based authorization) defines four scopes (`read`, `write`,
  `security:admin`, `settings:write`) with the explicit note that "the MVP issues all
  scopes to the one configured operator." This phase supersedes ADR-0049 with a new
  RBAC ADR documenting the 4-role, 15-permission model.
- Target: 4 roles with a permission matrix. Roles are per-org (a user can be admin in
  Org A and viewer in Org B if they belong to both).
- Admin can manage users and org settings but cannot bypass privacy budget limits.
- Viewer is read-only — can see job status and results but cannot create, cancel, or delete.
- Auditor can read audit logs and compliance reports but nothing else.
- Permission checks must be middleware-level, not scattered across individual endpoints.
- **403 vs 404 boundary**: IDOR (wrong org) returns 404 (per P79 — don't leak resource
  existence). Insufficient permission within the correct org returns 403 (role is not a
  secret to the authenticated user). The org-scoping check runs BEFORE the permission
  check — if org doesn't match, 404; if org matches but role insufficient, 403.
- Role encoding in JWT: per T79.0 ADR decision. If JWT-embedded, the JWT carries
  `(org_id, user_id, role)`. If DB-lookup, JWT carries only `sub` and role is resolved
  per-request. Multi-org users with different roles per org: the JWT is org-scoped
  (one token per org session) or the DB lookup resolves role for the target org.
- The GDPR erasure endpoint (`DELETE /compliance/erasure`) currently erases data for
  the current operator. In multi-tenant RBAC, clarify: admin erases data for a specified
  subject within their org. The `subject_id` parameter already exists; scope it to org.

---

## Tasks

### T80.1 — Role Model & Permission Matrix

**Files to create/modify**:
- `src/synth_engine/shared/models/roles.py` (new)
- `bootstrapper/dependencies/auth.py`
- New ADR superseding ADR-0049

**Acceptance Criteria**:
- [ ] Enum: `admin`, `operator`, `viewer`, `auditor`
- [ ] Permission matrix defined (see table below)
- [ ] Role resolved per T79.0 ADR decision (JWT-embedded or DB-lookup)
- [ ] `User` model `role` field (default: `operator` for backward compatibility)
- [ ] ADR superseding ADR-0049 documenting the RBAC permission model, the 403/404
      boundary rule, and the role resolution mechanism

Permission matrix:

| Permission | admin | operator | viewer | auditor |
|-----------|-------|----------|--------|---------|
| connections:create | yes | yes | no | no |
| connections:read | yes | yes | yes | no |
| connections:delete | yes | yes | no | no |
| jobs:create | yes | yes | no | no |
| jobs:read | yes | yes | yes | no |
| jobs:cancel | yes | yes | no | no |
| jobs:download | yes | yes | yes | no |
| privacy:read | yes | yes | yes | yes |
| privacy:reset | yes | no | no | no |
| compliance:erasure | yes | no | no | no |
| compliance:audit-read | yes | no | no | yes |
| admin:users | yes | no | no | no |
| admin:settings | yes | no | no | no |
| settings:read | yes | yes | yes | no |
| settings:write | yes | no | no | no |

### T80.2 — Permission Middleware

**Files to create/modify**:
- `bootstrapper/dependencies/permissions.py` (new)
- All router files (replace `get_current_operator` with `require_permission(...)`)

**Acceptance Criteria**:
- [ ] `require_permission("jobs:create")` FastAPI dependency
- [ ] Returns 403 for insufficient permissions (not 404 — role is not a secret)
- [ ] Stacks with tenant isolation: org-scoping runs first (404 if wrong org),
      permission check runs second (403 if insufficient role). Implementation:
      `require_permission()` calls `get_current_user()` which returns `(org_id, user_id, role)`;
      ordering is correct by construction.
- [ ] Every endpoint annotated with its required permission
- [ ] No endpoint left with raw `get_current_operator` (migration complete)
- [ ] Permission matrix is a static data structure in `bootstrapper/dependencies/permissions.py`
      (not in `shared/` — modules don't need it; only the bootstrapper enforces permissions)

### T80.3 — Admin Endpoints

**Files to create**:
- `bootstrapper/routers/admin_users.py` (new)
- `bootstrapper/schemas/admin.py` (new)

**Acceptance Criteria**:
- [ ] `POST /api/v1/admin/users` — create user in org (admin only)
- [ ] `GET /api/v1/admin/users` — list users in org (admin only)
- [ ] `PATCH /api/v1/admin/users/{user_id}` — update role (admin only)
- [ ] `DELETE /api/v1/admin/users/{user_id}` — deactivate user (admin only)
- [ ] Admin cannot escalate beyond admin role
- [ ] Admin cannot modify users in other orgs
- [ ] Admin cannot deactivate themselves if they are the last admin in the org
      (guard: count admins in org; reject if count == 1 and target is self)

### T80.4 — Auditor Role Implementation

**Files to modify**:
- `bootstrapper/routers/compliance.py`

**Acceptance Criteria**:
- [ ] `GET /compliance/audit-log` endpoint (auditor + admin only)
- [ ] Read-only: auditor can read audit trail, privacy ledger, compliance reports
- [ ] Auditor cannot create, modify, or delete any resource
- [ ] Auditor cannot download job artifacts (synthesized data is not audit material)
- [ ] Audit of auditor access is itself logged (audit the auditor)

---

## Testing & Quality Gates

- Attack tests: viewer attempts job creation (403), auditor attempts connection creation (403)
- Attack tests: auditor attempts job download (403)
- IDOR tests: admin in Org A attempts user management in Org B (404)
- IDOR tests: viewer in Org A attempts to read Org B's jobs (404)
- IDOR tests: auditor in Org A attempts to read Org B's audit log (404)
- Permission matrix fully tested via parametrized test (all role x endpoint combinations).
  Note: this test file will likely exceed 800 lines. Pre-authorize with
  `# gate-exempt: permission-matrix coverage requires exhaustive parametrization`.
- Self-deactivation guard test: last admin cannot deactivate self (returns 409 Conflict)
- Backward compatibility: all existing integration tests must pass with the new auth dependency
- Integration tests against real PostgreSQL
