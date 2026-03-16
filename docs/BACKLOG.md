# Conclave Engine Master Backlog

This document serves as the top-level index for all project phases and their constituent tasks. The granular execution details (User Stories, Acceptance Criteria, Definition of Done, and specific developer mandates) for each task are maintained within the dedicated Phase Decomposition files located in `docs/backlog/`.

## Phase Hierarchy

*   [Phase 0.5: Agentic Backlog Decomp & Epic Sizing](backlog/phase-0.5.md)
*   [Phase 0.6: Autonomous Agile Environment Provisioning](backlog/phase-0.6.md)
*   [Phase 0.8: Technical Spikes (Fast-Fail Prototyping)](backlog/phase-0.8.md)
*   [Phase 1: Project Initialization & Quality Gates](backlog/phase-1.md)
*   [Phase 2: Foundational Architecture & Shared Services](backlog/phase-2.md)
*   [Phase 3: The "Thin Slice" (Rapid ROI)](backlog/phase-3.md)
*   [Phase 3.5: Technical Debt Sprint](backlog/phase-3.5.md)
*   [Phase 4: Advanced Generative AI & Differential Privacy](backlog/phase-4.md)
*   [Phase 5: Orchestration, UI, & Licensing](backlog/phase-5.md)
*   [Phase 6: Integration, Audit & Finalization](backlog/phase-6.md)
*   [Phase 7: Differential Privacy Integration](backlog/phase-7.md)
*   [Phase 8: Advisory Drain Sprint](backlog/phase-8.md)
*   [Phase 9: Production Hardening & Correctness Sprint](backlog/phase-9.md)
*   [Phase 10: Test Infrastructure Repair & Final Polish](backlog/phase-10.md)
*   [Phase 11: Documentation Currency & Workspace Hygiene](backlog/phase-11.md)
*   [Phase 12: Final Hygiene & Tooling Polish](backlog/phase-12.md)
*   [Phase 13: Pre-commit Repair & README Finalization](backlog/phase-13.md)
*   [Phase 14: Integration Test Repair & Frontend Lint Fix](backlog/phase-14.md)
*   [Phase 15: Frontend Coverage Gate & Operational Polish](backlog/phase-15.md)
*   [Phase 16: Migration Drift, Supply Chain & Accessibility Polish](backlog/phase-16.md)
*   [Phase 17: Docker Pinning, Dashboard WCAG & Process Cleanup](backlog/phase-17.md)

---

## Task Index

### [Phase 0.5: Agentic Backlog Decomp & Epic Sizing](backlog/phase-0.5.md)
*   **Task 0.5.1 [PM / Architect]:** Decompose tasks into User Stories with DoD.
*   **Task 0.5.2 [PM / Architect]:** Final Dependency & Constitution Check.

### [Phase 0.6: Autonomous Agile Environment Provisioning](backlog/phase-0.6.md)
*   **Task 0.6.1 [PM / System Admin]:** Host Initialization & MCP Setup.
*   **Task 0.6.2 [PM]:** Memory Seeding (Governance).
*   **Task 0.6.3 [PM / System Admin]:** Team Scaffolding & Git Worktree Hooks.
*   **Task 0.6.4 [PM]:** Task Queue Initialization & JSON Migration.

### [Phase 0.8: Technical Spikes (Fast-Fail Prototyping)](backlog/phase-0.8.md)
*   **Task 0.8.1 [Dev B]:** Spike A - ML Memory Physics & Open-Source Synthesizer Constraints.
*   **Task 0.8.2 [Dev C]:** Spike B - Deterministic Format-Preserving Encryption.
*   **Task 0.8.3 [Dev D]:** Spike C - Topological Graphing & Memory-Safe Transversal.

