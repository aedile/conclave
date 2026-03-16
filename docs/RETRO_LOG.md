# Conclave Engine — Retrospective Log

Living ledger of review retrospective notes and open advisory items.
Updated after each task's review phase completes.

---

## Open Advisory Items

Advisory findings without a resolved target task are tracked here.
Drain (delete) rows when their target task is completed.

| ID | Source | Target Task | Severity | Advisory |
|----|--------|-------------|----------|----------|
| *(none)* | | | | All advisories drained. |

---

## Task Reviews

---

### [2026-03-16] P21-T21.2 — Masking Algorithm Split: first_name, last_name, address

**Changes**:
- `src/synth_engine/modules/masking/algorithms.py`: Added `mask_first_name`, `mask_last_name`,
  `mask_address` functions using `Faker.first_name()`, `Faker.last_name()`, `Faker.address()`
  respectively. `mask_name` preserved unchanged for backward compat.
- `src/synth_engine/bootstrapper/cli.py`: Updated `_COLUMN_MASKS` to wire correct per-column
  functions. Added type annotation comment.
- `src/synth_engine/modules/masking/registry.py`: Added `ColumnType.FIRST_NAME`, `LAST_NAME`,
  `ADDRESS` enum members with `_apply()` dispatch.
- `tests/unit/test_masking_algorithms.py`: 14 new tests (determinism, single-word, max_length,
  empty input for all three new functions).
- `tests/unit/test_cli.py`: 3 function-reference identity tests + single-word assertions.
- `tests/unit/test_masking_registry.py`: 13 new tests for new ColumnType members.

**Quality Gates**: ruff PASS, mypy PASS, bandit PASS, 1052 unit tests PASS (96.85% coverage).
pre-commit PASS (all 8 hooks).

**Review**: QA FINDING (2 fixed), DevOps PASS, Architecture FINDING (1 fixed)

**QA** (FINDING — 2 fixed):
1. `mask_address` docstring omitted `Faker.address()` newline behavior — docstring updated.
2. `MaskingRegistry.ColumnType` missing `FIRST_NAME`/`LAST_NAME`/`ADDRESS` — added with dispatch
   and 13 tests. Prevents dual-dispatch drift between CLI and registry paths.

**Architecture** (FINDING — 1 fixed):
1. `_COLUMN_MASKS` `Callable[[str, str], str]` type annotation underspecifies `max_length` —
   comment added explaining call-site vs full signature distinction.

**Retrospective Note**:
The `mask_name` → per-column split is the same class of configuration drift that caused T21.1
(`"persons"` → `"customers"`). The function-reference identity tests (`is mask_first_name`)
and single-word assertions (`" " not in result`) are the structural guards. The QA finding
about dual dispatch (CLI `_COLUMN_MASKS` vs `MaskingRegistry.ColumnType`) is worth watching:
two independent dispatch paths for the same domain concept will drift unless consolidated.

---

### [2026-03-16] Phase 20 End-of-Phase Retrospective

**Phase Goal**: Address correctness, security, and functionality findings from the
post-Phase 19 roast. No new features.

