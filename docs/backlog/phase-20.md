# Phase 20 — Human-in-the-Loop Feedback

**Goal**: Address correctness, security, and functionality findings from the comprehensive
5-perspective roast conducted at the end of Phase 19. No new features.

**Prerequisite**: Phase 19 must be complete (all tasks merged, retrospective signed off).

**Source**: Post-Phase 19 roast by Sr Principal Engineer, Sr Principal Architect,
Sr Principal QA Engineer, Sr Principal UI/UX Engineer, and Sr PM.

---

## T20.1 — Exception Handling & Warning Suppression Fixes

**Priority**: P0 — Correctness (Constitution Priority 1).

### Context & Constraints

1. **Broad exception catch in telemetry** — OTEL/telemetry code uses `except Exception`
   which silently swallows errors that should surface. Fix: narrow to specific exception
   types or add `logger.exception()` in the catch block.

2. **`warnings.simplefilter("ignore")` in DP training** — suppresses Opacus `secure_mode`
   warnings globally, which could mask real issues beyond the ADR-0017a-documented
   suppression. Fix: use `warnings.filterwarnings` with a specific message pattern and
   category rather than blanket suppression.

3. **SDV private attribute access** (`_model`) — the synthesizer accesses SDV's private
   `_model` attribute, creating fragile coupling to SDV internals. Fix: use public API
   or document the coupling with a version-pinned comment and integration test.

### Acceptance Criteria

1. All `except Exception` in telemetry/OTEL code narrowed to specific types or
   augmented with `logger.exception()`.
2. `warnings.simplefilter("ignore")` replaced with targeted `warnings.filterwarnings`
   matching specific Opacus warning messages.
3. SDV `_model` access documented with version-pin comment and verified by integration test.
4. Unit tests for all three fixes.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `pre-commit run --all-files`
- All review agents spawned (conditional: QA + DevOps + Architecture).

---

## T20.2 — Integration Test Expansion (Real Infrastructure)

**Priority**: P0 — Test integrity (Constitution Priority 1).

### Context & Constraints

1. **Integration tests don't truly integrate** — only `privacy_accountant` uses real
   PostgreSQL via pytest-postgresql. Other modules (ingestion, subsetting, masking)
   test against mocks only. A mock/prod divergence could ship undetected.

2. **SDV/CTGAN mock proliferation** — the synthesizer module's tests mock the SDV
   library entirely, so if SDV changes its API, tests still pass while production breaks.
   At minimum, one integration test should exercise the real SDV training path.

3. **Silent failure paths not verified** — QA roast found that error paths lack `caplog`
   verification. When code logs a warning and continues, tests should verify the warning
   was emitted, not just that no exception was raised.

### Acceptance Criteria

1. At least 3 new integration tests using real PostgreSQL (pytest-postgresql):
   - Ingestion adapter pre-flight privilege check against real PostgreSQL
   - Subsetting engine FK traversal against real PostgreSQL schema
   - Masking engine deterministic output verified in real PostgreSQL write
2. At least 1 integration test exercising real SDV/CTGAN training (small dataset,
   FORCE_CPU mode). May be slow — mark with `@pytest.mark.slow`.
3. At least 5 existing tests augmented with `caplog` assertions verifying warning/error
   log messages on failure paths.
