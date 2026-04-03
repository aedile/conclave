# Phase 80 — Role-Based Access Control (RBAC)

**Tier**: 8 (Enterprise Scale)
**Goal**: Replace the single "operator" role with a role hierarchy supporting admin,
operator, viewer, and auditor permissions.

**Dependencies**: Phase 79 (multi-tenancy — users and orgs must exist first)

---

## Context & Constraints

- Currently: one role ("operator"), one permission level, JWT `sub` is the identity.
- Target: 4 roles with a permission matrix. Roles are per-org (a user can be admin in
  Org A and viewer in Org B if they belong to both).
- Admin can manage users and org settings but cannot bypass privacy budget limits.
- Viewer is read-only — can see job status and results but cannot create, cancel, or delete.
- Auditor can read audit logs and compliance reports but nothing else.
- Permission checks must be middleware-level, not scattered across individual endpoints.

---

## Tasks

### T80.1 — Role Model & Permission Matrix

**Files to create/modify**:
- `src/synth_engine/shared/models/roles.py` (new)
- `bootstrapper/dependencies/auth.py`

**Acceptance Criteria**:
- [ ] Enum: `admin`, `operator`, `viewer`, `auditor`
- [ ] Permission matrix defined (see table below)
- [ ] Role stored in JWT claims
- [ ] `User` model updated with `role` field (default: `operator` for backward compatibility)

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
- [ ] Stacks with tenant isolation: permission check happens AFTER org scoping
- [ ] Every endpoint annotated with its required permission
- [ ] No endpoint left with raw `get_current_operator` (migration complete)

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

### T80.4 — Auditor Role Implementation

**Files to modify**:
- `bootstrapper/routers/compliance.py`

**Acceptance Criteria**:
- [ ] `GET /compliance/audit-log` endpoint (auditor + admin only)
- [ ] Read-only: auditor can read audit trail, privacy ledger, compliance reports
- [ ] Auditor cannot create, modify, or delete any resource
- [ ] Audit of auditor access is itself logged (audit the auditor)

---

## Testing & Quality Gates

- Attack tests: viewer attempts job creation (403), auditor attempts connection creation (403)
- IDOR tests: admin in Org A attempts user management in Org B (404)
- Permission matrix fully tested via parametrized test (all role x endpoint combinations)
- Integration tests against real PostgreSQL
