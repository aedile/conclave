# Phase 3: The "Thin Slice" (Rapid ROI - Ingest, Subset, Egress)

**Goal:** Deliver immediate business value to QA Engineers by establishing the end-to-end pipeline for deterministic masking and subsetting, deferring complex AI.

---

## Task 3.1: Build the Target Ingestion Engine
**Assignee:** [Dev A]
**Priority:** Blocked by 2.1
**Estimated Effort:** 5 days

### User Story / Agentic Goal
As a Data Engineer, I want the system to safely connect to external PostgreSQL source databases in a read-only manner, so that it can stream production data into memory/local-storage without ever risking mutation of live production data.

### Context & Constraints
*   Ingestion must strictly stream data; it cannot load massive tables entirely into memory (must survive under restricted Docker memory constraints).
*   Connections must be tightly validated.

### Acceptance Criteria
*   [ ] Create a PostgreSQL ingestion adapter utilizing `asyncpg` or `SQLAlchemy` streaming yield patterns.
*   [ ] Implement connection string validation (reject connections that do not specify `sslmode=require` if not local).
*   [ ] Implement a "Pre-flight Check" that executes a `SELECT 1` and verifies the provided user ONLY has `CONNECT` and `SELECT` privileges on the target schema.
*   [ ] Fail immediately if the connection allows `INSERT`, `UPDATE`, or `DELETE`.

### Testing & Quality Gates
*   Integration test: Connect to a local dummy database as a superuser. The pre-flight check MUST fail and raise a `PrivilegeEscalationError`.
*   Connect as a read-only user. The pre-flight check MUST pass.

### Files to Create/Modify
*   [NEW] `src/synth_engine/ingestion/postgres_adapter.py`
*   [NEW] `src/synth_engine/ingestion/validators.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Adheres to read-only strictness.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 3.2: Implement Relational Mapping & Topological Sort
**Assignee:** [Dev B]
**Priority:** Blocked by 2.1
**Estimated Effort:** 5 days

### User Story / Agentic Goal
As a Data Architect, I want the system to automatically infer the database schema and build a topological graph of foreign keys, so that subsetting operations know exactly which order to process tables (parents before children).

### Context & Constraints
*   Must handle explicit Foreign Keys (defined in DB) and Virtual Foreign Keys (user-defined mappings where no physical FK exists).

### Acceptance Criteria
*   [ ] Build a schema reflection module that extracts tables, columns, data types, and explicit foreign keys from the source DB.
*   [ ] Build a `DirectedAcyclicGraph` (DAG) representation of the schema.
*   [ ] Implement a topological sort algorithm (e.g., Kahn's Algorithm) to determine the absolute table processing order.
*   [ ] Detect cycle dependencies (self-referential tables or circular loops) and raise a specific `CycleDetectionError` requiring explicit user cycle-breaking rules.

### Testing & Quality Gates
*   Unit test graph sorting with a complex 5-table hierarchy including an intentionally injected circular dependency. Assert it catches the cycle.
*   Unit test successful topological sorting on a clean hierarchy.

### Files to Create/Modify
*   [NEW] `src/synth_engine/mapping/reflection.py`
*   [NEW] `src/synth_engine/mapping/graph.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Efficient graph processing.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 3.3: Build Deterministic Masking Engine
**Assignee:** [Dev C]
**Priority:** Blocked by 2.1
**Estimated Effort:** 5 days

### User Story / Agentic Goal
As a Compliance Officer, I need deterministic, format-preserving encryption/masking for PII fields, so that masked data looks realistic to applications and retains referential integrity, but cannot be reversed to plaintext.

### Context & Constraints
*   Given the same salt and input "John", the output must always be the exact same fake name (e.g., "Alex").
*   Must pass basic format validation (e.g., LUHN checks for credit cards, valid email formats).

### Acceptance Criteria
*   [ ] Implement a Deterministic Masking Registry (mapping column types to algorithms).
*   [ ] Implement Format-Preserving Encryption (FPE) or deterministic hashing + Faker seeding for: Names, Emails, SSNs, Credit Cards, Phone Numbers.
*   [ ] Implement a "Collision Prevention" mechanism (ensure two different real names don't deterministically mask to the identical fake name within the same table).
*   [ ] Ensure algorithms check external length constraints (e.g., don't generate a 50-character email if `VARCHAR(30)`).

### Testing & Quality Gates
*   Generate 100,000 masked records. Assert 0 collisions.
*   Generate a Credit Card and run a LUHN algorithm verifier on the output. Assert True.

### Files to Create/Modify
*   [NEW] `src/synth_engine/masking/deterministic.py`
*   [NEW] `src/synth_engine/masking/algorithms.py`
*   [NEW] `src/synth_engine/masking/registry.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Format preserving mathematics validated.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 3.4: Build Subsetting & Materialization Core
**Assignee:** [Dev D]
**Priority:** Blocked by 2.1
**Estimated Effort:** 5 days

### User Story / Agentic Goal
As a QA Engineer, I want the system to extract a surgically precise 5% subset of production data, automatically walking the relational graph to pick up all required parent/child records, and egress it to a target database, so that I have a lightweight, valid test environment.

### Context & Constraints
*   Must utilize the Topological Graph from Task 3.2.
*   Must utilize the Masking Engine from Task 3.3 on the fly.
*   Must implement the Saga pattern for rollbacks (if the egress database insertion fails halfway, wipe the target database clean).

### Acceptance Criteria
*   [ ] Implement the Subsetting Core: take a starting target (e.g., `SELECT * FROM users LIMIT 1000`) and use the DAG to recursively query all dependent records (e.g., the specific `orders` for those `users`, and the specific `products` for those `orders`).
*   [ ] Stream the subsetted, masked records directly into the Sink (Target) PostgreSQL database.
*   [ ] Implement a robust failure recovery mechanism that executes a complete truncate/wipe of the Target database if the process fails or OOMs.

### Testing & Quality Gates
*   Full integration test: Seed a source DB locally. Run a 10% subset targeted at a specific table. Assert the target DB contains precisely the expected subset and that all Foreign Key constraints in the target DB remain valid (no orphaned records).

### Files to Create/Modify
*   [NEW] `src/synth_engine/subsetting/core.py`
*   [NEW] `src/synth_engine/subsetting/transversal.py`
*   [NEW] `src/synth_engine/subsetting/egress.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Streaming and Saga implementations are robust.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 3.5: Execute E2E Subsetting Subsystem Tests
**Assignee:** [Dev A]
**Priority:** Blocked by 3.4
**Estimated Effort:** 3 days

### User Story / Agentic Goal
As a Release Manager, I want full end-to-end integration tests confirming the "Thin Slice" works from an API perspective, so that we can declare Phase 3 complete and prove Rapid ROI.

### Context & Constraints
*   Tests must be entirely automated.
*   Must utilize `pytest-postgresql` for pure isolation.

### Acceptance Criteria
*   [ ] Write a comprehensive `pytest` integration suite that spins up an ephemeral Source DB and Target DB.
*   [ ] The test invokes the Subsetting API endpoint (or CLI entrypoint) representing a complete user job.
*   [ ] The test asserts the final state of the Target DB matches the mathematical expectations of the subsetting logic + masking logic.
*   [ ] If any basic UI components were developed alongside this, utilize Playwright with `@axe-core/playwright` to run accessibility scans on the workflow.

### Testing & Quality Gates
*   These ARE the quality gates. The tests must pass consistently in CI without flake.

### Files to Create/Modify
*   [NEW] `tests/integration/test_e2e_subsetting.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Clean, isolated integration testing.
2. **Coverage Gate:** N/A (this is the test itself).
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.
