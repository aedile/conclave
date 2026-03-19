# Conclave Engine — Documentation Index

This is the central navigation page for all Conclave Engine documentation.
Documents are organized by primary audience. Many documents are relevant to
multiple audiences; cross-references are noted.

**Total documents indexed: 89**

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

Operators run the system in production. These documents are written for
people who need to deploy, configure, monitor, and recover Conclave Engine
installations.

| Document | Description |
|----------|-------------|
| [OPERATOR_MANUAL.md](OPERATOR_MANUAL.md) | Primary reference for day-to-day operations: startup, vault unseal, job creation, monitoring, security considerations, DP configuration |
| [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) | Step-by-step first-time deployment playbook: TLS setup, firewall rules, vault ceremony, secret provisioning, first synthesis job |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Diagnostic flowcharts for 10 failure scenarios: stuck tasks, storage failures, connection pool exhaustion, OOM events, budget exhaustion |
| [SCALABILITY.md](SCALABILITY.md) | Capacity limits and hardware sizing: pool configuration, concurrent job limits, SSE client scaling, memory model, latency ranges |
| [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) | Recovery procedures: failed subsetting jobs, OOM events, cryptographic key loss, PostgreSQL backup/restore, Redis failure, container crashes |
| [LICENSING.md](LICENSING.md) | License activation protocol for air-gapped environments, QR code workflow, key rotation |
| [DP_QUALITY_REPORT.md](DP_QUALITY_REPORT.md) | Empirical benchmark of epsilon/delta tradeoffs at various noise multiplier settings; use to calibrate DP parameters for your dataset |
| [E2E_VALIDATION.md](E2E_VALIDATION.md) | End-to-end validation procedures: smoke tests, synthesis quality checks, output verification steps |
| [infrastructure_security.md](infrastructure_security.md) | Infrastructure security configuration reference: network isolation, capability model, secret management patterns |

---

## Developer Documentation

Developers extend, maintain, and test the Conclave Engine codebase.

| Document | Description |
|----------|-------------|
| [../CLAUDE.md](../CLAUDE.md) | Agent directives, TDD workflow, quality gates, git workflow, module placement rules — the primary developer reference |
| [../CONSTITUTION.md](../CONSTITUTION.md) | Constitutional directives: security priority, quality gates, development workflow, enforcement mechanisms |
| [ARCHITECTURAL_REQUIREMENTS.md](ARCHITECTURAL_REQUIREMENTS.md) | Modular monolith rules, import boundaries, cross-module constraints, module responsibility table |
| [BUSINESS_REQUIREMENTS.md](BUSINESS_REQUIREMENTS.md) | Business context: why privacy-preserving synthetic data, target users, compliance drivers |
| [DEPENDENCY_AUDIT.md](DEPENDENCY_AUDIT.md) | Full audit table of all direct dependencies with runtime usage, transitive counts, and group assignments |
| [DEPENDENCY_AUDIT_POLICY.md](DEPENDENCY_AUDIT_POLICY.md) | pip-audit usage policy, severity tiers, exemption process, new-dependency checklist |
| [RETRO_LOG.md](RETRO_LOG.md) | Living retrospective ledger: advisories, findings, phase exit criteria — read before starting any task |
| [TEAM_BLUEPRINT.md](TEAM_BLUEPRINT.md) | Team roles, review agent responsibilities, PR process |

### Backlog Documents

The backlog is split into per-phase files for navigability:

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
| [backlog/deferred-items.md](backlog/deferred-items.md) | Deferred and parked items |
| [BACKLOG.md](BACKLOG.md) | Backlog summary and current phase status |

### Retrospective Archives

| Document | Coverage |
|----------|----------|
| [retro_archive/phases-0-to-7.md](retro_archive/phases-0-to-7.md) | Phases 0 through 7 retrospective entries |
| [retro_archive/phases-8-to-14.md](retro_archive/phases-8-to-14.md) | Phases 8 through 14 retrospective entries |
| [RETRO_LOG.md](RETRO_LOG.md) | Current (active) retrospective log — Phases 15 onwards |

### Review Prompt Templates

These prompts are used by AI reviewer agents:

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

Historical records no longer actively maintained. Not indexed — see `docs/archive/` for
the full list. Archived documents include superseded requirement drafts, the original
execution plan, and Phase 0.8 spike findings (ML memory, FPE, and topological subsetting).

---

## Architect Documentation

Architects evaluate and maintain the system's structural integrity. These
documents define and justify key architectural decisions.

### Architectural Decision Records (ADRs)

ADRs are append-only records of significant technical decisions. New decisions
get new ADR numbers; existing ADRs are never deleted. Superseded or retired ADRs
are annotated with a status notice at the top.

