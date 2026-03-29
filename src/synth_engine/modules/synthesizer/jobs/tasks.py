"""Huey background task entry point for synthesis training.

Defines ``run_synthesis_job``, a ``@huey.task()`` that drives the full
synthesis training lifecycle: OOM pre-flight check, CTGAN training,
epoch checkpointing, synthetic data generation, and database status
updates.

Status lifecycle::

    QUEUED → TRAINING → GENERATING → COMPLETE   (success)
                                   ↘ FAILED     (OOM guardrail rejection or RuntimeError)
                                   ↘ FAILED     (BudgetExhaustionError from spend_budget)

This module is the Huey task entry point only.  Implementation is split
across two focused sub-modules (P26-T26.1):

- :mod:`synth_engine.modules.synthesizer.jobs.job_orchestration` — training loop,
  DP accounting, OOM pre-flight, and the injectable ``_run_synthesis_job_impl``.
- :mod:`synth_engine.modules.synthesizer.jobs.job_finalization` — Parquet artifact
  persistence and HMAC-SHA256 signing.

Canonical import paths::

    from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl
    from synth_engine.modules.synthesizer.jobs.job_orchestration import set_dp_wrapper_factory
    from synth_engine.modules.synthesizer.jobs.job_orchestration import set_spend_budget_fn
    from synth_engine.modules.synthesizer.jobs.job_orchestration import _OOM_FALLBACK_ROWS
    from synth_engine.modules.synthesizer.jobs.job_orchestration import _OOM_FALLBACK_COLUMNS
    from synth_engine.modules.synthesizer.jobs.job_orchestration import _get_parquet_dimensions

Internal helpers and constants that are only needed inside job_orchestration
(e.g. _OOM_OVERHEAD_FACTOR, _DEFAULT_LEDGER_ID, _generate_and_finalize)
are NOT re-exported; callers should import directly from job_orchestration.

Bootstrapper wiring note (Rule 8)
-----------------------------------
``bootstrapper/main.py`` imports this module at startup via::

    from synth_engine.modules.synthesizer.jobs import tasks as _synthesizer_tasks  # noqa: F401

This side-effect registers ``run_synthesis_job`` with the shared Huey instance
so the worker process discovers it.  The bootstrapper also calls
``set_dp_wrapper_factory(build_dp_wrapper)`` and
``set_spend_budget_fn(build_spend_budget_fn())`` to inject both factories.
Those setters live in ``job_orchestration`` but are re-exported from this
module for backward compatibility.

DP wiring (P22-T22.2)
---------------------
When ``run_synthesis_job`` is called for a job with ``enable_dp=True``, the
task reads the job's DP parameters (``max_grad_norm``, ``noise_multiplier``)
from a short-lived pre-flight session and constructs a ``DPTrainingWrapper``
via the injected ``_dp_wrapper_factory``.

Privacy budget wiring (P22-T22.3)
----------------------------------
After successful DP training and epsilon recording, ``_spend_budget_fn`` is
called to deduct the spent epsilon from the global ``PrivacyLedger``.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

Task: P4-T4.2c — Huey Task Wiring & Checkpointing
Task: P22-T22.2 — Wire DP into run_synthesis_job()
Task: P22-T22.3 — Wire spend_budget() into Synthesis Pipeline
Task: P23-T23.1 — Generation Step in Huey Task
Task: P26-T26.1 — Split Oversized Files (Refactor Only)
Task: T64.1 — Migrate internal imports away from re-export shims
"""

from __future__ import annotations

import logging

# (T70.6) Re-exports of set_dp_wrapper_factory and set_spend_budget_fn removed.
# Callers now import directly from synth_engine.modules.synthesizer.jobs.job_orchestration.
from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob
from synth_engine.modules.synthesizer.jobs.job_orchestration import (
    _run_synthesis_job_impl,
)
from synth_engine.shared.protocols import DPWrapperProtocol
from synth_engine.shared.task_queue import huey
from synth_engine.shared.telemetry import extract_trace_context, get_tracer

_logger = logging.getLogger(__name__)

__all__ = [
    "_run_synthesis_job_impl",
    "run_synthesis_job",
]


# ---------------------------------------------------------------------------
# Public Huey task
# ---------------------------------------------------------------------------