4. Fixture singleton teardown pattern reviewed — setup verification added where missing.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/ -v --no-cov`
- New integration tests pass with real infrastructure.
- All review agents spawned (conditional: QA + DevOps).

---

## T20.3 — Frontend Accessibility Production Readiness

**Priority**: P0 — Accessibility (Constitution Priority 9 / WCAG 2.1 AA).

### Context & Constraints

1. **No Playwright axe-core e2e tests** — WCAG compliance is claimed but not validated
   end-to-end in a browser. The `@axe-core/playwright` package should be added and
   at least 3 pages tested for a11y violations.

2. **Inline styles conflict with CSP** — `style-src 'self'` CSP header would block
   inline styles. Either extract inline styles to CSS classes or add CSP nonce/hash.

3. **Missing `aria-modal` on toast notifications** — screen readers don't announce
   toasts as modal interruptions. Add `role="alertdialog"` and `aria-modal="true"`
   where appropriate.

4. **No focus trapping in modal dialogs** — tab key can escape modals into background
   content. Implement focus trap using a focus sentinel pattern or `inert` attribute
   on background content.

### Acceptance Criteria

1. `@axe-core/playwright` added to frontend dev dependencies.
2. At least 3 Playwright axe-core e2e tests covering: dashboard, vault unseal, and
   task detail pages. Zero critical or serious a11y violations.
3. All inline `style=` attributes extracted to CSS classes or modules.
4. Toast notifications have `role="alertdialog"` and `aria-modal="true"`.
5. Modal dialogs trap focus — tab key cycles within modal, not into background.

### Testing & Quality Gates

- `npm run lint` passes.
- `npm run test -- --coverage` passes with ≥85% coverage.
- Playwright axe-core tests pass with zero critical/serious violations.
- All review agents spawned (conditional: QA + DevOps + UI/UX).

---

## T20.4 — Architecture Tightening

**Priority**: P1 — Architecture hygiene.

### Context & Constraints

1. **Import-linter not in pre-commit hooks** — module boundary violations are only
   caught in CI (which is currently offline). Fix: add `import-linter` to
   `.pre-commit-config.yaml` so violations are caught at commit time.

2. **ADR-0029 deferred items untracked** — 5 deferred requirements (webhooks, rate
   limiting, mTLS, custom metrics, OTEL propagation) are not in the backlog. Add
   them as explicit "Phase: TBD" items to prevent silent debt accumulation.

3. **Synthesizer `ignore_missing_imports`** — mypy ignores missing imports for `sdv`,
   `opacus`, `ctgan` even when installed, so API breakage goes undetected. Evaluate
   whether to move synthesizer to main deps or add a separate mypy pass.

4. **`fetchall()` OOM risk in key rotation** — the key rotation path may load all
   rows into memory. Verify and fix with server-side cursor or batching if confirmed.

### Acceptance Criteria

1. `import-linter` added as a pre-commit hook (local hook invoking `poetry run lint-imports`).
2. ADR-0029 deferred items added to backlog as "Phase: TBD" entries.
3. Mypy synthesizer strategy documented (ADR or CLAUDE.md amendment).
4. Key rotation verified for OOM safety; if vulnerable, fixed with batched reads.
5. Unit tests for any code changes.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `pre-commit run --all-files` (including new import-linter hook)
- All review agents spawned (conditional: QA + DevOps + Architecture).

---

## T20.5 — Polish Batch (Cosmetic & Documentation)

**Priority**: P2 — Documentation hygiene.

### Context & Constraints

Batched cosmetic findings from the roast (per Rule 16 — materiality threshold):

1. `schema_topology.py` placement rationale underdocumented in CLAUDE.md.
2. `ARCHITECTURAL_REQUIREMENTS.md` lacks forward reference to ADR-0029 gap analysis.
3. No ADR archival/supersedure policy.
4. CONSTITUTION enforcement table lacks automated verification CI script.
5. Optional ADR-0032: Worker Task Configuration Pattern (when modules may read env
   vars directly vs. going through bootstrapper).
6. README Phase 19 completion status update.

### Acceptance Criteria

1. CLAUDE.md File Placement Rules updated with neutral value object exception note.
2. `ARCHITECTURAL_REQUIREMENTS.md` preamble added referencing ADR-0029.
3. ADR template updated with `Status: Accepted | Superseded by ADR-00XX | Rejected`.
4. README updated with Phase 19 completion and Phase 20 in-progress status.
5. `pre-commit run --all-files` passes.

### Testing & Quality Gates

- `pre-commit run --all-files`
- All review agents spawned (conditional: QA + DevOps — docs/process task).

---

## Phase 20 Exit Criteria

- All `except Exception` in telemetry narrowed or augmented.
- Opacus warning suppression targeted, not blanket.
- SDV `_model` coupling documented and tested.
- Integration tests added for ingestion, subsetting, masking (real PostgreSQL).
- Real SDV training integration test added.
- `caplog` assertions added to failure path tests.
- Playwright axe-core e2e tests passing (zero critical/serious).
- Inline styles extracted from frontend.
- Toast aria-modal and focus trapping implemented.
- Import-linter in pre-commit hooks.
- ADR-0029 deferred items tracked in backlog.
- Key rotation OOM safety verified.
- Documentation polish complete.
- All quality gates passing (locally).
- Phase 20 end-of-phase retrospective completed.
