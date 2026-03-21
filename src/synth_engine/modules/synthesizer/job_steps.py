"""Re-exports for the step-based synthesis orchestration (T35.1, ADR-0038).

All step classes, value objects, and the protocol are defined in
``job_orchestration`` (where they were introduced to preserve the
existing test-suite patch paths for ``get_audit_logger``,
``_write_parquet_with_signing``, and ``check_memory_feasibility``).

This module re-exports everything so new code and new tests can import from
either ``job_steps`` or ``job_orchestration`` without caring about the
implementation location.

Patch-path note
---------------
Existing tests patch names in ``job_orchestration``::

    patch("synth_engine.modules.synthesizer.job_orchestration.get_audit_logger")
    patch("synth_engine.modules.synthesizer.job_orchestration._write_parquet_with_signing")
    patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility")
    patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn")

New tests (``test_job_steps.py``) that exercise steps in isolation should
patch names in ``job_orchestration`` as well, OR use the re-exports below
with the understanding that they refer to the same objects.

``_spend_budget_fn`` is intentionally NOT re-exported here.  It is a
module-level ``None`` sentinel in ``job_orchestration`` that is swapped at
runtime by ``set_spend_budget_fn()``.  Re-exporting it would bind the
``None`` value at import time, creating a structural trap: callers that
imported from this module would hold a stale reference rather than the live
one.  Always patch / read ``_spend_budget_fn`` from ``job_orchestration``
directly.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

Task: T35.1 — Decompose _run_synthesis_job_impl Into Discrete Job Steps
ADR: ADR-0038 — Synthesis Orchestration Step Decomposition
Task: T43.1 — Extract dp_accounting.py (architecture review fix)
"""

from __future__ import annotations

# Re-export everything from job_orchestration so importers of job_steps get
# the canonical objects (with the same module identity as job_orchestration).
# This means patching job_steps.X and patching job_orchestration.X both
# affect the same underlying binding.
#
# NOTE: _spend_budget_fn is deliberately excluded — see module docstring.
from synth_engine.modules.synthesizer.job_orchestration import (
    DpAccountingStep,
    GenerationStep,
    JobContext,
    OomCheckStep,
    StepResult,
    SynthesisJobStep,
    TrainingStep,
    _get_parquet_dimensions,
    _handle_dp_accounting,
    check_memory_feasibility,
    get_audit_logger,
)

__all__ = [
    "DpAccountingStep",
    "GenerationStep",
    "JobContext",
    "OomCheckStep",
    "StepResult",
    "SynthesisJobStep",
    "TrainingStep",
    "_get_parquet_dimensions",
    "_handle_dp_accounting",
    "check_memory_feasibility",
    "get_audit_logger",
]
