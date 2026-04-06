# Phase 79 — Spec Challenge Results

**Challenger verdict**: SPEC INCOMPLETE — 12 missing ACs, 27 negative tests, 7 attack vectors, 4 config risks.

All findings below MUST be incorporated into the developer brief before implementation begins.

---

## Missing Acceptance Criteria

### MISSING-AC-01: JWT ADR missing Option C (short-lived tokens)
T79.0 presents only two options (embed vs DB lookup) but omits a hybrid: short-lived tokens
(e.g., 5-minute expiry) with embedded claims and no revocation list. Eliminates most staleness
risk without DB round-trip cost. ADR must evaluate all three options and address what happens
to the pass-through sentinel (`sub=""`) in multi-tenant mode.

### MISSING-AC-02: Alembic model base class unspecified
T79.0b does not specify whether `Organization` and `User` extend `BaseModel` (UUID PK,
auto-discovered by Alembic) or `SQLModel` directly (integer PK, requires explicit import in
`env.py`). Wrong choice causes `alembic autogenerate` to silently miss new tables.
**Required test**: `test_alembic_autogenerate_detects_organization_model`.

### MISSING-AC-03: Default org creation mechanism unnamed
"Default organization created on first boot" does not specify mechanism (migration seed,
lifespan hook, or Huey task). Must be idempotent (upsert, not insert).
**Required test**: `test_default_org_creation_idempotent`.

### MISSING-AC-04: Migration reversibility is physically impossible as written
Down migration cannot reconstruct original `owner_id` varchar values unless preserved in a
shadow column. Spec must either require shadow column preservation or acknowledge downgrade
is "structurally reversible but data-lossy."
**Required test**: `test_migration_009_downgrade_restores_owner_id_values`.

### MISSING-AC-05: Huey tasks have no org context
`run_synthesis_job` takes only `job_id` — no `org_id`, no auth context. Must resolve org via
`SynthesisJob.org_id` FK and pass to `spend_budget()`. Background tasks are NOT routers and
are NOT covered by "every DB query in every router filters by org_id."
**Required tests**: `test_huey_task_spends_correct_org_budget`,
`test_huey_task_cannot_spend_cross_org_budget`.

### MISSING-AC-06: OrphanTaskReaper has no org context
Reaper queries stale `IN_PROGRESS` jobs across all orgs with no org scoping. Audit events
from reaped jobs must include correct `org_id`.
**Required test**: `test_reaper_audit_event_includes_org_id`.

### MISSING-AC-07: Erasure endpoint identity model undefined post-multi-tenancy
`DELETE /compliance/erasure` currently compares `subject_id` against JWT `sub` string.
Post-multi-tenancy: does a user erase themselves within their org? Can an admin erase a
departed user? Spec must define scoping.
**Required tests**: `test_erasure_scoped_to_requesting_user_org`,
`test_org_a_cannot_erase_org_b_user`.

### MISSING-AC-08: Webhook registration limit scoping undefined
"Max 10 active registrations per operator" — is this per-user or per-org post-multi-tenancy?
**Required test**: `test_webhook_limit_scoped_correctly`.

### MISSING-AC-09: Settings table has no org scoping
`Setting` model has no `owner_id` or `org_id`. Settings mutations by Org A would affect Org B.
Spec must explicitly state whether settings get `org_id` FK or remain global (with justification).
**Required test**: `test_settings_org_isolation_or_global_documented`.

### MISSING-AC-10: PrivacyTransaction has no org_id
`PrivacyTransaction` is keyed by `ledger_id` FK only. No `org_id` column. A future admin
endpoint querying transactions directly without `ledger_id` filter would expose all orgs'
spending histories. Add `org_id` FK as defense-in-depth or add strict AC that no route may
query without `ledger_id` filter.
**Required test**: `test_privacy_transaction_audit_scoped_to_org`.

### MISSING-AC-11: Pass-through sentinel behavior undefined
Existing `get_current_operator` returns `""` as sentinel. New `get_current_user` must return
`(org_id, user_id)`. What sentinel values are used? Must resolve to default org with a
reserved UUID that cannot collide with user-created orgs.
**Required test**: `test_get_current_user_passthrough_returns_default_org_sentinel`.

### MISSING-AC-12: Tenant-aware connection pooling mechanism unspecified
"Per-org connection limits" has no design: per-org engine instances (breaks singleton),
PgBouncer routing, or application semaphores? Cannot implement without a design decision.
**Required test**: `test_one_org_cannot_exhaust_connection_pool_for_other_orgs`.

---

## Negative Test Requirements (from spec-challenger)

These are MANDATORY additions to the developer's test plan:

