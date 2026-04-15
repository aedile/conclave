# ADR-0066: Role-Based Access Control (RBAC) Permission Model

**Status**: Accepted — supersedes ADR-0049 (Scope-Based Authorization)
**Date**: 2026-04-04
**Deciders**: Engineering team
**Task**: T80.1 — Role Model & Permission Matrix; T80.2 — Permission Middleware

---

## Context

ADR-0049 established scope-based authorization with four scopes
(`read`, `write`, `security:admin`, `settings:write`) for the single-operator MVP.
All scopes were issued to the one configured operator — effectively no access
differentiation between operation types.

Phase 79 introduced multi-tenancy: organizations, users, and org-scoped JWT
identity (`TenantContext(org_id, user_id, role)`). The JWT now carries a
`role` claim with values in `{admin, operator, viewer, auditor}`. The foundation
exists for role-differentiated access control.

Phase 80 operationalizes the role hierarchy. The enterprise use case requires:

- **Admin**: full control over org resources, user management, all data operations
- **Operator**: can run synthesis jobs and manage connections; cannot administer users
- **Viewer**: read-only access to jobs and connections; cannot create or modify
- **Auditor**: compliance-only access to audit log and privacy ledger; no data access

A scope-based model cannot express this cleanly — scopes are additive and
issued at token time, requiring the token to enumerate all permitted operations.
A role-based model encodes the access policy centrally: one role claim implies
the full set of permissions for that role.

---

## Decision

Replace scope-based authorization at route-enforcement level with a **static
role-based permission matrix** implemented as a FastAPI dependency factory.

### 1. Role Hierarchy

Four roles defined as a Python `enum.Enum` in
`bootstrapper/dependencies/permissions.py`:

| Role | Purpose |
|------|---------|
| `admin` | Full org control, user management, all operations |
| `operator` | Synthesis job lifecycle, connection management |
| `viewer` | Read-only: job status, results, connections |
| `auditor` | Compliance-only: audit log and privacy ledger reads |

Roles are **per-org** (ADR-0065). A user can hold different roles in different
organizations. The JWT is org-scoped (one token per org session).

### 2. Permission Matrix

20 permissions across 4 roles. The matrix is a **static frozen dict** —
no runtime override mechanism at Tier 8.

| Permission | admin | operator | viewer | auditor |
|-----------|-------|----------|--------|---------|
| connections:create | yes | yes | no | no |
| connections:read | yes | yes | yes | no |
| connections:delete | yes | yes | no | no |
| jobs:create | yes | yes | no | no |
| jobs:read | yes | yes | yes | no |
| jobs:cancel | yes | yes | no | no |
| jobs:download | yes | yes | yes | no |
| jobs:shred | yes | yes | no | no |
| jobs:legal-hold | yes | no | no | no |
| webhooks:write | yes | yes | no | no |
| webhooks:read | yes | yes | yes | no |
| privacy:read | yes | yes | yes | yes |
| privacy:reset | yes | no | no | no |
| compliance:erasure | yes | no | no | no |
| compliance:audit-read | yes | no | no | yes |
| security:admin | yes | no | no | no |
| admin:users | yes | no | no | no |
| admin:settings | yes | no | no | no |
| settings:read | yes | yes | yes | no |
| settings:write | yes | no | no | no |
| sessions:revoke | yes | no | no | no |

Rationale for key decisions:
- `jobs:start` maps to `jobs:create` (same capability, no separate permission)
- `jobs:legal-hold` is admin-only (legal obligations — requires elevated trust)
- `privacy:reset` is admin-only (affects metering — requires elevated trust)
- `compliance:erasure` is admin-only (multi-subject scope change from self-only)
- `compliance:audit-read` is admin + auditor (audit materiality)
- `settings:write` is admin-only (runtime behavior mutation)
- `sessions:revoke` is admin-only (cross-user session revocation, Phase 81 / ADR-0067)
- `security:admin` is admin-only (cryptographic operations are destructive)
- Auditor has only `privacy:read` and `compliance:audit-read` (strict least-privilege)

### 3. `require_permission()` Dependency Factory

Authorization is enforced by a FastAPI dependency factory in
`bootstrapper/dependencies/permissions.py`:

```python
@router.get("/jobs")
async def list_jobs(
    ctx: Annotated[TenantContext, Depends(require_permission("jobs:read"))],
) -> JobListResponse: ...
```

`require_permission(permission)` returns a dependency that:

1. Calls `get_current_user(request)` → `TenantContext(org_id, user_id, role)`
2. Looks up `role` in the static `PERMISSION_MATRIX`
3. If permission not granted → raises `HTTPException(403, "Insufficient permissions")`
4. If permission granted → returns the `TenantContext`

Error ordering (401 → 403 → 404):
- **401**: No or invalid JWT (`get_current_user` raises HTTPException 401)
- **403**: Valid JWT, wrong role (`require_permission` raises HTTPException 403)
- **404**: Valid JWT, valid role, wrong org (DB query returns None — org_id scoping)

This is correct: wrong-role users never reach the DB query, preventing timing
oracle attacks and ensuring role errors are distinguishable from IDOR errors.
Role is not a secret to the authenticated user (they know their own role).
Org existence is never leaked (404 from query, not from permission check).

### 4. 403 vs 404 Boundary Rule

- **403 Forbidden**: authenticated user with valid JWT, valid role, but the role
  does not have the required permission. Role is NOT a secret — the user knows
  their own role.
- **404 Not Found**: authenticated user with valid JWT and sufficient permission,
  but the requested resource does not belong to their org. Org existence of
  OTHER orgs is NOT disclosed (IDOR prevention per ADR-0065).

The permission check fires BEFORE the DB query. So:
- A viewer cannot access `POST /jobs` — 403 before any query
- An admin accessing another org's job — 200 permission check, 404 at DB

