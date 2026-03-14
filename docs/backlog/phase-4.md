# Phase 4: Advanced Generative AI & Differential Privacy

**Goal:** Implement GPU-accelerated synthetic data generation using established open-source
ML libraries (SDV/CTGAN + Opacus) with mathematically provable Differential Privacy
guarantees, so that generated datasets cannot be used to reverse-engineer individual records.

**Dependency:** Blocked by Phase 3.5 completion. All Phase 3.5 exit criteria must pass before
any Phase 4 task begins.

**Parallelization note:** T4.0 gates everything. After T4.0 merges, T4.1, T4.2a, and T4.3a
can run in parallel. T4.2b blocks T4.2c and T4.3b. T4.4 is blocked by T4.3b.

---

## Task 4.0: Synthesizer & DP Library Selection Spike
**Assignee:** [Dev A / PM + Architect]
**Priority:** BLOCKER — gates all other Phase 4 tasks.
**Estimated Effort:** 1 day (research + ADR — no production code)

### User Story / Agentic Goal
As a Machine Learning Architect, I need a documented, binding decision on the synthesis
library (SDV HMA1 vs CTGAN/TVAE) and the Differential Privacy library (Opacus vs OpenDP),
so that T4.2 and T4.3 can be planned and implemented without making contradictory
architectural assumptions.

### Context & Constraints
- **The libraries are not interchangeable.** The wrong pairing produces a system where DP
  cannot be applied to the chosen synthesizer:
  - **HMA1** (SDV Hierarchical Modeling Algorithm): handles multi-table relational schemas
    natively, preserves FK relationships across tables. It is a statistical model — DP-SGD
    (gradient perturbation) cannot be applied to it. DP for HMA1 would require output
    perturbation (Laplace/Gaussian mechanisms on query results), which has weaker guarantees.
  - **CTGAN / TVAE** (SDV single-table GAN/VAE): neural network-based, DP-SGD applies
    directly via Opacus. Does not natively handle multi-table FK relationships — requires
    per-table training with FK constraints enforced by the subsetting engine.
  - **Opacus** (Meta/PyTorch DP-SGD): the industry standard for training PyTorch neural
    networks with DP. Works with CTGAN/TVAE. Does NOT work with HMA1.
  - **OpenDP**: query-based mechanisms (Laplace, Gaussian on aggregate statistics). Works
    with HMA1 output perturbation. Not designed for neural network training.
- Phase 0.8 Spike A proved memory feasibility and that SDV solves tabular synthesis within
  constraints. It did not resolve the multi-table vs single-table tradeoff.
- The ADV-008 advisory (spike code uses unseeded PRNG and silent error swallow) will be
  fixed in T3.5.5 before this spike reuses any spike code.
- This task produces an ADR only. No production code is committed.

### Acceptance Criteria
- [ ] A new ADR (`docs/adr/ADR-0016-synthesizer-dp-library-selection.md`) is committed that:
  - Documents the evaluated options (HMA1 vs CTGAN/TVAE; Opacus vs OpenDP).
  - States the chosen combination with explicit rationale.
  - Documents the FK-handling strategy for the chosen synthesizer (if CTGAN: how per-table
    training preserves FK relationships; if HMA1: how DP is applied without DP-SGD).
  - Documents the Epsilon/Delta accounting strategy (Rényi DP accountant vs Moments
    accountant vs simple composition).
  - References Phase 0.8 Spike A findings.
- [ ] The ADR is reviewed by the PM and user before any T4.2/T4.3 implementation begins.

### Testing & Quality Gates
- No code, no tests. This is a research and documentation task.
- ADR must be approved by the user before the task closes.

### Files to Create/Modify
- [NEW] `docs/adr/ADR-0016-synthesizer-dp-library-selection.md`

### Definition of Done (DoD) Checklist
1. **Decision Made:** Library pair is chosen and rationale documented.
2. **FK Strategy Defined:** Multi-table handling approach is unambiguous.
3. **DP Accounting Defined:** How Epsilon is tracked per training run is specified.
4. **User Approved:** ADR reviewed and approved before implementation starts.

---

## Task 4.1: GPU Passthrough & Ephemeral Storage Provisioning
**Assignee:** [Dev A]
**Priority:** Blocked by T4.0 (needs to know whether GPU training is required)
**Estimated Effort:** 1.5 days
**Can run in parallel with T4.2a and T4.3a after T4.0 merges.**

