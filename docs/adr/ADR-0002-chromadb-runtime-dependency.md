# ADR-0002: ChromaDB as a Runtime Dependency

**Status:** Accepted
**Date:** 2026-03-13
**Deciders:** Project team

## Context

The Conclave Engine requires a local vector store to serve as the "Agile Brain" — a semantic memory layer queryable by all agent streams. The store must run natively on macOS (Apple Silicon M4) and in air-gapped environments without cloud API calls.

## Decision

Use **ChromaDB** (`chromadb ^1.5.5`) as the semantic memory backend, declared as a runtime dependency in `pyproject.toml`.

- Collections: `Constitution`, `ADRs`, `Retrospectives`
- Persistence: `~/.chroma_data` (local filesystem, not gitignored data/)
- Seeding: `scripts/seed_chroma.py` (CONSTITUTION.md and ARCHITECTURAL_REQUIREMENTS.md)

ChromaDB is a **runtime** (not dev-only) dependency because it will be imported by production modules in Phase 2+ (agent streams querying governance context). Placing it in the main dependency group ensures it is present in production installations.

## Consequences

- **Positive:** Fully local, no cloud egress. Compatible with air-gap deployment.
- **Positive:** Python-native — no external daemon required for the vector store.
- **Negative:** ChromaDB is a heavyweight dependency (~135 transitive packages). The air-gap bundle (Task 1.7) must include the full dependency tree.
- **Air-gap procurement:** Pin to `^1.5.5`. Update `poetry.lock` and re-bundle at each upgrade. Run `pip-audit` after every chromadb version bump.
- **Version governance:** The pin in `pyproject.toml` is the single source of truth. The CI `pip install "chromadb==x.y.z"` line was removed in Task 1.1 in favour of `poetry install`.