### 5. `require_scope()` Backward Compatibility

`require_scope()` in `bootstrapper/dependencies/auth.py` is NOT removed.
It remains for external consumers or integration tests that may use scope-based
checks. All internal router usage is migrated to `require_permission()`.

The `_DEFAULT_OPERATOR_SCOPES` list in `auth.py` is retained for token issuance
but the routing layer no longer uses it for access decisions.

### 6. Token Issuance — DB-Resolved Role

`POST /auth/token` resolves the user's role from the DB `users` table:

1. Verify credentials via bcrypt (`verify_operator_credentials`)
2. Look up user row by `username` in `users` table
3. Embed the DB-stored `role` in the JWT via `create_token(..., role=user.role)`
4. In single-tenant mode (`conclave_multi_tenant_enabled=False`): issue `role="admin"`
   for backward compatibility (the seeded default user has admin role)

The client cannot specify a `role` claim in the token request — role is
always derived from the authoritative DB record. This prevents privilege
escalation via token claim manipulation.

### 7. Erasure Semantics (Admin-Delegated)

The `DELETE /compliance/erasure` endpoint is updated to admin-delegated erasure:

- Requires `compliance:erasure` permission (admin-only)
- Admin can erase any `subject_id` within their org (not just self-erasure)
- IDOR guard: `subject_org_id` must equal `admin_org_id` → otherwise 404
- Non-admin callers → 403 from `require_permission` before IDOR check runs

This replaces the previous T69.6 self-erasure-only guard. The cross-org IDOR
check is still enforced at the DB query level (subject must exist in admin's org).

### 8. Last-Admin Guard

Both deactivation (`DELETE /admin/users/{id}`) and demotion (`PATCH /admin/users/{id}`
with role != admin) apply a last-admin guard:

```sql
SELECT COUNT(*) FROM users WHERE org_id = :org_id AND role = 'admin'
```

If count == 1 and the target is an admin (deactivation) or is being demoted
from admin, return HTTP 409 Conflict. The query uses pessimistic locking
(`SELECT ... FOR UPDATE`) to prevent a race condition where two concurrent
admin demotion requests each see count=2 and both proceed, leaving count=0.

### 9. Audit Log Endpoint

`GET /compliance/audit-log` is a paginated, org-scoped endpoint:
- Requires `compliance:audit-read` permission (admin + auditor)
- Auditor access is itself logged as `AUDIT_LOG_ACCESS` event ("audit the auditor")
- Pagination: cursor-based using `before` timestamp cursor
- Scoped to requesting user's org_id
- No PII scrubbing: auditors have enumeration capability by design (documented risk)

### 10. Webhook Delivery Non-Re-Check

Webhook delivery (background task) does NOT re-check permissions at delivery
time. Registration-time permission is sufficient — if the user lost the
`webhooks:write` permission after registering a webhook, their existing
registrations continue to deliver. This is an accepted tradeoff between
delivery reliability and permission freshness.

### 11. Background Task RBAC Exemption

Huey tasks (`run_synthesis_job`, `rotate_ale_keys_task`) and the retention
reaper are explicitly exempt from RBAC. They run outside the HTTP request
context and cannot present a JWT. Audit events for background task actions
use `actor="system/huey"` or `actor="system/reaper"`.

### 12. Stale JWT Window

Role changes take effect after at most `jwt_expiry_seconds` (≤ 900 seconds,
15 minutes). A user whose role is changed continues to use their old JWT
until it expires. This is an accepted risk:

- Mitigation: short token lifetime (≤ 900s per ADR-0065)
- Detection: role change emits an `RBAC_ROLE_CHANGED` audit event
- Acceptance: token revocation would require a revocation list (deferred to Tier 9)

---

## Module Boundary

The permission matrix and `require_permission()` live exclusively in
`bootstrapper/dependencies/permissions.py`. Domain modules (`modules/`)
do NOT import from this module — they have no need for HTTP-level access
control. This preserves the modular monolith boundary (ADR-0001).

---

## Consequences

### Positive

- Fine-grained access control with 4 distinct roles replaces the single-operator model
- Static permission matrix is easy to audit, test, and reason about
- No per-request DB lookup — roles are embedded in the JWT (stateless)
- 403/404 boundary rule prevents both IDOR and role oracle attacks
- Auditor role enables compliance review without granting data access

### Negative / Constraints

- **Stale JWT window**: up to 15 minutes between role change and enforcement.
  Mitigated by short token lifetime + audit event.
- **JWT-embedded roles**: role cannot be revoked without token expiry.
  Revocation list deferred to Tier 9.
- **Single-org tokens**: multi-org users must obtain separate tokens per org.
  This is an inherent consequence of the ADR-0065 org-scoped JWT design.

---

## Alternatives Considered

| Option | Rejected Because |
|--------|-----------------|
| Scope-based (status quo) | Cannot express role hierarchy; requires all-or-nothing issuance |
| DB lookup per request | Adds latency and DB dependency to every authenticated request |
| Policy language (OPA, Casbin) | Over-engineered for 4 roles and 20 permissions; adds external dependency |
| Middleware-level enforcement | Cannot inspect route-specific required permissions without coupling |

---

## References

- ADR-0039 — JWT Bearer Token Authentication
- ADR-0049 — Scope-Based Authorization (superseded by this ADR)
- ADR-0065 — Multi-Tenant JWT Identity (provides TenantContext)
- `src/synth_engine/bootstrapper/dependencies/permissions.py` — implementation
- `src/synth_engine/bootstrapper/routers/admin_users.py` — admin user management
- OWASP ASVS v4.0 — Access Control (V4)
- RFC 6750 — OAuth 2.0 Bearer Token