### User Story / Agentic Goal
As a Machine Learning Engineer, I need GPU hardware accessible to PyTorch containers and
a high-throughput ephemeral blob storage layer for temporary Parquet/checkpoint files,
so that training jobs can run fast without polluting the main PostgreSQL database or
surviving container termination.

### Context & Constraints
- Docker Compose must enable the NVIDIA Container Toolkit via the `deploy.resources.reservations`
  GPU spec (not the deprecated `runtime: nvidia`).
- Ephemeral storage must use `tmpfs` — data must evaporate when the container stops. This is
  a privacy mandate: no training artifacts survive termination.
- MinIO is already provisioned for local dev in `docker-compose.yml` (Task 1.5). The Phase 4
  use is a dedicated ephemeral bucket, not the existing local dev bucket.
- The storage utility must live in `src/synth_engine/modules/synthesizer/storage.py` — NOT
  `src/synth_engine/storage/` (which does not exist in the module topology).
- The data flow for training: `SubsettingEngine` output → Parquet files in ephemeral storage
  → synthesizer reads Parquet. The synthesizer does NOT re-read from PostgreSQL directly.
  This preserves the module boundary: synthesizer depends on files, not on ingestion.
- If GPU hardware is unavailable (CI, development machines without NVIDIA), the system MUST
  fall back to CPU via a `FORCE_CPU=true` environment variable. The fallback must be
  documented and testable without hardware.

### Acceptance Criteria
- [ ] `docker-compose.yml` updated with NVIDIA Container Toolkit GPU spec using the
  `deploy.resources.reservations.devices` format.
- [ ] A dedicated `synth-ephemeral` MinIO bucket is configured with `tmpfs` backing in
  Docker Compose. Data does not persist past container stop.
- [ ] `src/synth_engine/modules/synthesizer/storage.py` provides:
  - `EphemeralStorageClient` — upload/download Parquet files to/from the ephemeral bucket.
  - `FORCE_CPU` env var respected; `torch.cuda.is_available()` result logged at INFO.
- [ ] The storage client is injectable (takes bucket config as constructor params) so tests
  can use an in-memory or local-path backend without a real MinIO instance.

### Testing & Quality Gates
- Unit test: `EphemeralStorageClient` with a mock backend; upload a small DataFrame, download
  it back, assert equality. Does not require MinIO.
- Unit test: `FORCE_CPU=true` → `EphemeralStorageClient` logs CPU fallback at INFO level;
  no error raised.
- Unit test: GPU detection path is mocked (patch `torch.cuda.is_available`) — do not require
  hardware in CI.

