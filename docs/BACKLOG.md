# Conclave Engine Master Backlog

This document serves as the top-level index for all project phases and their constituent tasks. The granular execution details (User Stories, Acceptance Criteria, Definition of Done, and specific developer mandates) for each task are maintained within the dedicated Phase Decomposition files located in `docs/backlog/`.

## Phase Hierarchy

*   [Phase 0.5: Agentic Backlog Decomp & Epic Sizing](backlog/phase-0.5.md)
*   [Phase 0.6: Autonomous Agile Environment Provisioning](backlog/phase-0.6.md)
*   [Phase 0.8: Technical Spikes (Fast-Fail Prototyping)](backlog/phase-0.8.md)
*   [Phase 1: Project Initialization & Quality Gates](backlog/phase-01.md)
*   [Phase 2: Foundational Architecture & Shared Services](backlog/phase-02.md)
*   [Phase 3: The "Thin Slice" (Rapid ROI)](backlog/phase-03.md)
*   [Phase 3.5: Technical Debt Sprint](backlog/phase-03.5.md)
*   [Phase 4: Advanced Generative AI & Differential Privacy](backlog/phase-04.md)
*   [Phase 5: Orchestration, UI, & Licensing](backlog/phase-05.md)
*   [Phase 6: Integration, Audit & Finalization](backlog/phase-06.md)
*   [Phase 7: Differential Privacy Integration](backlog/phase-07.md)
*   [Phase 8: Advisory Drain Sprint](backlog/phase-08.md)
*   [Phase 9: Production Hardening & Correctness Sprint](backlog/phase-09.md)
*   [Phase 10: Test Infrastructure Repair & Final Polish](backlog/phase-10.md)
*   [Phase 11: Documentation Currency & Workspace Hygiene](backlog/phase-11.md)
*   [Phase 12: Final Hygiene & Tooling Polish](backlog/phase-12.md)
*   [Phase 13: Pre-commit Repair & README Finalization](backlog/phase-13.md)
*   [Phase 14: Integration Test Repair & Frontend Lint Fix](backlog/phase-14.md)
*   [Phase 15: Frontend Coverage Gate & Operational Polish](backlog/phase-15.md)
*   [Phase 16: Migration Drift, Supply Chain & Accessibility Polish](backlog/phase-16.md)
*   [Phase 17: Docker Pinning, Dashboard WCAG & Process Cleanup](backlog/phase-17.md)
*   [Phase 18: Type Safety, Dependency Audit & E2E Validation](backlog/phase-18.md)
*   [Phase 19: Production Hardening & Integration Integrity](backlog/phase-19.md)
*   [Phase 20: Human-in-the-Loop Feedback](backlog/phase-20.md)
*   [Phase 21: CLI Masking Fix & E2E Smoke Tests](backlog/phase-21.md) ✅
*   [Phase 22: DP Pipeline End-to-End Integration](backlog/phase-22.md) ✅
*   [Phase 23: Job Lifecycle Completion](backlog/phase-23.md) ✅
*   [Phase 24: Integration Test Repair](backlog/phase-24.md) ✅
*   [Phase 25: Observability](backlog/phase-25.md) ✅
*   [Phase 26: Backend Production Hardening](backlog/phase-26.md) ✅
*   [Phase 27: Frontend Production Hardening](backlog/phase-27.md) ✅
*   [Phase 28: Full E2E Validation](backlog/phase-28.md) ✅
*   [Phase 29: Documentation Integrity & Review Debt](backlog/phase-29.md) ✅
*   [Phase 30: True Discriminator-Level DP-SGD](backlog/phase-30.md) ✅
*   [Phase 31: Code Health & Bus Factor Elimination](backlog/phase-31.md) ✅
*   [Phase 32: Dead Module Cleanup & Development Process Documentation](backlog/phase-32.md) ✅
*   [Phase 33: Governance Hygiene, Documentation Currency & Codebase Polish](backlog/phase-33.md) ✅
*   [Phase 34: Exception Hierarchy Unification & Operator Error Coverage](backlog/phase-34.md) ✅
*   [Phase 35: Synthesis Layer Refactor & Test Replacement](backlog/phase-35.md) ✅
*   [Phase 36: Configuration Centralization, Documentation Pruning & Hygiene](backlog/phase-36.md) ✅
*   [Phase 37: Advisory Drain, CHANGELOG Currency & E2E Demo Capstone](backlog/phase-37.md) ✅
*   [Phase 38: Audit Integrity, Timing Side-Channel Fix & Pre-Commit Hardening](backlog/phase-38.md) ✅
*   [Phase 39: Authentication, Authorization & Rate Limiting](backlog/phase-39.md) ✅
*   [Phase 40: Test Suite Quality Overhaul](backlog/phase-40.md) ✅
*   [Phase 41: Data Compliance, Retention Policy & GDPR/CCPA Readiness](backlog/phase-41.md)
*   [Phase 42: Security Hardening, Key Rotation & Deployment Safety](backlog/phase-42.md)
*   [Phase 43: Architectural Polish, Code Hygiene & Rule Sunset Evaluation](backlog/phase-43.md)
*   [Phase 44: Comprehensive Documentation Audit & Cleanup](backlog/phase-44.md)
*   [Phase 45: Webhook Callbacks, Idempotency Middleware & Orphan Task Reaper](backlog/phase-45.md)
*   [Phase 46: mTLS Inter-Container Communication](backlog/phase-46.md)
*   [Phase 47: Auth Gap Remediation, Safety Hardening & Operational Fixes](backlog/phase-47.md)

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

