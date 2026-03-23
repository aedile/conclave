# Advisory Drain — Pre-Phase 44

**Goal**: Close all 8 open advisories to meet Phase 44's prerequisite of zero
open advisories. Security BLOCKERs (Priority 0) addressed first.

**Prerequisite**: Phase 43 merged.

**ADR**: None required — remediation of existing gaps, no new architecture.

**Source**: RETRO_LOG advisories ADV-017 through ADV-024.

---

## ADR-D1 — Add Authentication to Settings, Security & Privacy Routers

**Priority**: P0 — Security. Closes ADV-021, ADV-022, ADV-024.

### Context & Constraints

1. `/settings` GET/PUT/DELETE — no `Depends(get_current_operator)` (ADV-021)
2. `/security/shred`, `/security/keys/rotate` — in `COMMON_INFRA_EXEMPT_PATHS`
   but no route-level auth (ADV-022). Keep middleware exemption for pre-boot
   emergency access but add route-level `Depends(get_current_operator)`.
3. `/privacy/budget` GET/POST — no auth (ADV-024)

### Acceptance Criteria

1. All settings endpoints require `Depends(get_current_operator)`.
2. Security endpoints require `Depends(get_current_operator)` at route level.
3. Privacy budget endpoints require `Depends(get_current_operator)`.
4. Unauthenticated requests return 401 on all affected endpoints.
5. Authenticated requests work as before.
6. Attack tests: unauthenticated access returns 401 for each endpoint.
7. Full gate suite passes.

### Files to Modify

- `src/synth_engine/bootstrapper/routers/settings.py`
- `src/synth_engine/bootstrapper/routers/security.py`
- `src/synth_engine/bootstrapper/routers/privacy.py`

---

## ADR-D2 — Add Admin Scope Check to Legal Hold Endpoint

**Priority**: P0 — Security. Closes ADV-023.

### Context & Constraints

1. `/admin/jobs/{id}/legal-hold` has auth but no ownership check — any
   authenticated operator can toggle legal hold on any job.
2. This is intentional for admin operations in the single-operator model.
3. Fix: Document as intentional admin behavior + add explicit authorization
   check that the operator has admin privileges (by verifying `current_operator`
   is non-empty, which it always is post-auth — the real defense is the auth
   requirement itself, which already exists).
4. Close ADV-023 with documentation that admin endpoints are intentionally
   not ownership-scoped.

### Acceptance Criteria

1. ADV-023 closed with documentation that admin endpoints are system-wide by design.
2. Admin endpoint already requires authentication (verified).
3. Docstring updated to explicitly state admin operations are not ownership-scoped.

---

## ADR-D3 — Wire Retention Cleanup to Huey Periodic Task

**Priority**: P1 — Operational. Closes ADV-019, ADV-020.

### Context & Constraints

1. `RetentionCleanup.cleanup_expired_jobs()` exists but is not wired to any
   scheduler (ADV-019).
2. Artifact cleanup is decoupled from job cleanup (ADV-020).
3. Wire both to Huey periodic tasks using `@huey.periodic_task(crontab())`.

### Acceptance Criteria

1. `cleanup_expired_jobs()` runs as a Huey periodic task (configurable interval).
2. Artifact cleanup runs as a separate Huey periodic task.
3. Both tasks log their activity to the audit trail.
4. Both tasks are tested with `huey_immediate=True`.
5. Full gate suite passes.

---

## ADR-D4 — Cosmetic Advisory Polish

**Priority**: P3 — Polish. Closes ADV-017, ADV-018.

### Context & Constraints

1. README.md references stale `EpsilonAccountant` class name (ADV-017).
2. `test_boundary_values.py` docstring says "rounds to zero" but test was
   renamed (ADV-018).

### Acceptance Criteria

1. README.md references updated to match current API.
2. Test docstring corrected.

---

## Task Execution Order

```
ADR-D1 (Auth gap remediation) ──> parallel
ADR-D2 (Admin scope docs) ──────> parallel (docs-only, PM can execute)
ADR-D3 (Retention wiring) ──────> parallel
ADR-D4 (Cosmetic polish) ───────> parallel
```

All four tasks are independent.