### Files to Create/Modify
- [MODIFY] `docker-compose.yml`
- [NEW] `src/synth_engine/modules/synthesizer/__init__.py` (if not exists)
- [NEW] `src/synth_engine/modules/synthesizer/storage.py`
- [NEW] `tests/unit/test_synthesizer_storage.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Storage module lives in `modules/synthesizer/`, not a new top-level directory.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes without GPU hardware.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 4.2a: Statistical Profiler
**Assignee:** [Dev B]
**Priority:** Blocked by T4.0
**Estimated Effort:** 2 days
**Can run in parallel with T4.1 and T4.3a after T4.0 merges.**

### User Story / Agentic Goal
As a Data Scientist, I want a Statistical Profiler that calculates baseline distributions of
source data before synthesis, so that I can quantitatively verify that the synthetic output
is statistically comparable to the original and detect model drift.

### Context & Constraints
- The profiler has NO dependency on the synthesis library chosen in T4.0 — it operates on
  raw data (DataFrames or Parquet files) and computes statistics. It belongs in
  `modules/profiler/`, which is already a placeholder in the module topology.
- The profiler must handle numeric columns (histograms, mean, stddev, min/max, quartiles,
  nullability rate) and categorical columns (value frequencies, cardinality, nullability rate).
- For numeric columns, compute covariance matrices between all numeric pairs in a table.
- Results are stored as a `ProfileReport` dataclass — a serializable, JSON-exportable
  snapshot of the source data's statistical shape.
- The profiler should be runnable standalone (not tied to a training job) so that users can
  inspect source data statistics before deciding whether to synthesize.
- Cross-module boundary: profiler reads DataFrames (plain Python objects), not from
  PostgreSQL directly. The bootstrapper or subsetting engine is responsible for converting
  DB rows to DataFrames before passing them to the profiler.

### Acceptance Criteria
- [ ] `src/synth_engine/modules/profiler/profiler.py` provides a `StatisticalProfiler` class.
- [ ] `StatisticalProfiler.profile(table_name: str, df: pd.DataFrame) -> TableProfile` computes:
  - Per-column: dtype, null count, null rate, min, max, mean, stddev, quartiles (numeric);
    value_counts, cardinality (categorical).
  - Covariance matrix for all numeric column pairs.
- [ ] `src/synth_engine/modules/profiler/models.py` defines `TableProfile` and
  `ColumnProfile` as frozen dataclasses with `to_dict()` / `from_dict()` methods.
- [ ] `StatisticalProfiler.compare(baseline: TableProfile, synthetic: TableProfile) -> ProfileDelta`
  returns a comparison showing mean/stddev/distribution drift per column.

### Testing & Quality Gates
- Unit test: profile a known DataFrame (10 rows, 3 numeric + 2 categorical columns). Assert
  computed statistics match hand-calculated values.
- Unit test: `compare()` on identical profiles returns zero drift on all columns.
- Unit test: `compare()` on significantly different profiles correctly identifies the drifting
  columns.
- Unit test: profile a DataFrame with `None` values — assert null rates are correct.
- Unit test: all-null column handled gracefully (no division-by-zero).

### Files to Create/Modify
- [NEW] `src/synth_engine/modules/profiler/profiler.py`
- [NEW] `src/synth_engine/modules/profiler/models.py`
- [NEW] `tests/unit/test_profiler.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Profiler is standalone; no dependency on synthesizer or ingestion.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** All profile calculations verified against hand-calculated values.

---

## Task 4.2b: Synthesizer Core — SDV/CTGAN Integration
**Assignee:** [Dev C]
**Priority:** Blocked by T4.0 (library choice), T4.1 (ephemeral storage), T4.2a (profiler)
**Estimated Effort:** 3 days

### User Story / Agentic Goal
As a Data Scientist, I want the chosen synthesis library (per ADR-0016) integrated into the
Conclave engine, so that I can train a model on a source dataset and generate a statistically
comparable synthetic dataset with the correct schema and relational constraints.

### Context & Constraints
- The specific implementation of this task depends on ADR-0016. This spec uses CTGAN as the
  illustrative example; substitute HMA1 paths if ADR-0016 chooses that direction.
- **If CTGAN/TVAE**: train one model per table in topological order (parents before children).
  For each child table, include the parent FK column as a conditional feature so the
  synthesizer respects the FK distribution. FK values in synthetic output must reference
  actual rows in the synthetic parent table (enforced post-generation, not by the model).
- **If HMA1**: use SDV's built-in multi-table API. The topology from `modules/mapping/` maps
  directly to SDV's `MultiTableMetadata` format.
- Training is synchronous in this task. Async Huey task wiring is T4.2c.
- The synthesizer reads from Parquet files in ephemeral storage (written by T4.1), NOT from
  PostgreSQL directly.
- The synthesizer writes synthetic output as Parquet to ephemeral storage. Writing to the
  target PostgreSQL database is the responsibility of the subsetting/egress module (called
  by the bootstrapper), not the synthesizer.
- The module must not import from `modules/ingestion/`, `modules/masking/`, or
  `modules/subsetting/`. Cross-module data transfer uses Parquet files and `shared/` DTOs.

### Acceptance Criteria
- [ ] `src/synth_engine/modules/synthesizer/engine.py` provides `SynthesisEngine` class.
- [ ] `SynthesisEngine.train(table_name: str, parquet_path: str) -> ModelArtifact` trains
  the model on the data at `parquet_path` and returns a serializable `ModelArtifact`.
- [ ] `SynthesisEngine.generate(artifact: ModelArtifact, n_rows: int) -> pd.DataFrame`
  generates `n_rows` synthetic rows matching the trained schema.
- [ ] Generated output schema (column names, dtypes, nullable flags) exactly matches the
  source schema.
- [ ] `src/synth_engine/modules/synthesizer/models.py` defines `ModelArtifact` as a
  serializable dataclass with `save(path)` / `load(path)` methods (pickle or torch.save).