### [Phase 1: Project Initialization & Quality Gates](backlog/phase-01.md)
*   **Task 1.1 [Dev A]:** Configure CI/CD Pipeline & Scanners (`gitleaks`, `bandit`, `ruff`, `mypy`, `trivy`, `pip-audit`, SBOM, `import-linter`).
*   **Task 1.2 [Dev B]:** Setup TDD Framework (`pytest`, `pytest-cov`, `pytest-postgresql`) with 90% coverage gates.
*   **Task 1.3 [Dev A]:** Construct Base Docker Image (Node.js/Python final stages, `tini` / `su-exec`).
*   **Task 1.4 [Dev B]:** Configure Container Security & Storage Policies (LUKS volumes, `IPC_LOCK`).
*   **Task 1.5 [Dev C]:** Establish Local Developer Experience (base & override `docker-compose.yml`, local MinIO, Jaeger).
*   **Task 1.6 [Dev D]:** Docker Hardening (`--read-only` rootfs, Redis config, log-rotation, tmpfs Secrets).
*   **Task 1.7 [Dev A]:** Air-Gap Artifact Bundler (`make build-airgap-bundle` script).

### [Phase 2: Foundational Architecture & Shared Services](backlog/phase-02.md)
*   **Task 2.1 [Dev A]:** Implement Module Bootstrapper (FastAPI), OTEL injection, Idempotency, and "Orphan Task Reaper".
*   **Task 2.2 [Dev B]:** Configure PostgreSQL config, PgBouncer, SQLModel base ORM, and Application-Level Encryption (ALE).
*   **Task 2.3 [Dev C]:** Implement JWT Auth middleware, granular scopes, and IP/mTLS Certificate binding.
*   **Task 2.4 [Dev D]:** Develop "Vault Unseal" API, internal mTLS CA, WORM Audit Logger, `/metrics`, Alertmanager, and as-code Grafana.