@huey.task()  # type: ignore[untyped-decorator]  # huey.task() has no type stub; unfixable without upstream py.typed marker
def run_synthesis_job(job_id: int, *, trace_carrier: dict[str, str] | None = None) -> None:
    """Huey background task: run a synthesis training job by ID.

    Reads job configuration from the ``SynthesisJob`` record identified by
    ``job_id``, runs the OOM pre-flight check, trains a CTGAN model with
    epoch checkpointing, generates synthetic data as a Parquet file, and
    updates the record status throughout.

    When the job has ``enable_dp=True``, a ``DPWrapperProtocol`` instance is
    constructed via the injected ``_dp_wrapper_factory`` (registered at startup
    by the bootstrapper via ``set_dp_wrapper_factory()`` — see ADR-0029) using
    the job's ``max_grad_norm`` and ``noise_multiplier`` fields.  The wrapper is
    then passed to ``_run_synthesis_job_impl``.  After training, the actual
    epsilon privacy budget is recorded on ``job.actual_epsilon`` and deducted
    from the global ``PrivacyLedger`` via the injected ``_spend_budget_fn``.

    This task is registered with the shared Huey instance
    (``shared/task_queue.py``) and is executed by the Huey worker process.
    It is imported in ``bootstrapper/main.py`` so the Huey worker discovers
    the task at process start (see bootstrapper wiring note in module docstring).

    Args:
        job_id: Primary key of the ``SynthesisJob`` record to process.
        trace_carrier: Optional W3C Trace Context carrier dict produced by
            ``inject_trace_context()`` at the dispatch site.  When present,
            the remote span context is extracted and a child span is created
            to continue the distributed trace across the queue boundary
            (T25.2 AC1-AC3).  Defaults to ``None`` for backward compatibility.

    Note:
        On OOM guardrail rejection or ``RuntimeError`` during training or
        generation the task sets ``status=FAILED`` and returns normally (does
        not re-raise).  The Huey worker marks the task as completed from the
        queue perspective.  The database record carries the failure reason in
        ``error_msg``.

    Note:
        On budget exhaustion (``BudgetExhaustionError`` raised by
        ``_spend_budget_fn``), the task sets ``status=FAILED`` with
        ``error_msg="Privacy budget exhausted"`` and returns normally.
        The synthesis artifact is NOT persisted.

    Raises:
        RuntimeError: If ``enable_dp=True`` but no ``_dp_wrapper_factory``
            has been registered via ``set_dp_wrapper_factory()``.
    """
    # Import module at call time so we always read the live _dp_wrapper_factory
    # value (set by bootstrapper via set_dp_wrapper_factory).  A module-level
    # import would bind to the value at import time (None) and miss later
    # injections.
    import synth_engine.modules.synthesizer.jobs.job_orchestration as _orch

    # T25.2 AC1-AC3: Re-attach the distributed trace context propagated
    # from the dispatch site. extract_trace_context handles None gracefully
    # (returns default Context) for backward compatibility with callers that
    # predate T25.2. The child span links this worker execution to the
    # originating FastAPI request span under the same trace ID.
    _task_tracer = get_tracer(__name__)
    _trace_ctx = extract_trace_context(trace_carrier)
    with _task_tracer.start_as_current_span("run_synthesis_job", context=_trace_ctx):
        from sqlmodel import Session

        from synth_engine.modules.synthesizer.training.engine import SynthesisEngine
        from synth_engine.shared.db import get_worker_engine
        from synth_engine.shared.settings import get_settings

        database_url = get_settings().database_url or "sqlite:///:memory:"
        db_engine = get_worker_engine(database_url)
        synthesis_engine = SynthesisEngine()

        # Pre-flight: read DP settings from the job record before starting impl.
        # A short-lived session is used here so the DP wrapper is constructed
        # before the main training session is opened.
        dp_wrapper: DPWrapperProtocol | None = None
        with Session(db_engine) as preflight_session:
            job = preflight_session.get(SynthesisJob, job_id)
            # If the job is not found here, dp_wrapper stays None and
            # _run_synthesis_job_impl will raise ValueError on its own lookup.
            if job is not None and job.enable_dp:
                # Read from _orch module reference (live value, not stale copy).
                if _orch._dp_wrapper_factory is None:
                    raise RuntimeError(
                        "DP training requested but no dp_wrapper_factory has been "
                        "registered. Ensure bootstrapper calls "
                        "set_dp_wrapper_factory() at startup."
                    )
                dp_wrapper = _orch._dp_wrapper_factory(
                    job.max_grad_norm,
                    job.noise_multiplier,
                )

        with Session(db_engine) as session:
            _orch._run_synthesis_job_impl(
                job_id=job_id,
                session=session,
                engine=synthesis_engine,
                dp_wrapper=dp_wrapper,
            )
