# Phase 76 — Advisory Drain & Polish (Retroactive Spec)

**Tier**: 7 (Enterprise Ready)
**Goal**: Drain all remaining advisories to zero. Close ADV-P70-04, ADV-P73-01, ADV-P73-02.
**Status**: COMPLETE — merged as PR #229

**Note**: This spec was reconstructed retroactively in P78 to maintain backlog continuity.

---

## Context & Constraints

- Three advisories remained open after P75:
  - ADV-P70-04: Composite FK traversal integration test gap
  - ADV-P73-01: Test-to-production LOC ratio above 1:2.5 threshold
  - ADV-P73-02: E712 ruff rule disabled in tests for `== True` assertion pattern
- Goal was to drain advisory count to zero before Tier 8 work begins.
- `set_spend_budget_fn` double-set behavior was undocumented — needed a WARNING log.

---

## Tasks Delivered

### T76.1 — Close ADV-P70-04 (Composite FK Traversal)

- Composite FK traversal covered by unit tests (`test_subsetting_composite_fk_attack.py`)
- Integration test deferred: requires PostgreSQL fixtures with composite FK schema not
  available in CI
- Closed as covered-by-unit-tests with documented deferral

### T76.2 — Close ADV-P73-01 (Test-to-Production LOC Ratio)

- Accepted tradeoff: ratio driven by enforcement gates and fault injection infrastructure
- Waived per spec-challenger recommendation from P73
- Closed as accepted-tradeoff

### T76.3 — Close ADV-P73-02 (E712 Ruff Rule)

- Accepted incremental adoption tradeoff
- E712 rule intentionally disabled in `tests/` to support explicit `== True` assertion pattern
- Closed as accepted-tradeoff

### T76.4 — Double-Set WARNING for `set_spend_budget_fn`

- Added WARNING log when `set_spend_budget_fn` is called after already being set
- Attack/negative tests written first (`test: add negative/attack tests for set_spend_budget_fn`)
- Feature implemented (`feat: add double-set WARNING to set_spend_budget_fn`)

### T76.5 — Documentation Updates

- PROMETHEUS_MULTIPROC_DIR added to OPERATOR_MANUAL and docker-compose documentation
- RETRO_LOG updated: ADV-P70-04, ADV-P73-01, ADV-P73-02 closed

---

## Outcome

Advisory count: 3 → 0. All advisories drained. System ready for Tier 8 expansion.
