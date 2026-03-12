# Phase 4: Advanced Generative AI & Differential Privacy

**Goal:** Implement complex, GPU-accelerated data generation workflows utilizing established open-source ML libraries (OpenDP, SDV) to avoid bespoke R&D TCO.

---

## Task 4.1: GPU Passthrough & Ephemeral Storage Provisioning
**Assignee:** [Dev A]
**Priority:** Blocked by 3.1
**Estimated Effort:** 3 days

### User Story / Agentic Goal
As a Machine Learning Engineer, I need access to underlying GPU hardware and a high-throughput, ephemeral blob storage mechanism, so that PyTorch-based synthesis models can train on massive datasets without crushing the main PostgreSQL database or running out of memory.

### Context & Constraints
*   Docker compose must enable the NVIDIA Container Toolkit.
*   Blob storage must be completely destroyed when the container stops (ephemeral tmpfs) to align with privacy mandates.

### Acceptance Criteria
*   [ ] Update `docker-compose.yml` to support `--gpus all` using the `nvidia-container-toolkit` specification.
*   [ ] Provision a lightweight Object Storage container (e.g., `MinIO` or a custom tmpfs-backed chunker) specifically for holding temporary Parquet files during model training.
*   [ ] Create connection utilities in the core application to stream data out of PostgreSQL (from Phase 3) into this ephemeral blob storage.

### Testing & Quality Gates
*   Write a unit test that verifies the Python process has access to `torch.cuda.is_available()` (if hardware permits) or gracefully falls back to CPU via a hardcoded configuration flag.
*   Verify the Object Storage container directory is mounted as `tmpfs`, ensuring data evaporates on termination.

### Files to Create/Modify
*   [MODIFY] `docker-compose.yml`
*   [NEW] `src/synth_engine/storage/ephemeral.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Ephemeral storage strictly enforced.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes (mocking CUDA if necessary).
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 4.2: Open-Source Synthesizer Integration
**Assignee:** [Dev B]
**Priority:** Blocked by 3.2
**Estimated Effort:** 7 days

### User Story / Agentic Goal
As a Data Scientist, I want a robust wrapping layer around established ML synthesis libraries (like Synthetic Data Vault/SDV), so that we can train deep tabular models on complex relational schemas without writing bespoke ML architectures.

### Context & Constraints
*   Must utilize the Topological Schema defined in Task 3.2.
*   Model training can take hours. Must support async batching and checkpointing (via Huey tasks from Phase 2).

### Acceptance Criteria
*   [ ] Integrate the chosen synthesis engine (e.g., SDV HMA1 or CTGAN algorithms).
*   [ ] Implement a Statistical Profiler that calculates baseline marginal distributions (histograms, covariance) of the *source* data before training.
*   [ ] Implement the training loop as an asynchronous `Huey` task.
*   [ ] Implement checkpointing to the ephemeral storage (saving `.pkl` or `.pt` model states) every N epochs so OOM failures don't ruin 4 hours of training.

### Testing & Quality Gates
*   Integration test: Execute a fast, 1-epoch training loop on the dummy SQLite database. Assert a synthetic dataset is produced that perfectly matches the schema of the source dataset.

### Files to Create/Modify
*   [NEW] `src/synth_engine/ml/synthesizer.py`
*   [NEW] `src/synth_engine/ml/profiler.py`
*   [NEW] `src/synth_engine/ml/tasks.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Checkpointing prevents state loss.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 4.3: Differential Privacy (DP) Engine & Guardrails
**Assignee:** [Dev C]
**Priority:** Blocked by 3.3
**Estimated Effort:** 7 days

### User Story / Agentic Goal
As a Privacy Engineer, I want the synthesis engine strictly wrapped by Differential Privacy mathematics, so that we have mathematical guarantees that individual outlier records (e.g., the CEO's salary) cannot be reverse-engineered from the synthetic output.

### Context & Constraints
*   Leverage `OpenDP` or `SmartNoise`.
*   Memory limits are rigid. Must implement pre-flight memory guardrails before initiating massive matrix multiplications.

### Acceptance Criteria
*   [ ] Integrate the DP library to inject calibrated noise during the synthesis training phase (e.g., utilizing the DP-SGD algorithm).
*   [ ] Implement logic to calculate and explicitly enforce the `Epsilon` ($\epsilon$) and `Delta` ($\delta$) privacy parameters for a given generation run.
*   [ ] Implement a "Pre-flight OOM Guardrail" that estimates the memory required for the training matrix (rows * columns * float64 bytes * algorithm overhead factor). If the estimate exceeds 85% of available RAM/VRAM, the job must fail instantly before starting.

### Testing & Quality Gates
*   Write unit tests asserting that executing a training run successfully spends the allocated Epsilon.
*   Write a unit test simulating a 1-billion row dataset; the OOM Guardrail MUST reject the job cleanly.

### Files to Create/Modify
*   [NEW] `src/synth_engine/ml/differential_privacy.py`
*   [NEW] `src/synth_engine/ml/guardrails.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Memory guardrails protect system stability.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 4.4: Privacy Accountant Logic
**Assignee:** [Dev D]
**Priority:** Blocked by 3.4
**Estimated Effort:** 5 days

### User Story / Agentic Goal
As a System Admin, I want a global ledger that tracks the consumption of the "Privacy Budget" (Epsilon) across the entire platform, so that if the mathematical limit of safety is reached, all further data access is blocked to prevent privacy leakage.

### Context & Constraints
*   Must utilize robust SQL `SELECT ... FOR UPDATE` (pessimistic locking) to prevent race conditions when two concurrent jobs try to spend the last of the budget.

### Acceptance Criteria
*   [ ] Create a `PrivacyLedger` table in the database tracking total Epsilon allocated, total spent, and transactions.
*   [ ] Implement a `spend_budget(amount, connection_id)` service.
*   [ ] The service MUST wrap the spend validation in a strictly isolated, pessimistically locked SQL transaction.
*   [ ] If the spend exceeds the remaining budget, raise a specific `BudgetExhaustionError`.

### Testing & Quality Gates
*   Write an integration test utilizing Python's `asyncio.gather` or `threading` to fire 50 concurrent budget requests at the exact same millisecond. The pessimistic lock MUST ensure the available budget is never over-drawn (no negative balances).

### Files to Create/Modify
*   [NEW] `src/synth_engine/privacy/accountant.py`
*   [NEW] `src/synth_engine/models/ledger.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Pessimistic locking guarantees thread safety.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.