### [Phase 1: Project Initialization & Quality Gates](backlog/phase-1.md)
*   **Task 1.1 [Dev A]:** Configure CI/CD Pipeline & Scanners (`gitleaks`, `bandit`, `ruff`, `mypy`, `trivy`, `pip-audit`, SBOM, `import-linter`).
*   **Task 1.2 [Dev B]:** Setup TDD Framework (`pytest`, `pytest-cov`, `pytest-postgresql`) with 90% coverage gates.
*   **Task 1.3 [Dev A]:** Construct Base Docker Image (Node.js/Python final stages, `tini` / `su-exec`).
*   **Task 1.4 [Dev B]:** Configure Container Security & Storage Policies (LUKS volumes, `IPC_LOCK`).
*   **Task 1.5 [Dev C]:** Establish Local Developer Experience (base & override `docker-compose.yml`, local MinIO, Jaeger).
*   **Task 1.6 [Dev D]:** Docker Hardening (`--read-only` rootfs, Redis config, log-rotation, tmpfs Secrets).
*   **Task 1.7 [Dev A]:** Air-Gap Artifact Bundler (`make build-airgap-bundle` script).

### [Phase 2: Foundational Architecture & Shared Services](backlog/phase-2.md)
*   **Task 2.1 [Dev A]:** Implement Module Bootstrapper (FastAPI), OTEL injection, Idempotency, and "Orphan Task Reaper".
*   **Task 2.2 [Dev B]:** Configure PostgreSQL config, PgBouncer, SQLModel base ORM, and Application-Level Encryption (ALE).
*   **Task 2.3 [Dev C]:** Implement JWT Auth middleware, granular scopes, and IP/mTLS Certificate binding.
*   **Task 2.4 [Dev D]:** Develop "Vault Unseal" API, internal mTLS CA, WORM Audit Logger, `/metrics`, Alertmanager, and as-code Grafana.

### [Phase 3: The "Thin Slice" (Rapid ROI - Ingest, Subset, Egress)](backlog/phase-3.md)
*   **Task 3.1 [Dev A]:** Build the Ingestion Engine (DB connections, I/O protocols).
*   **Task 3.2 [Dev B]:** Implement Relational Mapping (Schema inference, foreign key mapping via topological sort).
*   **Task 3.3 [Dev C]:** Build Deterministic Masking Engine (Format-preserving algorithms, collision prevention, LUHN).
*   **Task 3.4 [Dev D]:** Build Subsetting & Materialization Core (Relational transversal, Saga rollbacks, secure egress).
*   **Task 3.5 [Dev A]:** Execute E2E Integration Tests for Subsetting workflow (`@axe-core/playwright`, `pytest-postgresql`).

### [Phase 3.5: Technical Debt Sprint](backlog/phase-3.5.md)
*   **Task 3.5.1 [Dev A]:** Supply Chain & CI Hardening (SHA-pin Actions, Trivy, pg_ctl).
*   **Task 3.5.2 [Dev B]:** Module Cohesion Refactor (extract mapping/ and subsetting/).
*   **Task 3.5.3 [Dev C]:** SchemaTopology Immutability & Virtual FK Support.
*   **Task 3.5.4 [Dev D]:** Bootstrapper Wiring & Minimal CLI Entrypoint (conclave-subset).
*   **Task 3.5.5 [Dev A]:** Advisory Sweep (remaining open items).

### [Phase 4: Advanced Generative AI & Differential Privacy](backlog/phase-4.md)
*   **Task 4.1 [Dev A]:** Integrate GPU Passthrough and Ephemeral Object/Blob storage.
*   **Task 4.2 [Dev B]:** Integrate OSS Synthesizer (e.g., SDV) with batching/checkpointing.
*   **Task 4.3 [Dev C]:** Integrate OSS Differential Privacy (OpenDP/SmartNoise) and OOM Guardrails.
*   **Task 4.4 [Dev D]:** Build Privacy Accountant Logic (Epsilon ledger limits, pessimistic locking).

### [Phase 5: Orchestration, UI, & Licensing](backlog/phase-5.md)
*   **Task 5.1 [Dev A]:** Build Task Orchestration API (FastAPI endpoints, Webhooks, Pagination, RFC 7807, SSE, schema sync).
*   **Task 5.2 [Dev B]:** Implement Offline License Activation Protocol (QR Challenge, JWT offline validation).
*   **Task 5.3 [Dev C]:** Build Accessible React SPA (WCAG 2.1 AA, `Content-Security-Policy`, local fonts, Vault Unseal router).
*   **Task 5.4 [Dev D]:** Develop Data Synthesis Dashboard (Determinate progress UX, SSE integration, `localStorage` state rehydration).
*   **Task 5.5 [Dev A]:** Implement Cryptographic Shredding & Re-Keying API (Rotate ALE keys, zeroize constraints).

