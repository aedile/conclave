# Phase 37 — Advisory Drain, CHANGELOG Currency & E2E Demo Capstone

**Goal**: Drain all 4 open advisory items to zero, update CHANGELOG through Phase 36,
and execute the deferred T36.5 full E2E demo run with production-worthy dataset.

**Prerequisite**: Phase 36 merged. Advisory count at 4 (under Rule 11 threshold of 8).

**ADR**: None required — no architectural decisions, only fixes and validation.

**Source**: Panel Roast #3 (post-Phase 36), open advisory ledger in RETRO_LOG.

---

## T37.1 — Fix Silent Privacy Budget Deduction Failure (ADV-P35-01)

**Priority**: P0 — Correctness. A DP training run can complete with `actual_epsilon=None`
and zero budget recorded if `dp_wrapper.epsilon_spent()` raises.

### Context & Constraints

1. `_handle_dp_accounting()` in `job_orchestration.py:240-280` has an `except Exception`
   handler at line 244 that logs ERROR when `epsilon_spent()` fails. However, the
   downstream guard `if job.actual_epsilon is None: return` at line 247 then silently
   skips the entire budget deduction path.

2. This means a DP training run can complete successfully (status=COMPLETE) while:
   - `job.actual_epsilon` remains `None`
   - No privacy budget is deducted from the ledger
   - The WORM audit trail has no PRIVACY_BUDGET_SPEND entry

3. This is a privacy accounting correctness issue — the system claims DP protection
   but may not track epsilon consumption.

4. Fix options:
   a. **Mark job FAILED** if `epsilon_spent()` raises — safest, prevents untracked DP use.
   b. **Use a fallback epsilon** (e.g., the configured max from the job) and log a WARNING.
   c. **Retry** with exponential backoff before giving up.

5. Option (a) is recommended: if we can't measure the privacy cost, the job output
   should not be delivered. This aligns with Constitution Priority 0 (security).

### Acceptance Criteria

1. If `dp_wrapper.epsilon_spent()` raises, the job is marked FAILED with a clear error
   message ("DP epsilon measurement failed — privacy budget cannot be verified").
2. The WORM audit trail logs the failure event.
3. A WARNING-level log entry distinguishes "epsilon read failed" from "no DP training ran".
4. New test: verify job status is FAILED when `epsilon_spent()` raises.
5. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/synthesizer/job_orchestration.py`
- Modify: `tests/unit/test_job_steps.py` (add epsilon failure test)

---

## T37.2 — Drain Remaining Advisories (ADV-P34-01, ADV-P34-02, ADV-P36-01)

**Priority**: P1 — Hygiene. Three non-blocking advisories that collectively reduce
code consistency and documentation accuracy.

### Context & Constraints

1. **ADV-P34-01**: `operator_error_response()` logs `str(exc)` at WARNING for
   security-event exceptions (PrivilegeEscalationError, ArtifactTamperingError)
   without `safe_error_msg()` wrapping. The HTTP response is already safe (uses
   sanitized strings from OPERATOR_ERROR_MAP), but the server log could contain
   sensitive details if the exception message includes internal state.

2. **ADV-P34-02**: PIIFilter referenced in documentation/comments does not exist
   in `src/`. Either implement a basic PIIFilter logging handler or remove references.

3. **ADV-P36-01**: `config_validation.py` line 103 still uses direct `os.environ.get()`
   in the variable-presence validation loop. The `_is_production()` method was fixed
   to delegate to `get_settings().is_production()` but the required-variable check
   was not centralized.

### Acceptance Criteria

1. ADV-P34-01: `operator_error_response()` wraps `str(exc)` with `safe_error_msg()`
   in the WARNING log for all exception types.
2. ADV-P34-02: PIIFilter references removed from comments/docs, OR a basic PIIFilter
   logging handler implemented. Evaluate which is more appropriate.
3. ADV-P36-01: `config_validation.validate_config()` delegates to `get_settings()`
   for variable-presence checks. Zero direct `os.environ.get()` calls remain in
   `config_validation.py`.
4. All 4 advisory rows in RETRO_LOG drained (deleted).
5. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/bootstrapper/errors/formatter.py` (ADV-P34-01)
- Modify: `src/synth_engine/bootstrapper/config_validation.py` (ADV-P36-01)
- Modify or delete PIIFilter references (ADV-P34-02)

---

## T37.3 — Update CHANGELOG Through Phase 36

**Priority**: P2 — Documentation currency.

### Context & Constraints

1. `CHANGELOG.md` covers Phase 0.8 through Phase 32. Phases 33-36 are not yet
   documented. These phases include significant changes:
   - P33: Governance hygiene, pydoclint gate, dependency tightening
   - P34: Exception hierarchy unification, RFC 7807 operator error coverage
   - P35: Synthesis layer refactor, behavioral test replacement, E2E integration test
   - P36: Pydantic settings centralization, errors.py decomposition, doc pruning

2. Keep entries concise — 3-5 bullet points per phase, user-facing perspective.

### Acceptance Criteria

1. `CHANGELOG.md` updated with Phase 33-36 entries.
2. Each entry: phase number, date, 3-5 bullet points of key changes.
3. Markdownlint passes.

### Files to Create/Modify

- Modify: `CHANGELOG.md`

---

## T37.4 — Full E2E Demo Run With Production-Worthy Dataset & Screenshots (T36.5 Deferred)

**Priority**: P0 — Final validation. The project's credibility rests on demonstrating the
complete pipeline works end-to-end with a realistic dataset.

### Context & Constraints

This is the deferred T36.5 task. See `docs/backlog/phase-36.md` T36.5 for the full
specification. All context and acceptance criteria from T36.5 apply here.

Key requirements:
- ≥1,000 source rows across 5+ tables with FK relationships
- Every pipeline stage exercised and documented with screenshots
- `docs/E2E_VALIDATION.md` overwritten with current demo results
- `docs/screenshots/` contains current screenshots
- Masking shows correct per-column FPE masking
- Privacy budget shows correct epsilon decrement
- HMAC signature verification passes

### Acceptance Criteria

(Same as T36.5 — see `docs/backlog/phase-36.md` lines 266-275)

### Files to Create/Modify

- Overwrite: `docs/E2E_VALIDATION.md`
- Overwrite: `docs/screenshots/*.png`

---

## Task Execution Order

```
T37.1 (Fix silent budget failure) ──────> sequential (correctness first)
T37.2 (Drain advisories) ──────────────> after T37.1 (some overlap in files)
T37.3 (CHANGELOG update) ──────────────> parallel with T37.1/T37.2
                          all above ──> T37.4 (Full E2E demo)
```

T37.1 must complete first (it changes job_orchestration.py which T37.4 validates).
T37.3 is independent. T37.4 runs LAST as the capstone validation.

---

## Phase 37 Exit Criteria

1. ADV-P35-01 fixed — DP epsilon failure marks job FAILED.
2. All 4 advisory rows drained from RETRO_LOG.
3. CHANGELOG.md current through Phase 36.
4. Full E2E demo completed with ≥1,000 rows, all stages documented.
5. `docs/E2E_VALIDATION.md` current with Phase 37 demo results.
6. All quality gates pass.
7. Zero open advisories in RETRO_LOG.
8. Review agents pass for all tasks.
