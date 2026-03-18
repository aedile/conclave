# Phase 28 — Full E2E Validation

**Historical summary.** This file is a backfill record, not a planning document.
Phase 28 was executed on 2026-03-18 and merged as a single PR.

---

## PRs Merged

| PR | Title | Merged |
|----|-------|--------|
| [#122](../../pull/122) | Phase 28: Full E2E Validation — 5 production bugs fixed, 11K synthetic rows | 2026-03-18 |

---

## Key Deliverables

- **Full E2E validation run**: End-to-end pipeline validated against real Docker Compose
  infrastructure — not mocked, not subset, not local Python. Playwright screenshots
  captured for the complete job lifecycle (vault unseal → job creation → SSE progress →
  completion → download).

- **Load test**: Synthesis run producing 11,000 synthetic rows across 4 tables.
  Privacy budget confirmed: 28.33 epsilon spent from 100 allocated.

- **5 production bugs found and fixed**:
  1. Multi-stage Docker build skipped pre-installed packages (`anyio`/`sniffio`) due to
     `--ignore-installed` flag in `pip install` layer.
  2. Tini path wrong for `python:3.14-slim` image (`/sbin/tini` vs `/usr/bin/tini`).
  3. Synthesizer dependencies (`torch`/`sdv`/`opacus`) excluded from Docker image due to
     missing `--with synthesizer` flag in `poetry export`.
  4. `asyncio.run()` called inside Huey worker thread causing `MissingGreenlet` error.
     Required switching Huey workers to sync SQLAlchemy engine (ADR-0035).
  5. `np.float64` → `float` cast missing for psycopg2 serialization of epsilon values.

- **Architecture review finding**: Dual-driver DB access pattern (sync + async SQLAlchemy
  engines) introduced without ADR. ADR-0035 created before merge.

- **Updated E2E_VALIDATION.md**: Full evidence document updated with load test results,
  screenshots, and bug fix summary.

---

## Retrospective Notes

- E2E validation through the actual Docker deployment entry point is irreplaceable. All
  five production bugs were invisible to unit tests and integration tests that did not
  use the multi-stage Docker build.
- `asyncio.run()` in Huey workers is a common mistake. Huey runs in a thread pool —
  there is already a running event loop. Use sync SQLAlchemy for Huey workers.
- `np.float64` psycopg2 serialization: numpy scalar types are not automatically coerced
  to Python primitives. Always cast: `float(epsilon)`.
- Dual-driver DB access pattern (ADR-0035): any new architectural pattern introduced
  without explicit ADR is a Rule 6 violation.
