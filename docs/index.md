# Conclave Engine — Documentation Index

Central navigation for all Conclave Engine documentation, organized by primary audience.

**Total documents indexed: 135**

---

## Quick Links

| I need to... | Go to |
|--------------|-------|
| Deploy for the first time | [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) |
| Unseal the vault | [OPERATOR_MANUAL.md](OPERATOR_MANUAL.md#4-vault-unseal-procedure) |
| Diagnose a stuck job | [TROUBLESHOOTING.md](TROUBLESHOOTING.md#1-huey-worker--task-stuck-in-queued) |
| Understand hardware requirements | [SCALABILITY.md](SCALABILITY.md) |
| Recover from a disaster | [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) |
| Activate a license | [LICENSING.md](LICENSING.md) |
| Understand the architecture | [ARCHITECTURAL_REQUIREMENTS.md](ARCHITECTURAL_REQUIREMENTS.md) |
| Review an architectural decision | [ADR index](#architectural-decision-records-adrs) |
| Check a dependency for CVEs | [DEPENDENCY_AUDIT_POLICY.md](DEPENDENCY_AUDIT_POLICY.md) |
| Read the project backlog | [BACKLOG.md](BACKLOG.md) |

---

## Operator Documentation

| Document | Description |
|----------|-------------|
| [OPERATOR_MANUAL.md](OPERATOR_MANUAL.md) | Primary reference: startup, vault unseal, job creation, monitoring, DP configuration |
| [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) | First-time deployment playbook: TLS, firewall, vault ceremony, secret provisioning |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Diagnostic flowcharts for 10 failure scenarios |
| [SCALABILITY.md](SCALABILITY.md) | Capacity limits, hardware sizing, memory model, latency ranges |
| [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) | Recovery procedures: OOM, key loss, PostgreSQL backup, Redis failure |
| [LICENSING.md](LICENSING.md) | License activation protocol, QR code workflow, key rotation |
| [DP_QUALITY_REPORT.md](DP_QUALITY_REPORT.md) | Empirical epsilon/delta benchmarks; use to calibrate DP parameters |
| [E2E_VALIDATION.md](E2E_VALIDATION.md) | End-to-end validation: smoke tests, synthesis quality checks, output verification |
| [E2E_VALIDATION_RESULTS.md](E2E_VALIDATION_RESULTS.md) | Full E2E DP synthesis validation against Pagila: pipeline execution, FK integrity, epsilon accounting, statistical comparison (T54.3) |
| [infrastructure_security.md](infrastructure_security.md) | Infrastructure security: network isolation, capability model, secret management |

---

## Developer Documentation

| Document | Description |
|----------|-------------|
| [../CLAUDE.md](../CLAUDE.md) | Agent directives, TDD workflow, quality gates, git workflow, module placement rules |
| [../CONSTITUTION.md](../CONSTITUTION.md) | Constitutional directives: security priority, quality gates, enforcement |
| [ARCHITECTURAL_REQUIREMENTS.md](ARCHITECTURAL_REQUIREMENTS.md) | Modular monolith rules, import boundaries, module responsibility table |
| [BUSINESS_REQUIREMENTS.md](BUSINESS_REQUIREMENTS.md) | Business context: why privacy-preserving synthetic data, target users, compliance drivers |
| [DEPENDENCY_AUDIT.md](DEPENDENCY_AUDIT.md) | Full audit table of direct dependencies with transitive counts and group assignments |
| [DEPENDENCY_AUDIT_POLICY.md](DEPENDENCY_AUDIT_POLICY.md) | pip-audit usage policy, severity tiers, exemption process, new-dependency checklist |
| [RETRO_LOG.md](RETRO_LOG.md) | Living retrospective ledger: advisories, findings, phase exit criteria |
| [TEAM_BLUEPRINT.md](archive/TEAM_BLUEPRINT.md) | Team roles, review agent responsibilities, PR process (archived) |

### Backlog Documents

| Document | Phases |
|----------|--------|
| [backlog/phase-0.6.md](backlog/phase-0.6.md) | Phase 0.6 tasks |
| [backlog/phase-0.8.md](backlog/phase-0.8.md) | Phase 0.8 spike tasks |
| [backlog/phase-01.md](backlog/phase-01.md) | Phase 1 — Bootstrap |
| [backlog/phase-02.md](backlog/phase-02.md) | Phase 2 — Secure DB |
| [backlog/phase-03.md](backlog/phase-03.md) | Phase 3 — Ingestion |
| [backlog/phase-03.5.md](backlog/phase-03.5.md) | Phase 3.5 — Subsetting |
| [backlog/phase-04.md](backlog/phase-04.md) | Phase 4 — Synthesis |
| [backlog/phase-05.md](backlog/phase-05.md) | Phase 5 — Orchestration |
| [backlog/phase-06.md](backlog/phase-06.md) | Phase 6 — Frontend |
| [backlog/phase-07.md](backlog/phase-07.md) | Phase 7 — CTGAN Training |
| [backlog/phase-08.md](backlog/phase-08.md) | Phase 8 — Advisory Drain |
| [backlog/phase-09.md](backlog/phase-09.md) | Phase 9 |
| [backlog/phase-10.md](backlog/phase-10.md) | Phase 10 |
| [backlog/phase-11.md](backlog/phase-11.md) | Phase 11 |
| [backlog/phase-12.md](backlog/phase-12.md) | Phase 12 |
| [backlog/phase-13.md](backlog/phase-13.md) | Phase 13 |
| [backlog/phase-14.md](backlog/phase-14.md) | Phase 14 |
| [backlog/phase-15.md](backlog/phase-15.md) | Phase 15 |
| [backlog/phase-16.md](backlog/phase-16.md) | Phase 16 |
| [backlog/phase-17.md](backlog/phase-17.md) | Phase 17 |
| [backlog/phase-18.md](backlog/phase-18.md) | Phase 18 |
| [backlog/phase-19.md](backlog/phase-19.md) | Phase 19 |
| [backlog/phase-20.md](backlog/phase-20.md) | Phase 20 |
| [backlog/phase-21.md](backlog/phase-21.md) | Phase 21 — CLI Masking Fix & E2E Smoke Tests |
| [backlog/phase-22.md](backlog/phase-22.md) | Phase 22 — DP Pipeline End-to-End Integration |
| [backlog/phase-23.md](backlog/phase-23.md) | Phase 23 — Job Lifecycle Completion |
| [backlog/phase-24.md](backlog/phase-24.md) | Phase 24 — Integration Test Repair |
| [backlog/phase-25.md](backlog/phase-25.md) | Phase 25 — Observability |
| [backlog/phase-26.md](backlog/phase-26.md) | Phase 26 — Backend Production Hardening |
| [backlog/phase-27.md](backlog/phase-27.md) | Phase 27 — Frontend Production Hardening |
| [backlog/phase-28.md](backlog/phase-28.md) | Phase 28 — Full E2E Validation |
| [backlog/phase-29.md](backlog/phase-29.md) | Phase 29 — Documentation Integrity & Review Debt |
| [backlog/phase-30.md](backlog/phase-30.md) | Phase 30 — True Discriminator-Level DP-SGD |
| [backlog/phase-31.md](backlog/phase-31.md) | Phase 31 — Code Health & Bus Factor Elimination |
| [backlog/phase-32.md](backlog/phase-32.md) | Phase 32 — Dead Module Cleanup & Development Process Documentation |
| [backlog/phase-33.md](backlog/phase-33.md) | Phase 33 — Governance Hygiene, Documentation Currency & Codebase Polish |
| [backlog/phase-34.md](backlog/phase-34.md) | Phase 34 — Exception Hierarchy Unification & Operator Error Coverage |
| [backlog/phase-35.md](backlog/phase-35.md) | Phase 35 — Synthesis Layer Refactor & Test Replacement |
| [backlog/phase-36.md](backlog/phase-36.md) | Phase 36 — Configuration Centralization, Documentation Pruning & Hygiene |
| [backlog/phase-37.md](backlog/phase-37.md) | Phase 37 — Advisory Drain, CHANGELOG Currency & E2E Demo Capstone |
| [backlog/phase-38.md](backlog/phase-38.md) | Phase 38 — Audit Integrity, Timing Side-Channel Fix & Pre-Commit Hardening |
| [backlog/phase-39.md](backlog/phase-39.md) | Phase 39 — Authentication, Authorization & Rate Limiting |
| [backlog/phase-40.md](backlog/phase-40.md) | Phase 40 — Test Suite Quality Overhaul |
| [backlog/phase-41.md](backlog/phase-41.md) | Phase 41 — Data Compliance, Retention Policy & GDPR/CCPA Readiness |
| [backlog/phase-42.md](backlog/phase-42.md) | Phase 42 — Security Hardening, Key Rotation & Deployment Safety |
| [backlog/phase-43.md](backlog/phase-43.md) | Phase 43 — Architectural Polish, Code Hygiene & Rule Sunset Evaluation |
| [backlog/phase-44.md](backlog/phase-44.md) | Phase 44 — Comprehensive Documentation Audit & Cleanup |
| [backlog/phase-45.md](backlog/phase-45.md) | Phase 45 — Webhook Callbacks, Idempotency & Reaper |
| [backlog/phase-46.md](backlog/phase-46.md) | Phase 46 — mTLS Inter-Container Communication |
| [backlog/deferred-items.md](backlog/deferred-items.md) | Deferred and parked items |
| [BACKLOG.md](BACKLOG.md) | Backlog summary and current phase status |

### Retrospective Archives

| Document | Coverage |
|----------|----------|
| [retro_archive/phases-0-to-7.md](retro_archive/phases-0-to-7.md) | Phases 0 through 7 |
| [retro_archive/phases-8-to-14.md](retro_archive/phases-8-to-14.md) | Phases 8 through 14 |
| [retro_archive/phases-15-to-45.md](retro_archive/phases-15-to-45.md) | Phases 15 through 45 |
| [RETRO_LOG.md](RETRO_LOG.md) | Current (active) — Phases 46 onwards |

### Review Prompt Templates

| Document | Reviewer Role |
|----------|---------------|
| [prompts/review/ARCHITECTURE.md](prompts/review/ARCHITECTURE.md) | Architecture reviewer |
| [prompts/review/DEVELOPER.md](prompts/review/DEVELOPER.md) | Developer / QA reviewer |
| [prompts/review/DEVOPS.md](prompts/review/DEVOPS.md) | DevOps reviewer |
| [prompts/review/EXECUTIVE.md](prompts/review/EXECUTIVE.md) | Executive / PM reviewer |
| [prompts/review/EXPERT_PROTOTYPE.md](prompts/review/EXPERT_PROTOTYPE.md) | Expert prototype reviewer |
| [prompts/review/PROJECT_MANAGER.md](prompts/review/PROJECT_MANAGER.md) | Project manager reviewer |
| [prompts/review/SECURITY.md](prompts/review/SECURITY.md) | Security reviewer |
| [prompts/review/UI_UX.md](prompts/review/UI_UX.md) | UI/UX reviewer |

### Archived Documents

Historical records no longer actively maintained. Not indexed — see `docs/archive/` for the full list. Includes superseded requirement drafts, the original execution plan, and Phase 0.8 spike findings.

---

## Architect Documentation

### Architectural Decision Records (ADRs)

ADRs are append-only records of significant technical decisions. Superseded or retired ADRs are annotated with a status notice at the top.

| ADR | Title | Status |
|-----|-------|--------|
| [ADR-0001](adr/ADR-0001-modular-monolith-topology.md) | Modular Monolith Topology | Accepted |
| [ADR-0002](adr/ADR-0002-chromadb-runtime-dependency.md) | ChromaDB Runtime Dependency | Superseded |
| [ADR-0003](adr/ADR-0003-redis-idempotency.md) | Redis Idempotency | Accepted |
| [ADR-0004](adr/ADR-0004-opentelemetry.md) | OpenTelemetry | Accepted |
| [ADR-0005](adr/ADR-0005-orphan-task-reaper.md) | Orphan Task Reaper | Accepted |
| [ADR-0006](adr/ADR-0006-application-level-encryption.md) | Application-Level Encryption | Accepted |
| [ADR-0007](adr/ADR-0007-jwt-library-selection.md) | JWT Library Selection | Accepted |
| [ADR-0008](adr/ADR-0008-zero-trust-token-binding.md) | Zero-Trust Token Binding | Accepted |
| [ADR-0009](adr/ADR-0009-vault-unseal-pattern.md) | Vault Unseal Pattern | Accepted |
| [ADR-0010](adr/ADR-0010-worm-audit-logger.md) | WORM Audit Logger | Accepted |
| [ADR-0011](adr/ADR-0011-prometheus-metrics.md) | Prometheus Metrics | Accepted |
| [ADR-0012](adr/ADR-0012-ingestion-adapter.md) | Ingestion Adapter | Accepted |
| [ADR-0013](adr/ADR-0013-relational-mapping.md) | Relational Mapping | Accepted |
| [ADR-0014](adr/ADR-0014-masking-engine.md) | Masking Engine | Accepted |
| [ADR-0015](adr/ADR-0015-subsetting-saga.md) | Subsetting Saga Pattern | Accepted |
| [ADR-0016](adr/ADR-0016-cli-click-dependency.md) | CLI Click Dependency | Accepted |
| [ADR-0017](adr/ADR-0017-synthesizer-dp-library-selection.md) | Synthesizer & DP Library Selection (v2 — consolidated with ADR-0017a) | Accepted |
| [ADR-0017a](adr/ADR-0017a-opacus-secure-mode-decision.md) | Opacus `secure_mode` Decision | Superseded by ADR-0017 v2 |
| [ADR-0018](adr/ADR-0018-psutil-ram-introspection.md) | psutil RAM Introspection | Accepted |
| [ADR-0019](adr/ADR-0019-ai-pr-review-governance.md) | AI PR Review Governance | Accepted |
| [ADR-0020](adr/ADR-0020-huey-task-queue-singleton.md) | Huey Task Queue Singleton | Accepted |
| [ADR-0021](adr/ADR-0021-sse-and-bootstrapper-owned-tables.md) | SSE and Bootstrapper-Owned Tables | Accepted |
| [ADR-0022](adr/ADR-0022-offline-license-activation.md) | Offline License Activation | Accepted |
| [ADR-0023](adr/ADR-0023-frontend-react-vite-spa.md) | Frontend React/Vite SPA | Accepted |
| [ADR-0024](adr/ADR-0024-pure-asgi-body-replay-middleware.md) | Pure ASGI Body Replay Middleware | Accepted |
| [ADR-0025](adr/ADR-0025-custom-ctgan-training-loop.md) | Custom CTGAN Training Loop | Superseded by Phase 30 |
| [ADR-0026](adr/ADR-0026-dp-parameter-accessibility.md) | DP Parameter Accessibility | Accepted |
| [ADR-0027](adr/ADR-0027-bootstrapper-submodule-re-export-pattern.md) | Bootstrapper Submodule Re-Export Pattern | Accepted |
| [ADR-0028](adr/ADR-0028-pytest-asyncio-1x-upgrade.md) | pytest-asyncio 1.x Upgrade | Accepted |
| [ADR-0029](adr/ADR-0029-architectural-requirements-gap-analysis.md) | Architectural Requirements Gap Analysis | Accepted |
| [ADR-0030](adr/ADR-0030-float-to-numeric-epsilon-precision.md) | Float to Numeric Epsilon Precision | Accepted |
| [ADR-0031](adr/ADR-0031-pgbouncer-image-substitution.md) | PgBouncer Image Substitution | Accepted |
| [ADR-0032](adr/ADR-0032-mypy-synthesizer-ignore-missing-imports.md) | Mypy Synthesizer `ignore_missing_imports` | Retired (see file) |
| [ADR-0033](adr/ADR-0033-cross-module-exception-detection-by-class-name.md) | Cross-Module Exception Detection by Class Name | Superseded |
| [ADR-0034](adr/ADR-0034-shredded-lifecycle-state-and-audit-tolerance.md) | Shredded Lifecycle State and Audit Tolerance | Accepted |
| [ADR-0035](adr/ADR-0035-dual-driver-db-access.md) | Dual Driver DB Access | Accepted |
| [ADR-0036](adr/ADR-0036-discriminator-level-dp-sgd.md) | Discriminator-Level DP-SGD | Accepted |
| [ADR-0037](adr/ADR-0037-exception-hierarchy-consolidation.md) | Exception Hierarchy Consolidation | Accepted |
| [ADR-0038](adr/ADR-0038-synthesis-orchestration-step-decomposition.md) | Synthesis Orchestration Step Decomposition | Accepted |
| [ADR-0039](adr/ADR-0039-jwt-bearer-authentication.md) | JWT Bearer Authentication | Accepted |
| [ADR-0040](adr/ADR-0040-idor-protection-ownership-model.md) | IDOR Protection Ownership Model | Accepted |
| [ADR-0041](adr/ADR-0041-data-retention-compliance.md) | Data Retention Compliance | Accepted |
| [ADR-0042](adr/ADR-0042-artifact-signing-key-versioning.md) | Artifact Signing Key Versioning | Accepted |
| [ADR-0043](adr/ADR-0043-https-enforcement-middleware.md) | HTTPS Enforcement Middleware | Accepted |
| [ADR-0044](adr/ADR-0044-webhook-idempotency-reaper-architecture.md) | Webhook, Idempotency & Reaper Architecture | Accepted |
| [ADR-0045](adr/ADR-0045-mtls-inter-container-communication.md) | mTLS Inter-Container Communication | Accepted |
| [ADR-0046](adr/ADR-0046-priority-sequencing-constraint.md) | Priority Sequencing Constraint | Accepted |
| [ADR-0047](adr/ADR-0047-mutation-testing-gate.md) | Mutation Testing Gate | Accepted |
| [ADR-0048](adr/ADR-0048-audit-trail-anchoring.md) | Audit Trail Anchoring | Accepted |
| [ADR-0049](adr/ADR-0049-scope-based-authorization.md) | Scope-Based Authorization Model | Accepted |
| [ADR-0050](adr/ADR-0050-dp-budget-fail-closed.md) | DP Budget Deduction — Fail Closed | Accepted |
| [ADR-0052](adr/ADR-0052-mutmut-python-314-gap.md) | mutmut / Python 3.14 Compatibility Gap | Superseded by ADR-0054 |
| [ADR-0053](adr/ADR-0053-demos-directory-placement.md) | demos/ Directory Placement and Quality Gate Scope | Accepted |
| [ADR-0054](adr/ADR-0054-cosmic-ray-adoption.md) | Adopt cosmic-ray as Mutation Testing Tool | Accepted |
| [ADR-0055](adr/ADR-0055-restricted-unpickler.md) | Restricted Unpickler for ModelArtifact Deserialization | Accepted |
| [ADR-template.md](adr/ADR-template.md) | Template for new ADRs | Template |

---

## Security Documentation

| Document | Description |
|----------|-------------|
| [infrastructure_security.md](infrastructure_security.md) | Network segmentation, container capabilities, secret management, mTLS |
| [OPERATOR_MANUAL.md](OPERATOR_MANUAL.md) (Section 8) | TLS termination, network isolation, artifact signing, reverse proxy requirements |
| [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) (Steps 2–4) | TLS setup, firewall rules, vault initialization ceremony |
| [DEPENDENCY_AUDIT_POLICY.md](DEPENDENCY_AUDIT_POLICY.md) | pip-audit CVE scanning policy, severity tiers, exemption process |
| [DEPENDENCY_AUDIT.md](DEPENDENCY_AUDIT.md) | Current dependency audit table with supply chain notes |
| [LICENSING.md](LICENSING.md) | License JWT validation, offline activation, key rotation |
| [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) (Section 3) | Cryptographic key recovery, lost passphrase, ALE key shredding |
| [OPERATOR_MANUAL.md](OPERATOR_MANUAL.md) (Section 9.6) | Opacus `secure_mode` operational notes and threat model |
| [adr/ADR-0006](adr/ADR-0006-application-level-encryption.md) | ALE design rationale |
| [adr/ADR-0008](adr/ADR-0008-zero-trust-token-binding.md) | Zero-trust token binding decision |
| [adr/ADR-0009](adr/ADR-0009-vault-unseal-pattern.md) | Vault unseal pattern and KEK derivation |
| [adr/ADR-0010](adr/ADR-0010-worm-audit-logger.md) | WORM audit log design |
| [adr/ADR-0017](adr/ADR-0017-synthesizer-dp-library-selection.md) | DP library selection and `secure_mode` decision |
| [adr/ADR-0022](adr/ADR-0022-offline-license-activation.md) | Offline license activation security model |
| [adr/ADR-0034](adr/ADR-0034-shredded-lifecycle-state-and-audit-tolerance.md) | Cryptographic erasure and audit tolerance |

---

## Document Count by Category

| Category | Count |
|----------|-------|
| Operator guides | 10 |
| Developer guides | 8 |
| Backlog documents | 49 |
| Retrospective archives | 4 |
| Review prompt templates | 8 |
| Archived documents | 6 (not indexed — see `docs/archive/`) |
| ADRs (including template) | 56 |
| **Total** | **135** |

---

## Maintenance Notes

- This index is manually maintained. When adding a document to `docs/`, add a row to the appropriate section.
- ADR status values: `Accepted`, `Superseded`, `Retired`, `Template`.
- `Superseded`: replaced by a newer decision; original file retained with a supersession notice.
- `Retired`: decision no longer separately tracked (e.g., content moved to `pyproject.toml`); file retained.
