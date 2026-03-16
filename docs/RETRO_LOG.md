# Conclave Engine — Retrospective Log

Living ledger of review retrospective notes and open advisory items.
Updated after each task's review phase completes.

---

## Open Advisory Items

Advisory findings without a resolved target task are tracked here.
Drain (delete) rows when their target task is completed.

| ID | Source | Target Task | Severity | Advisory |
|----|--------|-------------|----------|----------|
| ADV-015 | P17-T17.1 DevOps review | P17 or next pgbouncer task | BLOCKER | `pgbouncer/pgbouncer:1.23.1` does not exist in Docker Hub (Registry v2 API confirmed tag unknown; max available is 1.15.0). Cannot be SHA-256 pinned until the image reference is replaced with a valid image. Candidate: `edoburu/pgbouncer:v1.23.1-p3` (verified available). Requires ADR per Rule 6 (technology substitution). Blocks supply chain security completeness for the pgbouncer service. |

---

## Task Reviews

---

### [2026-03-16] P17-T17.2 — Dashboard WCAG Form Accessibility Parity

**Changes**:
- `frontend/src/routes/Dashboard.tsx`: Added `aria-required="true"` to all 4 form inputs
  (`table_name`, `parquet_path`, `total_epochs`, `checkpoint_every_n`). Added
  `aria-invalid="true"` to `total_epochs` and `checkpoint_every_n` when client-side
  validation fails. Visible asterisks wrapped with `aria-hidden="true"`. Form validation
  error div (`role="alert"`) changed from conditional mount/unmount to always-present
  container with conditional text content (UI/UX review fix).
- `frontend/src/__tests__/Dashboard.test.tsx`: 5 new tests for aria attribute presence.
  4 existing RFC 7807 tests updated to handle multiple `role="alert"` elements.
- `.env.example`: Added `OTEL_EXPORTER_OTLP_ENDPOINT` documentation section with
  explanatory comments about optional observability configuration. Fixed `pip install` →
  `poetry add` in the Requires comment (DevOps review fix).
- `tests/unit/test_docker_image_pinning.py`: Added `type: ignore` justification comment
  (T17.1 arch review carry-forward).

**Quality Gates**:
- ruff check: PASS, ruff format: PASS, mypy: PASS, bandit: PASS
- Frontend lint: PASS, type-check: PASS, test coverage: 98.84% (131/131) — PASS
- pre-commit (all hooks): PASS

**QA** (PASS):
dead-code PASS — no dead code introduced. reachable-handlers PASS — all test branches
reachable. exception-specificity PASS. silent-failures PASS. coverage-gate PASS — 98.84%
frontend coverage. edge-cases PASS — both valid and invalid states tested for aria
attributes. meaningful-asserts PASS — all assertions verify specific aria attribute values.
backlog-compliance PASS — all 5 ACs addressed.

**DevOps** (PASS with advisory):
hardcoded-credentials PASS. no-pii-in-code PASS. supply-chain PASS. dependency-management
ADVISORY — `.env.example` line 216 said `pip install` instead of `poetry add` for
opentelemetry-exporter-otlp. Fixed in review fix commit.

**UI/UX** (FINDING — 1 blocker fixed):
aria-required PASS — all 4 inputs have `aria-required="true"`. aria-invalid PASS —
`total_epochs` and `checkpoint_every_n` correctly set `aria-invalid="true"` on validation
failure. aria-hidden PASS — visible asterisks wrapped with `aria-hidden="true"`.
FINDING: `role="alert"` div for form validation errors used conditional mount/unmount
(`{formValidationError !== null && (...)}`). NVDA+Firefox can silently swallow repeat
error announcements when the container is destroyed and recreated with identical content.
Fix: changed to always-present container with conditional text content. Padding collapses
to 0 when empty. Fixed in review fix commit.

