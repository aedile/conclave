# Phase 73 — Test Quality Rehabilitation (PRIORITY 1)

**Goal**: Fix the catastrophic test quality regression from 8-9/10 to 5/10.
Eliminate copy-paste test proliferation, remove shallow assertions that violate
Constitution Priority 4, parametrize repeated patterns, and reduce the 3.9:1
test-to-code ratio to ≤2.5:1 while maintaining ≥95% coverage.

**Rationale**: Over 20 cycles of "test improvement", the test suite has
bloated from focused behavioral tests to copy-paste templates with shallow
assertions. 3,516 test functions with only 32 parametrize decorators is
indefensible. 410 shallow-only assertions (`is not None`, `isinstance`)
violate Constitution Priority 4 which explicitly prohibits this pattern.
This phase is a full rehabilitation, not incremental improvement.

**Source**: Production Audit 2026-03-29, Findings C2, C3

**Advisory incorporation**: ADV-P70-04 (missing composite FK integration
test) is addressed inline in T73.5.

---

## Tasks

### T73.1 — Parametrize Auth Gap Remediation Tests

**Files**: `tests/unit/test_auth_gap_remediation_security.py`,
`tests/unit/test_auth_gap_remediation_privacy.py`,
`tests/unit/test_auth_gap_remediation_settings.py`,
`tests/unit/test_auth_gap_remediation_audit.py`

These 4 files contain 8+ near-identical test functions per endpoint
(unauthenticated, expired token, empty sub, wrong key) that should be
1-2 parametrized tests each.

**ACs**:
1. Each file reduced to ≤3 test functions using `@pytest.mark.parametrize`.
2. All endpoint/scenario combinations still covered.
3. Net line reduction ≥60% per file.
4. Coverage unchanged or improved.
5. No `assert x is not None` as sole assertion — assert specific status codes
   and response bodies.

### T73.2 — Parametrize Auth Middleware and Router Tests

**Files**: `tests/unit/test_auth.py`, `tests/unit/test_scope_enforcement.py`,
`tests/unit/test_authorization_idor_jobs.py`,
`tests/unit/test_authorization_idor_connections.py`,
`tests/unit/test_authorization_jwt_ownership.py`

Replace near-duplicate test functions with parametrized versions.

**ACs**:
1. `test_auth.py:408-637` (6 near-identical middleware tests) reduced to 1-2
   parametrized tests.
2. IDOR test files use `@pytest.mark.parametrize("endpoint", [...])` instead
   of copy-paste per-endpoint tests.
3. Net line reduction ≥40% across all files.
4. All scenarios still covered.

### T73.3 — Eliminate Shallow-Only Assertions (Constitution Violation)

**Scope**: All 116 test files containing `assert x is not None` or
`assert isinstance(x, ...)` as the sole assertion.

Systematic sweep: for each shallow assertion, either:
(a) Add a specific value assertion alongside it, or
(b) Replace it entirely with a behavioral assertion.

Priority targets (highest shallow assertion counts):
- `tests/unit/test_settings_decomposition.py` (19 instances)
- `tests/unit/test_benchmark_infrastructure.py` (17 instances)
- `tests/unit/test_migration_007_encrypt_connection_metadata.py` (12 instances)
- `tests/unit/test_reaper_stale_jobs.py` (12 instances)
- `tests/unit/test_synthesizer_tasks_errors.py` (10 instances)

**ACs**:
1. Zero test functions where `is not None`, `isinstance()`, or `in` is the
   ONLY assertion.
2. Every test function contains at least one assertion comparing against a
   specific expected value.
3. No reduction in coverage.
4. File-by-file audit log in commit message listing changes per file.

### T73.4 — Consolidate Redundant Test Modules

**Scope**: Identify test files testing the same production code from different
angles that could be merged. Look for:
- `test_<module>.py` + `test_<module>_attack.py` + `test_<module>_feature.py`
  that could be sections within one file.
- Test files with ≤3 test functions that should be merged into related files.

**ACs**:
1. No test file with ≤2 test functions remains standalone (merged into
   thematically related file).
2. Merged files use class-based grouping (`class TestAttackScenarios:`,
   `class TestHappyPath:`) for organization.
3. Net reduction of ≥20 test files.
4. All tests still pass, coverage unchanged.

### T73.5 — Add Missing Integration Tests

**Scope**: Fill gaps identified in the audit:
- ADV-P70-04: Composite FK integration test with real PostgreSQL.
- Integration test verifying `httpx.Client` connection reuse (after T72.5).
- Integration test verifying concurrent budget resets return consistent data.

**ACs**:
1. Composite FK integration test uses `pytest-postgresql` with a real database,
   not mocks.
2. Each new test asserts specific values, not just "didn't crash".
3. ADV-P70-04 closed in RETRO_LOG.

### T73.6 — Remove Copy-Paste Infrastructure Tests

**Scope**: `tests/unit/test_dependency_audit.py` (18 tests),
`tests/unit/test_validate_pipeline_infrastructure.py` (27 tests),
`tests/unit/test_notebook_infrastructure.py` (13 tests),
`tests/unit/test_ci_infrastructure.py` (20 tests),
`tests/unit/test_benchmark_infrastructure.py` (10+ tests)

These files often test that files exist or imports work — not behavior.
Evaluate each: if the test only checks file existence or import success,
delete it. If it checks behavior, keep and strengthen.

**ACs**:
1. Every remaining test asserts a behavioral property, not file existence.
2. Infrastructure validation that IS needed (e.g., "all routes have auth") uses
   programmatic enumeration, not hardcoded lists.
3. Net line reduction ≥3,000 lines across infrastructure test files.

### T73.7 — Final Ratio Verification and Quality Gate

**Scope**: Full test suite run with ratio measurement.

**ACs**:
1. Test-to-code ratio ≤ 2.5:1.
2. Coverage ≥ 95%.
3. Zero Constitution Priority 4 violations (no shallow-only assertions).
4. `@pytest.mark.parametrize` used in ≥100 test functions (up from 32).
5. Total test function count reduced from 3,516 to ≤2,500.
