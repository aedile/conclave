# ADR-0038: Synthesis Orchestration Step Decomposition

**Status:** Accepted
**Date:** 2026-03-18
**Deciders:** PM, Architecture Reviewer
**Task:** T35.1 — Decompose `_run_synthesis_job_impl` Into Discrete Job Steps

---

## Context

The synthesis job lifecycle driver `_run_synthesis_job_impl` in
`modules/synthesizer/job_orchestration.py` had grown to 232 lines — a
textbook god-function spanning OOM pre-flight, epoch-chunked training,
differential-privacy accounting, and Parquet generation in a single
linear procedure.

This violated several CLAUDE.md quality constraints (max ~50 line functions),
made each concern untestable in isolation, and centralized `job.status`
mutation across twelve separate sites within the function body.

---

## Decision

Replace `_run_synthesis_job_impl` with a step-based orchestrator under 50
non-blank/non-comment lines.  All business concerns are delegated to four
discrete, independently-testable step classes:

- **`OomCheckStep`** — OOM pre-flight feasibility check.
- **`TrainingStep`** — Epoch-chunked CTGAN training with checkpointing.
- **`DpAccountingStep`** — DP epsilon recording and privacy budget deduction.
- **`GenerationStep`** — Synthetic data generation and Parquet persistence.

### Key design choices

**`JobContext` dataclass** — a mutable shared-state carrier passed to every
step.  Fields: `job`, `session`, `engine`, `dp_wrapper`, `checkpoint_dir`,
`last_artifact`, `last_ckpt_path`.

**`StepResult` dataclass** — a value object `(success: bool, error_msg: str | None)`
returned by every step.  Steps never set `job.status` directly.

**`SynthesisJobStep` protocol** — structural `Protocol` with a single
`execute(ctx: JobContext) -> StepResult` method.  Satisfiable by any of the
four concrete step classes.

**Orchestrator is the sole `job.status` owner (AC4)** — the step loop reads
`result.success` and sets `job.status` (`TRAINING`, `GENERATING`, `FAILED`,
`COMPLETE`) in exactly one place.  Steps signal failure via `StepResult`,
never by mutating `job.status`.

**`_handle_dp_accounting` re-raises `BudgetExhaustionError` and raises `EpsilonMeasurementError`** — the former
pattern of setting `job.status = "FAILED"` inside `_handle_dp_accounting`
was removed.  The function now re-raises `BudgetExhaustionError`, and raises
`EpsilonMeasurementError` when `dp_wrapper.epsilon_spent()` itself raises.
`DpAccountingStep.execute()` catches both and returns a failure `StepResult`.
This preserves AC4 (status ownership) while retaining all budget-exhaustion and
epsilon-measurement-failure behavior (T37.1, ADV-P35-01).

**`artifact_path` deferred past DP accounting** — `TrainingStep` no longer
writes `job.artifact_path`.  Instead it stores the checkpoint path on
`ctx.last_ckpt_path`.  The orchestrator writes `job.artifact_path` only
after `DpAccountingStep` succeeds, preserving the invariant tested by
`test_budget_exhaustion_artifact_not_persisted`.

**Patch-path compatibility** — all step implementations and helpers live in
`job_orchestration.py` so existing tests that patch
`job_orchestration.check_memory_feasibility`,
`job_orchestration._spend_budget_fn`,
`job_orchestration.get_audit_logger`, and
`job_orchestration._write_parquet_with_signing` continue to work without
modification.  `job_steps.py` remains a thin re-export façade.

**Line-budget helpers** — three private helpers keep the orchestrator under
50 lines without hiding logic: `_commit_job(job, session)` (adds + commits),
`_run_oom_preflight(job, session) -> bool` (OOM check + commit FAILED),
`_build_ctx(...) -> JobContext` (constructs the shared context).

---

## Consequences

**Positive:**
- `_run_synthesis_job_impl` is 47 non-blank/non-comment lines (AC1 satisfied).
- `DpAccountingStep.execute()` catches both `BudgetExhaustionError` and
  `EpsilonMeasurementError` (T37.1) — jobs where `epsilon_spent()` raises are
  marked FAILED rather than silently completing with an unverified privacy cost.
- Each step is independently unit-testable with a mock `JobContext` (AC2 satisfied).
- `job.status` transitions are centralized in the orchestrator (AC4 satisfied).
- 31 new unit tests in `tests/unit/test_job_steps.py` cover all step isolation,
  the `JobContext`/`StepResult` value objects, and the orchestrator size limit.
- All 106 pre-existing tests in `tests/unit/test_synthesizer_tasks.py` continue
  to pass with no modification.
- Combined coverage of `job_orchestration.py` exceeds 95%.

**Negative / Constraints:**
- Three new private helpers (`_commit_job`, `_run_oom_preflight`, `_build_ctx`)
  add surface area to the module.  These are intentionally minimal and do not
  encapsulate business logic — they exist solely to satisfy the 50-line limit.
- Step classes are defined in `job_orchestration.py` (not `job_steps.py`) for
  patch-path compatibility.  New code importing step classes should prefer
  `job_steps` as the stable public path.

---

## Alternatives Considered

**Move step classes to `job_steps.py`** — would allow `job_steps.xxx` patch
paths in new tests but would require moving `_handle_dp_accounting` there too,
which would break existing tests that patch `job_orchestration._spend_budget_fn`
and `job_orchestration.get_audit_logger`.  The current layout avoids all
breakage with zero test modifications to the pre-existing suite.

**Async step pipeline** — rejected; Huey workers are synchronous and the
current `asyncio.run()` wrapping in `set_spend_budget_fn` is sufficient.

---

## References

- `src/synth_engine/modules/synthesizer/job_orchestration.py` — step classes + orchestrator
- `src/synth_engine/modules/synthesizer/job_steps.py` — re-export façade
- `tests/unit/test_job_steps.py` — 31 new step isolation tests
- ADR-0029 — DI injection pattern (`set_dp_wrapper_factory`, `set_spend_budget_fn`)
- ADR-0033 — Duck-typing replaced by typed `BudgetExhaustionError` catch
- ADR-0037 — Exception hierarchy consolidation (`shared/exceptions.py`)
