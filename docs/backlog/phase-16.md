# Phase 16 — Migration Drift, Supply Chain & Accessibility Polish

**Goal**: Close the Alembic migration drift for epsilon columns (correctness risk), fix
undeclared frontend dependencies (supply chain auditability), improve nosec justification
accuracy, add missing operator documentation, and add WCAG skip navigation. No new features.

**Prerequisite**: Phase 15 must be complete (all tasks merged, retrospective signed off).

---

## T16.1 — Alembic Migration 003: Epsilon Column Precision Fix

**Priority**: P0 — Correctness risk (Constitution Priority 4 violation). New deployments via
migrations create FLOAT8 epsilon columns while ORM writes NUMERIC(20,10).

### Context & Constraints

1. Migration 001 (`alembic/versions/001_add_privacy_ledger_tables.py`) creates epsilon columns
   as `sa.Float()` (FLOAT8 / DOUBLE PRECISION on PostgreSQL):
   - Line 74: `total_allocated_epsilon` — `sa.Float()`
   - Line 75: `total_spent_epsilon` — `sa.Float()`
   - Line 91: `epsilon_spent` — `sa.Float()`

2. ORM models (`src/synth_engine/modules/privacy/ledger.py`) declare the same columns as
   `Numeric(precision=20, scale=10)` (lines 137-154, 208-214). This was changed in Phase 8
   (ADV-050) to prevent floating-point accumulation drift.

3. The ledger.py docstring (lines 28-44) explicitly acknowledges the migration debt:
   > "When Alembic is wired in T8.4, the migration for existing deployments must ALTER
   > the columns from DOUBLE PRECISION / FLOAT8 to NUMERIC(20, 10)."
   T8.4 was completed without creating this migration.

4. No ADR exists for the Float→Numeric precision decision. Per CLAUDE.md Rule 6, this
   technology substitution requires an ADR.

### Acceptance Criteria

1. New Alembic migration 003 created that ALTERs `total_allocated_epsilon`,
   `total_spent_epsilon` (PrivacyLedger), and `epsilon_spent` (PrivacyTransaction) from
   `FLOAT8` / `DOUBLE PRECISION` to `NUMERIC(20, 10)`.
2. `poetry run alembic upgrade head` applies cleanly on a fresh database.
3. `poetry run alembic downgrade -1` cleanly reverts migration 003.
4. ADR-0030 created documenting the Float→Numeric precision decision, rationale
   (ADV-050 accumulation drift), and migration path.
5. ledger.py docstring migration note (lines 28-44) updated to reference migration 003.

### Testing & Quality Gates

- `poetry run alembic upgrade head` — must succeed.
- `poetry run alembic downgrade -1` — must succeed (reversibility).
- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error` — no regression.
- `poetry run pytest tests/integration/ -v --no-cov` — no regression.
- All review agents spawned.

---

## T16.2 — Frontend Supply Chain & Nosec Accuracy

**Priority**: P1 — Supply chain auditability (Constitution Priority 0) + security annotation accuracy.

### Context & Constraints

1. `frontend/eslint.config.js` imports `@eslint/js` (line 16) and `globals` (line 22), but
   neither is declared in `frontend/package.json` devDependencies. They resolve via transitive
   dependencies of `eslint`, but this is fragile and breaks supply chain auditability. Explicit
   declaration pins the version and makes the dependency visible in lockfile audits.

2. `src/synth_engine/modules/subsetting/traversal.py` line 142 has `# nosec B608` with
   justification: "seed_query is a pre-validated application-controlled SELECT; never
   constructed from user input." The method `_execute_seed()` receives a raw string with
   zero validation, zero parsing, zero quoting. The actual defense is a caller-contract:
   only `SubsettingEngine` calls this method, and the seed query originates from bootstrapper
   configuration. The justification should accurately describe this defense rather than
   claiming "pre-validated" when no validation code exists in the method.

3. `.env.example` does not document the `ENV` or `CONCLAVE_ENV` environment variables that
   `config_validation.py` (lines 56-57) checks to detect production mode. Operators deploying
   to production have no template guidance for enabling stricter validation.

### Acceptance Criteria

1. `@eslint/js` and `globals` added to `frontend/package.json` devDependencies with pinned
   versions matching those currently resolved in node_modules.
2. `npm ci && npm run lint` still passes after adding the dependencies.
3. nosec B608 justification on `traversal.py:142` rewritten to accurately describe the
   caller-contract defense (e.g., "seed_query is supplied by SubsettingEngine from
   bootstrapper-controlled configuration; not constructed from user input").
4. `.env.example` updated to document `ENV` and `CONCLAVE_ENV` with explanatory comments
   about production mode detection.
5. `poetry run bandit -c pyproject.toml -r src/` still passes.

### Testing & Quality Gates

- `cd frontend && npm ci && npm run lint` — must pass.
- `cd frontend && npm run test:coverage` — must pass (90%+ all thresholds).
- `poetry run bandit -c pyproject.toml -r src/` — must pass.
- All review agents spawned.

---

## T16.3 — WCAG Skip Navigation, README Update & Branch Cleanup

**Priority**: P2 — Accessibility (Constitution Priority 9) + documentation currency (Priority 6).

### Context & Constraints

1. Frontend lacks a skip-to-content navigation link. WCAG 2.1 AA Success Criterion 2.4.1
   requires "A mechanism is available to bypass blocks of content that are repeated on
   multiple Web pages." While the app currently has minimal navigation (2 routes), the
   requirement applies regardless of page count.

2. README.md line 93 says "Phase 15 — Frontend Coverage Gate & Operational Polish is in
   progress." Phase 15 is complete (retrospective committed). Phase table line 114 also
   shows Phase 15 as "In Progress."

3. `origin/feat/P15-T15.2-readme-cleanup` is a stale merged branch that was not cleaned
   up during T15.2.

4. GitHub "Automatically delete head branches" setting has been noted in Phase 8, 12, and
   15 retrospectives but has never been enabled. This is a one-time repo settings change.

### Acceptance Criteria

1. Skip-to-content link added to the root layout (visually hidden, visible on focus,
   targets `#main-content`). The `<main>` elements in Dashboard.tsx and Unseal.tsx must
   have `id="main-content"`.
2. Skip link tested: keyboard-accessible, screen-reader-announced, visible on `:focus`.
3. README.md line 93 updated to reflect Phase 16 current status.
4. README.md phase table: Phase 15 → "Complete", Phase 16 row added as "In Progress".
5. `docs/BACKLOG.md` updated to index Phase 16.
6. `origin/feat/P15-T15.2-readme-cleanup` deleted.
7. GitHub "Automatically delete head branches" setting confirmed enabled (screenshot or
   `gh api` verification if available).

### Testing & Quality Gates

- `cd frontend && npm run lint` — must pass.
- `cd frontend && npm run test:coverage` — must pass (90%+ all thresholds).
- `cd frontend && npm run type-check` — must still pass.
- All review agents spawned.

---

## Phase 16 Exit Criteria

- Alembic migration 003 applies and reverts cleanly.
- ADR-0030 documents Float→Numeric precision decision.
- Frontend supply chain: all imports declared as direct devDependencies.
- nosec B608 justification accurate (caller-contract, not overclaimed validation).
- `.env.example` documents production mode variables.
- Skip navigation link present and tested.
- README current with Phase 15 completion and Phase 16 status.
- All stale remote branches cleaned.
- GitHub auto-delete branches enabled.
- All quality gates passing.
- Phase 16 end-of-phase retrospective completed.
