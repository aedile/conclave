# Phase 21 — CLI Masking Fix & E2E Smoke Tests

**Historical summary.** This file is a backfill record, not a planning document.
Phase 21 was executed on 2026-03-16 and merged as part of the March 16 development sprint.

---

## PRs Merged

| PR | Title | Merged |
|----|-------|--------|
| [#103](../../pull/103) | docs: E2E validation evidence and UI screenshots in README | 2026-03-16 |
| [#104](../../pull/104) | fix(P21-T21.2): Split mask_name into per-column masking functions | 2026-03-16 |
| [#105](../../pull/105) | feat(P21-T21.3): Automated E2E smoke test for CLI subset+mask pipeline | 2026-03-16 |

---

## Key Deliverables

- **Per-column masking fix (T21.2)**: `mask_name` was not correctly split into
  `first_name`/`last_name` column-specific masking functions. The `split_mask_name`
  function was introduced and wired into the masking engine so each column receives
  the correct masking function. Referential integrity verified post-fix.

- **Automated E2E smoke test (T21.3)**: Added an automated end-to-end smoke test for
  the CLI `conclave-subset` + mask pipeline. Test exercises the full path from a source
  PostgreSQL schema through FK traversal, masking, and egress to the target database.
  Established the vacuous-truth guard pattern (row-count precondition assertions) to
  prevent silent pass on empty result sets.

- **README E2E evidence (T21.1)**: Updated README with masking evidence screenshots and
  FK traversal row counts from a live Docker run (50 customers → 116 orders → 396 order
  items + 116 payments; zero orphan rows).

---

## Retrospective Notes

- The per-column masking bug had existed since Phase 3. Unit tests did not catch it because
  they exercised the masking engine directly with pre-split column names. The CLI-integrated
  smoke test caught it immediately.
- Vacuous-truth trap: `for row in empty_result: assert ...` silently passes. Pattern
  identified here; row-count preconditions became standard in Phase 22+.
- Review finding: README masking evidence must show correct per-column output — original
  screenshots showed full names in first_name/last_name columns. Fixed before merge.
