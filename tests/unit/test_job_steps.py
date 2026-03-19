"""Unit tests for the step-based synthesis job orchestration (T35.1).

Tests follow TDD Red/Green/Refactor.  All tests are isolated — no real DB,
no real Huey worker, no network I/O.

Acceptance Criteria covered:
    AC1: _run_synthesis_job_impl is replaced by a step-based orchestrator < 50 lines.
    AC2: Each step is independently unit-testable without mocking the other steps.
    AC3: JobContext dataclass defined with typed fields for all shared state.
    AC4: Job status transitions centralized — only the orchestrator sets job.status.
    AC5: No functional changes — synthesis output is identical before and after.

Task: T35.1 — Decompose _run_synthesis_job_impl Into Discrete Job Steps
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_synthesis_job(**kwargs: Any) -> Any:
    """Create a SynthesisJob instance with default values overridden by kwargs."""
    from synth_engine.modules.synthesizer.job_models import SynthesisJob

    defaults: dict[str, Any] = {
        "id": 1,
        "status": "QUEUED",
        "current_epoch": 0,
        "total_epochs": 5,
        "num_rows": 100,
        "artifact_path": None,
        "output_path": None,
        "error_msg": None,
        "table_name": "persons",
        "parquet_path": "/data/persons.parquet",
        "checkpoint_every_n": 5,
    }
    defaults.update(kwargs)
    return SynthesisJob(**defaults)


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


class TestJobContext:
    """JobContext must be a typed dataclass carrying all shared state."""

    def test_job_context_is_importable(self) -> None:
        """JobContext must be importable from job_steps."""
        from synth_engine.modules.synthesizer.job_steps import JobContext  # noqa: F401

    def test_job_context_has_job_field(self) -> None:
        """JobContext must have a job field holding the SynthesisJob record."""
        ctx = _make_job_context()
        assert hasattr(ctx, "job")
        assert ctx.job is not None

    def test_job_context_has_session_field(self) -> None:
        """JobContext must have a session field for DB operations."""
        ctx = _make_job_context()
        assert hasattr(ctx, "session")

    def test_job_context_has_engine_field(self) -> None:
        """JobContext must have an engine field for the SynthesisEngine."""
        ctx = _make_job_context()
        assert hasattr(ctx, "engine")

    def test_job_context_has_dp_wrapper_field(self) -> None:
        """JobContext must have an optional dp_wrapper field."""
        ctx = _make_job_context(dp_wrapper=None)
        assert hasattr(ctx, "dp_wrapper")
        assert ctx.dp_wrapper is None

    def test_job_context_has_checkpoint_dir_field(self) -> None:
        """JobContext must have a checkpoint_dir field."""
        ctx = _make_job_context(checkpoint_dir="/tmp/test")
        assert ctx.checkpoint_dir == "/tmp/test"

    def test_job_context_accepts_dp_wrapper(self) -> None:
        """JobContext must accept a non-None dp_wrapper."""
        mock_wrapper = MagicMock()
        ctx = _make_job_context(dp_wrapper=mock_wrapper)
        assert ctx.dp_wrapper is mock_wrapper


# ---------------------------------------------------------------------------
# AC2: StepResult dataclass tests
# ---------------------------------------------------------------------------


class TestStepResult:
    """StepResult must carry success flag and optional error message."""

    def test_step_result_is_importable(self) -> None:
        """StepResult must be importable from job_steps."""
        from synth_engine.modules.synthesizer.job_steps import StepResult  # noqa: F401

    def test_step_result_success_true(self) -> None:
        """StepResult(success=True) must be constructable."""
        from synth_engine.modules.synthesizer.job_steps import StepResult

        result = StepResult(success=True)
        assert result.success is True
        assert result.error_msg is None

    def test_step_result_failure_with_msg(self) -> None:
        """StepResult(success=False, error_msg=...) must carry the failure message."""
        from synth_engine.modules.synthesizer.job_steps import StepResult

        result = StepResult(success=False, error_msg="OOM: 6.8 GiB estimated")
        assert result.success is False
        assert result.error_msg == "OOM: 6.8 GiB estimated"


# ---------------------------------------------------------------------------
# AC2: SynthesisJobStep protocol tests
# ---------------------------------------------------------------------------


class TestSynthesisJobStepProtocol:
    """SynthesisJobStep Protocol must be satisfiable by concrete step classes."""

    def test_protocol_is_importable(self) -> None:
        """SynthesisJobStep must be importable from job_steps."""
        from synth_engine.modules.synthesizer.job_steps import SynthesisJobStep  # noqa: F401

    def test_all_step_classes_are_importable(self) -> None:
        """All concrete step classes must be importable from job_steps."""
        from synth_engine.modules.synthesizer.job_steps import (  # noqa: F401
            DpAccountingStep,
            GenerationStep,
            OomCheckStep,
            TrainingStep,
        )

    def test_all_steps_have_execute_method(self) -> None:
        """All concrete step classes must have an execute(ctx) method."""
        from synth_engine.modules.synthesizer.job_steps import (
            DpAccountingStep,
            GenerationStep,
            OomCheckStep,
            TrainingStep,
        )

        for step_cls in (OomCheckStep, TrainingStep, DpAccountingStep, GenerationStep):
            assert hasattr(step_cls(), "execute"), f"{step_cls.__name__} missing execute() method"


# ---------------------------------------------------------------------------
# AC2: OomCheckStep isolation tests
# ---------------------------------------------------------------------------


class TestOomCheckStepIsolation:
    """OomCheckStep must be independently testable with a mock JobContext."""

    def test_oom_check_step_returns_success_when_memory_ok(self) -> None:
        """OomCheckStep.execute() must return StepResult(success=True) when memory OK."""
        from synth_engine.modules.synthesizer.job_steps import OomCheckStep, StepResult

        ctx = _make_job_context()
        step = OomCheckStep()

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            result = step.execute(ctx)

        assert isinstance(result, StepResult)
        assert result.success is True

    def test_oom_check_step_returns_failure_on_oom(self) -> None:
        """OomCheckStep.execute() must return StepResult(success=False) on OOMGuardrailError."""
        from synth_engine.modules.synthesizer.guardrails import OOMGuardrailError
        from synth_engine.modules.synthesizer.job_steps import OomCheckStep

        ctx = _make_job_context()
        step = OomCheckStep()

        with patch(
            "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility",
            side_effect=OOMGuardrailError("6.8 GiB estimated, 4.0 GiB available"),
        ):
            result = step.execute(ctx)

        assert result.success is False
        assert result.error_msg is not None
        assert "6.8 GiB" in result.error_msg

    def test_oom_check_step_does_not_set_job_status(self) -> None:
        """OomCheckStep must NOT mutate job.status — the orchestrator owns status.

        AC4: Job status transitions centralized — only the orchestrator sets job.status.
        """
        from synth_engine.modules.synthesizer.guardrails import OOMGuardrailError
        from synth_engine.modules.synthesizer.job_steps import OomCheckStep

        job = _make_synthesis_job(status="QUEUED")
        ctx = _make_job_context(job=job)
        step = OomCheckStep()

        with patch(
            "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility",
            side_effect=OOMGuardrailError("too big"),
        ):
            step.execute(ctx)

        # The step must NOT set job.status — the orchestrator does that
        assert job.status == "QUEUED", (
            f"OomCheckStep must not set job.status; expected QUEUED, got {job.status!r}"
        )


# ---------------------------------------------------------------------------
# AC2: TrainingStep isolation tests
# ---------------------------------------------------------------------------


class TestTrainingStepIsolation:
    """TrainingStep must be independently testable with a mock JobContext."""

    def test_training_step_returns_success_on_normal_run(self) -> None:
        """TrainingStep.execute() must return StepResult(success=True) when training succeeds."""
        from synth_engine.modules.synthesizer.job_steps import StepResult, TrainingStep

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        job = _make_synthesis_job(id=1, total_epochs=5, checkpoint_every_n=5)
        ctx = _make_job_context(job=job, engine=mock_engine, checkpoint_dir="/tmp/ckpt")
        step = TrainingStep()

        result = step.execute(ctx)

        assert isinstance(result, StepResult)
        assert result.success is True

    def test_training_step_returns_failure_on_runtime_error(self) -> None:
        """TrainingStep.execute() must return StepResult(success=False) on RuntimeError."""
        from synth_engine.modules.synthesizer.job_steps import TrainingStep

        mock_engine = MagicMock()
        mock_engine.train.side_effect = RuntimeError("CUDA OOM")

        job = _make_synthesis_job(id=1, total_epochs=5, checkpoint_every_n=5)
        ctx = _make_job_context(job=job, engine=mock_engine)
        step = TrainingStep()

        result = step.execute(ctx)

        assert result.success is False
        assert result.error_msg is not None

    def test_training_step_does_not_set_job_status(self) -> None:
        """TrainingStep must NOT mutate job.status — the orchestrator owns status.

        AC4: Job status transitions centralized — only the orchestrator sets job.status.
        """
        mock_engine = MagicMock()
        mock_engine.train.side_effect = RuntimeError("fail")

        job = _make_synthesis_job(id=1, total_epochs=5, checkpoint_every_n=5, status="TRAINING")
        ctx = _make_job_context(job=job, engine=mock_engine)

        from synth_engine.modules.synthesizer.job_steps import TrainingStep

        TrainingStep().execute(ctx)

        # The step must NOT set job.status — the orchestrator does that
        assert job.status == "TRAINING", (
            f"TrainingStep must not set job.status; expected TRAINING, got {job.status!r}"
        )

    def test_training_step_calls_engine_train(self) -> None:
        """TrainingStep.execute() must call engine.train() at least once."""
        from synth_engine.modules.synthesizer.job_steps import TrainingStep

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        job = _make_synthesis_job(id=1, total_epochs=5, checkpoint_every_n=5)
        ctx = _make_job_context(job=job, engine=mock_engine, checkpoint_dir="/tmp/ckpt")
        TrainingStep().execute(ctx)

        mock_engine.train.assert_called()

    def test_training_step_sets_context_last_artifact(self) -> None:
        """TrainingStep.execute() must store the last trained artifact on the context."""
        from synth_engine.modules.synthesizer.job_steps import TrainingStep

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        job = _make_synthesis_job(id=1, total_epochs=5, checkpoint_every_n=5)
        ctx = _make_job_context(job=job, engine=mock_engine, checkpoint_dir="/tmp/ckpt")
        TrainingStep().execute(ctx)

        assert hasattr(ctx, "last_artifact")
        assert ctx.last_artifact is mock_artifact


# ---------------------------------------------------------------------------
# AC2: DpAccountingStep isolation tests
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

        mock_budget_fn = MagicMock(side_effect=BudgetExhaustionError("over budget"))

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
        mock_budget_fn = MagicMock(side_effect=BudgetExhaustionError("over budget"))

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
    AC2: WORM audit trail records the failure event.
    AC3: WARNING-level log distinguishes epsilon read failure from no-DP case.
    AC4: New test — job status is FAILED when epsilon_spent() raises RuntimeError.
    """

    def test_dp_accounting_step_returns_failure_when_epsilon_spent_raises(self) -> None:
        """DpAccountingStep.execute() must return StepResult(success=False) when epsilon_spent raises.

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

        assert result.error_msg == "DP epsilon measurement failed — privacy budget cannot be verified"

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