**Exit Criteria Verification**:
- All `except Exception` in telemetry narrowed or augmented: PASS (T20.1 — PR #99)
- Opacus warning suppression targeted, not blanket: PASS — all 7 simplefilter calls eliminated (T20.1)
- SDV `_model` coupling documented and tested: PASS — version-pin comment added (T20.1)
- Integration tests added for ingestion, subsetting, masking (real PostgreSQL): PASS — 5 new tests (T20.2 — PR #100)
- Real SDV training integration test added: PASS — @pytest.mark.slow + @pytest.mark.synthesizer (T20.2)
- `caplog` assertions added to failure path tests: PASS — 5 tests (T20.2)
- Playwright axe-core e2e tests passing: PASS — pre-existing (T20.3 — PR #98)
- Inline styles extracted from frontend: PASS — 38 style= attributes moved to CSS (T20.3)
- Toast aria-modal and focus trapping implemented: PASS — useFocusTrap hook + alertdialog (T20.3)
- Import-linter in pre-commit hooks: PASS (T20.4 — PR #102)
- ADR-0029 deferred items tracked in backlog: PASS — 5 TBD items (T20.4)
- Key rotation OOM safety verified: PASS — fetchall→fetchmany + batch_size guard (T20.4)
- Documentation polish complete: PASS (T20.5 — PR #101)
- All quality gates passing (locally): PASS — 1008 unit tests, 96.83% coverage
- Phase 20 end-of-phase retrospective: this entry

**Open advisory count**: 0 (all 5 advisories from T19.4 drained during this phase: ADV-017/018/019 in T20.2, ADV-020 in T20.4, ADV-021 in T20.1)

**What went well**:
1. All three waves executed as planned with successful parallelization:
   - Wave 1: T20.1 (backend) + T20.3 (frontend) in parallel — no conflicts
   - Wave 3: T20.4 + T20.5 in parallel — no conflicts
   T20.4 required rebase after T20.5 merged; resolved cleanly.
2. Advisory drain rate: 5/5 (100%). Phase 20 entered with 5 open advisories and exits with 0.
   ADV-021 (FK traversal broken) — the most critical bug in project history — was fixed in T20.1.
3. Review agents caught 19 total findings across 5 tasks (QA: 11, DevOps: 7, Architecture: 3,
   UI/UX: 3). All 19 were fixed before merge. The feedback_review_findings_must_be_fixed memory
   continues to hold at 100%.
4. Test count grew from 974 → 1008 (+34). Coverage maintained at 96.83%. Integration tests
   grew from 74 → 79 (+5 real PostgreSQL tests + 1 real SDV test).
5. The roast-to-backlog-to-execution pipeline worked end-to-end: Phase 19 roast produced
   Phase 20 backlog, which was fully executed with zero scope creep.

**What could improve**:
1. T20.1 developer agent took ~58 minutes (longest of any task). The 4-area scope (telemetry,
   warnings, SDV coupling, FK traversal) could have been split into two smaller tasks for
   better parallelization.
2. Several review findings recurred across tasks: weak attribute assertions (QA), missing
   edge-case tests for zero/boundary values (QA), and .env.example gaps for new env vars
   (DevOps). These should be added as standing checklist items in the developer brief template.
3. The CLAUDE.md consumer list inaccuracy (T20.5 QA finding) shows that documentation examples
   need grep-verification before committing — a pattern first noted in T17.3's retro.

---

### [2026-03-16] P20-T20.4 — Architecture Tightening

**Changes**:
- `.pre-commit-config.yaml`: import-linter added as local pre-commit hook.
- `src/synth_engine/shared/security/rotation.py`: OOM fix — `fetchall()` → `fetchmany(batch_size=1000)`.
  batch_size<=0 guard added. Docstrings corrected for transaction semantics.
- `src/synth_engine/modules/ingestion/validators.py`: ADV-020 — `CONCLAVE_SSL_REQUIRED` env var for
  sslmode override in Docker environments.
- `src/synth_engine/bootstrapper/config_validation.py`: Production-mode warning when SSL override active.
- `docs/adr/ADR-0032-mypy-synthesizer-ignore-missing-imports.md`: New ADR documenting mypy strategy.
- `docs/backlog/deferred-items.md`: 5 ADR-0029 deferred items tracked as Phase: TBD entries.
- `.env.example`: CONCLAVE_SSL_REQUIRED documented.
- `pyproject.toml`: mypy overrides comment references ADR-0032.

**Quality Gates**: ruff PASS, mypy PASS, bandit PASS, 1008 unit tests PASS (96.83% coverage). pre-commit PASS (including new import-linter hook).

**ADV drain**: ADV-020 (ADVISORY) drained — sslmode now configurable via `CONCLAVE_SSL_REQUIRED`.

**Review**: QA FINDING (2 fixed), DevOps FINDING (1 fixed), Architecture FINDING (2 fixed)

**QA** (FINDING — 2 blockers fixed):
1. batch_size<=0 silent failure — ValueError guard added with two tests.
2. Docstring inaccuracy — module Security Properties and function docstring corrected for
   all-or-nothing transaction semantics over all batches.

**DevOps** (FINDING — 1 fixed):
1. CONCLAVE_SSL_REQUIRED missing from .env.example — added with security documentation.

**Architecture** (FINDING — 2 fixed):
1. BLOCKER: Production SSL override warning — added to config_validation.py with 3 tests.
2. Hygiene: batch_size added to Args docstring in rotation.py.

**Retrospective Note**:
The batch_size<=0 silent failure mirrors the FeistelFPE rounds=0 advisory (ADV-011) — both are
zero-value boundary bugs in security modules. The CLAUDE.md spike promotion checklist (item 3)
explicitly gates on "zero/empty inputs" but was not applied here because this wasn't a spike
promotion. Security modules should have a standing zero-input guard convention. The production
SSL warning closes the configuration-validation gap: security-affecting env vars should always
be surfaced in config_validation.py with a production-mode guard.

---

### [2026-03-16] P20-T20.5 — Polish Batch (Cosmetic & Documentation)

**Changes**:
- `CLAUDE.md`: Added neutral value object exception to File Placement Rules table.
- `docs/ARCHITECTURAL_REQUIREMENTS.md`: Added preamble referencing ADR-0029 gap analysis.
- `docs/adr/ADR-template.md`: New template with Status field (Accepted/Superseded/Rejected).
- `README.md`: Phase 19 complete, Phase 20 in progress.

**Quality Gates**: pre-commit PASS. Docs-only task.

**Review**: QA FINDING (1 fixed), DevOps PASS

**QA** (FINDING — 1 item fixed):
1. CLAUDE.md SchemaTopology consumer list incorrect — listed StatisticalProfiler and SynthesisEngine
   as consumers; actual consumers are SubsettingEngine (traversal.py, core.py) and bootstrapper/cli.py.

**DevOps** (PASS): No security, PII, or infrastructure concerns.

**Retrospective Note**:
CLAUDE.md examples that reference specific classes must be grep-verified before committing.
The 30-second check would have caught the incorrect consumer list.

---

### [2026-03-16] P20-T20.2 — Integration Test Expansion (Real Infrastructure)

**Changes**:
- `Dockerfile`: ADV-017 fix — inline comments moved off `FROM...AS` lines.
- `docker-compose.yml`: ADV-018 fix — `cap_drop: ALL` removed from redis. ADV-019 fix — `DATABASES_HOST` → `DB_HOST` for pgbouncer.
- `docs/adr/ADR-0031-pgbouncer-image-substitution.md`: Amendment correcting `DATABASES_HOST` → `DB_HOST`.
- `tests/integration/test_t20_2_new_integration.py`: 5 new integration tests (ingestion preflight x2, subsetting FK traversal, masking deterministic, real SDV/CTGAN training).
- `tests/unit/test_t20_2_caplog_assertions.py`: 5 caplog assertion tests for failure path logging.
- `tests/unit/test_docker_image_pinning.py`: Updated for ADV-017 comment placement.
- `pyproject.toml`: `slow` and `synthesizer` markers registered.

**Quality Gates**: ruff PASS, mypy PASS, bandit PASS, 995 unit tests PASS (96.80% coverage), 79 integration tests PASS. pre-commit PASS.

**ADV drain**: ADV-017, ADV-018, ADV-019 (all BLOCKER) drained.

**Review**: QA FINDING (2 fixed), DevOps FINDING (3 fixed)

**QA** (FINDING — 2 items fixed):
1. Missing positive assertion on preflight readonly test — added `SELECT 1` execution after preflight.
2. Missing orders row_count assertion on FK traversal test — added `result.row_counts.get("orders") == 2`.

**DevOps** (FINDING — 3 items fixed):
1. BLOCKER: CTGAN test missing `@pytest.mark.synthesizer` — added (prevents silent CI skip).
2. Advisory: `slow` marker description corrected (no CI exclusion claim).
3. Advisory: ADR-0031 amended — `DATABASES_HOST` → `DB_HOST` with amendment section.

**Retrospective Note**:
The integration test expansion fulfills the Phase 20 roast's core finding: mock-only tests masked real
infrastructure incompatibilities for 19 phases. The CTGAN synthesizer marker finding is particularly
instructive — `pytest.importorskip` provides a graceful local fallback but becomes a silent skip in CI
when the test isn't routed to the correct job. Future tests using `importorskip` for optional
dependencies should always also carry the corresponding CI routing marker. The ADR-0031 staleness
finding confirms the Phase 19 retro pattern: ADRs capturing configuration snapshots go stale when
those configs change without atomic ADR amendment.

### [2026-03-16] P20-T20.1 — Exception Handling & Warning Suppression Fixes

**Changes**:
- `src/synth_engine/shared/telemetry.py`: `except Exception` → `except ValueError` in `_redact_url()`.
- `src/synth_engine/modules/synthesizer/dp_training.py`: All 7 `warnings.simplefilter("ignore"...)` calls
  replaced with targeted `warnings.filterwarnings()`. Blanket suppression eliminated entirely.
  Two module-level constants for Opacus warning patterns.
- `src/synth_engine/modules/mapping/reflection.py`: New `get_pk_constraint()` method on SchemaReflector.
- `src/synth_engine/bootstrapper/cli.py`: ADV-021 fix — `col.get('primary_key', 0)` replaced with
  `Inspector.get_pk_constraint()` via SchemaReflector. Exception sanitization: raw exc no longer
  shown to CLI users, logged instead.
- `src/synth_engine/shared/schema_topology.py`: ColumnInfo docstring updated — composite PK ordering
  contract corrected to reflect ADV-021 fix behavior.
- Tests: test_cli.py (new, 7 tests), test_dp_training.py (updated), test_telemetry.py (updated).

**Quality Gates**: ruff PASS, mypy PASS, bandit PASS, 990 unit tests PASS (96.80% coverage). pre-commit PASS.

**ADV drain**: ADV-021 (BLOCKER) drained — FK traversal now uses `get_pk_constraint()`.

**Review**: QA FINDING (2 fixed), DevOps FINDING (2 fixed), Architecture PASS

**QA** (FINDING — 2 items fixed):
1. Missing behavioral propagation test for `_redact_url` — added `test_redact_url_non_value_error_propagates`.
2. ColumnInfo docstring inaccuracy — updated to "composite PK members assigned primary_key=1 (ordering not preserved)".

**DevOps** (FINDING — 2 items fixed):
1. cli.py `except Exception` exposed raw SQLAlchemy text to users — sanitized to generic message + `_logger.exception()`.
2. Residual `simplefilter("ignore", Category)` calls — all converted to `filterwarnings()`. Test tightened to flag any `simplefilter` call.

**Architecture** (PASS): Import direction valid (bootstrapper→modules). `get_pk_constraint()` fits existing SchemaReflector pattern. No boundary violations.

**Retrospective Note**:
ADV-021 was the most critical correctness bug in the project's history — FK traversal via the CLI
path never worked because `Inspector.get_columns()` doesn't include `primary_key` in its return dict
on PostgreSQL. The fix correctly delegates to `get_pk_constraint()` through SchemaReflector's existing
API pattern. The AST-based test for exception narrowing is a strong enforcement technique but must be
paired with behavioral tests (as QA correctly identified). The `simplefilter` → `filterwarnings`
conversion eliminates all blanket warning suppression in the synthesizer module.

---

### [2026-03-16] P20-T20.3 — Frontend Accessibility Production Readiness

**Changes**:
- `frontend/src/components/RFC7807Toast.tsx`: Upgraded to `role="alertdialog"` + `aria-modal="true"` +
  always-present container with `hidden` attribute. Added `aria-describedby`, `tabIndex={-1}`, focus
  transfer on show. Removed redundant `aria-live="assertive"` (implicit in alertdialog).
- `frontend/src/hooks/useFocusTrap.ts`: New hook trapping Tab/Shift+Tab within toast modal.
- `frontend/src/styles/global.css`: All inline `style=` from Dashboard (26), Unseal (12), JobCard,
  AriaLive, ErrorBoundary extracted to BEM CSS classes. `@keyframes spin` moved from inline JSX.
- `frontend/src/routes/Dashboard.tsx`, `Unseal.tsx`, `components/JobCard.tsx`, `AriaLive.tsx`,
  `ErrorBoundary.tsx`: Inline styles replaced with class references.
- Tests: RFC7807Toast.test.tsx (new), useFocusTrap.test.tsx (new), Dashboard.test.tsx and
  ErrorBoundary.test.tsx updated for `role="alertdialog"`.

**Quality Gates**: ESLint PASS, 157/157 Vitest tests PASS, 98.75% coverage. pre-commit PASS.

**Review**: QA FINDING (4 fixed), DevOps PASS, UI/UX FINDING (1 blocker + 2 advisory, all fixed)

**QA** (FINDING — 4 items fixed):
1. Weak `aria-labelledby` assertion — now checks specific value `"rfc7807-toast-title"`.
2. Weak `aria-label` progressbar assertion — now checks `"Job 1 progress"`.
3. Missing edge cases: `visible=true + problem=null` and zero-focusable-elements tests added.
4. Missing AriaLive base class assertion — `.aria-live-region` now verified.
Advisory: redundant `aria-live="assertive"` on alertdialog removed (double-announcement risk).

**DevOps** (PASS): No secrets, no PII, gitleaks clean. CSP positive: JSX `<style>` block removed
from Unseal.tsx, reducing `unsafe-inline` surface area.

**UI/UX** (FINDING — 3 items fixed):
1. BLOCKER: `:focus { outline: none }` — agent reported already using `:focus-visible` on branch; verified.
2. Advisory: `aria-describedby="rfc7807-toast-detail"` added to alertdialog container.
3. Advisory: Focus transfer on toast appearance via `useEffect` + `containerRef.focus()`.

**Retrospective Note**:
The inline-style extraction (AC3) was the largest mechanical change — 38 `style=` attributes moved to
BEM classes in global.css. Two intentional inline styles remain (JobCard status badge color token and
progress fill width) because they are dynamic runtime values. The always-present container pattern
(T17.2 retro) is now the established pattern for all `role="alert"` and `role="alertdialog"` elements
in the project. The redundant `aria-live` removal is a subtle but important fix: `alertdialog` carries
implicit assertive semantics, and the explicit attribute caused NVDA+Firefox double-announcement.

---

### [2026-03-16] Phase 19 End-of-Phase Retrospective

**Phase Goal**: Fix critical correctness and security findings from the Phase 18 roast,
close the E2E validation gap, and add missing production safeguards. No new features.

**Exit Criteria Verification**:
- RFC7807Middleware converted to pure ASGI middleware: PASS (T19.1 — PR #93)
- DB engine singleton cached: PASS (T19.1 — PR #93)
- EgressWriter transaction boundaries verified: PASS (T19.1 — PR #93)
- X-Forwarded-For proxy trust documented/enforced: PASS (T19.2 — PR #94)
- MASKING_SALT enforced in production config validation: PASS (T19.2 — PR #94)
- ADV-016 resolved (pgbouncer scram-sha-256): PASS (T19.2 — PR #94)
- CI integration test gate enforces >0 collected: PASS (T19.3 — PR #96)
- hypothesis property-based tests added (≥5): PASS — 15 tests (T19.3 — PR #96)
- Concurrent budget contention tested: PASS (T19.3 — PR #96)
- Live E2E pipeline executed through Docker Compose: PARTIAL (T19.4 — PR #97)
  - 3 of 8 services started; 5 findings documented as ADV-017 through ADV-021
  - Seed script: SUCCESS. CLI: exit 0 but FK traversal broken (ADV-021).
- E2E_VALIDATION.md TODO markers replaced with evidence: PASS (T19.4 — PR #97)
- CLAUDE.md ≤400 lines with rule sunset evaluation: PASS — 256 lines (T19.5 — PR #95)
- All quality gates passing: PASS — 974 unit tests, 96.30% coverage
- Phase 19 end-of-phase retrospective: this entry

**Open advisory count**: 5 (ADV-017 through ADV-021, all from T19.4 E2E validation)
- ADV-017, ADV-018, ADV-019 → T20.2 (Docker infrastructure fixes)
- ADV-020 → T20.4 (architecture tightening)
- ADV-021 → T20.1 (correctness — FK traversal broken)

**What went well**:
1. T19.3 and T19.5 ran in parallel — third successful parallel execution. No rebase
   conflicts because they touched non-overlapping files (tests+pyproject vs CLAUDE.md+docs).
2. T19.4 E2E validation proved its value immediately: discovered 5 real issues including
   a critical correctness bug (ADV-021: FK traversal never fires via CLI path). This bug
   was masked for 19 phases because integration tests use SubsettingEngine directly,
   bypassing the CLI's topology loading path. The task justified its P0 priority.
3. T19.5 process sunset reduced CLAUDE.md from 505→256 lines — 49% reduction. Rules 2, 3, 7
   retired after evidence-based evaluation against git history. Lower cognitive overhead for
   future developer agents.
4. Every review FINDING across T19.1, T19.2, T19.3 was fixed before merge (12 total fixes).
   The feedback_review_findings_must_be_fixed memory continues to hold at 100%.
5. ADV-016 (pgbouncer md5→scram-sha-256) drained in T19.2, closing a Phase 18 advisory.

**What could improve**:
1. T19.4 E2E validation was PARTIAL — only 3/8 Docker services started. The remaining 5
   findings (ADV-017 through ADV-021) mean we still cannot prove the system works end-to-end
   in containers. These are now Phase 20 entry blockers for T20.2.
2. ADV-021 (FK traversal broken in CLI) is the most serious finding in the project's history.
   The subsetting engine's core value proposition — relational traversal — has never worked
   via the CLI path. This was not caught because all tests exercise the engine directly with
   pre-built SchemaTopology objects. Lesson: E2E validation through the actual deployment
   entry point (CLI, API) should be a phase-exit gate, not a one-time task.
3. The Phase 19 roast (which created Phase 20) found issues that existed since early phases.
   Periodic roasts should be formalized — every 5 phases, not just when the backlog empties.

---

### [2026-03-16] P19-T19.4 — Live E2E Pipeline Validation

**Changes**:
- `docs/E2E_VALIDATION.md`: All TODO markers replaced with live terminal output from
  Docker Compose execution on 2026-03-16. 5 findings documented.
- `pyproject.toml`: Added `huey` to mypy `ignore_missing_imports` (pre-existing gate fix).
- `src/synth_engine/shared/task_queue.py`: Removed stale `# type: ignore[import-untyped]`.
- `tests/unit/test_seed_sample_data.py`: Test updated from TODO-marker check to evidence check.

**Quality Gates**: ruff PASS, mypy PASS, bandit PASS, 974 unit tests PASS (96.30% coverage).

**E2E Results**:
- Docker postgres: HEALTHY. MinIO: UP. Redis: FAILING (cap_drop). pgbouncer: FAILING (env vars).
- Seed script: SUCCESS — 100 customers, 250 orders, 888 order_items, 250 payments.
- conclave-subset CLI: exit 0, but only seed table written (FK traversal broken).
- 5 findings documented as ADV-017 through ADV-021.

**Review**: Skipped for this task — docs/infrastructure validation only, no production code logic changed.

**Retrospective Note**:
The live E2E validation fulfilled its purpose: it discovered 5 real infrastructure/correctness
issues that would have remained hidden without actually running the system. The most critical
finding (ADV-021: FK traversal broken) means the subsetting engine's CLI path has never
actually traversed foreign keys. This was masked because integration tests use the
SubsettingEngine directly with a pre-built SchemaTopology, bypassing the CLI's topology
loading path. Future E2E validation should be a phase-exit gate, not an optional task.

---

### [2026-03-16] P19-T19.3 — Integration Test CI Gate & Property-Based Testing

**Changes**:
- `tests/unit/test_property_based.py`: 15 property-based tests using Hypothesis covering
  5 invariant categories: masking determinism, FK traversal ordering, epsilon monotonicity,
  subsetting FK integrity, profile comparison symmetry.
- `tests/integration/test_concurrent_budget_contention.py`: 2 concurrent budget contention
  tests using real PostgreSQL (pytest-postgresql) with asyncio.gather for parallel spends.
- `scripts/verify_integration_count.sh`: CI gate ensuring integration tests don't silently
  pass with 0 collected. Wired into `.github/workflows/ci.yml`.
- `pyproject.toml`: `hypothesis ^6.151.9` added to dev dependencies.

**Quality Gates**: ruff PASS, mypy PASS, bandit PASS, 970 unit tests PASS (96.30% coverage).

**Review**: QA FINDING (5 fixed), DevOps FINDING (1 fixed)

**QA** (FINDING — 5 items fixed):
1. Type narrowing: `assert ledger_id is not None` guards added after `ledger.id` assignment.
2. AsyncGenerator annotation: `AsyncGenerator[AsyncEngine]` → `AsyncGenerator[AsyncEngine, None]`.
3. Empty-string masking edge case: `test_mask_value_empty_string_is_deterministic` added.
4. Zero-spend epsilon: `min_value=Decimal("0")` in monotonicity test amounts strategy.
5. Empty-seed traversal: parametrized case for 0 parent rows added.

**DevOps** (FINDING — 1 item fixed):
1. hypothesis placement: moved above integration group comment block with explanatory comment.

**Retrospective Note**:
CI mypy runs only on `src/`, making test files a blind spot for type correctness. The
`ledger_id: int | None` issue is exactly the class of runtime error that type narrowing
assertions prevent. Consider adding `mypy tests/integration/` to CI (even with relaxed
settings). The `hypothesis` group placement mirrors a recurring pattern where TOML comment
blocks don't match section headers — the structural header is ground truth, not comments.

---

### [2026-03-16] P19-T19.1 — Middleware & Engine Singleton Fixes

**Changes**:
- `src/synth_engine/bootstrapper/errors.py`: `RFC7807Middleware` converted from `BaseHTTPMiddleware`
  to pure ASGI middleware. Implements `__call__(scope, receive, send)` directly with `headers_sent`
  tracking. SSE streaming no longer buffered. Dead `BaseHTTPMiddleware` imports removed.
- `src/synth_engine/shared/db.py`: `get_engine()` and `get_async_engine()` cache engines in
  module-level dicts keyed by URL. `dispose_engines()` added for test cleanup. Dead
  `if TYPE_CHECKING: pass` block removed (review fix).
- `src/synth_engine/modules/subsetting/egress.py`: Transaction boundaries documented — already
  correct (single connection, single commit per batch).
- `tests/unit/test_bootstrapper_errors.py`: 8 tests (7 + 1 review fix for headers_sent re-raise).
- `tests/unit/test_db.py`: 8 tests for engine caching + dispose.
- `tests/unit/test_subsetting_egress.py`: 4 tests for transaction atomicity.

**Quality Gates**: ruff PASS, mypy PASS, bandit PASS, 952 unit tests PASS (96.30% coverage).

**Review**: QA FINDING (4 fixed), DevOps PASS, Architecture PASS

**QA** (FINDING — 4 items fixed):
1. Dead code: empty `if TYPE_CHECKING: pass` block in db.py removed.
2. Edge case: headers_sent=True re-raise path test added — inner app sends response.start
   then raises; asserts exception propagates (not silently swallowed).
3. Meaningful assert: `callable(RFC7807Middleware)` → instance-level callable check with
   `inspect.signature` parameter verification.
4. Docstring accuracy: dispose_engines() "await engine.dispose()" → "async_engine.sync_engine.dispose()".

**DevOps** (PASS): No secrets, no PII, gitleaks clean, bandit clean. Advisory: engine singleton
thread-safety note — CPython GIL makes dict ops atomic; race window effectively zero for
single-threaded startup path. No fix needed for current architecture.

**Architecture** (PASS): ADR-0024 compliance (pure ASGI). Dependency direction clean. File
placement correct. Abstraction minimal and appropriate.

**Retrospective Note**:
The headers_sent re-raise path is a correctness-critical code path that had zero test coverage.
Testing pure ASGI middleware via raw ASGI callables (not full FastAPI stacks) is the right
pattern and should be the standard for future middleware additions. The `callable(ClassName)`
rubber-stamp assertion pattern recurs — all future middleware/protocol tests should test on
instances, not classes.

---

### [2026-03-16] P19-T19.2 — Security Hardening: Proxy Trust & Config Validation

**Changes**:
- `src/synth_engine/bootstrapper/config_validation.py`: `MASKING_SALT` added to
  `_PRODUCTION_REQUIRED` tuple. Module and function docstrings updated.
- `docker-compose.yml`: `PGBOUNCER_AUTH_TYPE: md5` → `scram-sha-256` (ADV-016 resolved).
- `docs/OPERATOR_MANUAL.md`: Section 8.8 added — X-Forwarded-For proxy trust requirement
  with nginx configuration sample.
- `docs/adr/ADR-0031-pgbouncer-image-substitution.md`: Compatibility table updated md5→scram-sha-256.
  Amendment section added (review fix).
- `docs/adr/ADR-0014-masking-engine.md`: Amendment section added closing deferred MASKING_SALT
  documentation item (review fix).
- `tests/unit/test_config_validation.py`: 7 new tests. Dead `_BASE_ENV`/`_PROD_ENV` removed
  (review fix).

**Quality Gates**: ruff PASS, mypy PASS, bandit PASS, 939 unit tests PASS (96.25% coverage).

**ADV drain**: ADV-016 (DEFERRED) drained — pgbouncer auth upgraded from md5 to scram-sha-256.

**Review**: QA FINDING (1 blocker + 1 advisory, all fixed), DevOps PASS, Architecture FINDING (2 fixed)

**QA** (FINDING — 2 items fixed):
1. BLOCKER: Empty-string MASKING_SALT test added — `MASKING_SALT=""` in production raises SystemExit.
2. Advisory: Dead `_BASE_ENV`/`_PROD_ENV` module-level constants removed from test file.

**DevOps** (PASS): gitleaks clean, bandit clean. .secrets.baseline correctly updated.
.env.example already contains MASKING_SALT entry. No new dependencies.

**Architecture** (FINDING — 2 items fixed):
1. ADR-0031 compatibility table updated from md5 to scram-sha-256 with ADV-016 note.
2. ADR-0014 deferred MASKING_SALT documentation promise closed with amendment section.

**Retrospective Note**:
ADR staleness is a recurring pattern: ADR-0031 was immediately made stale by the auth type
change in the same release cycle. ADRs capturing configuration snapshots need explicit amendment
when those configs change. The "will be documented in Phase N" pattern in ADRs should carry a
tracking marker that forces closure when that phase ships.

---

### [2026-03-16] Phase 18 End-of-Phase Retrospective

**Phase Goal**: Reduce type:ignore suppressions, audit and slim dependency tree, execute
full E2E validation infrastructure with sample data.

**Exit Criteria Verification**:
- type:ignore count reduced (src/ 24→15, target ≤15): PASS (T18.1 — PR #90)
- type:ignore count reduced (tests/ 147→100, target ≤100): PASS (T18.1 — PR #90)
- Dependency audit completed: PASS — docs/DEPENDENCY_AUDIT.md covers all 26 direct deps (T18.2 — PR #91)
- chromadb moved to dev group: PASS (T18.2 — PR #91)
- ADV-015 BLOCKER drained (pgbouncer phantom tag → edoburu/pgbouncer): PASS (T18.2 — PR #91)
- ADR-0031 documents pgbouncer substitution per Rule 6: PASS (T18.2 — PR #91)
- Sample data seeding script created: PASS (T18.3 — PR #92)
- sample_data/ populated with CSV exports: PASS — 4 files, 1489 rows total (T18.3 — PR #92)
- E2E validation documented in docs/E2E_VALIDATION.md: PASS (T18.3 — PR #92)
- All quality gates passing: PASS — 932 unit tests, 96.25% coverage
- Open advisory count: 1 (ADV-016 — pgbouncer md5 auth, DEFERRED)

**What went well**:
1. T18.1 and T18.2 ran in parallel on separate branches — second successful parallel execution
   (first was T17.2+T17.3). Both merged cleanly without rebase conflicts.
2. ADV-015 (pgbouncer phantom tag) finally resolved after 18 phases. The ADR-first approach
   (Rule 6) produced a well-documented substitution with registry API digest provenance.
3. All review FINDINGs across all 3 tasks were fixed before merge — the
   `feedback_review_findings_must_be_fixed` memory continues to hold.
4. The chromadb-to-dev move reduced production install by ~25 transitive packages with a 3-line
   pyproject.toml change — demonstrates periodic dependency audits are high-value, low-effort.
5. T18.3 QA review was thorough: 5 findings caught real gaps (untested default paths, inaccurate
   docstring, loose assertions). The review agent pattern continues to earn its keep.

**What could improve**:
1. T18.2 developer agent modified RETRO_LOG with fabricated review results ("QA PASS, DevOps PASS")
   before reviews actually ran. The PM had to manually correct the entry. The implementation brief
   should explicitly state "Do NOT modify RETRO_LOG.md" — but it DID state that, and the agent
   ignored it. Stronger enforcement needed: the PM should verify RETRO_LOG diff after each
   developer agent run.
2. T18.3 AC4/5/6/7 (docker-compose up, conclave-subset CLI, API synthesis, screenshots) cannot
   be validated without a running Docker Compose stack. The task created the infrastructure and
   documentation but the actual live validation is deferred. This should become a standing
   operational validation task.
3. The passlib dependency (noted in DEPENDENCY_AUDIT.md as having no src/ imports) should be
   evaluated for removal in a future phase — requires ADR-0007 amendment.

---

### [2026-03-16] P18-T18.3 — End-to-End Validation with Sample Data

**Changes**:
- `scripts/seed_sample_data.py`: New 587-line Click-based seeding script. Generates 4 related
  tables (customers→orders→order_items, orders→payments) with Faker seed=42. Exports CSVs,
  generates SQL DDL+INSERT, optionally executes against PostgreSQL.
- `sample_data/{customers,orders,order_items,payments}.csv`: Reference CSV exports (100+250+888+250 rows).
- `docs/E2E_VALIDATION.md`: 350-line step-by-step pipeline validation guide covering Docker Compose
  startup, seeding, conclave-subset CLI, API synthesis, and verification checkpoints.
- `tests/unit/test_seed_sample_data.py`: 70 tests across 8 classes (schema, FK integrity, data types,
  determinism, edge cases, error paths, doc existence).
- `.secrets.baseline`: Updated for pre-existing T18.2 false positive.

**Quality Gates**: ruff PASS, mypy PASS, bandit PASS, 932 unit tests PASS (96.25% coverage).

**Review**: QA FINDING (5 items, all fixed), DevOps PASS

**QA** (FINDING — 5 items, all fixed):
1. Exception specificity: `except Exception` → `except psycopg2.Error` in `_execute_against_db`. Fixed.
2. Edge-case tests: Added n=None path, empty-rows export, unknown-table fallback. Fixed.
3. Error-path tests: Added empty-input generators, ImportError/SystemExit, rollback verification. Fixed.
4. Determinism tests: Added for generate_orders, generate_order_items, generate_payments. Fixed.
5. Docstring accuracy: Removed false split-payment claim from generate_payments docstring.
   Strengthened SSN regex and export_csv fieldnames assertions. Fixed.

**DevOps** (PASS):
hardcoded-credentials PASS. no-pii-in-code PASS — all SSN/email/phone data provably fictional
(Faker seed=42, RFC 2606 domains). secrets-hygiene PASS — gitleaks clean, .secrets.baseline
updated. DSN redaction at line 439 PASS (hand-rolled but acceptable for dev utility). bandit PASS.
ci-health PASS — existing pipeline covers scripts/ via bandit targets.

**Retrospective Note**:
Generator functions with two code paths (explicit n= vs n=None default) were only tested via the
explicit path. The CLI-invoked default path was untested. Rule: the zero-argument / default-parameter
path of any generator should be the FIRST test written, not an afterthought. The generate_payments
docstring described a split-payment feature that didn't exist in code — false-contract risk from
spec-first development where the implementation was simplified but the docs weren't updated.

---

### [2026-03-16] P18-T18.2 — Dependency Tree Audit & Slimming

**Changes**:
- `pyproject.toml`: `chromadb` moved from `[tool.poetry.dependencies]` to
  `[tool.poetry.group.dev.dependencies]`. `datamodel-code-generator` placement
  formalized in dev section with explanatory comment. `asyncpg` and `greenlet`
  documented with inline comments explaining their runtime role (no direct import
  but required as SQLAlchemy dialect registrations / platform workaround).
- `poetry.lock`: Regenerated after pyproject.toml changes.
- `docker-compose.yml`: `pgbouncer/pgbouncer:1.23.1` (phantom tag, does not exist
  in Docker Hub) replaced with `edoburu/pgbouncer:v1.23.1-p3@sha256:377dec3c...`
  (verified via Registry v2 API). `WARNING(P17-T17.1)` comment removed.
  ADR-0031 referenced in new comment block. ADV-015 BLOCKER resolved.
- `docs/DEPENDENCY_AUDIT.md`: Created. Full audit table covering all 26 direct
  production dependencies with purpose, runtime usage, group, and notes.
- `docs/adr/ADR-0031-pgbouncer-image-substitution.md`: Created. Documents the
  technology substitution (pgbouncer/pgbouncer to edoburu/pgbouncer) per Rule 6,
  including registry API digest provenance and alternatives considered.
- `tests/unit/test_dependency_audit.py`: New — 16 tests covering audit doc
  existence, chromadb placement, and ADV-015 resolution.
- `tests/unit/test_docker_image_pinning.py`: Updated — removed `_PGBOUNCER_UNPINNABLE_MARKER`
  exclusion, replaced `test_pgbouncer_invalid_tag_is_documented` with
  `test_phantom_pgbouncer_tag_absent` and `test_pgbouncer_uses_edoburu_image`.
  All 9 external service images now included in blanket pinning check.

**Quality Gates**:
- ruff check: PASS, ruff format: PASS, mypy: PASS, bandit: PASS
- poetry install: PASS (production, without chromadb)
- poetry install --with dev,synthesizer: PASS (chromadb in dev group)
- pytest unit: 862 passed, 1 skipped, 96.24% coverage (>=90%) — PASS
- lint-imports: 4 contracts KEPT, 0 broken — PASS
- pre-commit (all hooks): PASS

**ADV drain**: ADV-015 (BLOCKER) drained — pgbouncer phantom tag replaced + SHA-256 pinned.

**Review**: QA FINDING (1 fixed), DevOps FINDING (1 fixed, 1 advisory deferred)

**QA** (FINDING — 1 item fixed):
dead-code PASS. coverage-gate PASS — 96.25%. meaningful-asserts PASS. backlog-compliance PASS.
FINDING: `test_chromadb_present_in_dev_or_scripts_group` was over-permissive — accepted
chromadb in ANY Poetry group section, not specifically dev. If chromadb were accidentally placed
in synthesizer or integration group, the test would silently pass. Fixed: tightened to match
only `[tool.poetry.group.dev.dependencies]`. Error-path testing on file-inspection tests noted
as advisory — negative-path tests should be standard practice for config-inspection test classes.

**DevOps** (FINDING — 1 item fixed, 1 advisory deferred):
supply-chain PASS — all 9 external images SHA-256 pinned. digest-provenance PASS.
dependency-audit PASS — chromadb correctly moved, pip-audit found no CVEs.
FINDING: `pgbouncer/userlist.txt` contained plaintext dev credential (`synth_dev_password`) and
was git-tracked (pre-existing since P2-T2.2). Inconsistent with Docker secrets pattern. Fixed:
`git rm --cached`, added to `.gitignore`, created `userlist.txt.example` with SCRAM-SHA-256
template. ADVISORY: `PGBOUNCER_AUTH_TYPE: md5` is deprecated in PostgreSQL 14+; should migrate
to `scram-sha-256`. Deferred — pre-existing, not introduced by this diff. Tracked as ADV-016.

**Retrospective Note**:
The phantom tag problem (pgbouncer/pgbouncer:1.23.1) persisted for 17+ phases because
Docker image references are not validated at CI time — only when docker pull is actually
run. Future PRs adding new Docker image references should include a Registry v2 API
validation step (the same pattern used in T17.1 and T18.2) to confirm the tag exists
before committing. The chromadb move demonstrates that auditing transitive trees
periodically is worth doing: a 25-package reduction in the production install comes from
a 3-line change in pyproject.toml.

---

### [2026-03-16] P18-T18.1 — Type Ignore Suppression Audit & Reduction

**Changes**:
- `tests/conftest_types.py`: New module providing `PostgreSQLProc` type alias — eliminates 36 `[valid-type]` suppressions.
- 12 `src/` files: Eliminated 9 suppressions via `cast()`, `sqlmodel.col()`, if/else narrowing. Written justification added to all 15 remaining.
- 20 test files: Corrected fixture return types, replaced `[valid-type]` with PostgreSQLProc alias.

**Counts**: src/ 24→15 (≤15: PASS), tests/ 147→~98 (≤100: PASS).

**Quality gates**: mypy PASS, ruff PASS, bandit PASS, 842 unit tests PASS (96.25%), 72 integration tests PASS.

**Review**: QA FINDING (advisory), DevOps PASS, Architecture PASS

**QA** (FINDING — advisory, batched per Rule 16): Count wording inconsistency (commit "100" vs measured "~99"). 7 pre-existing unjustified suppressions in test_sse.py.
**DevOps** (PASS): No new deps, no secrets, CI unchanged.
**Architecture** (PASS): conftest_types.py correctly placed. PostgreSQLProc alias sound.

**Retrospective Note**: Ruff formatter moves `# type: ignore` comments on single-import lines to the symbol line during block-import formatting. The fix: place `# type: ignore` on the `from X import (  # type: ignore` line itself.

---

### [2026-03-16] Phase 17 End-of-Phase Retrospective

**Phase Goal**: Close ADV-014 Docker base image pinning debt, fix Dashboard WCAG
inconsistencies, correct stale process document references, and slim process governance.

**Exit Criteria Verification**:
- Docker base images pinned to SHA-256 digests (3 Dockerfile FROM lines + 6 compose services): PASS (T17.1 — PR #86)
- ADV-014 TODO comments removed from Dockerfile: PASS (0 remaining)
- Dashboard form inputs have aria-required and aria-invalid: PASS (T17.2 — PR #88)
- OTEL_EXPORTER_OTLP_ENDPOINT documented in .env.example: PASS (T17.2 — PR #88)
- CLAUDE.md stale references removed: PASS (T17.3 — PR #87)
- Phase 16 backlog corrected (migration 002 -> 003): PASS (T17.3 — PR #87)
- 5 stale remote branches cleaned: PASS (T17.3 — PR #87)
- ADR format consistency (4 ADRs fixed): PASS (T17.3 — PR #87)
- README current with Phase 16 complete, Phase 17 in progress: PASS (T17.3 — PR #87)
- CLAUDE.md under 500 lines: PASS (498 lines) (T17.4 — PR #89)
- RETRO_LOG under 800 lines: PASS (435 lines) (T17.4 — PR #89)
- Conditional reviewer spawning: PASS — tested on T17.4 (docs-only -> QA+DevOps only)
- Consolidated review commits: PASS — first use on T17.4
- Materiality threshold + small-fix batching rules: PASS (Rules 16+17)
- All quality gates passing: PASS
- Phase 17 end-of-phase retrospective completed: this entry

**Open advisory count**: 1 (ADV-015 — pgbouncer phantom tag BLOCKER)

**What went well**:
1. T17.2 and T17.3 ran in parallel on separate feature branches with non-overlapping files.
   T17.3 merged while T17.2 was still in review. This is the first time the PM successfully
   parallelized two tasks within a phase.
2. T17.4 was the first task to use the new conditional reviewer spawning and consolidated
   review commit format. Both worked correctly: UI/UX and Architecture reviewers were
   correctly skipped (docs-only task), and the single review: commit replaced 4 separate
   commits with no loss of information.
3. The RETRO_LOG archival was dramatic — 2687 to 435 lines. Future developer agents will
   consume ~85% fewer tokens on RETRO_LOG scans.
4. Every review FINDING was fixed before merge (T17.1 arch finding, T17.2 UI/UX finding,
   T17.4 QA finding). The feedback_review_findings_must_be_fixed memory held.

**What could improve**:
1. The "change the spec, forget the consumers" pattern recurred in T17.4 — CLAUDE.md commit
   format changed but .claude/agents/ files weren't updated. This is the same class of
   failure as T17.3 (AUTONOMOUS_DEVELOPMENT_PROMPT retirement left stale references). Both
   the PM brief and the developer agent should grep consumer files when changing process docs.
2. The T17.2 QA review arrived after the PR was already merged (10+ minute review on a
   frontend change). Its 3 findings (vacuous aria-invalid assertions, weak toBeGreaterThanOrEqual
   bound, implicit EMPTY_FORM dependency) are valid but cosmetic — batched for Phase 18 per
   Rule 16.
3. ADV-015 (pgbouncer phantom tag) remains open. It requires an ADR for technology substitution
   (Rule 6) and is appropriately tracked as a BLOCKER for the next pgbouncer-related task.

---

### [2026-03-16] P17-T17.4 — Process Governance Slimming

**Changes**:
- `CLAUDE.md`: Consolidated from 603 to 498 lines. Merged Rules 1+5 (Rule 5 is strict superset).
  Deleted Rule 14 (ChromaDB seeding — unvalidated overhead). Added conditional reviewer
  spawning (UI/UX only for frontend, Arch only for src/). Consolidated review commits
  (one review: commit per task instead of 4). Added Rule 15 (sunset clause), Rule 16
  (materiality threshold), Rule 17 (small-fix batching). All retrospective-sourced rules
  tagged [sunset: Phase 22].
- `docs/RETRO_LOG.md`: Archived phases 0-14 to `docs/retro_archive/`. Reduced from 2687 to 404 lines.
- `.claude/agents/pr-reviewer.md`, `.claude/agents/pr-describer.md`: Updated for consolidated
  review commit format (review: instead of review(qa/devops/arch/ui-ux):).
- `docs/backlog/phase-17.md`: T17.4 spec added. `docs/backlog/phase-18.md`: New backlog.

**Quality Gates**: Docs/process task. pre-commit: PASS. CLAUDE.md: 498 lines (<500). RETRO_LOG: 404 lines (<800).

**Review**: QA FINDING (1 blocker fixed), DevOps PASS

**QA**: pr-reviewer.md and pr-describer.md still used old `review(qa):` grep patterns — fixed.
Rule numbering gap (14 deleted) — cosmetic, batched per Rule 16. Advisory table intact.

**DevOps**: All scans clean. No CI impact from Rule 14 deletion. seed_chroma_retro.py orphaned
but harmless — T18.2 will resolve.

**Retrospective Note**:
"Change the spec, forget the consumers" pattern recurred — identical to T17.3's
AUTONOMOUS_DEVELOPMENT_PROMPT fix. Future governance changes must grep `.claude/agents/*.md`.
Conditional reviewer spawning saved ~26K tokens on this docs-only task (2 guaranteed SKIPs avoided).

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
  explanatory comments about optional observability configuration. Fixed `pip install` ->
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
FINDING: `role="alert"` div for form validation errors used conditional mount/unmount.
NVDA+Firefox can silently swallow repeat error announcements when the container is
destroyed and recreated with identical content. Fix: changed to always-present container
with conditional text content. Fixed in review fix commit.

**Retrospective Note**:
The Unseal.tsx -> Dashboard.tsx WCAG parity task revealed a subtle screen reader
announcement bug: conditional rendering of role="alert" containers works for one-shot
errors but fails for repeated identical errors in NVDA+Firefox. The always-present
container pattern (render container, conditionally fill content) is more robust. This
should be the standard pattern going forward for all role="alert" containers in the
project.

---

### [2026-03-16] P17-T17.3 — CLAUDE.md Stale References, Backlog Spec Fix & Branch Cleanup

**Changes**:
- `CLAUDE.md`: 4 stale `AUTONOMOUS_DEVELOPMENT_PROMPT.md` references replaced with current equivalents
- `docs/backlog/phase-16.md`: "Migration 002" -> "Migration 003" (5 occurrences corrected)
- 4 ADR files: format inconsistency fixed
- `README.md`: Phase 16 -> Complete, Phase 17 -> In Progress
- `docs/BACKLOG.md`: Phase 17 indexed
- 5 stale remote branches deleted (P15-T15.2, P16-T16.1, P16-T16.2, P16-T16.3, fix/P16-T16.3)

**Quality Gates**: Docs-only task. pre-commit: PASS. No Python code changes.

**QA** (PASS): Coverage 96.24% unchanged.
**DevOps** (PASS): gitleaks clean. docs-gate CI satisfied by docs: commit prefix.
**UI/UX** (SKIP): No UI surface area.

**Retrospective Note**:
The AUTONOMOUS_DEVELOPMENT_PROMPT.md retirement (Phase 3.5) left 4 stale references that
survived until Phase 17. Future doc-retirement operations should include a grep-and-replace
sweep as part of the retirement commit itself to avoid multi-phase cleanup.

---

### [2026-03-16] P17-T17.1 — Docker Base Image SHA-256 Pinning (ADV-014)

**Changes**:
- `Dockerfile`: All three FROM lines pinned to SHA-256 digests via Docker Registry v2 API.
- `docker-compose.yml`: Six of seven external service images pinned. pgbouncer tag
  confirmed non-existent; WARNING(P17-T17.1) comment added. Tracked as ADV-015 (BLOCKER).
- `tests/unit/test_docker_image_pinning.py`: 17 new file-inspection tests.

**Quality Gates**: ruff: PASS, mypy: PASS, bandit: PASS, pytest: 842 passed 96.24% — PASS

**QA** (PASS): All items PASS. coverage-gate PASS — 96.24%.
**Architecture** (PASS): adr-compliance ADVISORY — pgbouncer replacement requires ADR per Rule 6; tracked ADV-015 BLOCKER.
**DevOps** (FINDING): `pgbouncer/pgbouncer:1.23.1` does not exist in Docker Hub. Tracked as ADV-015 (BLOCKER).
**UI/UX** (SKIP): No UI surface area.

**Retrospective Note**:
Before declaring an image reference pinnable, verify the tag exists in the registry.
pgbouncer/pgbouncer:1.23.1 is a phantom tag that was silently referenced for 17+ phases.
Image reference validation should be a separate pre-production checklist item.

---

### [2026-03-16] Phase 16 End-of-Phase Retrospective

**Phase Goal**: Close Alembic migration drift, fix undeclared frontend deps, improve nosec
accuracy, add operator docs, add WCAG skip navigation.

**Exit Criteria**: All PASS. Open advisory count: 0.

**What went well**: Review agents caught real issues in all 3 tasks. GitHub auto-delete finally
enabled after 3 retro entries. ADR-0030 closed 7-phase Float->NUMERIC debt.

**What could improve**: PR #84 auto-merged before UI/UX review completed. nosec+docstring
atomicity: both must be updated together. Sequence number specs should use relative references.

---

### [2026-03-16] P16-T16.1, T16.2, T16.3 — Phase 16 Tasks

See Phase 16 End-of-Phase Retrospective above for details.

---

### [2026-03-16] Phase 15 End-of-Phase Retrospective

**Phase Goal**: Fix frontend test coverage gate, enforce in CI, clean stale branches, update README.

**Exit Criteria**: All PASS. Open advisory count: 0.

**What went well**: Root cause precise. Fix minimal. CI gate verified working.

**What could improve**: Coverage gate broken since Phase 14. Stale branches — enable auto-delete.

---

## Archived Reviews

Detailed reviews for phases 0-14 are archived in `docs/retro_archive/`.