### [Phase 3: The "Thin Slice" (Rapid ROI - Ingest, Subset, Egress)](backlog/phase-03.md)
*   **Task 3.1 [Dev A]:** Build the Ingestion Engine (DB connections, I/O protocols).
*   **Task 3.2 [Dev B]:** Implement Relational Mapping (Schema inference, foreign key mapping via topological sort).
*   **Task 3.3 [Dev C]:** Build Deterministic Masking Engine (Format-preserving algorithms, collision prevention, LUHN).
*   **Task 3.4 [Dev D]:** Build Subsetting & Materialization Core (Relational transversal, Saga rollbacks, secure egress).
*   **Task 3.5 [Dev A]:** Execute E2E Integration Tests for Subsetting workflow (`@axe-core/playwright`, `pytest-postgresql`).

### [Phase 3.5: Technical Debt Sprint](backlog/phase-03.5.md)
*   **Task 3.5.1 [Dev A]:** Supply Chain & CI Hardening (SHA-pin Actions, Trivy, pg_ctl).
*   **Task 3.5.2 [Dev B]:** Module Cohesion Refactor (extract mapping/ and subsetting/).
*   **Task 3.5.3 [Dev C]:** SchemaTopology Immutability & Virtual FK Support.
*   **Task 3.5.4 [Dev D]:** Bootstrapper Wiring & Minimal CLI Entrypoint (conclave-subset).
*   **Task 3.5.5 [Dev A]:** Advisory Sweep (remaining open items).

### [Phase 4: Advanced Generative AI & Differential Privacy](backlog/phase-04.md)
*   **Task 4.1 [Dev A]:** Integrate GPU Passthrough and Ephemeral Object/Blob storage.
*   **Task 4.2 [Dev B]:** Integrate OSS Synthesizer (e.g., SDV) with batching/checkpointing.
*   **Task 4.3 [Dev C]:** Integrate OSS Differential Privacy (OpenDP/SmartNoise) and OOM Guardrails.
*   **Task 4.4 [Dev D]:** Build Privacy Accountant Logic (Epsilon ledger limits, pessimistic locking).

### [Phase 5: Orchestration, UI, & Licensing](backlog/phase-05.md)
*   **Task 5.1 [Dev A]:** Build Task Orchestration API (FastAPI endpoints, Webhooks, Pagination, RFC 7807, SSE, schema sync).
*   **Task 5.2 [Dev B]:** Implement Offline License Activation Protocol (QR Challenge, JWT offline validation).
*   **Task 5.3 [Dev C]:** Build Accessible React SPA (WCAG 2.1 AA, `Content-Security-Policy`, local fonts, Vault Unseal router).
*   **Task 5.4 [Dev D]:** Develop Data Synthesis Dashboard (Determinate progress UX, SSE integration, `localStorage` state rehydration).
*   **Task 5.5 [Dev A]:** Implement Cryptographic Shredding & Re-Keying API (Rotate ALE keys, zeroize constraints).

### [Phase 6: Integration, Audit & Finalization](backlog/phase-06.md)
*   **Task 6.1 [Dev B]:** Execute E2E Integration Tests for Generative Synthesis (Dummy ML assertions).
*   **Task 6.2 [Dev C]:** Validate NIST SP 800-88 Cryptographic Erasure triggers, OWASP validation, and LLM Fuzz Testing.
*   **Task 6.3 [Dev D]:** Final Security Remediation, Documentation generation, and Platform Handover validation.

### [Phase 7: Differential Privacy Integration](backlog/phase-07.md)
*   **Task 7.1:** ADR-0025: Custom CTGAN Training Loop Architecture.
*   **Task 7.2:** Custom CTGAN Training Loop.
*   **Task 7.3:** Opacus End-to-End Wiring.
*   **Task 7.4:** ProfileDelta Validation & Quality Benchmarks.
*   **Task 7.5:** Phase 7 E2E Test & Retrospective.

### [Phase 8: Advisory Drain Sprint](backlog/phase-08.md)
*   **Task 8.1:** Integration Test Gaps (ADV-021, ADV-064).
*   **Task 8.2:** Security Hardening (ADV-040, ADV-057, ADV-058, ADV-067).
*   **Task 8.3:** Data Model & Architecture Cleanup (ADV-050, ADV-054, ADV-071).
*   **Task 8.4:** CI Infrastructure (ADV-052, ADV-062, ADV-065, ADV-066, ADV-069).
*   **Task 8.5:** Documentation & Operator Gaps (ADV-070, ADV-072).