**Retrospective Note**:
The Unseal.tsx → Dashboard.tsx WCAG parity task revealed a subtle screen reader
announcement bug: conditional rendering of `role="alert"` containers works for one-shot
errors but fails for repeated identical errors in NVDA+Firefox. The always-present
container pattern (render container, conditionally fill content) is more robust. This
should be the standard pattern going forward for all `role="alert"` containers in the
project.

---

### [2026-03-16] P17-T17.3 — CLAUDE.md Stale References, Backlog Spec Fix & Branch Cleanup

**Changes**:
- `CLAUDE.md`: 4 stale `AUTONOMOUS_DEVELOPMENT_PROMPT.md` references replaced with current equivalents
- `docs/backlog/phase-16.md`: "Migration 002" → "Migration 003" (5 occurrences corrected)
- 4 ADR files: format inconsistency fixed (`**Status**:` → `**Status:**`)
- `README.md`: Phase 16 → Complete, Phase 17 → In Progress
- `docs/BACKLOG.md`: Phase 17 indexed
- 5 stale remote branches deleted (P15-T15.2, P16-T16.1, P16-T16.2, P16-T16.3, fix/P16-T16.3)

**Quality Gates**: Docs-only task. pre-commit: PASS. No Python code changes.

**QA** (PASS): Coverage 96.24% unchanged. All doc cross-references verified internally
consistent. Phase-16 migration number corrected across all 5 occurrences.

**DevOps** (PASS): gitleaks clean. No new dependencies, env vars, or attack surface.
docs-gate CI satisfied by docs: commit prefix.

**UI/UX** (SKIP): No UI surface area.

**Retrospective Note**:
The AUTONOMOUS_DEVELOPMENT_PROMPT.md retirement (Phase 3.5) left 4 stale references that
survived until Phase 17. Future doc-retirement operations should include a grep-and-replace
sweep as part of the retirement commit itself to avoid multi-phase cleanup.

---

### [2026-03-16] P17-T17.1 — Docker Base Image SHA-256 Pinning (ADV-014)

**Changes**:
- `Dockerfile`: All three FROM lines pinned to SHA-256 digests via Docker Registry v2 API.
  - `node:20-alpine@sha256:b88333c42...` (stage 1 frontend builder)
  - `python:3.14-slim@sha256:6a27522...` (stages 2 and 3 — identical digest, intentional)
  - Three `TODO(ADV-014)` comments removed; version tags preserved as inline comments.
- `docker-compose.yml`: Six of seven external service images pinned to SHA-256 digests.
  - `redis:7-alpine`, `postgres:16-alpine`, `prom/prometheus:v2.53.0`,
    `prom/alertmanager:v0.27.0`, `grafana/grafana:11.3.0`, `minio/minio:RELEASE.2024-01-28T22-35-53Z`
  - `pgbouncer/pgbouncer:1.23.1` — NOT pinned. Tag confirmed non-existent in Docker Hub.
    `WARNING(P17-T17.1)` comment added. Tracked as ADV-015 (BLOCKER).
- `tests/unit/test_docker_image_pinning.py`: 17 new file-inspection tests covering
  Dockerfile FROM lines and docker-compose.yml image lines. pgbouncer invalid tag
  documented and excluded from blanket pinning check with dedicated test.

**Quality Gates**:
- ruff check: PASS, ruff format: PASS, mypy: PASS, bandit: PASS
- pytest: 842 passed, 1 skipped, 96.24% coverage — PASS
- pre-commit (all hooks): PASS

**QA** (PASS):
dead-code PASS — no dead code; `_PGBOUNCER_UNPINNABLE_MARKER` constant used in
`_extract_image_lines` and `test_pgbouncer_invalid_tag_is_documented`. reachable-handlers
PASS — all test branches reachable. exception-specificity PASS — tests use only `assert`
and `pytest.fail`. silent-failures PASS — no try/except swallows. coverage-gate PASS
— 96.24% total coverage. edge-cases PASS — pgbouncer invalid tag case explicitly tested.
meaningful-asserts PASS — all assertions carry descriptive failure messages. backlog-compliance
PASS — all AC items addressed; pgbouncer partial resolution is honest and documented.