### [Phase 6: Integration, Audit & Finalization](backlog/phase-6.md)
*   **Task 6.1 [Dev B]:** Execute E2E Integration Tests for Generative Synthesis (Dummy ML assertions).
*   **Task 6.2 [Dev C]:** Validate NIST SP 800-88 Cryptographic Erasure triggers, OWASP validation, and LLM Fuzz Testing.
*   **Task 6.3 [Dev D]:** Final Security Remediation, Documentation generation, and Platform Handover validation.

### [Phase 7: Differential Privacy Integration](backlog/phase-7.md)
*   **Task 7.1:** ADR-0025: Custom CTGAN Training Loop Architecture.
*   **Task 7.2:** Custom CTGAN Training Loop.
*   **Task 7.3:** Opacus End-to-End Wiring.
*   **Task 7.4:** ProfileDelta Validation & Quality Benchmarks.
*   **Task 7.5:** Phase 7 E2E Test & Retrospective.

### [Phase 8: Advisory Drain Sprint](backlog/phase-8.md)
*   **Task 8.1:** Integration Test Gaps (ADV-021, ADV-064).
*   **Task 8.2:** Security Hardening (ADV-040, ADV-057, ADV-058, ADV-067).
*   **Task 8.3:** Data Model & Architecture Cleanup (ADV-050, ADV-054, ADV-071).
*   **Task 8.4:** CI Infrastructure (ADV-052, ADV-062, ADV-065, ADV-066, ADV-069).
*   **Task 8.5:** Documentation & Operator Gaps (ADV-070, ADV-072).

### [Phase 9: Production Hardening & Correctness Sprint](backlog/phase-9.md)
*   **Task 9.1:** Advisory Drain + Startup Validation (ADV-073–077).
*   **Task 9.2:** Operator Manual Refresh.
*   **Task 9.3:** Bootstrapper Decomposition.

### [Phase 10: Test Infrastructure Repair & Final Polish](backlog/phase-10.md)
*   **Task 10.1:** Fix pytest-asyncio Python 3.14.1 Compatibility.
*   **Task 10.2:** Drain Stale TODO and Update README Status.

### [Phase 11: Documentation Currency & Workspace Hygiene](backlog/phase-11.md)
*   **Task 11.1:** Documentation Currency (README, BACKLOG.md).
*   **Task 11.2:** Workspace Hygiene (Worktrees, Spikes, .gitignore).
*   **Task 11.3:** Architectural Requirements Gap ADR.

### [Phase 12: Final Hygiene & Tooling Polish](backlog/phase-12.md)
*   **Task 12.1:** Stale Remote Branch Cleanup & README Final Status.
*   **Task 12.2:** Vulture Whitelist for FastAPI False Positives.

### [Phase 13: Pre-commit Repair & README Finalization](backlog/phase-13.md)
*   **Task 13.1:** Fix Vulture Whitelist Ruff Compliance & README Final Status.

### [Phase 14: Integration Test Repair & Frontend Lint Fix](backlog/phase-14.md)
*   **Task 14.1:** Fix Integration Test Failures (DP, Privacy Accountant, SSE).
*   **Task 14.2:** Frontend ESLint 9.x Configuration & Nosec Justifications.
*   **Task 14.3:** README Phase 13 Completion & Phase 14 Status.

### [Phase 15: Frontend Coverage Gate & Operational Polish](backlog/phase-15.md)
*   **Task 15.1:** Frontend Test Coverage Gate Repair.
*   **Task 15.2:** README Phase 14 Completion & Operational Cleanup.

### [Phase 16: Migration Drift, Supply Chain & Accessibility Polish](backlog/phase-16.md)
*   **Task 16.1:** Alembic Migration 003: Epsilon Column Precision Fix.
*   **Task 16.2:** Frontend Supply Chain & Nosec Accuracy.
*   **Task 16.3:** WCAG Skip Navigation, README Update & Branch Cleanup.

### [Phase 17: Docker Pinning, Dashboard WCAG & Process Cleanup](backlog/phase-17.md)
*   **Task 17.1:** Docker Base Image SHA Pinning.
*   **Task 17.2:** Dashboard WCAG 2.1 AA Improvements.
*   **Task 17.3:** CLAUDE.md Stale References, Backlog Spec Fix & Branch Cleanup.