### [Phase 9: Production Hardening & Correctness Sprint](backlog/phase-09.md)
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

### [Phase 18: Type Safety, Dependency Audit & E2E Validation](backlog/phase-18.md)
*   **Task 18.1:** type:ignore Suppression Audit & Reduction.
*   **Task 18.2:** Dependency Tree Audit & ADV-015 PgBouncer Fix.
*   **Task 18.3:** E2E Validation Infrastructure with Sample Data.

### [Phase 19: Production Hardening & Integration Integrity](backlog/phase-19.md)
*   **Task 19.1:** Middleware & Engine Singleton Fixes.
*   **Task 19.2:** Security Hardening: Proxy Trust & Config Validation.
*   **Task 19.3:** Integration Test CI Gate & Property-Based Testing.
*   **Task 19.4:** Live E2E Pipeline Validation.
*   **Task 19.5:** Process Sunset & Rule Consolidation.

### [Phase 20: Human-in-the-Loop Feedback](backlog/phase-20.md)
*   **Task 20.1:** Exception Handling & Warning Suppression Fixes.
*   **Task 20.2:** Integration Test Expansion (Real Infrastructure).
*   **Task 20.3:** Frontend Accessibility Production Readiness.
*   **Task 20.4:** Architecture Tightening.
*   **Task 20.5:** Polish Batch (Cosmetic & Documentation).

### [Phase 21: CLI Masking Fix & E2E Smoke Tests](backlog/phase-21.md) ✅
*   Per-column masking fix, automated E2E smoke test, README evidence.

### [Phase 22: DP Pipeline End-to-End Integration](backlog/phase-22.md) ✅
*   DP parameters on SynthesisJob, DP wrapper wiring, budget enforcement API, E2E integration test.

### [Phase 23: Job Lifecycle Completion](backlog/phase-23.md) ✅
*   Generation step, download endpoint, cryptographic erasure, frontend download button.

### [Phase 24: Integration Test Repair](backlog/phase-24.md) ✅
*   Parameter rename regression fix, CLI wiring fix, test isolation fixtures.

### [Phase 25: Observability](backlog/phase-25.md) ✅
*   Custom Prometheus metrics, OTEL trace propagation.

### [Phase 26: Backend Production Hardening](backlog/phase-26.md) ✅
*   Router decomposition, shared exception hierarchy, protocol typing for DI, HTTP round-trip tests.

### [Phase 27: Frontend Production Hardening](backlog/phase-27.md) ✅
*   Responsive breakpoints, AsyncButton standardization, Playwright E2E accessibility tests.

### [Phase 28: Full E2E Validation](backlog/phase-28.md) ✅
*   Full E2E validation run, load test (11,000 rows), 5 production bugs fixed, dual-driver DB ADR.

### [Phase 29: Documentation Integrity & Review Debt](backlog/phase-29.md) ✅
*   **Task 29.1:** README DP Claim Correction.
*   **Task 29.2:** Frontend `node_modules` Gitignore Audit.
*   **Task 29.3:** Error Message Audience Differentiation.
*   **Task 29.4:** Coverage Threshold Elevation to 95%.
*   **Task 29.5:** ADR-0025 Amendment: Proxy Model Limitation & Phase 30 Plan.

### [Phase 30: True Discriminator-Level DP-SGD](backlog/phase-30.md) ✅
*   **Task 30.1:** ADR-0036: Discriminator-Level DP-SGD Architecture.
*   **Task 30.2:** Opacus-Compatible Discriminator Wrapper.
*   **Task 30.3:** Custom GAN Training Loop with Discriminator DP-SGD.
*   **Task 30.4:** DP Quality Benchmark: Proxy vs Discriminator.
*   **Task 30.5:** Integration Test: Real Opacus on Real Discriminator.
*   **Task 30.6:** ADR-0025 Final Amendment: Proxy Model Superseded.