| ADR | Title | Status |
|-----|-------|--------|
| [ADR-0001](adr/ADR-0001-modular-monolith-topology.md) | Modular Monolith Topology | Accepted |
| [ADR-0002](adr/ADR-0002-chromadb-runtime-dependency.md) | ChromaDB Runtime Dependency | Accepted |
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
| [ADR-0025](adr/ADR-0025-custom-ctgan-training-loop.md) | Custom CTGAN Training Loop | Accepted |
| [ADR-0026](adr/ADR-0026-dp-parameter-accessibility.md) | DP Parameter Accessibility | Accepted |
| [ADR-0027](adr/ADR-0027-bootstrapper-submodule-re-export-pattern.md) | Bootstrapper Submodule Re-Export Pattern | Accepted |
| [ADR-0028](adr/ADR-0028-pytest-asyncio-1x-upgrade.md) | pytest-asyncio 1.x Upgrade | Accepted |
| [ADR-0029](adr/ADR-0029-architectural-requirements-gap-analysis.md) | Architectural Requirements Gap Analysis | Accepted |
| [ADR-0030](adr/ADR-0030-float-to-numeric-epsilon-precision.md) | Float to Numeric Epsilon Precision | Accepted |
| [ADR-0031](adr/ADR-0031-pgbouncer-image-substitution.md) | PgBouncer Image Substitution | Accepted |
| [ADR-0032](adr/ADR-0032-mypy-synthesizer-ignore-missing-imports.md) | Mypy Synthesizer `ignore_missing_imports` | Retired (see file) |
| [ADR-0033](adr/ADR-0033-cross-module-exception-detection-by-class-name.md) | Cross-Module Exception Detection by Class Name | Accepted |
| [ADR-0034](adr/ADR-0034-shredded-lifecycle-state-and-audit-tolerance.md) | Shredded Lifecycle State and Audit Tolerance | Accepted |
| [ADR-template.md](adr/ADR-template.md) | Template for new ADRs | Template |

---

## Security Documentation

Security personnel review deployment posture, access controls, and compliance
evidence.

| Document | Description |
|----------|-------------|
| [infrastructure_security.md](infrastructure_security.md) | Infrastructure security controls: network segmentation, container capabilities, secret management, mTLS considerations |
| [OPERATOR_MANUAL.md](OPERATOR_MANUAL.md) (Section 8) | Security considerations: TLS termination, network isolation, secret management, capability model, artifact signing, reverse proxy requirements |
| [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) (Steps 2–4) | TLS setup, firewall rules, vault initialization ceremony |
| [DEPENDENCY_AUDIT_POLICY.md](DEPENDENCY_AUDIT_POLICY.md) | pip-audit CVE scanning policy, severity tiers, exemption process |
| [DEPENDENCY_AUDIT.md](DEPENDENCY_AUDIT.md) | Current dependency audit table with supply chain notes |
| [LICENSING.md](LICENSING.md) | License JWT validation, offline activation, key rotation |
| [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) (Section 3) | Cryptographic key recovery, lost passphrase procedures, ALE key shredding |
| [OPERATOR_MANUAL.md](OPERATOR_MANUAL.md) (Section 9.6) | Opacus `secure_mode` operational notes and threat model |
| [adr/ADR-0006-application-level-encryption.md](adr/ADR-0006-application-level-encryption.md) | ALE design rationale |
| [adr/ADR-0008-zero-trust-token-binding.md](adr/ADR-0008-zero-trust-token-binding.md) | Zero-trust token binding decision |
| [adr/ADR-0009-vault-unseal-pattern.md](adr/ADR-0009-vault-unseal-pattern.md) | Vault unseal pattern and KEK derivation |
| [adr/ADR-0010-worm-audit-logger.md](adr/ADR-0010-worm-audit-logger.md) | WORM audit log design |
| [adr/ADR-0017-synthesizer-dp-library-selection.md](adr/ADR-0017-synthesizer-dp-library-selection.md) | DP library selection and `secure_mode` decision |
| [adr/ADR-0022-offline-license-activation.md](adr/ADR-0022-offline-license-activation.md) | Offline license activation security model |
| [adr/ADR-0034-shredded-lifecycle-state-and-audit-tolerance.md](adr/ADR-0034-shredded-lifecycle-state-and-audit-tolerance.md) | Cryptographic erasure and audit tolerance |

---

## Document Count by Category

| Category | Count |
|----------|-------|
| Operator guides | 9 |
| Developer guides | 8 |
| Backlog documents | 25 |
| Retrospective archives | 3 |
| Review prompt templates | 8 |
| Archived documents | 6 (not indexed — see `docs/archive/`) |
| ADRs (including template) | 36 |
| Security cross-references | (subset of above) |
| **Total** | **89** |

---

## Maintenance Notes

- This index is manually maintained. When adding a new document to `docs/`, add
  a row to the appropriate section above.
- ADR status values: `Accepted`, `Superseded`, `Retired`, `Template`.
- A `Superseded` ADR has been replaced by a newer decision. The original file is
  retained but marked with a supersession notice.
- A `Retired` ADR documented a decision that is no longer separately tracked
  (e.g., content moved to `pyproject.toml` comments). The file is retained.
