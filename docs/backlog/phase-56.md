# Phase 56 — Refactoring Priorities

**Goal**: Execute the highest-yield refactoring items synthesized from the
staff-level architecture review (2026-03-24) and all review agent findings
across Phases 45–53. Prioritized by cognitive load reduction and maintainability.

**Prerequisite**: Phase 55 merged.

**Source**: Staff-level architecture review (all tiers), RETRO_LOG advisory
history, test quality analysis, and full codebase audit.

---

## Refactoring Items — Full List

### Tier 1: Highest-Yield Structural Changes

| ID | Refactor | Source | Yield |
|----|----------|--------|-------|
| RF-01 | Decompose `modules/synthesizer/` into sub-packages | Arch review | Reduce 24-file, 5,199-LOC module to 4 focused packages |
| RF-02 | Extract bootstrapper wiring from `main.py` | Arch review | Eliminate fragile module-scope side effects |
| RF-03 | Consolidate large test files (5 files > 1,000 LOC) | QA review | Reduce per-file cognitive load |

### Tier 2: Code Quality Improvements

| ID | Refactor | Source | Yield |
|----|----------|--------|-------|
| RF-04 | Strengthen shallow test assertions (59 files with `is not None` only) | QA review, Constitution P4 | Improve mutation kill rate |
| RF-05 | Migrate mock-heavy transactional tests to integration tests | QA review | Improve confidence in egress/rollback paths |
| RF-06 | Eliminate `# noqa: F401, E402` density in `main.py` | Arch review | Clean import structure |

### Tier 3: Documentation & Governance Cleanup

| ID | Refactor | Source | Yield |
|----|----------|--------|-------|
| RF-07 | Add programmatic gates for remaining ADVISORY Constitution rows | Arch review, P49 retro | Complete enforcement table |
| RF-08 | RETRO_LOG archival — move phases 15–45 to `retro_archive/` | QA review | Reduce token load (54,646 tokens → ~10,000) |
| RF-09 | ADR status audit — verify all ADR statuses match current code | Phase boundary auditor | Eliminate stale ADR references |

### Tier 4: Infrastructure Modernization

| ID | Refactor | Source | Yield |
|----|----------|--------|-------|
| RF-10 | Evaluate Python 3.13 downgrade for ecosystem compatibility | DevOps review | Unblock mutmut, reduce supply chain risk |
| RF-11 | Add `Makefile` targets for common multi-step workflows | DevOps review | Reduce onboarding friction |
| RF-12 | Consolidate `docker-compose*.yml` overlays | DevOps review | Reduce deployment configuration surface |

---

## T56.1 — Decompose Synthesizer Module

**Priority**: P5 — Architecture.

### Context & Constraints

1. `modules/synthesizer/` contains 24 files and 5,199 LOC — 10x the size of
   every other module. It owns at least 6 distinct responsibilities: ML training,
   job orchestration, storage, retention, webhook delivery, and reaper lifecycle.
2. Decompose into focused sub-packages:
   ```
   modules/synthesizer/
   ├── __init__.py          # Re-exports for backward compatibility
   ├── training/            # engine.py, dp_training.py, dp_discriminator.py,
   │                        # dp_accounting.py, training_strategies.py, ctgan_*.py
   ├── jobs/                # job_models.py, job_orchestration.py, job_steps.py,
   │                        # job_finalization.py, tasks.py
   ├── storage/             # storage.py, models.py (ModelArtifact), shred.py,
   │                        # erasure.py
   └── lifecycle/           # retention.py, retention_tasks.py, reaper_repository.py,
                            # reaper_tasks.py, webhook_delivery.py, guardrails.py
   ```
3. This is a pure refactor — no behavior change. All existing tests must pass
   without modification (use `__init__.py` re-exports for backward compatibility).
4. Import-linter contracts may need updating if sub-packages are treated as
   separate modules. Evaluate whether sub-packages should be independent or
   whether the existing synthesizer contract is sufficient.
5. Move `_optional_deps.py` to the synthesizer package root (shared by all
   sub-packages).

### Acceptance Criteria

1. Synthesizer module decomposed into 4 sub-packages.
2. All existing tests pass without modification.
3. Import-linter contracts pass.
4. `__init__.py` re-exports preserve backward compatibility.
5. No file exceeds 500 LOC in the new structure.
6. Module docstrings updated to reflect new sub-package structure.
7. Full gate suite passes.

---

## T56.2 — Extract Bootstrapper Wiring Module

**Priority**: P5 — Maintainability.

### Context & Constraints

1. `bootstrapper/main.py` lines 245–255 contain module-scope import side
   effects that register Huey tasks and inject DI factories. These are
   guarded by `# noqa: F401, E402` comments.
2. The import order is fragile — if a circular import appears, tasks will
   silently fail to register.
