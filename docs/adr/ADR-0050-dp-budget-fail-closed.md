# ADR-0050: DP Budget Deduction — Fail Closed on Unexpected Exceptions

**Status**: Accepted
**Date**: 2026-03-23
**Deciders**: Engineering team
**Task**: T50.1 — DP Budget Deduction: Fail Closed

---

## Context

`modules/synthesizer/dp_accounting.py` is responsible for measuring the
actual epsilon cost of a DP-SGD training run and deducting it from the
privacy budget ledger.  The system's core value proposition is verifiable
(epsilon, delta)-DP guarantees: the reported epsilon must accurately reflect
the actual privacy cost of every synthesis job.

### The Bug (ADV-P38-01 Resolution)

Prior to T50.1, `_handle_dp_accounting()` contained a broad `except Exception`
block (line 140) that caught ALL unexpected exceptions from `spend_budget_fn`
and re-raised them as `AuditWriteError`:

```python
except Exception:  # ADV-P38-01: broad catch
    _logger.error("Job %d: Unexpected error from spend_budget_fn ...", job_id, exc_info=True)
    raise AuditWriteError(_BUDGET_SPEND_FAILED_MSG) from None
```

This was semantically incorrect for two reasons:

1. **Wrong exception type**: `AuditWriteError` has a precise meaning — the
   privacy budget was successfully deducted but the WORM audit trail failed to
   record it.  A `ConnectionError` *during* `spend_budget_fn` means the
   deduction status is **unknown**, not that it succeeded without an audit
   record.  Wrapping it as `AuditWriteError` was a semantic lie that would
   mislead incident responders.

2. **Compliance risk**: If `spend_budget_fn` raises due to a transient
   infrastructure failure, the epsilon ledger may or may not have been
   incremented.  Silencing the exception and surfacing a misleading error type
   makes it harder for operators to identify that the epsilon ledger is in an
   inconsistent state.  The system's (epsilon, delta)-DP guarantee requires
   that the ledger accurately reflects all privacy costs.

The advisory was logged in P38 as ADV-P38-01 with the note "broad catch —
any unexpected error from spend_budget_fn" but was not fixed at that time.
T50.1 resolves it.

---

## Decision

**Remove the broad `except Exception` block from `_handle_dp_accounting()`.**

`spend_budget_fn` is called with only one expected failure mode: the budget
is exhausted (`BudgetExhaustionError`).  That is caught specifically.
Everything else — `ConnectionError`, `RuntimeError`, `TypeError`, `OSError`,
etc. — represents an **unexpected** failure that must not be silenced.

Unexpected exceptions from `spend_budget_fn` propagate through
`_handle_dp_accounting()` to `DpAccountingStep.execute()`, which:

1. Catches them with a **broad catch-all** at the `execute()` level.
2. Logs the full traceback at `ERROR` level for operator visibility.
3. Returns `StepResult(success=False, error_msg=_BUDGET_SPEND_FAILED_MSG)`.
4. Does NOT include the raw exception message in `error_msg` to prevent
   sensitive state (connection strings, internal paths, DB credentials) from
   leaking into API responses or Huey task result records.

The job is therefore marked FAILED by the orchestration layer, alerting
operators that the epsilon ledger may need manual reconciliation.

### Why fail-closed at the `execute()` level rather than propagating further?

`DpAccountingStep.execute()` already acts as the boundary between the DP
accounting concern and the job orchestration pipeline.  The existing handlers
for `BudgetExhaustionError`, `EpsilonMeasurementError`, and `AuditWriteError`
all return `StepResult(success=False)` rather than propagating.  The
catch-all for unexpected exceptions follows this same pattern for consistency.

Propagating beyond `execute()` would reach Huey's task runner, which would
log the exception but not guarantee a clean job-status update.  Catching at
`execute()` ensures the job is always explicitly marked FAILED via
`StepResult`.

### `_BUDGET_SPEND_FAILED_MSG` sentinel

The existing constant `_BUDGET_SPEND_FAILED_MSG = "Budget spend failed with
unexpected error — manual reconciliation required"` is retained and used as
the `error_msg` in the catch-all.  It accurately describes the situation:
the budget spend failed for an unknown reason, and the operator should check
the epsilon ledger manually.

---

## Consequences

### Positive

- **Compliance**: The epsilon ledger inconsistency is surfaced immediately as
  a FAILED job rather than being silently misclassified as an audit write
  failure.
- **Operator visibility**: The full traceback (including the real exception
  type and message) is logged at ERROR level, making incident triage accurate.
- **Semantic correctness**: `AuditWriteError` is no longer raised in contexts
  where it does not apply.
- **Security**: Raw exception messages (which may contain connection strings
  or internal paths) are not exposed in API responses or task result records.
- **Testability**: The behaviour is now directly testable: injecting
  `ConnectionError` into `spend_budget_fn` produces a `ConnectionError` at
  the `_handle_dp_accounting` boundary, which `DpAccountingStep.execute()`
  converts to a `StepResult(success=False)`.

### Negative / Trade-offs

- **Operator burden**: A `ConnectionError` during budget deduction produces a
  FAILED job that may require manual epsilon ledger inspection.  This is
  intentional: the system must not silently continue when the ledger state is
  unknown.
- **No automatic retry**: This ADR does not introduce retry logic for
  transient `ConnectionError` failures.  If retry semantics are needed, they
  should be added to `spend_budget_fn` itself or via a Huey retry decorator on
  the task — not in `_handle_dp_accounting`.

---

## Alternatives Considered

### 1. Keep the broad catch, fix the exception type

Wrap unexpected exceptions as a new `BudgetSpendError` instead of
`AuditWriteError` to fix the semantic mismatch.  **Rejected**: wrapping still
silences the original exception type and makes triage harder.  The
`DpAccountingStep.execute()` catch-all achieves the same job-failure outcome
without losing the original exception information.

### 2. Propagate all the way to Huey

Let unexpected exceptions from `spend_budget_fn` propagate out of
`DpAccountingStep.execute()` and be handled by Huey's retry/error mechanism.
**Rejected**: Huey retries would re-run the entire synthesis job, potentially
double-spending the epsilon if the first call to `spend_budget_fn` partially
succeeded.  The fail-closed catch at `execute()` ensures the job is explicitly
FAILED once, not retried.

### 3. Add retry logic with idempotency key

Wrap `spend_budget_fn` with retry logic and an idempotency key so that
repeated calls are safe.  **Deferred**: this is a valid future enhancement but
is outside the scope of this security fix.  The fail-closed approach provides
a safe baseline.

---

## Enforcement

- `test_dp_budget_fail_closed.py`: attack tests verifying that `ConnectionError`,
  `RuntimeError`, and `TypeError` propagate through `_handle_dp_accounting()`
  and that `DpAccountingStep.execute()` returns `StepResult(success=False)`.
- `test_dp_accounting.py`: feature tests replacing the OLD wrapping-behaviour
  tests with NEW propagation-behaviour tests.
- `ruff` and `mypy` prevent re-introduction of bare `except Exception` blocks
  (B001/B002 rules if enabled; currently enforced via test coverage).