1. `test_get_current_user_rejects_unauthenticated` — 401, no Bearer token
2. `test_get_current_user_rejects_forged_org_id_claim` — 401, JWT signature fails
3. `test_get_current_user_passthrough_returns_default_org_sentinel` — sentinel → default org
4. `test_org_a_connection_returns_404_to_org_b` — IDOR
5. `test_org_a_job_returns_404_to_org_b` — IDOR
6. `test_org_a_cannot_download_org_b_artifact` — IDOR on download
7. `test_org_a_cannot_cancel_org_b_job` — IDOR on mutation
8. `test_org_a_privacy_budget_not_visible_to_org_b` — 404
9. `test_org_a_cannot_reset_org_b_budget` — 403/404
10. `test_org_a_cannot_enumerate_org_b_connections_via_pagination` — cursor scoping
11. `test_sql_injection_in_org_id_path_parameter` — 422
12. `test_http_header_spoofing_x_org_id_ignored` — header cannot override JWT
13. `test_pagination_cursor_from_org_a_under_org_b_returns_only_org_b_data` — cursor leakage
14. `test_jwt_with_forged_org_id_rejected_by_signature` — tampered JWT
15. `test_migration_009_upgrade_is_idempotent` — double-apply safe
16. `test_migration_009_downgrade_restores_owner_id` — reversibility
17. `test_default_org_seed_is_idempotent` — no duplicate rows
18. `test_existing_single_operator_data_accessible_after_migration` — backward compat
19. `test_huey_task_spends_correct_org_budget` — correct org ledger
20. `test_huey_task_cannot_spend_cross_org_budget` — no cross-org spend
21. `test_reaper_scoped_audit_event_includes_org_id` — reaper audit context
22. `test_privacy_transaction_audit_scoped_to_org` — transaction isolation
23. `test_settings_org_isolation_or_global_documented` — settings scoping
24. `test_webhook_limit_scoped_correctly` — per-user or per-org
25. `test_tenant_aware_pooling_prevents_pool_exhaustion_by_one_org` — integration
26. `test_erasure_scoped_to_requesting_user_within_org` — self-erasure boundary
27. `test_org_a_cannot_erase_org_b_user` — cross-org erasure

---

## Attack Vectors

### ATTACK-01: JWT Claim Staleness (Option A)
Revoked org membership retains valid token until expiry. If expiry is large (e.g., 24h),
attacker has hours of access. **Mitigation**: cap `jwt_expiry_seconds` ≤ 900 under Option A.

### ATTACK-02: Pass-through Sentinel Org Collision
`sub=""` sentinel in non-production could resolve to a real org UUID, exposing all data to
unauthenticated callers. **Mitigation**: reserve default-org UUID, distinct from user-creatable IDs.

### ATTACK-03: Cursor-Based Pagination Cross-Tenant Leakage
Integer job IDs are sequential. `after=0` cursor could return Org A data if `org_id` filter
is applied after cursor comparison. **Mitigation**: `WHERE org_id = :org_id` before cursor.

### ATTACK-04: Huey Task org_id Trust Boundary
`run_synthesis_job` trusts `job.org_id` from DB. If DB is compromised or TOCTOU during
migration inserts falsified `org_id`, wrong org's budget is spent. **Mitigation**: validate
`job.org_id` matches `PrivacyLedger.org_id` before `spend_budget()`.

### ATTACK-05: Prometheus Cardinality DoS via Org Creation
Adding `org_id` to Prometheus labels — if org creation is unrestricted, attacker creates
thousands of orgs causing cardinality explosion. **Mitigation**: org creation requires admin
auth; rate limit or max org count.

### ATTACK-06: PrivacyTransaction Cross-Org Visibility
`PrivacyTransaction` has no `org_id` — future admin endpoint could expose all orgs' spending.
**Mitigation**: add `org_id` FK or strict `ledger_id` filter AC.

### ATTACK-07: Migration Race Window
Live system during migration: new rows inserted after backfill SELECT but before commit lack
`User` rows. **Mitigation**: require maintenance window or advisory lock; document explicitly.

---

## Configuration Risks

### CONFIG-01: jwt_expiry_seconds unbounded in multi-tenant mode
Under Option A, large expiry = long staleness window. Mitigation: Pydantic validator ≤ 900.

### CONFIG-02: Default org epsilon allocation defaults to zero
Pre-migration ledger allocation not carried to default org. All jobs blocked immediately.
Mitigation: migration reads existing ledger allocation.

### CONFIG-03: No per-org epsilon allocation mechanism at org creation
New orgs start at zero budget with no endpoint to set allocation. Mitigation: configurable
default allocation + admin endpoint.

### CONFIG-04: shared/models/ metadata registration ambiguity
Spec does not state which metadata object new models register against. Mitigation: explicit
base class in AC + autogenerate CI test.

---

## Retrospective Note

The recurring omission pattern: background execution paths (Huey tasks, reaper, retention)
are treated as outside the scope of security ACs even when they directly touch security-critical
data being modified. This must become a checklist item in every spec: "enumerate all
non-request execution paths that touch any table being modified."
