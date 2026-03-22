# Phase 50 — Test Quality Hardening

**Goal**: Systematically remediate test suite quality issues identified in the
architecture review. The test suite is 74,290 LOC with ~30% of tests that would
pass even if the implementation were broken. This phase fixes the tests, not the
production code.

**Prerequisite**: Phase 49 merged (framework amendments establishing assertion
quality gate and mutation testing requirement).

**ADR**: ADR-0047 (Mutation Testing Gate) governs T50.5.

**Source**: Staff-level architecture review, 2026-03-22 — test efficacy analysis.

---

## T50.1 — Assertion Hardening: Security-Critical Tests

**Priority**: P0 — Security. Shallow assertions on security features mean defects
go undetected.

### Context & Constraints

1. `test_download_hmac_signing.py` has only 4 tests for a security-critical
   feature. Missing: signature forgery, replay attacks, key rotation, wrong
   algorithm.
2. `test_audit.py:138-161` uses `assert field in parsed` — only checks key
   presence, not value validity. An audit event with `timestamp: null` passes.
3. `test_dp_accounting.py` logs budget deduction failures but tests don't verify
   the failure propagates correctly.
4. `test_ale.py` asserts `ciphertext is not None` — would pass if encryption
   returns empty bytes.

### Acceptance Criteria

1. `test_download_hmac_signing.py` expanded to >=12 tests: add forgery (tampered
   signature), replay (old sig on new data), key rotation handling, wrong hash
   algorithm (SHA1 vs SHA256), empty payload, oversized payload.
2. `test_audit.py` field-presence assertions replaced with value validity: type
   check + non-empty + format validation for timestamp, event_type, actor.
3. `test_dp_accounting.py` gains tests verifying that unknown exceptions from
   `spend_budget_fn` propagate (not just log). At least 3 negative test cases for
   budget failure paths.
4. `test_ale.py` assertions replaced: `assert len(ciphertext) > 0`, round-trip
   decrypt equals original plaintext, different plaintexts produce different
   ciphertexts.
5. All amended tests pass. No coverage regression.

### Files to Create/Modify

- Modify: `tests/unit/test_download_hmac_signing.py`
- Modify: `tests/unit/test_audit.py`
- Modify: `tests/unit/test_dp_accounting.py`
- Modify: `tests/unit/test_application_level_encryption.py`

---

## T50.2 — Assertion Hardening: Masking & Subsetting Tests

**Priority**: P1 — Quality. Determinism tests would pass if salt parameter is
completely ignored.

### Context & Constraints

1. 8+ masking determinism tests repeat the same pattern:
   `assert mask_X(val, salt) == mask_X(val, salt)`. This would pass if the
   function ignores the salt and returns a constant.
2. These 8 tests should be parametrized into 1 test with
   `@pytest.mark.parametrize`.
3. `test_subsetting_core.py` mocks 100% of SQLAlchemy. Missing negative cases:
   circular FK reference during traversal, egress writer mid-stream failure, DB
   disconnect.
4. `test_settings_router.py` — 5 tests in 266 lines, assertions are exclusively
   `isinstance()`. No field value assertions.

### Acceptance Criteria

1. Every masking determinism test also asserts:
   `mask_X(val, salt_a) != mask_X(val, salt_b)` — different salt produces
   different output.
2. 8 duplicate determinism functions parametrized into 1
   `@pytest.mark.parametrize` test.
3. `test_subsetting_core.py` gains >=3 negative test cases: circular FK,
   mid-stream egress failure, connection loss.
4. `test_settings_router.py` assertions replaced with specific field value checks.
5. Net test function count may decrease (parametrization). Coverage must not
   regress.

### Files to Create/Modify

- Modify: `tests/unit/test_masking_determinism.py` (or whichever files contain
  the determinism tests)
- Modify: `tests/unit/test_masking_algorithms.py`
- Modify: `tests/unit/test_subsetting_core.py` (or equivalent test file for
  subsetting)
- Modify: `tests/unit/test_settings_router.py`

---

## T50.3 — Mock Reduction Pass

**Priority**: P2 — Reliability. 100% mocked tests won't catch API version drift.

### Context & Constraints

1. `test_dp_engine.py` mocks 100% of Opacus. Zero actual Opacus invocations.
   Would not detect Opacus API version break.
2. `test_synthesizer_guardrails.py` mocks `psutil.virtual_memory()` and
   `torch.cuda` completely. Missing: psutil raising exception, torch.cuda failure,
   memory=0 edge case.
3. Mock helpers (`_make_engine()`, `_make_topology()`, `_mock_vmem()`) are
   duplicated across 4+ test files.