### [Phase 31: Code Health & Bus Factor Elimination](backlog/phase-31.md) ✅
*   **Task 31.1:** Human Developer Guide.
*   **Task 31.2:** Vulture Whitelist Audit & Reduction.
*   **Task 31.3:** dp_training.py Decomposition.

### [Phase 32: Dead Module Cleanup & Development Process Documentation](backlog/phase-32.md) ✅
*   **Task 32.1:** Remove Unwired Scaffolding Modules.
*   **Task 32.2:** README Development Process Section.
*   **Task 32.3:** Development Story Case Study.

### [Phase 33: Governance Hygiene, Documentation Currency & Codebase Polish](backlog/phase-33.md) ✅
*   **Task 33.1:** CLAUDE.md Rule Sunset Evaluation.
*   **Task 33.2:** Docstring Validation Gate.
*   **Task 33.3:** Documentation Currency & Gaps.
*   **Task 33.4:** Codebase Cleanup.

### [Phase 34: Exception Hierarchy Unification & Operator Error Coverage](backlog/phase-34.md) ✅
*   **Task 34.1:** Unify Vault Exceptions Under SynthEngineError.
*   **Task 34.2:** Consolidate Module-Local Exceptions Into Shared Hierarchy.
*   **Task 34.3:** Complete OPERATOR_ERROR_MAP for All Domain Exceptions.

### [Phase 35: Synthesis Layer Refactor & Test Replacement](backlog/phase-35.md) ✅
*   **Task 35.1:** Decompose `_run_synthesis_job_impl()` Into Discrete Job Steps.
*   **Task 35.2:** Split `dp_training.py` Into Strategy Classes.
*   **Task 35.3:** Replace Tautological DP Training Tests.
*   **Task 35.4:** Add Full E2E Pipeline Integration Test.

### [Phase 36: Configuration Centralization, Documentation Pruning & Hygiene](backlog/phase-36.md) ✅
*   **Task 36.1:** Centralize Configuration Into Pydantic Settings Model.
*   **Task 36.2:** Split `bootstrapper/errors.py` Into Focused Modules.
*   **Task 36.3:** Documentation Pruning & Credibility Fixes.
*   **Task 36.4:** Standardize Module Exports, Logging, and Missing Edge-Case Tests.
*   **Task 36.5:** Full E2E Demo Run With Production-Worthy Dataset & Screenshots.

### [Phase 37: Advisory Drain, CHANGELOG Currency & E2E Demo Capstone](backlog/phase-37.md) ✅
*   **Task 37.1:** Fix Silent Privacy Budget Deduction Failure (ADV-P35-01).
*   **Task 37.2:** Drain Remaining Advisories (ADV-P34-01, ADV-P34-02, ADV-P36-01).
*   **Task 37.3:** Update CHANGELOG Through Phase 36.
*   **Task 37.4:** Full E2E Demo Run With Production-Worthy Dataset & Screenshots.

### [Phase 38: Audit Integrity, Timing Side-Channel Fix & Pre-Commit Hardening](backlog/phase-38.md) ✅
*   **Task 38.1:** Fix Silent Audit Failure After Budget Deduction (CRITICAL).
*   **Task 38.2:** Fix Vault Unseal Timing Side-Channel.
*   **Task 38.3:** Add Import-Linter to Pre-Commit Hooks.
*   **Task 38.4:** Documentation & Hygiene Polish Batch.

### [Phase 39: Authentication, Authorization & Rate Limiting](backlog/phase-39.md)
*   **Task 39.1:** Add Authentication Middleware (JWT Bearer Token).
*   **Task 39.2:** Add Authorization & IDOR Protection on All Resource Endpoints.
*   **Task 39.3:** Add Rate Limiting Middleware.
*   **Task 39.4:** Encrypt Connection Metadata with ALE.

