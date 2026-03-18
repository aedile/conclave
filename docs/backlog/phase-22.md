# Phase 22 — DP Pipeline End-to-End Integration

**Historical summary.** This file is a backfill record, not a planning document.
Phase 22 was executed on 2026-03-17 and merged across six PRs.

---

## PRs Merged

| PR | Title | Merged |
|----|-------|--------|
| [#106](../../pull/106) | feat(P22-T22.1): Add DP parameters to SynthesisJob and job API | 2026-03-17 |
| [#107](../../pull/107) | feat(P22-T22.2): wire DP wrapper into run_synthesis_job | 2026-03-17 |
| [#108](../../pull/108) | feat(P22-T22.3): wire spend_budget() into synthesis pipeline | 2026-03-17 |
| [#109](../../pull/109) | test(P22-T22.5): bump property-based test max_examples | 2026-03-17 |
| [#110](../../pull/110) | feat(P22-T22.4): Budget Management API endpoints | 2026-03-17 |
| [#111](../../pull/111) | test(P22-T22.6): Integration E2E full DP synthesis pipeline | 2026-03-17 |
| [#112](../../pull/112) | fix(P22-T22.6): QA findings — dead code, vacuous-truth guard, docstring accuracy | 2026-03-17 |

---

## Key Deliverables

- **DP parameters on SynthesisJob (T22.1)**: Added `epsilon`, `delta`, `max_grad_norm`,
  `num_epochs` to the `SynthesisJob` model and the job creation API. Schema migration
  included.

- **DP wrapper wiring (T22.2)**: Wired `DPTrainingWrapper` into `run_synthesis_job` Huey
  task. Fixed an architecture violation: initial implementation used `importlib.import_module`
  to circumvent import-linter boundary enforcement. Architecture reviewer caught and required
  replacement with DI factory injection.

- **Budget enforcement (T22.3)**: Wired `spend_budget()` into the synthesis pipeline.
  Jobs that would exceed the configured epsilon/delta budget are blocked before training
  starts. Fixed URL double-substitution bug in `build_spend_budget_fn()` where
  `str.replace()` corrupted URLs already containing the async driver prefix.

- **Budget Management API (T22.4)**: `GET /privacy/budget` and
  `POST /privacy/budget/refresh` endpoints. Fixed PII risk: `_logger.info` had interpolated
  the `actor` field from the `X-Operator-Id` request header into logs.

- **E2E integration test (T22.6)**: Full DP synthesis pipeline integration test against
  real PostgreSQL and real SDV/CTGAN. Added vacuous-truth guards (row-count precondition
  assertions).

---

## Retrospective Notes

- `importlib.import_module` circumvention pattern: if a module cannot import another due
  to import-linter boundaries, `importlib` is not an acceptable workaround. Fix: DI injection.
- `str(exc)` in API-visible error messages is a recurring PII risk; sanitize via a helper.
- `actor` field from request headers must not be interpolated into structured logs.
