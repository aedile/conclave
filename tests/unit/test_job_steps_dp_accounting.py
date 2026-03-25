"""Unit tests for DP accounting and generation step isolation (T35.1).

Tests cover:
- DpAccountingStep isolation (normal DP, skipped, budget exhaustion).
- DpAccountingStepEpsilonFailure (epsilon_spent raises).
- GenerationStepIsolation.

Split from test_job_steps.py (T56.3).

Task: T35.1 — Decompose _run_synthesis_job_impl Into Discrete Job Steps
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from tests.unit.helpers_synthesizer import _make_synthesis_job


def _make_job_context(job: Any = None, **kwargs: Any) -> Any:
    """Build a JobContext with sensible defaults for unit tests."""
    from synth_engine.modules.synthesizer.job_steps import JobContext

    if job is None:
        job = _make_synthesis_job()

    defaults: dict[str, Any] = {
        "job": job,
        "session": MagicMock(),
        "engine": MagicMock(),
        "dp_wrapper": None,
        "checkpoint_dir": "/tmp/ckpt",
    }
    defaults.update(kwargs)
    return JobContext(**defaults)


# ---------------------------------------------------------------------------
# AC3: JobContext dataclass tests
# ---------------------------------------------------------------------------


class TestDpAccountingStepIsolation:
    """DpAccountingStep must be independently testable with a mock JobContext."""

    def test_dp_accounting_step_returns_success_on_normal_dp(self) -> None:
        """DpAccountingStep.execute() must return StepResult(success=True) for normal DP."""
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep, StepResult

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 2.5

        job = _make_synthesis_job(id=1)
        ctx = _make_job_context(job=job, dp_wrapper=mock_wrapper)
        step = DpAccountingStep()

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            result = step.execute(ctx)

        assert isinstance(result, StepResult)
        assert result.success is True

    def test_dp_accounting_step_skipped_when_no_dp_wrapper(self) -> None:
        """DpAccountingStep.execute() must return success immediately when dp_wrapper is None."""
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep

        job = _make_synthesis_job(id=1)
        ctx = _make_job_context(job=job, dp_wrapper=None)
        step = DpAccountingStep()

        result = step.execute(ctx)

        assert result.success is True

    def test_dp_accounting_step_returns_failure_on_budget_exhaustion(self) -> None:
        """DpAccountingStep.execute() must return StepResult(success=False) on exhaustion."""
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep
        from synth_engine.shared.exceptions import BudgetExhaustionError

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 999.0

        mock_budget_fn = MagicMock(
            side_effect=BudgetExhaustionError(
                requested_epsilon=Decimal("0.5"),
                total_spent=Decimal("0.9"),
                total_allocated=Decimal("1.0"),
            )
        )

        job = _make_synthesis_job(id=1)
        ctx = _make_job_context(job=job, dp_wrapper=mock_wrapper)
        step = DpAccountingStep()

        with (
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            result = step.execute(ctx)

        assert result.success is False
        assert result.error_msg == "Privacy budget exhausted"

    def test_dp_accounting_step_does_not_set_job_status(self) -> None:
        """DpAccountingStep must NOT mutate job.status — the orchestrator owns status.

        AC4: Job status transitions centralized — only the orchestrator sets job.status.
        """
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep
        from synth_engine.shared.exceptions import BudgetExhaustionError

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 999.0
        mock_budget_fn = MagicMock(
            side_effect=BudgetExhaustionError(
                requested_epsilon=Decimal("0.5"),
                total_spent=Decimal("0.9"),
                total_allocated=Decimal("1.0"),
            )
        )

        job = _make_synthesis_job(id=1, status="TRAINING")
        ctx = _make_job_context(job=job, dp_wrapper=mock_wrapper)
        step = DpAccountingStep()

        with (
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            step.execute(ctx)

        # The step must NOT set job.status — the orchestrator does that
        assert job.status == "TRAINING", (
            f"DpAccountingStep must not set job.status; expected TRAINING, got {job.status!r}"
        )


# ---------------------------------------------------------------------------
# T37.1: DpAccountingStep epsilon measurement failure tests
# ---------------------------------------------------------------------------


class TestDpAccountingStepEpsilonFailure:
    """DpAccountingStep must treat epsilon_spent() failures as fatal (T37.1, ADV-P35-01).

    AC1: If dp_wrapper.epsilon_spent() raises, job is marked FAILED.
    AC3: WARNING-level log distinguishes epsilon read failure from no-DP case.
    AC4: New test — job status is FAILED when epsilon_spent() raises RuntimeError.

    Note: There is no AC2 audit event on the epsilon-failure path.
    The audit trail records the *successful* budget spend (PRIVACY_BUDGET_SPEND),
    not the failure.  The failure is surfaced via the step result and job.error_msg.
    """

    def test_dp_accounting_step_returns_failure_when_epsilon_spent_raises(self) -> None:
        """DpAccountingStep.execute() must return StepResult(success=False) when epsilon_spent
        raises.

        AC4 (T37.1): When dp_wrapper.epsilon_spent() raises RuntimeError, the step
        must return a failure result — not silently continue with actual_epsilon=None.
        """
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep, StepResult

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.side_effect = RuntimeError("DP engine crashed")

        job = _make_synthesis_job(id=1)
        ctx = _make_job_context(job=job, dp_wrapper=mock_wrapper)
        step = DpAccountingStep()

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            result = step.execute(ctx)

        assert isinstance(result, StepResult)
        assert result.success is False
        assert result.error_msg is not None
        assert "privacy budget" in result.error_msg.lower() or "epsilon" in result.error_msg.lower()

    def test_dp_accounting_step_error_msg_references_budget_verification(self) -> None:
        """DpAccountingStep failure message must reference privacy budget verification (AC1)."""
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.side_effect = RuntimeError("internal DP failure")

        job = _make_synthesis_job(id=1)
        ctx = _make_job_context(job=job, dp_wrapper=mock_wrapper)

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            result = DpAccountingStep().execute(ctx)

        assert (
            result.error_msg == "DP epsilon measurement failed — privacy budget cannot be verified"
        )

    def test_job_actual_epsilon_remains_none_when_epsilon_spent_raises(self) -> None:
        """job.actual_epsilon must remain None when epsilon_spent() raises (not silently COMPLETE).

        This is a guard: the bug was that actual_epsilon stayed None and the job
        silently completed. After the fix, the job must instead FAIL.
        """
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.side_effect = RuntimeError("crash")

        job = _make_synthesis_job(id=1)
        assert job.actual_epsilon is None  # pre-condition

        ctx = _make_job_context(job=job, dp_wrapper=mock_wrapper)

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            result = DpAccountingStep().execute(ctx)

        # epsilon stays None AND the step reports failure (not silent success)
        assert job.actual_epsilon is None
        assert result.success is False

    def test_orchestrator_marks_job_failed_when_epsilon_spent_raises(self) -> None:
        """_run_synthesis_job_impl must set job.status=FAILED when epsilon_spent() raises.

        AC1 (T37.1): End-to-end orchestrator test — job must not reach COMPLETE
        if epsilon measurement fails.
        """
        from synth_engine.modules.synthesizer.job_orchestration import _run_synthesis_job_impl

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.side_effect = RuntimeError("DP accounting unavailable")

        job = _make_synthesis_job(id=1, total_epochs=5, checkpoint_every_n=5)
        mock_session = MagicMock()
        mock_session.get.return_value = job

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._get_parquet_dimensions",
                return_value=(100, 10),
            ),
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=mock_wrapper,
            )

        assert job.status == "FAILED", (
            f"Job must be FAILED when epsilon_spent() raises; got {job.status!r}"
        )
        assert job.error_msg is not None
        assert "epsilon" in job.error_msg.lower() or "privacy budget" in job.error_msg.lower()

    def test_dp_accounting_step_does_not_set_job_status_on_epsilon_failure(self) -> None:
        """DpAccountingStep must NOT set job.status on epsilon failure — orchestrator owns status.

        AC4 (T35.1): status ownership remains with the orchestrator, even in the new failure path.
        """
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.side_effect = RuntimeError("crash")

        job = _make_synthesis_job(id=1, status="TRAINING")
        ctx = _make_job_context(job=job, dp_wrapper=mock_wrapper)

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            DpAccountingStep().execute(ctx)

        assert job.status == "TRAINING", (
            f"DpAccountingStep must not set job.status; expected TRAINING, got {job.status!r}"
        )


# ---------------------------------------------------------------------------
# AC2: GenerationStep isolation tests
# ---------------------------------------------------------------------------


class TestGenerationStepIsolation:
    """GenerationStep must be independently testable with a mock JobContext."""

    def test_generation_step_returns_success_on_normal_generation(self) -> None:
        """GenerationStep.execute() must return StepResult(success=True) on success."""
        import tempfile

        import pandas as pd

        from synth_engine.modules.synthesizer.job_steps import GenerationStep, StepResult

        mock_engine = MagicMock()
        mock_df = pd.DataFrame({"a": [1, 2, 3]})
        mock_engine.generate.return_value = mock_df

        mock_artifact = MagicMock()
        job = _make_synthesis_job(id=1, num_rows=3)

        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _make_job_context(
                job=job,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )
            ctx.last_artifact = mock_artifact

            step = GenerationStep()

            with patch(
                "synth_engine.modules.synthesizer.job_orchestration._write_parquet_with_signing"
            ):
                result = step.execute(ctx)

        assert isinstance(result, StepResult)
        assert result.success is True

    def test_generation_step_returns_failure_on_runtime_error(self) -> None:
        """GenerationStep.execute() must return StepResult(success=False) on RuntimeError."""
        from synth_engine.modules.synthesizer.job_steps import GenerationStep

        mock_engine = MagicMock()
        mock_engine.generate.side_effect = RuntimeError("generation failed")
        mock_artifact = MagicMock()

        job = _make_synthesis_job(id=1, num_rows=3)
        ctx = _make_job_context(job=job, engine=mock_engine, checkpoint_dir="/tmp/ckpt")
        ctx.last_artifact = mock_artifact

        result = GenerationStep().execute(ctx)

        assert result.success is False
        assert result.error_msg is not None

    def test_generation_step_does_not_set_job_status(self) -> None:
        """GenerationStep must NOT mutate job.status — the orchestrator owns status.

        AC4: Job status transitions centralized — only the orchestrator sets job.status.
        """
        from synth_engine.modules.synthesizer.job_steps import GenerationStep

        mock_engine = MagicMock()
        mock_engine.generate.side_effect = RuntimeError("fail")
        mock_artifact = MagicMock()

        job = _make_synthesis_job(id=1, status="GENERATING")
        ctx = _make_job_context(job=job, engine=mock_engine, checkpoint_dir="/tmp/ckpt")
        ctx.last_artifact = mock_artifact

        GenerationStep().execute(ctx)

        # The step must NOT set job.status — the orchestrator does that
        assert job.status == "GENERATING", (
            f"GenerationStep must not set job.status; expected GENERATING, got {job.status!r}"
        )


# ---------------------------------------------------------------------------
# Step ordering enforcement test
# ---------------------------------------------------------------------------