### [Phase 40: Test Suite Quality Overhaul](backlog/phase-40.md)
*   **Task 40.1:** Replace Shallow Assertions With Value-Checking Tests.
*   **Task 40.2:** Replace Mock-Heavy Tests With Behavioral Tests.
*   **Task 40.3:** Add Missing Test Categories: Concurrency, Boundary Values, Performance.

### [Phase 41: Data Compliance, Retention Policy & GDPR/CCPA Readiness](backlog/phase-41.md)
*   **Task 41.1:** Implement Data Retention Policy.
*   **Task 41.2:** Implement GDPR Right-to-Erasure & CCPA Deletion Endpoint.
*   **Task 41.3:** Document Data Retention & Compliance Policies.

### [Phase 42: Security Hardening, Key Rotation & Deployment Safety](backlog/phase-42.md)
*   **Task 42.1:** Implement Artifact Signing Key Versioning.
*   **Task 42.2:** Add HTTPS Enforcement & Deployment Safety Checks.
*   **Task 42.3:** Run and Document DP Quality Benchmarks.
*   **Task 42.4:** Document CORS Policy & Add DDoS Mitigation Notes.

### [Phase 43: Architectural Polish, Code Hygiene & Rule Sunset Evaluation](backlog/phase-43.md)
*   **Task 43.1:** Extract `dp_accounting.py` From `job_orchestration.py`.
*   **Task 43.2:** Consolidate Optional Import Pattern.
*   **Task 43.3:** Add Request Flow Documentation & Architecture Diagram.
*   **Task 43.4:** Code Hygiene Polish Batch.
*   **Task 43.5:** Evaluate CLAUDE.md Rule Sunset (Phase 40 Rules).

### [Phase 44: Comprehensive Documentation Audit & Cleanup](backlog/phase-44.md)
*   **Task 44.1:** Audit Root-Level Documents.
*   **Task 44.2:** Audit Architecture Decision Records (ADRs).
*   **Task 44.3:** Audit Operational & Developer Documentation.
*   **Task 44.4:** Audit Backlog, Retrospective & Archive Documents.
*   **Task 44.5:** Create Document Lifecycle Index.

### [Phase 45: Webhook Callbacks, Idempotency Middleware & Orphan Task Reaper](backlog/phase-45.md)
*   **Task 45.1:** Reintroduce Idempotency Middleware (TBD-07).
*   **Task 45.2:** Reintroduce Orphan Task Reaper (TBD-08).
*   **Task 45.3:** Implement Webhook Callbacks for Task Completion (TBD-01).
*   **Task 45.4:** Update Deferred Items & ADR-0029.

### [Phase 46: mTLS Inter-Container Communication](backlog/phase-46.md)
*   **Task 46.1:** Internal Certificate Authority & Certificate Issuance.
*   **Task 46.2:** Wire mTLS on All Container-to-Container Connections.
*   **Task 46.3:** Certificate Rotation Without Downtime.
*   **Task 46.4:** Network Policy Enforcement & Documentation.

### [Phase 47: Auth Gap Remediation, Safety Hardening & Operational Fixes](backlog/phase-47.md)
*   **Task 47.1:** Add Authentication to `/security` Endpoints.
*   **Task 47.2:** Add Authentication to `/privacy/budget` Endpoints.
*   **Task 47.3:** Add Authentication to All `/settings` CRUD Endpoints.
*   **Task 47.4:** Fail-Fast on Empty `JWT_SECRET_KEY`.
*   **Task 47.5:** Validate `OPERATOR_CREDENTIALS_HASH` at Startup.
*   **Task 47.6:** Harden Model Artifact Signature Verification.
*   **Task 47.7:** Add Memory Bounds to Parquet Loading.
*   **Task 47.8:** Add Shutdown Cleanup to Lifespan Hook.
*   **Task 47.9:** Scrub Budget Values From Exception Messages.
*   **Task 47.10:** Add Redis Health Check to Docker Compose.
