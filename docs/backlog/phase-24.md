# Phase 24 — Integration Test Repair

**Historical summary.** This file is a backfill record, not a planning document.
Phase 24 was executed on 2026-03-17 and merged as a single PR.

---

## PRs Merged

| PR | Title | Merged |
|----|-------|--------|
| [#118](../../pull/118) | fix(P24-T24.1-2): integration test repair — parameter rename, CLI wiring, test isolation | 2026-03-17 |

---

## Key Deliverables

- **Parameter rename regression fix**: The `n_rows` → `num_rows` parameter rename in the
  synthesis pipeline was not propagated through the CLI. Integration tests against real
  SDV caught the failure immediately; unit tests with mocks did not — mocks do not enforce
  keyword-argument signatures.

- **CLI wiring fix**: The `num_rows` parameter was not passed through the CLI invocation
  of the synthesis job. Fixed end-to-end from HTTP request → Huey task → SDV call.

- **Test isolation**: Added database state cleanup fixtures to prevent shared state
  between integration tests. Tests that ran in isolation passed; tests that ran in sequence
  after a prior test had failed due to stale database state.

---

## Retrospective Notes

- Mock/prod keyword argument divergence: unit mocks that patch a function do not validate
  keyword argument names. Integration tests against real SDV are required to catch renames.
- Test isolation: integration tests must clean up their database state explicitly. Relying
  on test ordering or transaction rollback is insufficient when Huey workers use separate
  connections.
