# ADR-0001: Modular Monolith Topology and Import-Linter Enforcement

**Status:** Accepted
**Date:** 2026-03-13
**Deciders:** Project team

## Context

The Conclave Engine is a Python-based Synthetic Data Generation platform that must be deployable as a single artifact in air-gapped environments while maintaining strict internal separation of concerns. Multiple modules (ingestion, profiling, synthesis, masking, privacy) must not bleed implementation details into each other, and the bootstrapper layer must remain the only entry point for cross-module coordination.

## Decision

Adopt a **Modular Monolith** architecture enforced by `import-linter`:

- Single deployable unit: `src/synth_engine/` compiled as one package
- Six subpackages with strict independence contracts:
  - `modules/ingestion/` — Database schema inference & mapping
  - `modules/profiler/` — Statistical distributions & latent patterns
  - `modules/masking/` — Deterministic format-preserving rules
  - `modules/synthesizer/` — DP-SGD generation & edge case amplification
  - `modules/privacy/` — Epsilon/Delta accountant ledger
  - `shared/` — Cross-cutting utilities (crypto, audit logs)
- `bootstrapper/` — Main API, DI config, global middleware (only layer permitted to orchestrate modules)
- `import-linter` contracts enforce at CI time that no module imports from another module, and no module imports from `bootstrapper`

## Consequences

- **Positive:** Architectural boundaries are machine-enforced, not convention-dependent. Cross-module coupling is a CI failure, not a code review comment.
- **Positive:** Each module can be reasoned about, tested, and evolved independently.
- **Negative:** All inter-module communication must pass through explicit Python interfaces — slightly more ceremony than direct imports.
- **Constraint:** No LangChain. All agent orchestration uses Claude's native `tool_use` API directly.