- [ ] FK integrity post-processing: for child tables, generated FK values are resampled from
  the set of actual synthetic parent PKs (no orphan FKs in output).

### Testing & Quality Gates
- Integration test: train a CTGAN model on a 100-row synthetic `persons` DataFrame (no real
  PII — use Faker-generated fixture data). Generate 50 rows. Assert output schema matches
  input schema exactly (column names, dtypes).
- Unit test: `ModelArtifact.save()` + `load()` round-trips without data loss.
- Unit test: FK post-processing step removes any generated FK values not present in parent
  PK set.
- Integration test: synthesizer does NOT import from `modules/ingestion/` — import-linter
  must pass.

### Files to Create/Modify
- [NEW] `src/synth_engine/modules/synthesizer/engine.py`
- [NEW] `src/synth_engine/modules/synthesizer/models.py`
- [NEW] `tests/unit/test_synthesizer_engine.py`
- [NEW] `tests/integration/test_synthesizer_integration.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** No cross-module imports; data flows via Parquet + shared DTOs.
2. **Schema Fidelity:** Generated output schema matches source exactly.
3. **Coverage Gate:** >= 90%.
4. **Pipeline Green:** CI passes.
5. **Peer Review:** Reviewed.

---

## Task 4.2c: Huey Task Wiring & Checkpointing
**Assignee:** [Dev D]
**Priority:** Blocked by T4.2b, T4.1
**Estimated Effort:** 2 days

### User Story / Agentic Goal
As a System Operator, I want synthesis training to run as an asynchronous background Huey
task with checkpointing every N epochs, so that OOM failures during multi-hour training runs
don't destroy all progress and the system can resume from the last good checkpoint.

### Context & Constraints
- The Huey task queue and bootstrapper wiring exist from Phase 2 (T2.1). This task connects
  synthesis training to that existing infrastructure.
- Checkpointing writes `ModelArtifact` snapshots to ephemeral storage every N epochs
  (configurable, default N=5). On OOM or task failure, the Orphan Task Reaper (T2.1) marks
  the task failed; the next run can resume from the last checkpoint if one exists.
- The Huey task must update a `SynthesisJob` record in the database with status
  (`QUEUED` → `TRAINING` → `COMPLETE` / `FAILED`) and current epoch number. This record
  is what the SSE endpoint (T5.1) will stream to the frontend.
- The task must not run training synchronously in the Huey worker thread if VRAM/RAM is
  insufficient — it must call the OOM guardrail (T4.3a) first, and if the guardrail fails,
  mark the job `FAILED` with a human-readable reason before the worker exits.

### Acceptance Criteria
- [ ] `src/synth_engine/modules/synthesizer/tasks.py` defines a `@huey.task` called
  `run_synthesis_job(job_id: int) -> None`.
- [ ] Task updates `SynthesisJob.status` in the database at QUEUED → TRAINING transition.
- [ ] Task calls OOM guardrail before starting training; sets status to FAILED with reason if
  guardrail rejects.
- [ ] Task checkpoints `ModelArtifact` to ephemeral storage every N epochs (N from job config).
- [ ] Task sets status to COMPLETE and records artifact path on successful completion.
- [ ] Task sets status to FAILED and records error message on any exception.
- [ ] `src/synth_engine/modules/synthesizer/job_models.py` defines `SynthesisJob` SQLModel
  with fields: `id`, `status`, `current_epoch`, `total_epochs`, `artifact_path`, `error_msg`.

### Testing & Quality Gates
- Unit test: mock `SynthesisEngine.train()`; assert Huey task transitions status
  QUEUED → TRAINING → COMPLETE on success.
- Unit test: mock OOM guardrail to reject; assert task sets FAILED status with guardrail
  error message; assert training never called.
- Unit test: mock training to raise `RuntimeError` after epoch 3; assert task sets FAILED;
  assert checkpoint for epoch 3 exists in mock storage.
- Integration test (pytest-postgresql): run task with real DB; assert final `SynthesisJob`
  record has `status == "COMPLETE"` and `artifact_path` is set.

### Files to Create/Modify
- [NEW] `src/synth_engine/modules/synthesizer/tasks.py`
- [NEW] `src/synth_engine/modules/synthesizer/job_models.py`
- [NEW] `tests/unit/test_synthesizer_tasks.py`
- [MODIFY] `tests/integration/test_synthesizer_integration.py`

### Definition of Done (DoD) Checklist
1. **Resilience:** OOM → FAILED (not crash); exception → FAILED with message.
2. **Checkpointing:** Epoch N checkpoint survives simulated mid-training failure.
3. **Coverage Gate:** >= 90%.
4. **Pipeline Green:** CI passes.
5. **Peer Review:** Reviewed.

---

## Task 4.3a: OOM Pre-flight Guardrails
**Assignee:** [Dev A]
**Priority:** Blocked by T4.0
**Estimated Effort:** 1 day
**Can run in parallel with T4.1 and T4.2a after T4.0 merges.**

### User Story / Agentic Goal
As a System Operator, I want a pre-flight memory guardrail that estimates the RAM/VRAM
required for a given training job before it starts, so that jobs too large for the available
hardware fail instantly with a clear message instead of OOM-killing the worker process.

### Context & Constraints
- The guardrail is a pure function of (rows, columns, dtype_sizes, algorithm_overhead_factor).
  It does not start training; it only estimates.
- Memory estimate formula:
  `estimated_bytes = rows × columns × avg_dtype_bytes × algorithm_overhead_factor`
  where `algorithm_overhead_factor` accounts for gradient buffers, optimizer state, etc.
  (typically 4-8× for GAN training). The exact factor is configurable per algorithm.
- Available RAM: use `psutil.virtual_memory().available`. Available VRAM: use
  `torch.cuda.memory_reserved()` or fall back to RAM estimate if no GPU.
- If `estimated_bytes > 0.85 × available_memory`: raise `OOMGuardrailError` with a message
  including the estimate, available memory, and required reduction factor.
- The guardrail lives in `modules/synthesizer/guardrails.py`. It has no dependency on the
  synthesis library (pure math + psutil).

### Acceptance Criteria
- [ ] `src/synth_engine/modules/synthesizer/guardrails.py` provides `OOMGuardrailError` and
  `check_memory_feasibility(rows, columns, dtype_bytes, overhead_factor) -> None`.
- [ ] At 80% of available memory: function returns without error.
- [ ] At 90% of available memory: `OOMGuardrailError` is raised.
- [ ] Error message includes: estimated bytes, available bytes, and the factor by which
  rows or columns must be reduced to fit.
- [ ] `psutil` and `torch` are both mockable — no real hardware required in tests.

### Testing & Quality Gates
- Unit test: mock `psutil.virtual_memory().available = 8GB`; input requiring 6.8GB (85%) →
  `OOMGuardrailError` raised.
- Unit test: input requiring 6.0GB (75%) → no error.
- Unit test: error message contains human-readable byte values (not raw integers).
- Unit test: simulate 1-billion-row dataset; guardrail rejects cleanly before any training.

### Files to Create/Modify
- [NEW] `src/synth_engine/modules/synthesizer/guardrails.py`
- [NEW] `tests/unit/test_synthesizer_guardrails.py`

### Definition of Done (DoD) Checklist
1. **No Hardware Required:** All tests pass on CI without GPU or large RAM.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.

---

## Task 4.3b: Differential Privacy Engine Wiring
**Assignee:** [Dev C]
**Priority:** Blocked by T4.0 (library choice), T4.2b (training loop must exist before DP can wrap it)
**Estimated Effort:** 3 days

### User Story / Agentic Goal
As a Privacy Engineer, I want the synthesis training loop strictly wrapped by Differential
Privacy mechanisms (per ADR-0016), so that we have mathematical guarantees that individual
records cannot be reverse-engineered from the synthetic output, with each training run
spending a calculable, bounded Epsilon budget.

### Context & Constraints
- **Implementation depends on ADR-0016.** This spec describes the CTGAN + Opacus path.
  If ADR-0016 chooses HMA1 + OpenDP query mechanisms, the file names are the same but
  the implementation differs. The ADR defines the approach; this task implements it.
- **CTGAN + Opacus path:** Wrap the CTGAN PyTorch optimizer with `opacus.PrivacyEngine`.
  `PrivacyEngine.make_private()` replaces the standard optimizer with a DP-SGD optimizer.
  The engine tracks Epsilon automatically via its RDP accountant.
- **Epsilon per epoch:** `PrivacyEngine.get_epsilon(delta)` returns cumulative Epsilon after
  each epoch. Training must stop if Epsilon exceeds the job's allocated budget.
- **Delta:** fixed at `1e-5` by convention (probability of privacy failure). Configurable.
- The DP engine does NOT track budget across runs — that is the Privacy Accountant (T4.4).
  This task only: wraps the optimizer, tracks per-run Epsilon, and stops training if the
  per-run budget is exceeded. The accountant (T4.4) enforces the global ledger.
- `src/synth_engine/modules/privacy/dp_engine.py` houses the DP wrapper. Import of
  `modules/synthesizer` from `modules/privacy` is FORBIDDEN by import-linter. The DP
  engine wraps a training callback — it does not import the synthesizer directly.

### Acceptance Criteria
- [ ] `src/synth_engine/modules/privacy/dp_engine.py` provides `DPTrainingWrapper`.
- [ ] `DPTrainingWrapper.wrap(optimizer, model, dataloader, max_grad_norm, noise_multiplier)`
  returns a DP-wrapped optimizer using `opacus.PrivacyEngine`.
- [ ] `DPTrainingWrapper.epsilon_spent(delta: float) -> float` returns cumulative Epsilon.
- [ ] `DPTrainingWrapper.check_budget(allocated_epsilon: float, delta: float) -> None`
  raises `BudgetExhaustionError` if `epsilon_spent >= allocated_epsilon`.
- [ ] `SynthesisEngine.train()` (T4.2b) accepts an optional `dp_wrapper: DPTrainingWrapper`
  parameter. If provided, wraps the optimizer before the training loop and calls
  `check_budget()` after each epoch.
- [ ] `BudgetExhaustionError` is defined in `modules/privacy/` and importable from there.
- [ ] `opacus` is added to `pyproject.toml` dependencies.

### Testing & Quality Gates
- Unit test: mock `PrivacyEngine`; assert `wrap()` is called with correct parameters.
- Unit test: `check_budget(allocated=1.0, delta=1e-5)` raises `BudgetExhaustionError` when
  `epsilon_spent() == 1.1`.
- Unit test: `check_budget(allocated=1.0)` does NOT raise when `epsilon_spent() == 0.8`.
- Integration test: train CTGAN for 2 epochs with a tiny budget (`max_epsilon=0.01`);
  assert `BudgetExhaustionError` is raised before training completes all epochs.
- Run `poetry run python -m importlinter` — `modules/privacy` must NOT import from
  `modules/synthesizer`.

### Files to Create/Modify
- [NEW] `src/synth_engine/modules/privacy/dp_engine.py`
- [MODIFY] `src/synth_engine/modules/synthesizer/engine.py` (add `dp_wrapper` param)
- [MODIFY] `pyproject.toml` (add `opacus` dependency)
- [MODIFY] `tests/unit/test_synthesizer_engine.py`
- [NEW] `tests/unit/test_dp_engine.py`
- [NEW] `tests/integration/test_dp_integration.py`

### Definition of Done (DoD) Checklist
1. **Mathematical Guarantees:** DP-SGD applied correctly; Epsilon tracked accurately.
2. **Budget Enforcement:** Training halts on budget exhaustion — no overrun possible.
3. **Import Boundary:** `modules/privacy` does not import from `modules/synthesizer`.
4. **Coverage Gate:** >= 90%.
5. **Pipeline Green:** CI passes.
6. **Peer Review:** Reviewed.

---

## Task 4.4: Privacy Accountant — Global Epsilon Ledger
**Assignee:** [Dev D]
**Priority:** Blocked by T4.3b (needs `BudgetExhaustionError` and Epsilon concept defined)
**Estimated Effort:** 3 days

### User Story / Agentic Goal
As a System Administrator, I want a global ledger that tracks cumulative Epsilon consumption
across all synthesis jobs on the platform, so that once the mathematical privacy limit is
reached, all further data generation is blocked to prevent privacy leakage.

### Context & Constraints
- The Privacy Accountant enforces the GLOBAL budget. T4.3b enforces PER-RUN budget.
  A single run might allocate Epsilon=1.0; the global budget might be Epsilon=10.0 across
  all runs. The accountant ensures total cumulative spend never exceeds the global limit.
- Pessimistic locking is mandatory. Race condition scenario: two jobs each request Epsilon=3.0
  when only 4.0 remains. Without locking, both checks pass and the budget is overdrawn by 2.0.
  `SELECT ... FOR UPDATE` on the `PrivacyLedger` row ensures only one transaction commits.
- The `PrivacyLedger` SQLModel belongs in `modules/privacy/`. There is NO `models/`
  top-level directory in this project — any file paths in the original backlog referencing
  `src/synth_engine/models/` are incorrect.
- `spend_budget()` must be async (called from FastAPI route handlers) and use SQLAlchemy
  async sessions with `FOR UPDATE`.
- The concurrency integration test MUST use a real PostgreSQL database (pytest-postgresql).
  `SELECT FOR UPDATE` is silently ignored by SQLite — a SQLite-based test would always pass
  but provides no correctness guarantee.

### Acceptance Criteria
- [ ] `src/synth_engine/modules/privacy/ledger.py` defines:
  - `PrivacyLedger` — SQLModel table with fields: `id`, `total_allocated_epsilon`,
    `total_spent_epsilon`, `last_updated`.
  - `PrivacyTransaction` — SQLModel table with fields: `id`, `ledger_id`, `job_id`,
    `epsilon_spent`, `timestamp`, `note`.
- [ ] `src/synth_engine/modules/privacy/accountant.py` provides
  `async def spend_budget(amount: float, job_id: int, session: AsyncSession) -> None`.
- [ ] `spend_budget()` acquires a `SELECT ... FOR UPDATE` lock on the ledger row before
  reading the current balance.
- [ ] If `total_spent + amount > total_allocated`: raises `BudgetExhaustionError` (imported
  from `modules/privacy/dp_engine.py`).
- [ ] If sufficient budget exists: deducts `amount`, writes a `PrivacyTransaction` record,
  and commits — all in the same transaction.
- [ ] An Alembic migration is provided for both new tables.

### Testing & Quality Gates
- Integration test (pytest-postgresql — REQUIRED, not unit test with mock):
  Create a ledger with total_allocated=5.0. Use `asyncio.gather` to fire 50 concurrent
  `spend_budget(0.2)` calls simultaneously. Assert:
  - Total spent is exactly 5.0 (25 successful calls × 0.2).
  - Exactly 25 calls raised `BudgetExhaustionError`.
  - `total_spent_epsilon` in the DB equals 5.0 (no overrun, no underrun).
- Unit test: `spend_budget()` with sufficient budget → transaction record created.
- Unit test: `spend_budget()` with insufficient budget → `BudgetExhaustionError` raised,
  no transaction written, ledger balance unchanged.

### Files to Create/Modify
- [NEW] `src/synth_engine/modules/privacy/ledger.py`
- [NEW] `src/synth_engine/modules/privacy/accountant.py`
- [NEW] `alembic/versions/<hash>_add_privacy_ledger_tables.py`
- [NEW] `tests/unit/test_privacy_accountant.py`
- [NEW] `tests/integration/test_privacy_accountant_integration.py`

### Definition of Done (DoD) Checklist
1. **Concurrency Safe:** 50-concurrent-request integration test passes; budget never overdrawn.
2. **Real PostgreSQL:** Integration test uses pytest-postgresql — not mocks, not SQLite.
3. **Alembic Migration:** Both tables created via migration, not `create_all()`.
4. **Coverage Gate:** >= 90%.
5. **Pipeline Green:** CI passes.
6. **Peer Review:** Reviewed.

---

## Phase 4 Exit Criteria

Before declaring Phase 4 complete and unblocking Phase 5:

| # | Criterion |
|---|-----------|
| 1 | ADR-0016 reviewed and approved (T4.0) |
| 2 | GPU passthrough and ephemeral storage operational (T4.1) |
| 3 | Statistical Profiler with verified calculations (T4.2a) |
| 4 | Synthesis engine generates schema-matching output (T4.2b) |
| 5 | Huey task wires training with checkpointing and OOM guard (T4.2c) |
| 6 | OOM guardrail rejects infeasible jobs before training starts (T4.3a) |
| 7 | DP-SGD applied; training halts on per-run budget exhaustion (T4.3b) |
| 8 | 50-concurrent Epsilon spend test passes with real PostgreSQL (T4.4) |
| 9 | All Phase 4 unit + integration tests pass in CI |
| 10 | import-linter: `modules/privacy` does not import from `modules/synthesizer` |
