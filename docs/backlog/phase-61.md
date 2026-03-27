# Phase 61 — Test Quality Elevation

**Goal**: Raise mutation kill rate by eliminating shallow assertions, test
duplication, and infrastructure test sprawl.  The 3.9:1 test-to-code ratio
(98 K test LOC vs 25 K source LOC) is inflated by ~6,500 LOC of scaffolding
tests and 153 shallow `is not None` sole assertions across 48 files.

**Prerequisite**: Phase 60 merged.

**Source**: Staff-level production readiness audit (2026-03-27), scored
test efficacy 6/10. Findings: C7 (shallow assertions), test duplication in
DP suite, infrastructure test sprawl, missing real DP integration test.

---

## Critical Issues Addressed

| ID | Issue | Source | Impact |
|----|-------|--------|--------|
| C7 | 153 shallow `is not None` sole assertions across 48 test files | Audit 2026-03-27 | Low mutation kill rate; false confidence in coverage |
| — | 20+ copy-paste tests in `test_synthesizer_tasks_dp.py` | Audit 2026-03-27 | Maintenance burden; 80% code duplication |
| — | ~6,500 LOC infrastructure tests mixed with business logic tests | Audit 2026-03-27 | Inflated test ratio; unclear coverage signal |
| — | No integration test exercises real DP-SGD training pipeline | Audit 2026-03-27 | Mock-only DP validation; real behavior untested |

---

## T61.1 — Replace Shallow `is not None` Assertions with Semantic Assertions

**Priority**: P2 — Test quality.

### Context & Constraints

1. 153 occurrences of `assert X is not None` as the sole assertion across 48
   test files.  These pass for any non-None value, including wrong types,
   empty collections, or stale data.
2. Top offenders by count:
   - `test_benchmark_infrastructure.py` (16)
   - `test_shred_endpoint.py` (16)
   - `test_reaper_stale_jobs.py` (12)
   - `test_migration_007_encrypt_connection_metadata.py` (12)
   - `test_synthesizer_tasks_errors.py` (10)
   - `test_authorization_idor_jobs.py` (6)
   - `test_jobs_router.py` (6)
   - `test_dp_budget_fail_closed.py` (5)
3. Fix: Replace each `is not None` with a semantic assertion that validates
   the actual value — type, shape, content, or business invariant.
4. Do NOT delete assertions; replace them.  Each replacement must assert
   something that a mutation would break.

### Acceptance Criteria

1. Zero `assert X is not None` as the sole assertion in any test function.
2. Every replacement asserts a business-meaningful property (value, type +
   content, structure, or invariant).
3. No test functions deleted.
4. Full gate suite passes.

---

## T61.2 — Parameterize DP Task Tests

**Priority**: P3 — Test maintainability.

### Context & Constraints

1. `tests/unit/test_synthesizer_tasks_dp.py` contains 20+ tests following an
   identical pattern: create mock session → create mock engine → call
   `_run_synthesis_job_impl` → assert `mock.call_args`.
2. 80% code duplication.  Each test varies only in: DP enabled/disabled,
   epsilon value, wrapper presence.
3. Fix: Collapse into ~5 parameterized tests using `@pytest.mark.parametrize`.
   Each parametrized case must retain a descriptive ID string.
4. Estimated LOC reduction: ~500 lines.

### Acceptance Criteria

1. `test_synthesizer_tasks_dp.py` reduced by at least 40% LOC.
2. All original test scenarios preserved as parameterized cases with IDs.
3. No behavioral coverage lost (same assertions, same edge cases).
4. Full gate suite passes.

---

## T61.3 — Separate Infrastructure Tests into Dedicated Suite

**Priority**: P4 — Test organization.

### Context & Constraints

1. ~15 test files (~6,500 LOC) validate infrastructure, scaffolding, and
   documentation rather than production business logic:
   - `test_validate_pipeline_infrastructure.py` (706 LOC)
   - `test_readme_links.py` (317 LOC)
   - `test_notebook_infrastructure.py` (404 LOC)
   - `test_benchmark_infrastructure.py` (542 LOC)
   - `test_dead_dependency_cleanup.py` (402 LOC)
   - `test_mutation_testing_infrastructure.py` (323 LOC)
   - `test_ai_builder_notebook.py` (399 LOC)
   - `test_pagila_provisioning.py` (442 LOC)
   - `test_release_workflow.py` (561 LOC) [if in tests/unit]
   - `test_version_bump.py` (461 LOC) [if in tests/unit]
2. Fix: Add `@pytest.mark.infrastructure` marker to all infrastructure tests.
   Register marker in `pyproject.toml`.  Document how to exclude them:
   `pytest -m "not infrastructure"`.
3. Do NOT move files — markers are less disruptive than directory moves.
4. Update CI to report infrastructure and business-logic coverage separately.

### Acceptance Criteria

1. All infrastructure test files marked with `@pytest.mark.infrastructure`.
2. Marker registered in `pyproject.toml` (no unknown-marker warnings).
3. `pytest -m "not infrastructure"` excludes all infrastructure tests.
4. Business-logic test-to-code ratio reported separately in CI output.
5. Full gate suite passes (all tests still run; marker is for filtering only).

---

## T61.4 — Add Real DP-SGD Integration Test

**Priority**: P3 — Test depth.

### Context & Constraints

1. All DP training tests in `test_synthesizer_tasks_dp.py` mock the engine,
   session, and DP wrapper.  No test exercises real Opacus DP-SGD training.
2. `tests/integration/test_dp_training_integration.py` exists but uses
   limited mocking — verify its coverage and extend if needed.
3. Fix: Add or extend an integration test that:
   - Creates a real `DPCompatibleCTGAN` instance
   - Trains on a small fixture DataFrame (≤100 rows, 3 columns)
   - Verifies epsilon consumption is non-zero and within bounds
   - Verifies generated output has correct schema
4. Guard with `@pytest.mark.synthesizer` so it only runs when the
   synthesizer optional dependency group is installed.

### Acceptance Criteria

1. Integration test exercises real CTGAN + Opacus DP-SGD training.
2. Test verifies: epsilon > 0, output schema matches input, row count
   matches requested count.
3. Test completes in < 60 seconds on a 4-core machine.
4. Guarded by `@pytest.mark.synthesizer`.
5. Full gate suite passes.

---

## Task Execution Order

```
T61.1 (shallow assertions) ──────────> largest scope, do first
T61.2 (parameterize DP tests) ───────> independent
T61.3 (infrastructure markers) ──────> independent
T61.4 (real DP integration test) ────> independent, requires synthesizer deps
```

---

## Phase 61 Exit Criteria

1. Zero `is not None` sole assertions in any test function.
2. `test_synthesizer_tasks_dp.py` reduced by ≥40% LOC via parameterization.
3. Infrastructure tests marked and filterable.
4. Real DP-SGD integration test passing.
5. All quality gates pass.
6. Review agents pass for all tasks.
