"""DP accounting: epsilon recording and privacy budget deduction (T43.1).

Extracted from ``job_orchestration.py`` to give the DP-accounting concern its
own focused module.  All logic that was previously embedded in
``job_orchestration._handle_dp_accounting()`` and ``DpAccountingStep`` now
lives here.

Patch-path compatibility
------------------------
Existing tests patch names in ``job_orchestration``::

    patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn")
    patch("synth_engine.modules.synthesizer.job_orchestration.get_audit_logger")

``_handle_dp_accounting`` preserves this by reading both names from
``job_orchestration``'s live namespace at call time (lazy module reference).
This is safe because the module is already in ``sys.modules`` when these
functions are called.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

Task: T43.1 — Extract dp_accounting.py from job_orchestration.py
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from synth_engine.shared.exceptions import (
    AuditWriteError,
    BudgetExhaustionError,
    EpsilonMeasurementError,
)

if TYPE_CHECKING:
    from synth_engine.modules.synthesizer.job_models import SynthesisJob
    from synth_engine.modules.synthesizer.job_orchestration import JobContext, StepResult
    from synth_engine.shared.protocols import DPWrapperProtocol

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DP_EPSILON_DELTA: float = 1e-5
_DEFAULT_LEDGER_ID: int = 1

_AUDIT_RECONCILIATION_MSG: str = (
    "Budget deducted but audit trail write failed — manual reconciliation required"
)


# ---------------------------------------------------------------------------
# Core DP accounting function
# ---------------------------------------------------------------------------


def _handle_dp_accounting(
    job: SynthesisJob,
    dp_wrapper: DPWrapperProtocol,
    job_id: int,
) -> None:
    """Record actual epsilon and optionally spend privacy budget (steps 5 + 5b).

    Called by ``DpAccountingStep.execute()``.

    Reads ``_spend_budget_fn`` and ``get_audit_logger`` from
    ``job_orchestration``'s live namespace so that existing test patches on
    ``job_orchestration._spend_budget_fn`` and
    ``job_orchestration.get_audit_logger`` remain effective (T43.1).

    On ``BudgetExhaustionError``, this function re-raises so that
    ``DpAccountingStep`` can return a failure ``StepResult`` without touching
    ``job.status`` (AC4: the orchestrator is the sole status owner).

    On ``AuditWriteError``, this function re-raises so that
    ``DpAccountingStep`` marks the job FAILED (T38.1: Constitution Priority 0 —
    every privacy budget spend MUST have a WORM audit entry).

    Args:
        job: The ``SynthesisJob`` record being updated (mutated in place).
        dp_wrapper: The DP training wrapper.
        job_id: Job primary key (for logging and audit details).

    Raises:
        BudgetExhaustionError: Re-raised when the privacy budget is exhausted.
        EpsilonMeasurementError: Raised when dp_wrapper.epsilon_spent() fails —
            if we cannot measure the privacy cost, the job must be marked FAILED
            (Constitution Priority 0: security over availability).
        AuditWriteError: Raised when the WORM audit write fails after a
            successful budget deduction — the operator must reconcile manually.
    """
    # Late import: avoids circular dependency with job_orchestration and
    # ensures we read the live (potentially patched) module-level bindings.
    import synth_engine.modules.synthesizer.job_orchestration as _orch

    try:
        actual_eps = dp_wrapper.epsilon_spent(delta=_DP_EPSILON_DELTA)
        job.actual_epsilon = actual_eps
        _logger.info("Job %d: DP complete, actual_epsilon=%.4f.", job_id, actual_eps)
    except Exception as exc:
        _logger.error(
            "Job %d: epsilon_spent() raised — privacy budget cannot be verified.",
            job_id,
            exc_info=True,
        )
        raise EpsilonMeasurementError(
            "DP epsilon measurement failed — privacy budget cannot be verified"
        ) from exc

    spend_budget_fn = _orch._spend_budget_fn
    if spend_budget_fn is None or job.actual_epsilon is None:
        return

    budget_spent = False
    try:
        spend_budget_fn(
            amount=job.actual_epsilon,
            job_id=job_id,
            ledger_id=_DEFAULT_LEDGER_ID,
            note=f"DP synthesis job {job_id}",
        )
        budget_spent = True
        _logger.info(
            "Job %d: budget deducted (epsilon=%.4f, ledger_id=%d).",
            job_id,
            job.actual_epsilon,
            _DEFAULT_LEDGER_ID,
        )
    except BudgetExhaustionError:
        _logger.error("Job %d: Privacy budget exhausted — marking FAILED.", job_id)
        raise  # Re-raise: orchestrator (not step) sets job.status (AC4).
    except Exception as exc:  # ADV-P38-01: broad catch — any unexpected error from spend_budget_fn
        # ADV-P38-01: Non-BudgetExhaustionError exceptions (e.g. ConnectionError, RuntimeError)
        # from spend_budget_fn must not propagate uncaught. We log at ERROR and surface as
        # AuditWriteError so DpAccountingStep marks the job FAILED with a meaningful message.
        _logger.error(
            "Job %d: Unexpected error from spend_budget_fn — budget spend status unknown.",
            job_id,
            exc_info=True,
        )
        raise AuditWriteError(
            f"Budget spend failed with unexpected error: {exc!s} — manual reconciliation required"
        ) from exc

    if budget_spent:
        try:
            audit = _orch.get_audit_logger()
            audit.log_event(
                event_type="PRIVACY_BUDGET_SPEND",
                actor="system/huey-worker",
                resource=f"privacy_ledger/{_DEFAULT_LEDGER_ID}",
                action="spend_budget",
                details={"job_id": str(job_id), "epsilon_spent": str(job.actual_epsilon)},
            )
        except Exception as exc:
            _logger.error(
                "Job %d: Audit log failed after budget deduction — reconciliation required.",
                job_id,
                exc_info=True,
            )
            raise AuditWriteError(_AUDIT_RECONCILIATION_MSG) from exc


# ---------------------------------------------------------------------------
# DpAccountingStep — the step class
# ---------------------------------------------------------------------------


class DpAccountingStep:
    """DP epsilon recording and optional privacy budget deduction.

    Delegates to ``_handle_dp_accounting()`` which reads ``_spend_budget_fn``
    and ``get_audit_logger`` from ``job_orchestration``'s live namespace,
    keeping existing test patch paths valid (T43.1).
    """

    def execute(self, ctx: JobContext) -> StepResult:
        """Record DP epsilon and optionally spend budget.

        Args:
            ctx: Shared job execution context.

        Returns:
            Success, or failure with ``error_msg="Privacy budget exhausted"`` on
            budget exhaustion, or ``error_msg="DP epsilon measurement failed — privacy
            budget cannot be verified"`` when ``epsilon_spent()`` raises, or
            ``error_msg="Budget deducted but audit trail write failed — manual
            reconciliation required"`` when the WORM audit write fails (T38.1).
        """
        # Late import avoids circular dependency; job_orchestration is already
        # in sys.modules by the time execute() is called.
        from synth_engine.modules.synthesizer.job_orchestration import StepResult

        if ctx.dp_wrapper is None:
            return StepResult(success=True)

        try:
            _handle_dp_accounting(
                job=ctx.job,
                dp_wrapper=ctx.dp_wrapper,
                job_id=ctx.job.id,  # type: ignore[arg-type]  # job.id guaranteed non-None: session.get() returned a live record
            )
        except BudgetExhaustionError:
            return StepResult(success=False, error_msg="Privacy budget exhausted")
        except EpsilonMeasurementError:
            _logger.error(
                "Job %d: DpAccountingStep returning failure — epsilon measurement raised.",
                ctx.job.id,
            )
            return StepResult(
                success=False,
                error_msg="DP epsilon measurement failed — privacy budget cannot be verified",
            )
        except AuditWriteError:
            _logger.error(
                "Job %d: DpAccountingStep returning failure — WORM audit write failed.",
                ctx.job.id,
            )
            return StepResult(
                success=False,
                error_msg=_AUDIT_RECONCILIATION_MSG,
            )
        return StepResult(success=True)