**Architecture** (PASS):
file-placement PASS — test in `tests/unit/`, no src/ files modified. naming-conventions
PASS — `TestDockerfileSHA256Pinning`, `TestDockerComposeSHA256Pinning` follow PascalCase.
dependency-direction PASS — test file imports only `re`, `pathlib`, `pytest`; no circular
imports. abstraction-level PASS — `_extract_from_lines` and `_extract_image_lines` are clean
single-responsibility helpers. interface-contracts PASS — all helper functions fully typed
with Google docstrings. adr-compliance ADVISORY — pgbouncer replacement
(`edoburu/pgbouncer`) is a technology substitution requiring an ADR per Rule 6; tracked as
ADV-015 BLOCKER so it cannot proceed without ADR.

**DevOps** (FINDING — 1 blocker documented):
hardcoded-credentials PASS — digests are content hashes, not secrets. no-pii-in-code PASS.
supply-chain-pinning PARTIAL — 8 of 9 external image references now pinned; pgbouncer
unpinnable due to invalid tag (ADV-015 BLOCKER). digest-provenance PASS — all digests
obtained via Docker Registry v2 API; none fabricated; API calls documented in commit body.
refresh-path PASS — each pinned line has a `To refresh:` comment with the exact
`docker pull ... && docker inspect ...` command. split-brain-prevention PASS — python
stages 2 and 3 use identical digest with explicit comment. TODO-cleanup PASS — all three
`TODO(ADV-014)` comments removed from Dockerfile; WARNING comment added for pgbouncer.
FINDING: `pgbouncer/pgbouncer:1.23.1` does not exist in Docker Hub. Tag unknown — only
versions ≤1.15.0 published. This is a pre-existing bug elevated here: the compose file
was referencing a phantom tag. Tracked as ADV-015 (BLOCKER) — must be replaced with a
valid image+digest before any production deployment.

**UI/UX** (SKIP): No UI surface area.

**Retrospective Note**:
SHA-256 pinning for Docker images requires a live Docker daemon OR authenticated access to
the Docker Registry v2 API. This task used the registry API directly (without Docker daemon)
which is a valid pattern for air-gapped and CI environments. The key lesson: before declaring
an image reference "pinnable", verify the tag exists in the registry — `pgbouncer/pgbouncer:1.23.1`
is a phantom tag that was silently referenced for at least 17 phases without anyone noticing.
Image reference validation (does the tag exist?) should be a separate pre-production checklist
item distinct from SHA-256 pinning. Future tasks: when replacing pgbouncer image, require an
ADR per Rule 6 since it is a technology substitution (different image repository).

---

### [2026-03-16] Phase 16 End-of-Phase Retrospective

**Phase Goal**: Close Alembic migration drift for epsilon columns (correctness risk),
fix undeclared frontend dependencies (supply chain auditability), improve nosec
justification accuracy, add missing operator documentation, and add WCAG skip
navigation. No new features.

