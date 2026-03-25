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
