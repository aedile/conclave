# Phase 26 — Backend Production Hardening

**Historical summary.** This file is a backfill record, not a planning document.
Phase 26 was executed on 2026-03-18 and merged as a single PR.

---

## PRs Merged

| PR | Title | Merged |
|----|-------|--------|
| [#120](../../pull/120) | refactor(P26): Backend Production Hardening | 2026-03-18 |

---

## Key Deliverables

- **Router decomposition**: Split large router files into focused sub-modules.
  `jobs.py` decomposed into `job_orchestration.py` (creation, listing, start) and
  `job_lifecycle.py` (download, shred, SSE stream). Reduced individual file sizes below
  the 50-line function guideline.

- **Shared exception hierarchy**: Introduced a shared exception base class in `shared/`
  to resolve the cross-module exception import problem documented in ADR-0033. Modules
  that previously compared exception class names via `str` comparison now raise from the
  shared hierarchy. ADR-0034 documents this resolution.

- **Protocol typing for DI callbacks**: Added `Protocol` type definitions in `shared/`
  for all dependency injection callbacks injected by the bootstrapper into modules.
  Eliminates `Any` typing at DI injection sites.

- **HTTP round-trip tests**: Added integration tests for all production error paths —
  budget exhaustion, artifact signing failure, SHREDDED job download attempt, and
  invalid job state transitions.

- **`str(exc)` audit**: Replaced raw `str(exc)` at all API-visible error message sites
  with `safe_error_msg()` helper that sanitizes before writing to `job.error_msg`.

- **Squash-merge Constitutional fix**: Discovered that CLAUDE.md Rules 12 and 13
  specified `gh pr merge --squash`, violating Constitution Priority 3 (TDD commit trail
  must be preserved). Both rules updated to `gh pr merge --merge`.

---

## Retrospective Notes

- Rules added to CLAUDE.md must be audited against all Constitutional priorities before
  adoption. The squash-merge conflict existed undetected for five phases (21–25).
- `str(exc)` in API error messages is a recurring PII risk. A project-wide audit is the
  correct response when a pattern recurs across multiple phases.
- Dual-driver DB access pattern (sync SQLAlchemy in Huey workers, async in FastAPI) was
  introduced without an ADR. Architecture reviewer required ADR-0035 to be created.
