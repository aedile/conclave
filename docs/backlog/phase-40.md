# Phase 40 — Test Suite Quality Overhaul

**Goal**: Replace shallow, mock-heavy, and trivial tests with assertions that
verify actual business logic. Address the finding that the 95% coverage number
is inflated by ~20-25% of test lines being `callable()`, `is not None`, or
`isinstance()` assertions. Add missing test categories: concurrency, boundary
values, performance regression, and real DP training integration.

**Prerequisite**: Phase 39 merged. Zero open advisories.

**ADR**: None required — no architectural decisions, only test quality improvement.

**Source**: Production Readiness Audit, 2026-03-19 — Critical Issue C4, High Issues H1, H2.

---

## T40.1 — Replace Shallow Assertions With Value-Checking Tests

**Priority**: P0 — Test Quality. 30+ test files contain assertions that verify
types or existence but not correctness. These inflate coverage without catching bugs.

### Context & Constraints

1. **Trivial existence tests to rewrite or delete:**
   - `test_bootstrapper_errors_package.py:20-55` — 6 tests that only assert
     `callable()` or `is not None`. These should be deleted (the import itself
     proves existence) or replaced with behavior tests.
   - `test_synth_engine.py` — 8 lines testing `__version__` is a string. Delete
     or replace with a semver format assertion.
   - `test_t34_2_exception_hierarchy.py:23-27,66-70` — `assert X is not None`
     after import. Delete — the import IS the test.

2. **Type-only assertions to strengthen:**
   - `test_masking_algorithms.py:42,92,143,172,319,353` — `isinstance(result, str)`
     should assert the masked value differs from input, preserves length (FPE),
     and is deterministic (same input → same output).
   - `test_dp_engine.py:351` — `isinstance(result, float)` should assert
     epsilon is positive and within expected range.
   - `test_vault.py:74,186` — `isinstance(kek, bytes)` should assert key length
     is 32 bytes (256-bit KEK).
   - `test_profiler.py:77` — `isinstance(result, TableProfile)` should assert
     column count matches input DataFrame.
   - `test_synthesizer_engine.py:82,245,399,440,510,569,591,639` — Replace all
     `isinstance(result, ModelArtifact)` with assertions on artifact path,
     metadata, and signature.

3. **`is not None` assertions to replace (32+ occurrences):**
   - `test_profiler.py:167,172,177,207` — 4x `assert col.value_counts is not None`
     should assert actual value counts match expected distribution.
   - `test_privacy_accountant.py:90-116` — Replace `is not None` with assertions
     on ledger balance, transaction amounts, and epsilon precision.
   - `test_dp_discriminator.py:53` — Assert discriminator has correct layer sizes.
   - `test_dp_engine.py:59` — Assert wrapper configuration matches input params.

4. **Trivial dataclass tests to parameterize:**
   - `test_synthesizer_tasks.py:66-150` — 6 separate tests for 6 dataclass fields.
     Replace with a single `@pytest.mark.parametrize` test.
   - `test_t34_2_exception_hierarchy.py:20-114` — 12 redundant exception tests.
     Replace with parameterized test over exception classes.

5. Do NOT delete tests that are the sole coverage for a code path. Strengthen them
   instead. Run coverage with `--cov-report=term-missing` before and after to
   verify no regressions.

### Acceptance Criteria

1. Zero `assert callable(...)` assertions remain in test suite.
2. Zero `assert X is not None` assertions that don't also check the value.
3. All `isinstance()` assertions accompanied by at least one value assertion.
4. Trivial dataclass field tests consolidated into parameterized tests.
5. Exception hierarchy tests consolidated into parameterized tests.
6. Coverage remains ≥95% (no regressions from deleting trivial tests).
7. Full gate suite passes.

### Files to Create/Modify

- Modify: `tests/unit/test_bootstrapper_errors_package.py`
- Modify: `tests/unit/test_synth_engine.py`
- Modify: `tests/unit/test_t34_2_exception_hierarchy.py`
- Modify: `tests/unit/test_masking_algorithms.py`
- Modify: `tests/unit/test_dp_engine.py`
- Modify: `tests/unit/test_vault.py`
- Modify: `tests/unit/test_profiler.py`
- Modify: `tests/unit/test_synthesizer_engine.py`
- Modify: `tests/unit/test_privacy_accountant.py`
- Modify: `tests/unit/test_dp_discriminator.py`
- Modify: `tests/unit/test_synthesizer_tasks.py`
- Modify: `tests/unit/test_bootstrapper_errors.py`

---

## T40.2 — Replace Mock-Heavy Tests With Behavioral Tests

**Priority**: P0 — Test Quality. 15+ test files mock away the core logic they
claim to test. These create false confidence — a regression in the mocked code
would never be caught.

### Context & Constraints

1. **Critical mock-heavy files to rewrite:**
   - `test_dp_training_init.py:228-301` — 12 tests with 3+ deep mocks each.
     Claims to test `DPCompatibleCTGAN.fit()` but mocks CTGAN, detect_discrete_columns,
     and all internals. Rewrite to test with a minimal real DataFrame (5 rows, 2 columns)
     and verify output shape, fitted state, and epsilon reporting.
   - `test_dp_training_edge_cases.py:107-149` — "Edge case" tests use `torch.zeros()`
     mocks. Rewrite to use real tensors with known edge-case shapes (single row,
     single column, all-null column).
   - `test_api_roundtrip.py` — Claims "full roundtrip" with 10 patches. Either
     convert to a real integration test or rename to `test_api_routing_wiring.py`
     to be honest about scope.

