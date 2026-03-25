# ADR-0033: Cross-Module Exception Detection by Class Name

> **Amendment (Phase 56):** File paths updated to reflect synthesizer sub-package decomposition.

**Status:** Superseded
**Date:** 2026-03-17
**Deciders:** PM, Architecture Reviewer
**Task:** P23-T23.1 — Generation Step in Huey Task (review finding F6)

**Superseded by:** P26-T26.2, which moved `BudgetExhaustionError` to
`shared/exceptions.py`, enabling direct class-based `except` clauses in
`modules/synthesizer/jobs/job_orchestration.py`.  The duck-typing pattern
documented here is no longer in use and must not be re-introduced.

---

## Context

`BudgetExhaustionError` is defined in `modules/privacy/dp_engine.py`.  The
synthesis pipeline in `modules/synthesizer/jobs/tasks.py` must detect when the
privacy budget is exhausted after calling `_spend_budget_fn`.

Import-linter enforces strict module independence: `modules/synthesizer` is
forbidden from importing anything in `modules/privacy`.  This prohibition
covers both runtime imports and `TYPE_CHECKING`-only imports, because any
import creates a coupling that could allow future code to drift across the
boundary.

Three options were evaluated:

1. **Direct import under `TYPE_CHECKING`** — import-linter still rejects this
   because the boundary contract is logical, not just runtime.
2. **Move `BudgetExhaustionError` to `shared/`** — a shared exception class
   would be correct if two or more modules needed to *raise and catch* the same
   type.  In this case only `modules/privacy` raises it and only
   `modules/synthesizer` needs to detect it.  Moving it to `shared/` couples
   `shared/` to a privacy-domain concept unnecessarily.
3. **Duck-typing by class name** — detect the exception using
   `"BudgetExhaustion" in type(exc).__name__` without any import.

---

## Decision

Use duck-typing exception name matching in `modules/synthesizer/jobs/tasks.py` to
detect `BudgetExhaustionError` raised by `_spend_budget_fn` at the
`modules/privacy` boundary:

```python
except Exception as exc:
    if "BudgetExhaustion" in type(exc).__name__:
        # handle budget exhaustion
    raise  # re-raise all other exceptions
```

This pattern is used only when import-linter enforces a hard cross-module
boundary that prevents type-based `isinstance` checks.  It is not a general
exception-handling pattern for this codebase.

---

## Consequences

**Positive:**

- Zero import coupling between `modules/synthesizer` and `modules/privacy`.
- No new shared/ types required for a single detection point.
- The pattern is explicit and easy to grep for.
- Import-linter contract remains clean and enforced.

**Negative / Constraints:**

- If `BudgetExhaustionError` is renamed in `modules/privacy`, the detection
  silently breaks — the exception would be re-raised instead of handled, which
  is a visible failure (job crashes rather than being marked FAILED).
- The pattern must only be used at documented cross-boundary callsites.  It
  must NOT be adopted as a general exception-handling idiom elsewhere.

**Mitigations:**

- The matching substring `"BudgetExhaustion"` is documented here and at the
  callsite in `tasks.py`.
- An integration test exercises the full `spend_budget → BudgetExhaustionError
  → job.status == FAILED` path against a real or near-real privacy module
  instance (see `tests/unit/test_synthesizer_tasks.py`,
  `TestSpendBudgetWiring.test_budget_exhaustion_marks_job_failed`).
- If `BudgetExhaustionError` is renamed, the detection will re-raise instead of
  silently ignoring — visible as a test failure and a Huey task exception.

---

## Alternatives Considered

**Move `BudgetExhaustionError` to `shared/`**

Rejected because `shared/` should contain genuinely cross-cutting concerns.
`BudgetExhaustionError` is a domain concept belonging to `modules/privacy`.
Placing it in `shared/` to satisfy one boundary detection would pollute the
shared namespace and invite future misuse.

**Define a parallel exception in `modules/synthesizer`**

Rejected because it creates two exception types for the same semantic condition
with no enforcement that they stay synchronized.

**Suppress all exceptions from `_spend_budget_fn`**

Rejected because non-budget exceptions (e.g., `ConnectionError`) must
propagate so Huey can record them and alert operators.

---

## References

- `src/synth_engine/modules/synthesizer/jobs/tasks.py` — callsite with inline comment
- `src/synth_engine/modules/privacy/dp_engine.py` — source of `BudgetExhaustionError`
- ADR-0001: Modular Monolith Topology (import-linter enforcement)
- ADR-0029: Architectural Requirements Gap Analysis
- CLAUDE.md: File Placement rules — neutral value objects in `shared/`
