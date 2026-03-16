# Phase 15 — Frontend Coverage Gate & Operational Polish

**Goal**: Fix frontend test coverage gate (85.66% < 90%), enforce coverage in CI,
clean up stale remote branches, and update README Phase 14 completion. No new features.

**Prerequisite**: Phase 14 must be complete (all tasks merged, retrospective signed off).

---

## T15.1 — Frontend Test Coverage Gate Repair

**Priority**: P0 — Quality gate broken (Constitution Priority 1 violation).

### Context & Constraints

1. `npm run test:coverage` in `frontend/` fails:
   ```
   ERROR: Coverage for lines (85.66%) does not meet global threshold (90%)
   ERROR: Coverage for statements (85.66%) does not meet global threshold (90%)
   ```

2. Root causes:
   - `eslint.config.js` (0% coverage) is a config file incorrectly included in coverage
     measurement. It should be excluded in `vitest.config.ts` coverage.exclude.
   - `vite-env.d.ts` (0% coverage) is a TypeScript declaration file, also should be excluded.
   - `useSSE.ts` has 71.27% coverage — error/reconnect paths (lines 37-135, 151-159)
     are not tested.

3. The CI frontend job (`.github/workflows/ci.yml`) runs `npm run test` but NOT
   `npm run test:coverage`. The 90% threshold exists in `vitest.config.ts` but is
   not enforced in CI — it is advisory-only.

4. Phase 14 exit criteria claimed "Frontend linting operational" but did not verify
   coverage. This is a process gap.

### Acceptance Criteria

1. `vitest.config.ts` coverage.exclude updated to exclude `eslint.config.js` and
   `vite-env.d.ts` (config/declaration files, not application code).
2. `useSSE.ts` error/reconnect paths tested — coverage for hooks/ reaches 90%+.
3. `npm run test:coverage` passes with 90%+ on all thresholds (lines, statements,
   branches, functions).
4. CI frontend job updated: replace `npm run test` with `npm run test:coverage`
   (or add coverage step) so the 90% gate is enforced in CI.

### Testing & Quality Gates

- `cd frontend && npm run test:coverage` — must pass (90%+ all thresholds).
- `cd frontend && npm run lint` — must still pass.
- `cd frontend && npm run type-check` — must still pass.
- All review agents spawned.

---

## T15.2 — README Phase 14 Completion & Operational Cleanup

**Priority**: P1 — Documentation currency (Constitution Priority 6) + workspace hygiene.

### Context & Constraints

1. README.md line 93 says "Phase 14 — Integration Test Repair & Frontend Lint Fix
   is in progress." Phase 14 is complete (retrospective committed).

2. Phase table line 113 shows Phase 14 as "In Progress".

3. Phase 15 row should be added to the README phase table and docs/BACKLOG.md.

4. 6 stale remote feature branches remain from Phases 12-14:
   - `origin/feat/P12-T12.1-*`, `origin/feat/P12-T12.2-*`
   - `origin/feat/P13-T13.1-*`
   - `origin/feat/P14-T14.1-*`, `origin/feat/P14-T14.2-*`, `origin/feat/P14-T14.3-*`
   All are merged. Should be deleted.

5. GitHub repo setting "Automatically delete head branches" should be enabled to
   prevent future accumulation.

### Acceptance Criteria

1. README.md line 93 updated to reflect Phase 15 current status.
2. README.md phase table: Phase 14 → "Complete", Phase 15 row added as "In Progress".
3. `docs/BACKLOG.md` updated to index Phase 15.
4. All merged remote branches deleted (only `main` and active in-progress branches remain).

### Testing & Quality Gates

- No code changes expected — docs-gate applies.
- All review agents spawned.

---

## Phase 15 Exit Criteria

- Frontend test coverage gate passes (90%+ all thresholds).
- Frontend coverage enforced in CI (`npm run test:coverage` in pipeline).
- README current with Phase 14 completion and Phase 15 status.
- All stale remote branches cleaned.
- All quality gates passing.
- Phase 15 end-of-phase retrospective completed.
