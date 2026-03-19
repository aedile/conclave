# Phase 35 — Synthesis Layer Refactor & Test Replacement

**Goal**: Decompose the two highest-cognitive-load files in the codebase (`dp_training.py`
at 1,144 lines and `job_orchestration.py` with a 232-line god-function), then replace
the tautological mock-poisoned DP training tests with tests that actually verify behavior.

**Prerequisite**: Phase 34 merged. Zero open advisories.

**ADR**: ADR-0038 — Synthesis Orchestration Step Decomposition (new).

**Source**: Production Readiness Audit, 2026-03-18 — Critical Issues C2, C3, C4.

---

## T35.1 — Decompose `_run_synthesis_job_impl()` Into Discrete Job Steps

**Priority**: P0 — The 232-line god-function at `job_orchestration.py:387-619` orchestrates
10 sequential steps with 8 levels of nesting. A bug in any step requires understanding all
steps to debug.

### Context & Constraints

1. `_run_synthesis_job_impl()` currently handles: OOM preflight check, Parquet loading,
   training dispatch, DP accounting, budget spending, generation, Parquet writing with HMAC
   signing, job status transitions, error recovery, and finalization — all in one function.

2. Job status transitions happen in **4 different files** (`job_orchestration.py`,
   `routers/jobs.py`, `routers/jobs_streaming.py`, `tasks.py`) with no state machine
   abstraction. This is a maintenance trap.

3. Proposed decomposition:

   ```
   class SynthesisJobStep(Protocol):
       def execute(self, context: JobContext) -> StepResult: ...

   Steps:
     OomCheckStep          → guardrails.py (already exists, wrap)
     TrainingStep          → new, delegates to SynthesisEngine.train()
     DpAccountingStep      → new, wraps _handle_dp_accounting()
     GenerationStep        → new, delegates to SynthesisEngine.generate()
     FinalizationStep      → job_finalization.py (already exists, wrap)
   ```

4. `JobContext` dataclass carries the mutable state (job record, session, engine instance,
   dp_wrapper) that currently lives as local variables in the god-function.

5. The orchestrator becomes a simple loop: `for step in steps: step.execute(ctx)`.

6. ADR-0038 must document the step protocol, ordering constraints, and error propagation
   strategy.

### Acceptance Criteria

1. `_run_synthesis_job_impl()` is replaced by a step-based orchestrator under 50 lines.
2. Each step is independently unit-testable without mocking the other steps.
3. `JobContext` dataclass defined with typed fields for all shared state.
4. Job status transitions centralized — only the orchestrator sets `job.status`.
5. No functional changes — synthesis output is identical before and after refactor.
6. ADR-0038 created.
7. Full gate suite passes. Coverage does not regress below 95%.

### Testing & Quality Gates

- Existing orchestration tests must pass (they test observable behavior, not internals).
- Add 1 test per step verifying it can execute in isolation with a mock `JobContext`.
- Add 1 test verifying step ordering is enforced (e.g., training before DP accounting).
- QA + Architecture reviewers spawned.

### Files to Create/Modify

- Create: `src/synth_engine/modules/synthesizer/job_steps.py` (Step protocol + JobContext)
- Modify: `src/synth_engine/modules/synthesizer/job_orchestration.py` (replace god-function)
- Modify: `src/synth_engine/bootstrapper/routers/jobs.py` (remove status transitions if present)
- Modify: `src/synth_engine/bootstrapper/routers/jobs_streaming.py` (remove status transitions if present)
- Create: `docs/adr/ADR-0038-synthesis-orchestration-step-decomposition.md`
- Create or modify: `tests/unit/test_job_steps.py`

---

## T35.2 — Split `dp_training.py` Into Strategy Classes

**Priority**: P0 — At 1,144 lines with 10+ instance variables and a 12-parameter method,
`DPCompatibleCTGAN` is the highest-cognitive-load class in the codebase.

### Context & Constraints

