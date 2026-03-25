"""Unit tests for step ordering, orchestrator constraints, and audit/non-budget errors (T35.1).

Tests cover:
- TestStepOrdering — steps must execute in correct order.
- TestOrchestratorSize — _run_synthesis_job_impl under 50 lines.
- TestDpAccountingStepAuditFailure — audit logger raises.
- TestDpAccountingStepNonBudgetError — ConnectionError from _spend_budget_fn.

Split from test_job_steps.py (T56.3).

Task: T35.1 — Decompose _run_synthesis_job_impl Into Discrete Job Steps
"""

from __future__ import annotations

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


class TestStepOrdering:
    """Verify that training occurs before DP accounting in the pipeline."""

    def test_training_before_dp_accounting(self) -> None:
        """TrainingStep must be executed before DpAccountingStep in the orchestrator.

        When training fails, DpAccountingStep must never be called — this
        implicitly verifies that training comes first in the step sequence.
        """
        from synth_engine.modules.synthesizer.job_steps import (
            DpAccountingStep,
            TrainingStep,
        )

        call_order: list[str] = []

        original_training_execute = TrainingStep.execute
        original_dp_execute = DpAccountingStep.execute

        def _recording_training_execute(self: TrainingStep, ctx: Any) -> Any:
            call_order.append("training")
            return original_training_execute(self, ctx)

        def _recording_dp_execute(self: DpAccountingStep, ctx: Any) -> Any:
            call_order.append("dp_accounting")
            return original_dp_execute(self, ctx)

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = MagicMock()

        import pandas as pd

        mock_engine.generate.return_value = pd.DataFrame({"a": [1, 2]})

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.5

        job = _make_synthesis_job(id=1, total_epochs=5, checkpoint_every_n=5, enable_dp=True)
        mock_session = MagicMock()
        mock_session.get.return_value = job

        with (
            patch.object(TrainingStep, "execute", _recording_training_execute),
            patch.object(DpAccountingStep, "execute", _recording_dp_execute),
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._get_parquet_dimensions",
                return_value=(100, 10),
            ),
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn"),
        ):
            from synth_engine.modules.synthesizer.job_orchestration import (
                _run_synthesis_job_impl,
            )

            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=mock_wrapper,
            )

        assert "training" in call_order, "TrainingStep must be called"
        training_idx = call_order.index("training")

        if "dp_accounting" in call_order:
            dp_idx = call_order.index("dp_accounting")
            assert training_idx < dp_idx, (
                f"TrainingStep (pos {training_idx}) must precede DpAccountingStep (pos {dp_idx})"
            )

    def test_dp_accounting_not_called_when_training_fails(self) -> None:
        """When TrainingStep fails, DpAccountingStep must not be executed.

        Enforces that step ordering gates prevent unnecessary work on failure.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_engine = MagicMock()
        mock_engine.train.side_effect = RuntimeError("training failure")
        mock_wrapper = MagicMock()

        job = _make_synthesis_job(id=1, total_epochs=5, checkpoint_every_n=5)
        mock_session = MagicMock()
        mock_session.get.return_value = job

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=mock_wrapper,
            )

        # dp_wrapper.epsilon_spent should never be called if training failed
        mock_wrapper.epsilon_spent.assert_not_called()


# ---------------------------------------------------------------------------
# AC1: Orchestrator line count test
# ---------------------------------------------------------------------------


class TestOrchestratorSize:
    """The step-based orchestrator must be under 50 lines."""

    def test_run_synthesis_job_impl_under_50_lines(self) -> None:
        """_run_synthesis_job_impl function body must be under 50 lines (AC1).

        This test reads the source file and counts the lines in the function.
        It guards against re-inflation of the god-function.
        """
        import ast
        from pathlib import Path

        src_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "synth_engine"
            / "modules"
            / "synthesizer"
            / "job_orchestration.py"
        )
        source = src_path.read_text()
        tree = ast.parse(source)

        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_run_synthesis_job_impl":
                func_node = node
                break

        assert func_node is not None, "_run_synthesis_job_impl not found in job_orchestration.py"

        # Count lines from function start to end
        lines = source.splitlines()
        # ast gives line numbers 1-based; end_lineno is inclusive
        func_lines = lines[func_node.lineno - 1 : func_node.end_lineno]
        # Exclude blank lines and comment-only lines for a fair count
        non_blank = [
            line for line in func_lines if line.strip() and not line.strip().startswith("#")
        ]
        actual_count = len(non_blank)

        assert actual_count <= 50, (
            f"_run_synthesis_job_impl has {actual_count} non-blank/non-comment lines "
            f"(limit: 50). Refactor is required. AC1 violated."
        )


# ---------------------------------------------------------------------------
# T38.1: DpAccountingStep audit write failure tests
# ---------------------------------------------------------------------------


class TestDpAccountingStepAuditFailure:
    """DpAccountingStep must fail the job when the WORM audit write fails (T38.1).

    Acceptance Criteria covered:
        AC1: If WORM audit logger raises after budget deduction, job is marked FAILED.
        AC2: Error message includes "audit trail write failed" and "manual reconciliation".
        AC3: job.actual_epsilon IS set (budget was measured) even though audit failed.
        AC4: Orchestrator marks job FAILED when audit logger raises.
        AC5: AuditWriteError present in all hierarchy touchpoints.
    """

    def test_dp_accounting_step_returns_failure_when_audit_raises(self) -> None:
        """DpAccountingStep.execute() must return StepResult(success=False) when audit raises.

        AC1 (T38.1): If the WORM audit logger raises after budget deduction, the step
        must return a failure result — the budget has been spent but there is no audit record.
        """
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep, StepResult

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.5

        mock_budget_fn = MagicMock()  # budget spend succeeds
        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("audit write failed")

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
                return_value=mock_audit,
            ),
        ):
            result = step.execute(ctx)

        assert isinstance(result, StepResult)
        assert result.success is False

    def test_dp_accounting_step_audit_failure_error_msg_contains_reconciliation_text(
        self,
    ) -> None:
        """Error message must mention 'audit trail write failed' and 'manual reconciliation'.

        AC2 (T38.1): The error message must clearly alert operators that manual
        reconciliation is needed because budget was spent but no audit record was written.
        """
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.5

        mock_budget_fn = MagicMock()
        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("storage failure")

        job = _make_synthesis_job(id=1)
        ctx = _make_job_context(job=job, dp_wrapper=mock_wrapper)

        with (
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=mock_audit,
            ),
        ):
            result = DpAccountingStep().execute(ctx)

        assert result.error_msg is not None
        assert "audit trail write failed" in result.error_msg.lower()
        assert "manual reconciliation" in result.error_msg.lower()

    def test_dp_accounting_step_actual_epsilon_is_set_before_audit_failure(self) -> None:
        """job.actual_epsilon must be set even when the audit write fails.

        AC3 (T38.1): The budget WAS measured and spent successfully before the audit
        write failed. job.actual_epsilon must reflect the measured value so operators
        know how much budget was consumed during reconciliation.
        """
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 2.7

        mock_budget_fn = MagicMock()
        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("storage failure")

        job = _make_synthesis_job(id=1)
        assert job.actual_epsilon is None  # pre-condition

        ctx = _make_job_context(job=job, dp_wrapper=mock_wrapper)

        with (
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=mock_audit,
            ),
        ):
            DpAccountingStep().execute(ctx)

        # Budget was measured — actual_epsilon must be set for operator reconciliation
        assert job.actual_epsilon == 2.7

    def test_orchestrator_marks_job_failed_when_audit_logger_raises(self) -> None:
        """_run_synthesis_job_impl must set job.status=FAILED when audit log write fails.

        AC4 (T38.1): End-to-end orchestrator test — job must be FAILED, not COMPLETE,
        when the WORM audit write raises after a successful budget deduction.
        """
        from synth_engine.modules.synthesizer.job_orchestration import _run_synthesis_job_impl

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.5

        mock_budget_fn = MagicMock()  # budget spend succeeds
        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("WORM storage unavailable")

        job = _make_synthesis_job(id=1, total_epochs=5, checkpoint_every_n=5)
        mock_session = MagicMock()
        mock_session.get.return_value = job

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._get_parquet_dimensions",
                return_value=(100, 10),
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=mock_audit,
            ),
        ):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=mock_wrapper,
            )

        assert job.status == "FAILED", (
            f"Job must be FAILED when audit write fails; got {job.status!r}"
        )
        assert job.error_msg is not None
        assert "audit trail write failed" in job.error_msg.lower()
        assert "manual reconciliation" in job.error_msg.lower()

    def test_audit_write_error_is_importable_from_shared_exceptions(self) -> None:
        """AuditWriteError must be importable from synth_engine.shared.exceptions.

        AC5 (T38.1): AuditWriteError must be present in the shared exception hierarchy.
        """
        from synth_engine.shared.exceptions import AuditWriteError  # noqa: F401

    def test_audit_write_error_is_in_all_list(self) -> None:
        """AuditWriteError must be in shared/exceptions.py __all__.

        AC5 (T38.1): All touchpoints must be updated atomically.
        """
        import synth_engine.shared.exceptions as exc_mod

        assert "AuditWriteError" in exc_mod.__all__

    def test_audit_write_error_is_in_operator_error_map(self) -> None:
        """AuditWriteError must be present in OPERATOR_ERROR_MAP.

        AC5 (T38.1): bootstrapper error mapping must include AuditWriteError.
        """
        from synth_engine.bootstrapper.errors.mapping import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import AuditWriteError

        assert AuditWriteError in OPERATOR_ERROR_MAP


# ---------------------------------------------------------------------------
# ADV-P38-01: DpAccountingStep non-BudgetExhaustionError exception handling
# ---------------------------------------------------------------------------


class TestDpAccountingStepNonBudgetError:
    """DpAccountingStep must handle non-BudgetExhaustionError exceptions from _spend_budget_fn.

    ADV-P38-01: A ConnectionError (or any other unexpected exception) raised by
    _spend_budget_fn must not propagate uncaught — the step must catch it, log at
    ERROR level, and mark the job FAILED via a StepResult(success=False).
    """

    def test_connection_error_from_spend_budget_fn_returns_failure(self) -> None:
        """ConnectionError from _spend_budget_fn must produce StepResult(success=False).

        ADV-P38-01: Before the fix, ConnectionError propagates uncaught from
        DpAccountingStep.execute(). After the fix it must be caught and returned
        as a StepResult failure so the orchestrator can mark the job FAILED.
        """
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep, StepResult

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.5

        mock_budget_fn = MagicMock(side_effect=ConnectionError("DB unreachable"))

        job = _make_synthesis_job(id=7, status="TRAINING")
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

        assert isinstance(result, StepResult)
        assert result.success is False, (
            "DpAccountingStep must return failure when _spend_budget_fn raises ConnectionError"
        )

    def test_connection_error_does_not_set_job_status(self) -> None:
        """DpAccountingStep must NOT mutate job.status on ConnectionError.

        ADV-P38-01: The orchestrator is the sole owner of job.status (AC4).
        The step must return a failure StepResult; the orchestrator sets FAILED.
        """
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.5

        mock_budget_fn = MagicMock(side_effect=ConnectionError("DB unreachable"))

        job = _make_synthesis_job(id=8, status="TRAINING")
        ctx = _make_job_context(job=job, dp_wrapper=mock_wrapper)

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
            DpAccountingStep().execute(ctx)

        assert job.status == "TRAINING", (
            f"DpAccountingStep must not set job.status; expected TRAINING, got {job.status!r}"
        )

    def test_connection_error_result_has_error_msg(self) -> None:
        """StepResult.error_msg must be non-None on ConnectionError from _spend_budget_fn.

        ADV-P38-01: The job's error_msg must convey that the budget spend failed
        so the operator can investigate.
        """
        from synth_engine.modules.synthesizer.job_steps import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.5

        mock_budget_fn = MagicMock(side_effect=ConnectionError("DB unreachable"))

        job = _make_synthesis_job(id=9, status="TRAINING")
        ctx = _make_job_context(job=job, dp_wrapper=mock_wrapper)

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
            result = DpAccountingStep().execute(ctx)

        assert result.error_msg is not None, (
            "StepResult.error_msg must be set when _spend_budget_fn raises ConnectionError"
        )
        assert len(result.error_msg) > 0, (
            f"error_msg must be a non-empty string, got: {result.error_msg!r}"
        )
        assert "budget" in result.error_msg.lower() or "spend" in result.error_msg.lower(), (
            f"error_msg must reference the budget spend failure, got: {result.error_msg!r}"
        )