3. Extract to `bootstrapper/wiring.py` with explicit registration functions:
   ```python
   def wire_task_registrations() -> None:
       """Register all Huey tasks and inject DI factories."""
       ...
   ```
4. Call `wire_task_registrations()` from `create_app()` (explicit) rather
   than relying on module-scope side effects (implicit).
5. This is a pure refactor — no behavior change.

### Acceptance Criteria

1. `bootstrapper/wiring.py` created with explicit registration functions.
2. Module-scope side effects removed from `main.py`.
3. `create_app()` calls wiring functions explicitly.
4. All `# noqa: F401, E402` comments in `main.py` eliminated or reduced
   to genuine re-exports only.
5. All existing tests pass without modification.
6. Full gate suite passes.

---

## T56.3 — Test File Consolidation & Assertion Hardening

**Priority**: P4 — Test quality.

### Context & Constraints

1. Five test files exceed 1,000 LOC:
   - `test_auth_gap_remediation.py` (1,369)
   - `test_bootstrapper_errors.py` (1,205)
   - `test_full_pipeline_e2e.py` (1,155)
   - `test_authorization.py` (1,151)
   - `test_job_steps.py` (1,146)
2. Split each by logical grouping (e.g., `test_auth_gap_remediation.py` →
   `test_auth_gap_scope.py`, `test_auth_gap_jwt.py`, etc.). Preserve all
   test functions — zero test deletion.
3. Separately, scan all 59 files with `is not None` as sole assertion.
   Replace with specific value assertions where possible. Flag cases where
   `is not None` is genuinely the right assertion (e.g., factory return type
   verification) — these are acceptable if documented.
4. This is a pure refactor — no behavior change. Test count must not decrease.

### Acceptance Criteria

1. No test file exceeds 600 LOC after splitting.
2. All tests preserved — zero test deletion.
3. `is not None` sole-assertion tests reduced by ≥50%.
4. Remaining `is not None` assertions documented with inline justification.
5. All tests pass without modification to production code.
6. Full gate suite passes.

---

## T56.4 — RETRO_LOG Archival

**Priority**: P6 — Documentation.

### Context & Constraints

1. `docs/RETRO_LOG.md` is 54,646 tokens. Agent context consumption at
   phase start is excessive.
2. Archive phases 15–45 to `docs/retro_archive/phases-15-to-45.md`.
3. Retain phases 46+ in the active RETRO_LOG (recent, actionable context).
4. Update the Open Advisory Items table (stays in active RETRO_LOG).
5. Update `docs/index.md` retro archive table.

### Acceptance Criteria

1. Phases 15–45 moved to `docs/retro_archive/phases-15-to-45.md`.
2. Active RETRO_LOG contains only phases 46+ and Open Advisory Items.
3. Active RETRO_LOG is under 15,000 tokens.
4. `docs/index.md` updated.
5. No content lost — all historical records preserved in archive.

---

## T56.5 — ADR Status Audit

**Priority**: P6 — Documentation accuracy.

### Context & Constraints

1. Phase boundary auditor has flagged potential ADR staleness in multiple
   phases. Several ADRs reference classes, functions, or modules that may
   have been renamed or removed.
2. Audit all 53 ADRs against current codebase:
   - Verify referenced classes/functions still exist (grep for each).
   - Verify ADR status matches reality (e.g., ADR-0002 was already
     Superseded but the index said Accepted until P53 cleanup).
   - Flag any ADR whose decision has been silently reversed without an
     amendment.
3. Update statuses and add supersession notices where needed.

### Acceptance Criteria

1. All 53 ADRs audited against current code.
2. Any ADR referencing deleted/renamed code updated with amendment notice.
3. ADR status in `docs/index.md` matches the ADR file header for all entries.
4. No ADR references a non-existent class, function, or module without a
   deprecation notice.
5. Findings documented in a summary table in the PR description.

---

## Task Execution Order

```
T56.1 (synthesizer decomposition) ──> largest refactor, start first
T56.2 (bootstrapper wiring) ─────────> can parallel with T56.1
T56.3 (test consolidation) ──────────> can parallel with T56.1/T56.2
T56.4 (RETRO_LOG archival) ──────┐
T56.5 (ADR status audit) ────────┼──> docs tasks, parallel with code tasks
```

---

## Phase 56 Exit Criteria

1. Synthesizer module decomposed into 4 focused sub-packages.
2. Bootstrapper wiring extracted from module-scope side effects.
3. No test file exceeds 600 LOC.
4. `is not None` sole-assertion tests reduced by ≥50%.
5. RETRO_LOG active section under 15,000 tokens.
6. All 53 ADRs audited and statuses corrected.
7. Zero test deletions — all refactoring preserves existing test count.
8. All quality gates pass.
9. Review agents pass for all tasks.