2. **Mock reduction targets:**
   - `test_synthesizer_guardrails.py` — Mocks `_monitor_memory()`. Should test
     with a real (small) allocation that triggers the OOM guardrail.
   - `test_synthesizer_engine.py` — Mocks DPCompatibleCTGAN initialization.
     Should test with real (minimal) CTGAN if synthesizer group is installed.

3. **Rule of thumb for this task:** If a test patches more than 2 things in the
   module under test, it is testing wiring, not behavior. Wiring tests are fine
   but must be LABELED as such (class name `TestXxxWiring`).

4. Tests that legitimately need mocks (external services, GPU, database) should
   mock at the boundary (e.g., mock the database session, not the ORM model).

5. The `synthesizer` optional dependency group may not be available in all CI
   environments. Tests requiring real torch/SDV must be marked with
   `@pytest.mark.synthesizer` and skipped gracefully.

### Acceptance Criteria

1. `test_dp_training_init.py` rewritten — tests exercise real DPCompatibleCTGAN
   with minimal data (or are honestly labeled as wiring tests).
2. `test_dp_training_edge_cases.py` rewritten — tests use real tensors.
3. `test_api_roundtrip.py` either converted to real integration test or renamed.
4. Mock-heavy tests that remain are labeled `TestXxxWiring` in class name.
5. Net mock count reduced by ≥30% (from 1,275 baseline).
6. Coverage remains ≥95%.
7. Full gate suite passes.

### Files to Create/Modify

- Rewrite: `tests/unit/test_dp_training_init.py`
- Rewrite: `tests/unit/test_dp_training_edge_cases.py`
- Modify: `tests/integration/test_api_roundtrip.py` (convert or rename)
- Modify: `tests/unit/test_synthesizer_guardrails.py`
- Modify: `tests/unit/test_synthesizer_engine.py`

---

## T40.3 — Add Missing Test Categories: Concurrency, Boundary Values, Performance

**Priority**: P1 — Test Quality. Zero concurrency tests beyond budget contention,
zero boundary value tests, zero performance regression tests.

### Context & Constraints

1. **Concurrency tests needed:**
   - Concurrent job starts: 2 operators start jobs simultaneously. Verify both
     get correct job IDs, no cross-contamination.
   - Concurrent masking: 2 threads mask the same table concurrently. Verify
     deterministic output (FPE is stateless, should be safe).
   - Vault state transition race: unseal + seal simultaneously. Verify no
     partial state (KEK must be fully set or fully zeroed).
   - Parallel artifact downloads: verify streaming doesn't corrupt chunks.

2. **Boundary value tests needed:**
   - Empty DataFrame → synthesis engine (should raise, not silently return empty)
   - Single-row DataFrame → DP training (minimum viable training set)
   - Zero epsilon budget → spend_budget() (should raise BudgetExhaustionError)
   - Negative epsilon value (invalid — should be rejected at validation)
   - Very large epsilon value (valid but suspicious — should work)
   - Unicode/emoji in masking input columns
   - Maximum-length strings in FPE masking
   - `NUMERIC(20,10)` precision boundary: value that rounds to 0

3. **Performance regression tests needed:**
   - Masking 10,000 rows must complete in <5 seconds
   - Privacy budget query must complete in <100ms
   - Artifact signing must complete in <1 second for 100MB file
   - These are not strict SLAs — they're regression detectors. If a change
     doubles the time, the test fails.

4. Performance tests should use `@pytest.mark.slow` and be excluded from the
   fast unit test gate. They run in the integration gate.

### Acceptance Criteria

1. ≥4 concurrency tests (job starts, masking, vault state, downloads).
2. ≥8 boundary value tests covering empty, single-row, zero/negative epsilon,
   unicode, max-length, and precision boundary.
3. ≥3 performance regression tests with time bounds.
4. Performance tests marked `@pytest.mark.slow`.
5. Full gate suite passes.

### Files to Create/Modify

- Create: `tests/integration/test_concurrency_safety.py`
- Create: `tests/unit/test_boundary_values.py`
- Create: `tests/integration/test_performance_regression.py`
- Modify: `pyproject.toml` (add `slow` marker definition)

---

## Task Execution Order

```
T40.1 (Shallow assertion rewrite) ──> parallel
T40.2 (Mock-heavy test rewrite) ────> parallel
T40.3 (Missing test categories) ────> parallel (independent files)
```

All three tasks are independent — they touch different test files.

---

## Phase 40 Exit Criteria

1. Zero `callable()`, bare `is not None`, or bare `isinstance()` assertions.
2. Mock-heavy tests rewritten or honestly labeled as wiring tests.
3. ≥4 concurrency, ≥8 boundary value, ≥3 performance regression tests added.
4. Net mock count reduced by ≥30%.
5. Coverage remains ≥95% (real coverage, not inflated).
6. All quality gates pass.
7. Zero open advisories in RETRO_LOG.
8. Review agents pass for all tasks.
