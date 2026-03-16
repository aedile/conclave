# Phase 14 — Integration Test Repair & Frontend Lint Fix

**Goal**: Fix 8 failing integration tests (Constitution Priority 4 violation), restore frontend
ESLint configuration for ESLint 9.x, finalize README Phase 13 status, and add missing nosec
justifications. No new features.

**Prerequisite**: Phase 13 must be complete (all tasks merged, retrospective signed off).

---

## T14.1 — Fix Integration Test Failures (DP, Privacy Accountant, SSE)

**Priority**: P0 — Constitution Priority 4 violation (comprehensive testing; integration tests
must pass independently per CLAUDE.md two-gate test policy).

### Context & Constraints

1. `poetry run pytest tests/integration/ -v` produces 5 failures and 3 errors:

   **Failures (5):**
   - `test_dp_integration.py::TestDPTrainingWrapperRealOpacus::test_epsilon_spent_returns_positive_after_training_step`
   - `test_dp_integration.py::TestDPTrainingWrapperRealOpacus::test_check_budget_raises_with_tiny_budget`
   - `test_e2e_synthesis.py::test_spend_budget_raises_on_exhaustion`
   - `test_e2e_synthesis.py::test_spend_budget_exact_boundary_allowed`
   - `test_sse_progress_streaming.py::TestSSEProgressStreaming::test_sse_streams_sequential_progress_events`

   **Errors (3):**
   - `test_privacy_accountant_integration.py::test_concurrent_spend_budget_for_update_prevents_overrun`
   - `test_privacy_accountant_integration.py::test_spend_budget_postgresql_creates_transaction_record`
   - `test_privacy_accountant_integration.py::test_spend_budget_postgresql_raises_on_exhaustion`

2. These are pre-existing failures — they were not introduced by Phase 13 changes.
   Root causes likely include: Opacus API changes, privacy accountant PostgreSQL fixture
   issues, and SSE test timing sensitivity.

3. The unit test suite (809 tests, 96.24% coverage) passes cleanly — this is an
   integration-only issue.

### Acceptance Criteria

1. All 5 failing integration tests pass.
2. All 3 erroring integration tests pass (or are correctly skipped with `@pytest.mark.skip`
   and a written justification if infrastructure is unavailable).
3. `poetry run pytest tests/integration/ -v --no-cov` passes with 0 failures and 0 errors.
4. Unit tests still pass with 90%+ coverage (no regression).

### Testing & Quality Gates

- `poetry run pytest tests/integration/ -v --no-cov` — must pass (this is the primary gate).
- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error` — no regression.
- All review agents spawned.

---

## T14.2 — Frontend ESLint 9.x Configuration & Nosec Justifications

**Priority**: P1 — Frontend linting is non-functional; nosec annotations violate documented standard.

### Context & Constraints

1. `npm run lint` in `frontend/` fails with:
   ```
   ERROR: ESLint couldn't find an eslint.config.(js|mjs|cjs) file.
   From ESLint v9.0.0, the default configuration file is now eslint.config.js.
   ```
   No `eslint.config.js`, `eslint.config.mjs`, or `.eslintrc.*` file exists in `frontend/`.

2. The CI job `Frontend (Lint, Test, Build)` includes `npm run lint` — this step is currently
   failing silently or being skipped.

3. Five `# nosec B608` annotations in production code lack inline justification:
   - `src/synth_engine/modules/ingestion/postgres_adapter.py` lines 154, 158, 171
   - `src/synth_engine/modules/subsetting/egress.py` line 159
   - `src/synth_engine/modules/subsetting/traversal.py` line 267
   Per CLAUDE.md Spike Promotion Checklist item 5: "`# nosec` suppressions require written
   justification in a comment on the same line."

### Acceptance Criteria

1. `frontend/eslint.config.js` (or `.mjs`) created with rules for TypeScript, React, and
   accessibility (jsx-a11y).
2. `cd frontend && npm run lint` passes with 0 errors.
3. All 5 nosec annotations have written justification comments on the same line.
4. `poetry run bandit -c pyproject.toml -r src/` still passes.

### Testing & Quality Gates

- `cd frontend && npm run lint` — must pass.
- `cd frontend && npm run type-check` — must still pass.
- `cd frontend && npx vitest run` — must still pass (120 tests).
- `poetry run bandit -c pyproject.toml -r src/` — must pass.
- All review agents spawned.

---

## T14.3 — README Phase 13 Completion & Phase 14 Status

**Priority**: P2 — Documentation currency (Constitution Priority 6).

### Context & Constraints

1. README.md line 93 still says "Phase 13 — Pre-commit Repair & README Finalization is in
   progress." Phase 13 is complete (retrospective committed).

2. Phase table line 112 shows Phase 13 as "In Progress".

3. Phase 14 row should be added to the README phase table and docs/BACKLOG.md.

### Acceptance Criteria

1. README.md line 93 updated to reflect Phase 14 current status.
2. README.md phase table: Phase 13 → "Complete", Phase 14 row added as "In Progress".
3. `docs/BACKLOG.md` updated to index Phase 14.

### Testing & Quality Gates

- No code changes expected — docs-gate applies.
- All review agents spawned.

---

## Phase 14 Exit Criteria

- All integration tests pass (0 failures, 0 errors).
- Frontend linting operational (`npm run lint` passes).
- All nosec annotations have written justification.
- README current with Phase 13 completion and Phase 14 status.
- All quality gates passing.
- Phase 14 end-of-phase retrospective completed.