**Exit Criteria Verification**:
- Alembic migration 003 applies and reverts cleanly: PASS (T16.1 — PR #82).
- ADR-0030 documents Float→Numeric precision decision: PASS (T16.1 — PR #82).
- Frontend supply chain — all imports declared as direct devDependencies: PASS (T16.2 — PR #83).
- nosec B608 justification accurate (caller-contract, not overclaimed validation): PASS (T16.2 — PR #83).
- `.env.example` documents production mode variables: PASS (T16.2 — PR #83).
- Skip navigation link present and tested: PASS (T16.3 — PR #84, fix PR #85).
- README current with Phase 15 completion and Phase 16 status: PASS (T16.3 — PR #84).
- All stale remote branches cleaned: PASS (T16.3 — PR #84; auto-delete now enabled).
- GitHub auto-delete branches enabled: PASS (T16.3 — PR #84).
- All quality gates passing: PASS. Open advisory count: **0**.
- Phase 16 end-of-phase retrospective completed (this entry).

**What went well**:
1. All three review cycles caught real issues that were fixed before (or immediately
   after) merge: QA caught weak test assertions in T16.1, QA caught docstring/nosec
   contradiction in T16.2, UI/UX caught tabIndex and AriaLive hiding in T16.3.
   The review agent pattern continues to earn its keep.
2. The GitHub "Automatically delete head branches" setting — noted in Phase 12, 15,
   and 15 retrospectives — was finally resolved by making it an explicit acceptance
   criterion in T16.3. This validates the retro lesson: infrastructure hygiene items
   must be converted to concrete ACs, not left as retro notes.
3. ADR-0030 properly documented a 7-phase-old technology substitution (Float→Numeric)
   that had been living only in a docstring comment. The migration drift is now closed
   with both a migration and an ADR.
4. Zero open advisories throughout the entire phase. Advisory table remains clean.

**What could improve**:
1. PR #84 (T16.3) auto-merged before the UI/UX review agent completed, requiring a
   follow-up PR #85 for the tabIndex and AriaLive fixes. The auto-merge via
   `gh pr merge --squash --auto` fires as soon as CI passes, which can race with
   slow review agents. Lesson: review commits should be pushed to the PR branch
   BEFORE `gh pr merge --auto` is called, not after. The PM should ensure all four
   review agents complete before enabling auto-merge.
2. The nosec B608 fix (T16.2) required updating both the inline annotation AND the
   docstring — but the developer only updated the annotation on the first pass. QA
   caught the docstring contradiction. Lesson (reinforced): when rewriting security
   annotations, atomically update all co-located documentation describing the same
   trust boundary. This lesson was captured in T16.2's retrospective note and should
   be included in future briefs touching nosec annotations.
3. The backlog spec for T16.1 said "migration 002" but the actual next migration was
   003 (002 already existed). The developer correctly used 003, but the spec was wrong.
   Lesson: backlog specs referencing sequence numbers should verify the current state
   of the sequence before writing the spec, or use relative references ("next migration")
   instead of absolute ones.

---
### [2026-03-16] P16-T16.3 -- WCAG Skip Navigation, README Update & Branch Cleanup

**Changes**:
- `frontend/src/App.tsx`: Skip-to-content link added as first rendered element,
  before ErrorBoundary, using `className="skip-link"` and `href="#main-content"`.
- `frontend/src/styles/global.css`: `.skip-link` and `.skip-link:focus` rules
  added (WCAG 2.1 AA 2.4.1). Hidden off-screen by default; fixed-position and
  visible at viewport top-left on keyboard focus.
- `frontend/src/routes/Dashboard.tsx`: `<main id="main-content">`.
- `frontend/src/routes/Unseal.tsx`: `<main id="main-content">`.
- `README.md`: Line 93 updated to Phase 16 current status. Phase 15 -> Complete.
  Phase 16 row added as In Progress.
- `docs/BACKLOG.md`: Phase 16 added to Phase Hierarchy and Task Index.
- GitHub repo setting `delete_branch_on_merge` set to `true` via `gh api`.
  Stale branches (P15-T15.2, P16-T16.1, P16-T16.2) already absent from origin.

**Quality Gates**:
- npm lint: PASS, npm test:coverage: 97.36% PASS (126 tests), npm type-check: PASS
- ruff: PASS, mypy: PASS, bandit: PASS
- pre-commit: PASS (all hooks)

**Reviews**:
- QA: PASS — 3 new skip-link tests, 126 total, 97.37% coverage
- UI/UX: FINDING (2 items, both fixed) — (1) main elements needed tabIndex={-1}
  for proper focus transfer in Firefox/Safari; added to Dashboard.tsx and Unseal.tsx.
  (2) AssertiveAnnouncement lacked visually-hidden styles; added inline styles
  matching PoliteAnnouncement pattern in AriaLive.tsx.
- DevOps: PASS — no secrets, no new dependencies, GitHub auto-delete enabled

**Retrospective Note**: The GitHub "Automatically delete head branches" setting
had been noted in three consecutive phase retrospectives (Phase 12, Phase 15,
and the Phase 15 end-of-phase retro) but was never acted upon. It took being
an explicit acceptance criterion in T16.3 to finally get it enabled. Lesson:
infrastructure hygiene items noted in retrospectives must be converted to
explicit acceptance criteria in a concrete task -- retro notes alone are
insufficient enforcement.

### [2026-03-16] P16-T16.2 — Frontend Supply Chain & Nosec Accuracy

**Changes**:
- `frontend/package.json`: Added `@eslint/js` (^9.39.4) and `globals` (^14.0.0) to
  devDependencies. Previously resolved only as transitive deps of `eslint`.
- `src/synth_engine/modules/subsetting/traversal.py`: Rewrote nosec B608 justification
  on line 142 and updated `_execute_seed` docstring to remove inaccurate "pre-validated"
  claim. Both now describe the actual caller-contract defense.
- `.env.example`: Added ENV/CONCLAVE_ENV documentation for production mode detection.

**Quality Gates**:
- npm lint: PASS, npm test:coverage: 97.35% PASS
- ruff: PASS, mypy: PASS, bandit: PASS
- pytest unit: 825 passed, 96.24% coverage

**Reviews**:
- QA: FINDING (1 item, fixed) — docstring at line 133 still said "pre-validated"
  after nosec annotation was corrected; docstring updated to match.
- UI/UX: SKIP — no template/route/form changes
- DevOps: PASS — explicit devDeps improve supply chain auditability, no secrets

**Retrospective Note**: When a `# nosec` justification is rewritten, the corresponding
docstring's description of that same parameter must be updated atomically in the same
diff. Security annotations and docstrings that describe the same trust boundary must
never contradict each other.

---

### [2026-03-16] P16-T16.1 — Alembic Migration 003: Epsilon Column Precision Fix

**Changes**:
- `alembic/versions/003_fix_epsilon_column_precision.py`: migration 003 ALTERs three
  epsilon columns on `privacy_ledger` and `privacy_transaction` from FLOAT8 to
  NUMERIC(20,10). Revision chain 003 -> 002. Reversible via downgrade.
- `docs/adr/ADR-0030-float-to-numeric-epsilon-precision.md`: ADR documenting the
  Float -> NUMERIC technology substitution (CLAUDE.md Rule 6), ADV-050 rationale,
  migration path, and alternatives. Status: Accepted.
- `src/synth_engine/modules/privacy/ledger.py`: module docstring migration note updated
  from stale "Alembic not yet initialised -- T8.4" to "resolved -- migration 003".
- `tests/unit/test_migration_003_epsilon_precision.py`: 16 file-inspection tests
  covering migration existence, revision chain, ALTER operations, Numeric type,
  Float downgrade, docstring update, and ADR-0030 presence.

**Quality Gates**:
- ruff: PASS (0 issues)
- mypy: PASS (80 source files, 0 issues)
- bandit: PASS (0 HIGH/MEDIUM findings)
- pytest unit: 825 passed, 1 skipped, 96.24% coverage
- pytest integration: 72 passed

**Reviews**:
- QA: FINDING (2 items, both fixed) — (1) test_downgrade_reverts_to_float was vacuously
  satisfiable (bare `"Float" in content` matched upgrade body); fixed to
  `content.count("type_=sa.Float()") >= 3`. (2) test_upgrade_targets_numeric_20_10 used
  bare `"20"/"10"` checks matching docstrings; fixed to `"precision=20"/"scale=10"`.
- UI/UX: SKIP — no template/route/form/frontend changes
- DevOps: PASS — no secrets, migration reversible, chain intact (001→002→003),
  ADR-0030 satisfies Rule 6. Advisory: lock contention runbook gap for large tables.
- Architecture: PASS — file placement correct, dependency direction clean, ADR-0030
  compliant with CLAUDE.md Rule 6, no cross-module violations

**Retrospective Note**: The Float → NUMERIC mismatch between migration 001 and
ledger.py persisted from Phase 8 through Phase 15 (7 phases) because the debt note
in the docstring used a task reference (T8.4) as a proxy for an open work item.
Going forward, migration debt notes in ORM docstrings should be tracked as explicit
advisory items in RETRO_LOG with BLOCKER severity so they surface in phase-entry
gate reviews, not just in docstring comments.

---


### [2026-03-16] Phase 15 End-of-Phase Retrospective

**Phase Goal**: Fix frontend test coverage gate (85.66% < 90%), enforce coverage in CI,
clean up stale remote branches, and update README Phase 14 completion. No new features.

**Exit Criteria Verification**:
- Frontend test coverage gate passes: 97.35% lines/statements (T15.1 — PR #80).
- Frontend coverage enforced in CI: `npm run test:coverage` already in pipeline (verified).
- README current with Phase 14 completion and Phase 15 status (T15.2 — PR #81).
- All stale remote branches cleaned: 8 deleted, only main remains (T15.2 — PR #81).
- All quality gates passing. Open advisory count: **0**.
- Phase 15 end-of-phase retrospective completed (this entry).

**What went well**:
1. Root cause analysis was precise: two non-source files (eslint.config.js, vite-env.d.ts)
   dragging down coverage denominator, plus 3 untested catch blocks in useSSE.ts.
2. Fix was minimal and targeted: 62 lines added (3 tests + 2 config exclusions + helper method).
3. CI already enforced `npm run test:coverage` — the gate existed but was failing silently
   because prior PRs didn't fail on it (no required status checks). Now verified working.

**What could improve**:
1. The coverage gate was broken since Phase 14 T14.2 (when eslint.config.js was created)
   but was not caught because the Phase 14 acceptance criteria said "npm run lint passes"
   rather than "npm run test:coverage passes". Lesson: phase exit criteria should explicitly
   include coverage verification for both backend AND frontend.
2. Stale branches continue to accumulate. The "Automatically delete head branches" GitHub
   setting should be enabled to prevent this permanently. This has been noted in Phase 12
   and Phase 15 retrospectives — it should now be treated as a standing action item.

---

### [2026-03-16] P15-T15.2 — README Phase 14 Completion & Operational Cleanup

**Changes**: Updated README.md Phase 14 → Complete, Phase 15 → In Progress. Updated
docs/BACKLOG.md with Phase 15 index. Deleted 8 stale remote feature branches.

**Reviews**:
- QA: SKIP — docs-only
- UI/UX: SKIP — no template/route/form changes
- DevOps: PASS — branch cleanup, gitleaks clean

**Retrospective Note**: Stale branch accumulation continues despite T12.1 cleanup.
GitHub "Automatically delete head branches" should be enabled at the repo level.

---

### [2026-03-16] P15-T15.1 — Frontend Test Coverage Gate Repair

**Changes**: Added 3 malformed SSE payload tests for useSSE.ts catch blocks. Excluded
`eslint.config.js` and `src/vite-env.d.ts` from vitest coverage measurement. Added
`simulateRawEvent` helper to mock-event-source.ts.

Coverage: 85.66% → 97.35% (all thresholds now exceed 90%). Tests: 120 → 123.

**Reviews**:
- QA: PASS — coverage gate repaired, 3 new meaningful assertions
- UI/UX: SKIP — test infrastructure only
- DevOps: PASS — no secrets, no new dependencies, CI already gates coverage

**Retrospective Note**: The root cause was config files (eslint.config.js, vite-env.d.ts)
being counted in coverage when they have no executable code. This should have been caught
in Phase 14 T14.2 when eslint.config.js was created — the file immediately entered the
coverage denominator. Lesson: when adding new non-source files to a directory measured by
coverage, check whether the coverage config excludes them.

---


## Archived Reviews

Detailed reviews for phases 0-14 are archived in `docs/retro_archive/`.
