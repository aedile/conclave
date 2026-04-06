# Phase 80 — Spec Challenge Results

**Challenger verdict**: SPEC INCOMPLETE — 12 missing ACs, 37 negative tests, 7 attack vectors, 4 config risks.

All findings below MUST be incorporated into the developer brief before implementation begins.

---

## Missing Acceptance Criteria

### MISSING-AC-01: Legal-hold endpoint missing from permission matrix
`PATCH /admin/jobs/{id}/legal-hold` exists but has no permission assigned. Must be explicitly
gated (likely `admin:settings` or new `jobs:legal-hold` permission).

### MISSING-AC-02: Security endpoints unspecified after require_scope migration
`POST /security/shred` and `POST /security/keys/rotate` use `require_scope("security:admin")`,
not `get_current_operator`. After P80's migration, these need explicit RBAC permissions.

### MISSING-AC-03: Privacy budget refresh not gated
`POST /privacy/budget/refresh` is accessible to any authenticated user. Must be gated at
`privacy:reset` (admin only per matrix).

### MISSING-AC-04: Erasure semantics change unspecified
`DELETE /compliance/erasure` currently enforces self-erasure only. Admin should be able to
erase any subject within their org. The IDOR guard must be modified, not just the permission.

### MISSING-AC-05: Settings endpoints missing from permission spec
`GET /settings` needs `settings:read`; `PUT/DELETE /settings` needs `settings:write`.
Auditor has no `settings:read` per matrix — must be blocked.

### MISSING-AC-06: Token issuance role resolution unspecified
`POST /auth/token` currently issues `role="operator"` hardcoded. Must look up user's actual
role from DB. Client must NEVER specify the role claim.

### MISSING-AC-07: Stale JWT window with role demotion
Admin demoted to operator retains admin token for up to 15 minutes. Must document accepted
risk and require audit event on role change.

### MISSING-AC-08: Last-admin guard must cover demotion, not just deactivation
Admin cannot demote self to non-admin if they are the last admin. Same guard as deactivation.

### MISSING-AC-09: Webhook endpoints absent from permission matrix
All webhook CRUD endpoints need permissions. Likely `webhooks:write` for create/delete,
`webhooks:read` for list/deliveries.

### MISSING-AC-10: Job shred endpoint missing from permission matrix
`POST /jobs/{id}/shred` is destructive — needs explicit permission (admin+operator only).

### MISSING-AC-11: Job start endpoint missing from permission matrix
`POST /jobs/{id}/start` needs explicit permission. Clarify if `jobs:create` covers both.

### MISSING-AC-12: Background tasks exempt from RBAC — undocumented
Huey/reaper tasks bypass RBAC. Must document exemption and ensure system-actor identity
in audit events.

---

## Negative Test Requirements (from spec-challenger)

1. `test_require_permission_unauthenticated_returns_401`
2. `test_require_permission_wrong_org_returns_404`
3. `test_require_permission_insufficient_role_returns_403`
4. `test_viewer_job_create_returns_403`
5. `test_viewer_job_cancel_returns_403`
6. `test_viewer_connection_create_returns_403`
7. `test_viewer_connection_delete_returns_403`
8. `test_viewer_settings_write_returns_403`
9. `test_auditor_connection_read_returns_403`
10. `test_auditor_job_read_returns_403`
11. `test_auditor_job_download_returns_403`
12. `test_auditor_settings_read_returns_403`
13. `test_auditor_webhook_create_returns_403`
14. `test_auditor_privacy_reset_returns_403`
15. `test_operator_privacy_reset_returns_403`
16. `test_operator_compliance_erasure_returns_403`
17. `test_operator_audit_log_read_returns_403`
18. `test_operator_admin_users_returns_403`
19. `test_operator_settings_write_returns_403`
20. `test_admin_org_a_cannot_manage_users_org_b_returns_404`
21. `test_admin_cannot_escalate_role_to_superadmin`
22. `test_admin_cannot_deactivate_self_if_last_admin_returns_409`
23. `test_admin_cannot_demote_self_if_last_admin_returns_409`
24. `test_auditor_audit_log_access_is_itself_logged`
25. `test_auditor_compliance_report_read_returns_200`
26. `test_auditor_compliance_erasure_returns_403`
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
37. `test_permission_matrix_parametrized_all_role_endpoint_combinations`

---

## Attack Vectors

### ATTACK-01: Permission check ordering inversion
If permission check runs before org check, attacker can probe resource existence via 403 vs 404.
Mitigation: `require_permission()` must call `get_current_user()` first via Depends.

### ATTACK-02: Role claim forgery via create_token
`create_token` accepts any role string. Token issuance must derive role from DB, not client.
Mitigation: Role in JWT derived from DB User record only.

### ATTACK-03: Admin user creation with invalid role
Admin could create user with `role="superadmin"` if enum check is loose.
Mitigation: Validate role against Role enum, return 422 for invalid values.

### ATTACK-04: Auditor exfiltration via audit log content
Audit events contain user IDs and resource IDs. Auditor can enumerate all entities.
Mitigation: Document as accepted (auditors audit users) or scrub PII from audit responses.

### ATTACK-05: Race condition on last-admin guard
Two concurrent admin deletions in a 2-admin org could both pass count check.
Mitigation: SELECT FOR UPDATE on admin count query within serializable transaction.

### ATTACK-06: Webhook delivery for demoted users
Webhooks registered by a user continue firing after demotion.
Mitigation: Spec must state whether delivery is re-checked at delivery time.

### ATTACK-07: User creation with sentinel UUID collision
Admin creates user whose UUID matches a sentinel.
Mitigation: Validate generated user_id is not any sentinel UUID.

---

## Configuration Risks

### CONFIG-01: Single-tenant mode role default
In single-tenant mode, `POST /auth/token` issues `role="operator"`. Admin-only endpoints
become inaccessible. Must issue `role="admin"` in single-tenant mode for backward compat.

### CONFIG-02: JWT expiry enforcement
Long-lived tokens undermine role-change propagation. The ≤900s cap from ADR-0065 must
be validated at startup.

### CONFIG-03: Default user role mismatch
Migration 009's default user has `role="operator"` but pass-through sentinel has `role="admin"`.
Must set default user's role to `admin`.

### CONFIG-04: Static permission matrix — no runtime override
Acceptable at Tier 8. Document explicitly: "Permission matrix changes require code deploy."

---

## Retrospective Note

Second consecutive spec that under-specifies the boundary between legacy single-operator
paths and the new multi-role model. Standing rule recommended: when an auth ADR is superseded,
a complete endpoint inventory must be attached to the spec.
