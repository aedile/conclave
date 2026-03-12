# Phase 0.8: Technical Spikes (Fast-Fail Prototyping)

**Goal:** Mathematically and computationally prove the core "physics" of the synthetic data generation engine in isolated Python scripts *before* any production architecture or containerization is built. 

---

## Task 0.8.1: Spike A - ML Memory Physics & Open-Source Synthesizer Constraints
**Assignee:** [Dev B / ML Engineer]
**Priority:** Critical Path
**Estimated Effort:** 1-2 days

### User Story / Agentic Goal
As a Machine Learning Engineer, I need to prove that we can train a deep learning tabular model (like SDV/OpenDP) on a standard dataset while strictly constrained by Docker-like memory limits, so that we don't build a massive platform around an engine that will OOM kill on day one.

### Context & Constraints
*   Script must run in isolation.
*   Must simulate a severely constrained environment (e.g., limit Python process memory to 2GB).

### Acceptance Criteria
*   [ ] Write a Python script (`spike_ml_memory.py`) that loads a 500MB synthetic CSV file.
*   [ ] The script configures the target ML Synthesizer (e.g., SDV CTGAN or GaussianCopula).
*   [ ] The script successfully completes a training loop and generates 1000 synthetic records *without* exceeding the enforced memory limits.
*   [ ] If it OOMs, the script must implement chunking/batching to prove viability.

### Files to Create/Modify
*   [NEW] `spikes/spike_ml_memory.py`
*   [NEW] `spikes/README.md` (Record findings)

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Proves memory limits are survivable.
2. **Coverage Gate:** N/A (Throwaway code).
3. **Pipeline Green:** Script executes start-to-finish locally.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 0.8.2: Spike B - Deterministic Format-Preserving Encryption
**Assignee:** [Dev C / Security Engineer]
**Priority:** Critical Path
**Estimated Effort:** 1-2 days

### User Story / Agentic Goal
As a Cryptography Engineer, I need to prove we can perfectly encrypt sensitive financial data while preserving its format and algorithmic validity (LUHN), so that the masking engine in Phase 3 is computationally possible.

### Context & Constraints
*   Must be 100% deterministic (same input + same salt = same output).
*   Standard AES CBC/GCM is not acceptable as it changes string length and byte format.

### Acceptance Criteria
*   [ ] Write a Python script (`spike_fpe_luhn.py`).
*   [ ] The script takes 10,000 valid, randomly generated Credit Card numbers.
*   [ ] The script deterministically encrypts/masks them.
*   [ ] The script verifies 0 collisions (no two different source CCs result in the exact same masked CC).
*   [ ] The script runs a LUHN algorithmic check on all 10,000 masked outputs, and 100% must pass.

### Files to Create/Modify
*   [NEW] `spikes/spike_fpe_luhn.py`
*   [MODIFY] `spikes/README.md` (Record findings)

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Proves mathematical viability of Phase 3.
2. **Coverage Gate:** N/A (Throwaway code).
3. **Pipeline Green:** Script executes start-to-finish locally.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 0.8.3: Spike C - Topological Graphing & Memory-Safe Transversal
**Assignee:** [Dev D / Data Engineer]
**Priority:** Critical Path
**Estimated Effort:** 1-2 days

### User Story / Agentic Goal
As a Data Architect, I need to prove we can trace relational foreign keys and dynamically stream a 5% subset of data from a massive database without locking the tables or loading the entire graph into RAM.

### Context & Constraints
*   Must use Python `asyncpg` or `SQLAlchemy` streaming.

### Acceptance Criteria
*   [ ] Write a Python script (`spike_topological_subset.py`).
*   [ ] Programmatically infer the schema and foreign keys of a sample database (e.g., a local SQLite or Postgres instance of the Sakila DB/Pagila DB).
*   [ ] Dynamically generate the recursive SQL required to grab a specific user, their orders, and their order items.
*   [ ] Prove the memory consumption of the Python process remains flat (streaming) rather than spiking.

### Files to Create/Modify
*   [NEW] `spikes/spike_topological_subset.py`
*   [MODIFY] `spikes/README.md` (Record findings)

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Proves relational math viability of Phase 3.
2. **Coverage Gate:** N/A (Throwaway code).
3. **Pipeline Green:** Script executes start-to-finish locally.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.