1. `dp_training.py` currently contains:
   - `DPCompatibleCTGAN` class — handles both vanilla CTGAN and DP-SGD paths
   - `_run_gan_epoch()` — 92 lines, 12 parameters, trains discriminator
   - `_train_dp_discriminator()` — 72 lines, DP loss calculation
   - `fit()` — 90 lines, 6+ branching paths (DP vs vanilla, cold vs warm)
   - Multiple private helpers (`_cap_batch_size`, `_parse_gan_hyperparams`,
     `_build_proxy_dataloader`, `_get_data_processor`, `_store_dp_training_state`)

2. Proposed decomposition:

   | New file | Contents | Lines (est.) |
   |----------|----------|:---:|
   | `dp_training.py` | `DPCompatibleCTGAN` (thin coordinator, delegates to strategy) | ~200 |
   | `training_strategies.py` | `VanillaCtganStrategy`, `DpCtganStrategy` | ~500 |
   | `ctgan_utils.py` | `_cap_batch_size`, `_parse_gan_hyperparams`, `_build_proxy_dataloader` | ~200 |

3. The 12-parameter `_run_gan_epoch()` should receive a `TrainingConfig` dataclass instead
   of individual arguments.

4. Instance variable proliferation (10+ on `DPCompatibleCTGAN`) should be reduced by
   grouping related state into dataclasses (`DPState`, `TrainingState`).

5. Import-linter contracts must be updated if new files are created within the synthesizer
   module.

### Acceptance Criteria

1. `dp_training.py` is under 300 lines.
2. No function exceeds 50 lines.
3. No function takes more than 5 parameters (use config dataclasses for the rest).
4. `DPCompatibleCTGAN` delegates to strategy objects, not branching internally.
5. `TrainingConfig` dataclass replaces the 12-parameter `_run_gan_epoch()` signature.
6. All existing DP training tests pass without modification (public API unchanged).
7. Import-linter contracts updated and passing.
8. Full gate suite passes.

### Testing & Quality Gates

- Existing tests are the regression baseline — zero test modifications allowed.
- Add 1 test verifying `VanillaCtganStrategy` can be constructed independently.
- Add 1 test verifying `DpCtganStrategy` can be constructed independently.
- QA + Architecture reviewers spawned.

### Files to Create/Modify

- Create: `src/synth_engine/modules/synthesizer/training_strategies.py`
- Create: `src/synth_engine/modules/synthesizer/ctgan_utils.py`
- Modify: `src/synth_engine/modules/synthesizer/dp_training.py` (thin down)
- Modify: `pyproject.toml` (import-linter contracts if needed)
- Create: `tests/unit/test_training_strategies.py`

---

## T35.3 — Replace Tautological DP Training Tests

**Priority**: P0 — The DP training tests have 54:1 and 79:1 setup-to-assertion ratios.
They mock the entire CTGAN call chain, making them tautological — they prove the mocks
were called, not that synthesis works.

### Context & Constraints

1. Three test files are critically affected:
   - `test_dp_training_loop.py` — 919 lines, 17 assertions (54:1 ratio)
   - `test_dp_training_sample.py` — 397 lines, 5 assertions (79:1 ratio)
   - `test_ingestion_adapter.py` — 461 lines, 14 assertions (32:1 ratio)

2. The root cause is that tests mock `CTGANSynthesizer` internals (`.preprocess()`,
   `._data_processor`, `._model_kwargs`) at 40+ lines of configuration, then assert
   the mock was called. If SDV changes its internal API, these tests still pass.