4. monkeypatch environment variable boilerplate repeated across 10+ files (3-5
   lines of identical setup per test).

### Acceptance Criteria

1. `test_dp_engine.py` gains >=1 integration-style test using real Opacus (tiny
   model, 10 rows) to verify API compatibility. Mark with
   `@pytest.mark.synthesizer` for optional CI.
2. `test_synthesizer_guardrails.py` gains 3 edge case tests: psutil exception,
   torch.cuda exception, available memory = 0.
3. Shared mock helpers moved to `tests/unit/conftest.py` or `tests/fixtures/`.
   Duplicates removed from individual test files.
4. Shared environment variable fixture created for JWT/auth test setup.
   Deduplicate across 10+ files.
5. No coverage regression. Mock count may decrease.

### Files to Create/Modify

- Modify: `tests/unit/test_dp_engine.py`
- Modify: `tests/unit/test_synthesizer_guardrails.py`
- Modify: `tests/unit/conftest.py` (add shared fixtures)
- Modify: Multiple test files (deduplicate env var setup)
- Create: `tests/fixtures/mock_helpers.py` (if not using conftest)

---

## T50.4 — Test Organization Cleanup

**Priority**: P3 — Maintainability. Large files and missing docs increase
cognitive load.

### Context & Constraints

1. `test_synthesizer_tasks.py` is 2,738 lines — largest test file, hard to
   navigate, mixes unit and integration patterns.
2. 30+ test files lack module docstrings explaining what they test.
3. Copy-paste test patterns identified across masking, auth, and subsetting tests.

### Acceptance Criteria

1. `test_synthesizer_tasks.py` split into <=3 focused files (by concern: task
   lifecycle, error handling, integration).
2. Module docstrings added to all test files that lack them (brief: one line
   stating what module/feature is under test).
3. No copy-paste test blocks (>5 lines identical) across files. Shared patterns
   extracted to fixtures or parametrized.
4. All tests pass. No coverage regression.

### Files to Create/Modify

- Split: `tests/unit/test_synthesizer_tasks.py` -> multiple files
- Modify: 30+ test files (add docstrings)
- Modify: Various test files (deduplicate patterns)

---

## T50.5 — Mutation Testing Baseline

**Priority**: P1 — Quality. Establish the mutation testing gate required by
ADR-0047.

### Context & Constraints

1. ADR-0047 mandates mutation testing on `shared/security/` and
   `modules/privacy/`.
2. `mutmut` must be added to dev dependencies.
3. Initial threshold: 60% mutation score.
4. Surviving mutants in security-critical code must be fixed (new tests or
   hardened assertions).

### Acceptance Criteria

1. `mutmut` added to `pyproject.toml` dev dependencies.
2. Mutation testing configured in `pyproject.toml` for `shared/security/` and
   `modules/privacy/`.
3. Baseline mutation score documented.
4. Surviving mutants in `shared/security/vault.py`,
   `shared/security/hmac_signing.py`, and `modules/privacy/accountant.py` killed
   (new tests written).
5. Mutation score >=60% on target modules.
6. CI gate configured (can be advisory-only initially if full enforcement blocks).

### Files to Create/Modify

- Modify: `pyproject.toml` (add mutmut dependency + config)
- Create: Tests to kill surviving mutants (locations TBD after baseline run)
- Modify: CI config if applicable

---

## Task Execution Order

```
T50.1 (Security assertion hardening) ──┐
T50.2 (Masking assertion hardening) ───┼──> parallel (assertion hardening)
                                        ┘
                                          ↓ assertion tasks complete
T50.3 (Mock reduction) ────────────────┐
T50.4 (Test organization cleanup) ─────┼──> parallel (structural cleanup)
                                        ┘
                                          ↓ structural cleanup complete
T50.5 (Mutation testing baseline) ─────> sequential (requires hardened tests)
```

T50.1 and T50.2 are independent assertion hardening tasks that can run in
parallel. T50.3 and T50.4 are structural cleanup tasks that can run in parallel
but benefit from hardened assertions being in place first. T50.5 runs last because
mutation scores are more meaningful after assertions are hardened.

---

## Phase 50 Exit Criteria

1. Security-critical tests have deep assertions (not just existence/type checks).
2. Masking determinism tests verify salt sensitivity, not just self-equality.
3. Shared mock helpers deduplicated into conftest or fixtures.
4. Largest test file split into focused modules.
5. All test files have module docstrings.
6. Mutation testing baseline established at >=60% on target modules.
7. All quality gates pass.
8. Zero open advisories in RETRO_LOG.
9. Review agents pass for all tasks.
