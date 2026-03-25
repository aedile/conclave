# ADR-0002: ChromaDB as a Runtime Dependency

**Status:** Historical — Technology Decision Reversed
**Original Status:** Accepted (2026-03-13)
**Amended:** 2026-03-18 (T33.3 — Documentation Currency & Gaps)
**Amended:** 2026-03-25 (T55.5 — passlib and chromadb dead dependency elimination)
**Deciders:** Project team

## Context

The Conclave Engine requires a local vector store to serve as the "Agile Brain" — a semantic memory layer queryable by all agent streams. The store must run natively on macOS (Apple Silicon M4) and in air-gapped environments without cloud API calls.

## Original Decision (2026-03-13)

Use **ChromaDB** (`chromadb ^1.5.5`) as the semantic memory backend, declared as a runtime dependency in `pyproject.toml`.

- Collections: `Constitution`, `ADRs`, `Retrospectives`
- Persistence: `~/.chroma_data` (local filesystem, not gitignored data/)
- Seeding: `scripts/seed_chroma.py` (CONSTITUTION.md and ARCHITECTURAL_REQUIREMENTS.md)

ChromaDB was declared a **runtime** (not dev-only) dependency because it was expected to be imported by production modules in Phase 2+ (agent streams querying governance context). Placing it in the main dependency group ensures it is present in production installations.

## Amendment (2026-03-18)

**The "Agile Brain" semantic memory layer was never promoted to production.**

This ADR originated in Phase 0.8 as part of a spike exploring autonomous agent memory. The spike was not promoted to production:

- No production module in `src/synth_engine/` imports ChromaDB.
- `scripts/seed_chroma.py` exists but is a development utility, not a production entrypoint.
- The semantic memory query path described in the original decision was never wired into
  any bootstrapper, router, or module.

**Action taken (Phase 18, PR [#91](../../pull/91))**: ChromaDB was moved from the main
dependency group to the `dev` optional group in `pyproject.toml`. This was tracked as ADV-015.
The production Docker image no longer includes ChromaDB.

**Current state as of 2026-03-18**: ChromaDB is available as `poetry install --with dev` for
development and spike work. It is not a production runtime dependency and should not be treated
as one.

## Amendment (Phase 55, 2026-03-25)

**Amendment (Phase 55):** ChromaDB was fully removed from all dependency groups
in T55.5 — passlib and chromadb dead dependency elimination. The scripts
(`seed_chroma.py`, `seed_chroma_retro.py`, `init_chroma.py`) and related test
files were deleted. The `chroma_data` Docker volume was removed. This ADR is
now historical — the technology decision it records has been reversed.

## Consequences

- **Original positive:** Fully local, no cloud egress. Compatible with air-gap deployment.
- **Original negative:** ChromaDB is a heavyweight dependency (~135 transitive packages).
  Mitigated by moving to dev-only — production air-gap bundles no longer include it.
- **Phase 18 state:** The air-gap bundle does not require ChromaDB. If a future phase
  promotes semantic memory to production, this ADR should be reopened and the dependency
  moved back to the main group with a corresponding ADR amendment.
- **Phase 55 state:** ChromaDB is entirely absent from the project. No path exists to
  re-enable it without an explicit ADR decision to reintroduce it.

## Supersession Note

This ADR is marked Historical to reflect that the original decision (ChromaDB as a
production runtime dependency) was fully reversed by Phase 55. The semantic memory spike
described here is documented in `docs/archive/spikes/` and tracked as a potential future
capability in `docs/backlog/deferred-items.md`.