3. Replacement strategy:
   - **Behavioral tests**: Use small (10-row) real DataFrames with known distributions.
     Call `fit()` / `sample()` and assert output shape, column types, and statistical
     properties (mean within 2 std devs of input).
   - **Spy-based tests**: Use `wraps=` on real objects instead of `MagicMock()` to verify
     call sequences while preserving real behavior.
   - **Contract tests**: For SDV internals that are too slow for unit tests, create
     contract tests that verify the assumptions the mocks encode (e.g., "CTGANSynthesizer
     has a `preprocess()` method that returns a DataFrame").

4. Slow tests (real CTGAN training) should be marked `@pytest.mark.slow` and excluded
   from the default unit gate but included in integration.

5. The 2:1 test-to-code ratio claim on the project is hollow if the tests don't assert
   business logic. This task must bring the assertion density to at least 1 assertion
   per 20 lines of test code (5:1 ratio or better).

### Acceptance Criteria

1. `test_dp_training_loop.py` has setup-to-assertion ratio under 10:1.
2. `test_dp_training_sample.py` has setup-to-assertion ratio under 10:1.
3. `test_ingestion_adapter.py` has setup-to-assertion ratio under 10:1.
4. Zero `MagicMock()` instances used for objects under test (mocks allowed only for
   external I/O boundaries: database, filesystem, network).
5. At least 3 behavioral tests using real (small) DataFrames with statistical assertions.
6. At least 2 contract tests verifying SDV API assumptions.
7. Tests marked `@pytest.mark.slow` for real CTGAN runs (>5s).
8. Full gate suite passes. Coverage does not regress.

### Testing & Quality Gates

- This IS the testing task. The deliverable is better tests.
- QA reviewer must verify assertion density and mock usage.
- QA reviewer spawned.

### Files to Create/Modify

- Rewrite: `tests/unit/test_dp_training_loop.py`
- Rewrite: `tests/unit/test_dp_training_sample.py`
- Rewrite: `tests/unit/test_ingestion_adapter.py`
- Create: `tests/unit/test_sdv_contracts.py` (contract tests for SDV API assumptions)
- Modify: `pyproject.toml` (add `slow` marker to pytest config if not present)

---

## T35.4 — Add Full E2E Pipeline Integration Test

**Priority**: P0 — No single test currently exercises the complete pipeline:
DB source -> masking -> subsetting -> synthesis -> download.

### Context & Constraints

1. Existing integration tests cover individual modules (budget contention, DP pipeline,
   NIST erasure) but no test validates the full pipeline end-to-end with zero mocks
   below the API boundary.

2. `tests/integration/test_subsetting_boundary.py` is an import boundary check
   masquerading as an integration test — it verifies module imports, not subsetting behavior.

3. The E2E test should:
   - Seed a real PostgreSQL database (via pytest-postgresql) with a small (5-table, 50-row)
     schema including FK relationships.
   - Call the masking pipeline and verify deterministic output.
   - Call the subsetting engine and verify FK-consistent subset extraction.
   - Call the synthesis engine with a small epoch count (1-2) and verify output shape.
   - Call the download endpoint and verify HMAC signature on the Parquet artifact.
   - Verify privacy budget was decremented correctly.

4. This test will be slow (30-60s). Mark with `@pytest.mark.slow` and
   `@pytest.mark.integration`.

5. Missing concurrent edge-case tests (concurrent masking, concurrent vault unsealing,
   concurrent budget exhaustion) should be added as separate test functions within the
   same file or a companion file.

### Acceptance Criteria

1. New test file exercises full pipeline: seed -> mask -> subset -> synthesize -> download.
2. Zero mocks below the API boundary (real PostgreSQL, real filesystem, real HMAC).
3. Assertions verify: masking determinism, FK consistency in subset, output DataFrame
   shape, HMAC signature validity, privacy budget decrement.
4. At least 1 concurrent budget exhaustion test (2+ simultaneous spend attempts).
5. Test passes in CI with pytest-postgresql and docker-compose test stack.
6. Full gate suite passes.

### Testing & Quality Gates

- This IS the testing task.
- DevOps reviewer must verify CI configuration supports the test dependencies.
- QA + DevOps reviewers spawned.

### Files to Create/Modify

- Create: `tests/integration/test_e2e_full_pipeline.py`
- Create: `tests/integration/test_concurrent_edge_cases.py` (if not folded into above)
- Modify: `conftest.py` or `tests/conftest.py` (add PostgreSQL fixtures if needed)
- Modify: `.github/workflows/ci.yml` (ensure integration gate runs this test)

---
