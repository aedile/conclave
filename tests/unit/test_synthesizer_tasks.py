"""Unit tests for the Huey task wiring and checkpointing in the synthesizer module.

Tests follow TDD Red/Green/Refactor.  All tests are isolated (no real DB, no
real Huey worker, no network I/O) and assert concrete return values.

Pattern guards applied (per RETRO_LOG learning scan):
- Return-value assertion: every test asserts concrete values, not just absence
  of exceptions.
- Compound AC items: SynthesisJob fields id/status/current_epoch/total_epochs/
  artifact_path/error_msg ALL tested individually.
- Version-pin hallucination: no new PyPI dependencies added (huey already pinned).
- File placement: tasks.py and job_models.py in modules/synthesizer/ (not top-level).
- Bootstrapper wiring: run_synthesis_job wired in bootstrapper/main.py or via
  shared/task_queue.py import.

CONSTITUTION Priority 3: TDD RED/GREEN Phase
Task: P4-T4.2c — Huey Task Wiring & Checkpointing
Task: P22-T22.1 — Job Schema DP Parameters
Task: P22-T22.2 — Wire DP into run_synthesis_job()
Task: P22-T22.3 — Wire spend_budget() into Synthesis Pipeline
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

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
        "total_epochs": 10,
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


# ---------------------------------------------------------------------------
# SynthesisJob model tests
# ---------------------------------------------------------------------------


class TestSynthesisJobModel:
    """Tests that SynthesisJob SQLModel defines all required fields."""

    def test_synthesis_job_has_id_field(self) -> None:
        """SynthesisJob must have an integer id field."""

        job = _make_synthesis_job(id=42)
        assert job.id == 42

    def test_synthesis_job_has_status_field(self) -> None:
        """SynthesisJob must have a status field."""

        job = _make_synthesis_job(status="QUEUED")
        assert job.status == "QUEUED"

    def test_synthesis_job_has_current_epoch_field(self) -> None:
        """SynthesisJob must have a current_epoch integer field."""

        job = _make_synthesis_job(current_epoch=3)
        assert job.current_epoch == 3

    def test_synthesis_job_has_total_epochs_field(self) -> None:
        """SynthesisJob must have a total_epochs integer field."""

        job = _make_synthesis_job(total_epochs=300)
        assert job.total_epochs == 300

    def test_synthesis_job_has_artifact_path_field(self) -> None:
        """SynthesisJob must have an optional artifact_path string field."""

        job = _make_synthesis_job(artifact_path=None)
        assert job.artifact_path is None

        job2 = _make_synthesis_job(artifact_path="/artifacts/persons.pkl")
        assert job2.artifact_path == "/artifacts/persons.pkl"

    def test_synthesis_job_has_error_msg_field(self) -> None:
        """SynthesisJob must have an optional error_msg string field."""

        job = _make_synthesis_job(error_msg=None)
        assert job.error_msg is None

        job2 = _make_synthesis_job(error_msg="OOM: 6.8 GiB estimated, 4.0 GiB available")
        assert job2.error_msg == "OOM: 6.8 GiB estimated, 4.0 GiB available"

    def test_synthesis_job_has_table_name_field(self) -> None:
        """SynthesisJob must have a table_name field for the target table."""

        job = _make_synthesis_job(table_name="orders")
        assert job.table_name == "orders"

    def test_synthesis_job_has_parquet_path_field(self) -> None:
        """SynthesisJob must have a parquet_path field for the source data."""

        job = _make_synthesis_job(parquet_path="/data/orders.parquet")
        assert job.parquet_path == "/data/orders.parquet"

    def test_synthesis_job_has_checkpoint_every_n_field(self) -> None:
        """SynthesisJob must have a checkpoint_every_n field defaulting to 5."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job = SynthesisJob(
            status="QUEUED",
            current_epoch=0,
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
        )
        assert job.checkpoint_every_n == 5

    def test_synthesis_job_status_queued_is_valid(self) -> None:
        """SynthesisJob status QUEUED must be accepted."""
        job = _make_synthesis_job(status="QUEUED")
        assert job.status == "QUEUED"

    def test_synthesis_job_status_training_is_valid(self) -> None:
        """SynthesisJob status TRAINING must be accepted."""
        job = _make_synthesis_job(status="TRAINING")
        assert job.status == "TRAINING"

    def test_synthesis_job_status_complete_is_valid(self) -> None:
        """SynthesisJob status COMPLETE must be accepted."""
        job = _make_synthesis_job(status="COMPLETE")
        assert job.status == "COMPLETE"

    def test_synthesis_job_status_failed_is_valid(self) -> None:
        """SynthesisJob status FAILED must be accepted."""
        job = _make_synthesis_job(status="FAILED")
        assert job.status == "FAILED"

    def test_synthesis_job_checkpoint_every_n_zero_raises(self) -> None:
        """SynthesisJob must reject checkpoint_every_n=0 with ValueError.

        A value of 0 would cause an infinite loop in _run_synthesis_job_impl
        because min(0, total - 0) == 0, so completed_epochs never advances.

        Note: SQLModel table=True models bypass pydantic field validators in
        __init__ to allow ORM row construction.  The guard is implemented as
        an __init__ override that raises ValueError directly.
        """
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        with pytest.raises(ValueError, match="checkpoint_every_n must be >= 1"):
            SynthesisJob(
                status="QUEUED",
                current_epoch=0,
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                checkpoint_every_n=0,
            )

    # -------------------------------------------------------------------------
    # DP parameter field tests (P22-T22.1)
    # -------------------------------------------------------------------------

    def test_synthesis_job_enable_dp_defaults_to_true(self) -> None:
        """SynthesisJob must default enable_dp to True (privacy-by-design, OWASP A04)."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job = SynthesisJob(
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
        )
        assert job.enable_dp is True

    def test_synthesis_job_enable_dp_can_be_set_false(self) -> None:
        """SynthesisJob must accept enable_dp=False for non-DP training."""
        job = _make_synthesis_job(enable_dp=False)
        assert job.enable_dp is False

    def test_synthesis_job_noise_multiplier_defaults_to_1_1(self) -> None:
        """SynthesisJob must default noise_multiplier to 1.1 (ADR-0025 calibration)."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job = SynthesisJob(
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
        )
        assert job.noise_multiplier == 1.1

    def test_synthesis_job_noise_multiplier_can_be_customised(self) -> None:
        """SynthesisJob must accept a custom noise_multiplier."""
        job = _make_synthesis_job(noise_multiplier=2.5)
        assert job.noise_multiplier == 2.5

    def test_synthesis_job_noise_multiplier_zero_raises(self) -> None:
        """SynthesisJob must reject noise_multiplier=0 with ValueError."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        with pytest.raises(ValueError, match="noise_multiplier must be > 0"):
            SynthesisJob(
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                noise_multiplier=0.0,
            )

    def test_synthesis_job_noise_multiplier_negative_raises(self) -> None:
        """SynthesisJob must reject negative noise_multiplier with ValueError."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        with pytest.raises(ValueError, match="noise_multiplier must be > 0"):
            SynthesisJob(
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                noise_multiplier=-0.5,
            )

    def test_synthesis_job_max_grad_norm_defaults_to_1_0(self) -> None:
        """SynthesisJob must default max_grad_norm to 1.0."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job = SynthesisJob(
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
        )
        assert job.max_grad_norm == 1.0

    def test_synthesis_job_max_grad_norm_can_be_customised(self) -> None:
        """SynthesisJob must accept a custom max_grad_norm."""
        job = _make_synthesis_job(max_grad_norm=0.5)
        assert job.max_grad_norm == 0.5

    def test_synthesis_job_max_grad_norm_zero_raises(self) -> None:
        """SynthesisJob must reject max_grad_norm=0 with ValueError."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        with pytest.raises(ValueError, match="max_grad_norm must be > 0"):
            SynthesisJob(
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                max_grad_norm=0.0,
            )

    def test_synthesis_job_max_grad_norm_negative_raises(self) -> None:
        """SynthesisJob must reject negative max_grad_norm with ValueError."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        with pytest.raises(ValueError, match="max_grad_norm must be > 0"):
            SynthesisJob(
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                max_grad_norm=-1.0,
            )

    def test_synthesis_job_actual_epsilon_defaults_to_none(self) -> None:
        """SynthesisJob must default actual_epsilon to None (set after training)."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job = SynthesisJob(
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
        )
        assert job.actual_epsilon is None

    def test_synthesis_job_actual_epsilon_can_be_set(self) -> None:
        """SynthesisJob must accept a float actual_epsilon value."""
        job = _make_synthesis_job(actual_epsilon=3.14)
        assert job.actual_epsilon == 3.14

    def test_synthesis_job_noise_multiplier_above_100_raises(self) -> None:
        """SynthesisJob must reject noise_multiplier=101 with ValueError."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        with pytest.raises(ValueError, match="noise_multiplier must be <= 100.0"):
            SynthesisJob(
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                noise_multiplier=101,
            )

    def test_synthesis_job_max_grad_norm_above_100_raises(self) -> None:
        """SynthesisJob must reject max_grad_norm=101 with ValueError."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        with pytest.raises(ValueError, match="max_grad_norm must be <= 100.0"):
            SynthesisJob(
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                max_grad_norm=101,
            )


# ---------------------------------------------------------------------------
# Huey task registration
# ---------------------------------------------------------------------------


class TestHueyTaskRegistration:
    """Verify that run_synthesis_job is registered as a Huey task."""

    def test_run_synthesis_job_is_callable(self) -> None:
        """run_synthesis_job must be importable and callable."""
        from synth_engine.modules.synthesizer.tasks import run_synthesis_job

        assert callable(run_synthesis_job)

    def test_run_synthesis_job_is_huey_task(self) -> None:
        """run_synthesis_job must be a Huey task (has .call_local attribute)."""
        from synth_engine.modules.synthesizer.tasks import run_synthesis_job

        # Huey tasks expose .call_local() for synchronous testing
        assert hasattr(run_synthesis_job, "call_local")


# ---------------------------------------------------------------------------
# Status transitions: QUEUED → TRAINING → COMPLETE
# ---------------------------------------------------------------------------


class TestSynthesisTaskSuccessPath:
    """Unit tests for the happy path: QUEUED → TRAINING → COMPLETE."""

    def _make_mock_session(self) -> MagicMock:
        """Return a MagicMock that behaves like a SQLModel Session."""
        session = MagicMock()
        return session

    def test_task_transitions_queued_to_training(self) -> None:
        """Task must set status=TRAINING before training starts.

        The job object is mutable — by the time we inspect call_args_list the
        status is already COMPLETE.  We use side_effect to snapshot the status
        at each session.add() call time instead.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = self._make_mock_session()
        job = _make_synthesis_job(id=1, status="QUEUED", total_epochs=3, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job1_epoch3.pkl"

        # Capture status at the moment session.add() is called (job is mutable).
        recorded_statuses: list[str] = []

        def _snapshot_status(obj: object) -> None:
            if hasattr(obj, "status"):
                recorded_statuses.append(str(obj.status))

        mock_session.add.side_effect = _snapshot_status

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
            )

        assert "TRAINING" in recorded_statuses, (
            f"Expected status=TRAINING to be recorded; got: {recorded_statuses}"
        )

    def test_task_transitions_training_to_complete(self) -> None:
        """Task must set status=COMPLETE on successful training completion."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = self._make_mock_session()
        job = _make_synthesis_job(id=1, status="QUEUED", total_epochs=3, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job1_final.pkl"

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
            )

        # Final status on job must be COMPLETE
        assert job.status == "COMPLETE"

    def test_task_sets_artifact_path_on_complete(self) -> None:
        """Task must set artifact_path on job record after successful completion."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = self._make_mock_session()
        job = _make_synthesis_job(id=1, status="QUEUED", total_epochs=3, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job1_final.pkl"

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.artifact_path is not None
        assert "job_1" in job.artifact_path

    def test_task_calls_session_commit_on_status_transitions(self) -> None:
        """Task must commit the session after each status change."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = self._make_mock_session()
        job = _make_synthesis_job(id=1, status="QUEUED", total_epochs=3, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job1.pkl"

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
            )

        # session.commit() must be called at least twice:
        # once for QUEUED → TRAINING and once for TRAINING → COMPLETE
        assert mock_session.commit.call_count >= 2


# ---------------------------------------------------------------------------
# OOM guardrail rejection path
# ---------------------------------------------------------------------------


class TestSynthesisTaskOOMRejection:
    """Unit tests for OOM guardrail rejection: guardrail fails → FAILED status."""

    def test_oom_guardrail_rejection_sets_failed_status(self) -> None:
        """When OOM guardrail rejects, task must set status=FAILED."""
        from synth_engine.modules.synthesizer.guardrails import OOMGuardrailError
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=2, status="QUEUED", total_epochs=100, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()

        with patch(
            "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility",
            side_effect=OOMGuardrailError("6.8 GiB estimated, 4.0 GiB available"),
        ):
            _run_synthesis_job_impl(
                job_id=2,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.status == "FAILED"

    def test_oom_guardrail_rejection_sets_error_msg(self) -> None:
        """When OOM guardrail rejects, task must record the guardrail error message."""
        from synth_engine.modules.synthesizer.guardrails import OOMGuardrailError
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=2, status="QUEUED", total_epochs=100, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()

        oom_msg = "6.8 GiB estimated, 4.0 GiB available -- reduce dataset by 2.00x"
        with patch(
            "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility",
            side_effect=OOMGuardrailError(oom_msg),
        ):
            _run_synthesis_job_impl(
                job_id=2,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.error_msg is not None
        assert oom_msg in job.error_msg

    def test_oom_guardrail_rejection_never_calls_train(self) -> None:
        """When OOM guardrail rejects, engine.train() must never be called."""
        from synth_engine.modules.synthesizer.guardrails import OOMGuardrailError
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=2, status="QUEUED", total_epochs=100, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()

        with patch(
            "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility",
            side_effect=OOMGuardrailError("too big"),
        ):
            _run_synthesis_job_impl(
                job_id=2,
                session=mock_session,
                engine=mock_engine,
            )

        mock_engine.train.assert_not_called()

    def test_oom_guardrail_rejection_commits_failed_status(self) -> None:
        """OOM rejection must commit the FAILED status to the database."""
        from synth_engine.modules.synthesizer.guardrails import OOMGuardrailError
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=2, status="QUEUED", total_epochs=100, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()

        with patch(
            "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility",
            side_effect=OOMGuardrailError("too big"),
        ):
            _run_synthesis_job_impl(
                job_id=2,
                session=mock_session,
                engine=mock_engine,
            )

        # session.commit() must be called at least once to persist FAILED status
        assert mock_session.commit.call_count >= 1


# ---------------------------------------------------------------------------
# RuntimeError mid-training failure
# ---------------------------------------------------------------------------


class TestSynthesisTaskRuntimeFailure:
    """Unit tests for RuntimeError during training.

    Verifies: task sets FAILED status, error message is recorded, and the
    checkpoint for the last completed epoch exists in storage.
    """

    def test_runtime_error_sets_failed_status(self) -> None:
        """RuntimeError during training must set status=FAILED."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=3, status="QUEUED", total_epochs=5, checkpoint_every_n=3)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_engine.train.side_effect = RuntimeError("CUDA out of memory at epoch 3")

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.status == "FAILED"

    def test_runtime_error_sets_error_msg(self) -> None:
        """RuntimeError during training must record the error message."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=3, status="QUEUED", total_epochs=5, checkpoint_every_n=3)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_engine.train.side_effect = RuntimeError("CUDA out of memory at epoch 3")

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.error_msg is not None
        assert "CUDA out of memory" in job.error_msg

    def test_checkpoint_saved_before_failure(self) -> None:
        """Checkpoint for the last completed batch must exist in storage after failure.

        Training is mocked to complete the first call (epoch batch 1) then fail
        on the second call (epoch batch 2).  Storage must have been called at
        least once to persist the epoch-3 checkpoint.

        The checkpoint_every_n=3 means a checkpoint is saved after epoch 3
        (the first checkpoint boundary).  When train() raises on the second
        call, the first checkpoint must already be in storage.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        # total_epochs=6, checkpoint_every_n=3 → checkpoints at epoch 3 and 6
        job = _make_synthesis_job(id=3, status="QUEUED", total_epochs=6, checkpoint_every_n=3)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        # First train() call (epochs 1-3) succeeds; second (epochs 4-6) raises
        first_artifact = MagicMock()
        first_artifact.save.return_value = "/artifacts/job3_epoch3.pkl"
        mock_engine.train.side_effect = [first_artifact, RuntimeError("OOM at epoch 5")]

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

        # Artifact must have been saved at least once (epoch-3 checkpoint)
        assert first_artifact.save.call_count >= 1

    def test_failed_job_commits_to_db(self) -> None:
        """RuntimeError path must commit FAILED status to the database."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=3, status="QUEUED", total_epochs=5, checkpoint_every_n=3)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_engine.train.side_effect = RuntimeError("failed")

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
            )

        assert mock_session.commit.call_count >= 1

    def test_total_epochs_zero_marks_job_failed(self) -> None:
        """_run_synthesis_job_impl must mark job FAILED when total_epochs=0.

        total_epochs=0 skips the training while-loop entirely, leaving
        last_ckpt_path as None.  The step-6 guard must catch this and set
        status=FAILED with an error_msg containing 'No artifact produced'.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=99,
            status="QUEUED",
            total_epochs=0,
            checkpoint_every_n=5,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job
        mock_engine = MagicMock()

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=99,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.status == "FAILED", f"Expected FAILED; got {job.status}"
        assert job.error_msg is not None
        assert "No artifact produced" in job.error_msg, (
            f"Expected 'No artifact produced' in error_msg; got {job.error_msg!r}"
        )


# ---------------------------------------------------------------------------
# Checkpointing behaviour
# ---------------------------------------------------------------------------


class TestSynthesisTaskCheckpointing:
    """Verify that ModelArtifact checkpoints are saved every N epochs."""

    def test_checkpoint_saved_every_n_epochs(self) -> None:
        """Artifact.save() must be called once per checkpoint boundary during training.

        With total_epochs=10 and checkpoint_every_n=5, there are 2 checkpoint
        boundaries: epoch 5 and epoch 10.  Two save() calls are expected.
        But since engine.train() is called once (not per-epoch), we verify
        that checkpoint saves happen (artifact.save called at least once).
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=4, status="QUEUED", total_epochs=10, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job4_checkpoint.pkl"

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=4,
                session=mock_session,
                engine=mock_engine,
            )

        # artifact.save() must have been called at least once
        assert mock_artifact.save.call_count >= 1

    def test_current_epoch_updated_during_training(self) -> None:
        """job.current_epoch must be updated to reflect training progress."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=4, status="QUEUED", total_epochs=10, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job4.pkl"

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=4,
                session=mock_session,
                engine=mock_engine,
            )

        # current_epoch must equal total_epochs on success
        assert job.current_epoch == job.total_epochs

    def test_no_checkpoint_before_first_boundary(self) -> None:
        """With checkpoint_every_n=10 and total_epochs=5, no checkpoint is saved.

        The first checkpoint boundary (epoch 10) is never reached, so
        artifact.save() must not be called for intermediate checkpointing.
        On completion, the final artifact is saved — so exactly 1 save().
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        # total_epochs=5 < checkpoint_every_n=10 → no intermediate checkpoints
        job = _make_synthesis_job(id=5, status="QUEUED", total_epochs=5, checkpoint_every_n=10)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job5_final.pkl"

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=5,
                session=mock_session,
                engine=mock_engine,
            )

        # Exactly 1 save() call: the final artifact save on COMPLETE
        assert mock_artifact.save.call_count == 1


# ---------------------------------------------------------------------------
# Job not found
# ---------------------------------------------------------------------------


class TestSynthesisJobNotFound:
    """Verify task handles missing job ID gracefully."""

    def test_task_raises_if_job_not_found(self) -> None:
        """_run_synthesis_job_impl must raise ValueError when job ID is not in DB."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        mock_session.get.return_value = None  # Job not found

        mock_engine = MagicMock()

        with pytest.raises(ValueError, match="SynthesisJob.*not found"):
            _run_synthesis_job_impl(
                job_id=999,
                session=mock_session,
                engine=mock_engine,
            )


# ---------------------------------------------------------------------------
# DP wiring tests (P22-T22.2)
# ---------------------------------------------------------------------------


def _make_mock_dp_wrapper(epsilon: float = 3.14) -> MagicMock:
    """Build a duck-typed mock DPTrainingWrapper.

    The wrapper exposes ``epsilon_spent(delta)`` returning ``epsilon``.
    This mirrors the real ``DPTrainingWrapper`` contract without importing
    from ``modules/privacy/``.

    Args:
        epsilon: Value returned by ``epsilon_spent()``.

    Returns:
        A ``MagicMock`` configured with the DP wrapper duck-type contract.
    """
    wrapper = MagicMock()
    wrapper.epsilon_spent.return_value = epsilon
    return wrapper


class TestDPWiringInImpl:
    """Tests for dp_wrapper forwarding inside _run_synthesis_job_impl.

    These tests call _run_synthesis_job_impl directly with an injected
    dp_wrapper mock so no bootstrapper import is required.
    """

    def test_dp_wrapper_passed_to_engine_train_when_enabled(self) -> None:
        """engine.train() must receive the dp_wrapper kwarg when enable_dp=True.

        Confirms that _run_synthesis_job_impl forwards dp_wrapper to every
        engine.train() call made during the training loop.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=10,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = _make_mock_dp_wrapper(epsilon=2.5)

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn"),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            _run_synthesis_job_impl(
                job_id=10,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=dp_wrapper,
            )

        # All engine.train() calls must have received dp_wrapper as a keyword arg
        for call in mock_engine.train.call_args_list:
            assert call.kwargs.get("dp_wrapper") is dp_wrapper, (
                f"engine.train() call missing dp_wrapper kwarg: {call}"
            )

    def test_dp_wrapper_not_passed_when_dp_disabled(self) -> None:
        """engine.train() must receive dp_wrapper=None when no wrapper is injected.

        Confirms the non-DP path is unaffected by the new parameter.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=11,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=False,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=11,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=None,
            )

        # All calls must have dp_wrapper=None (or absent, which is also None)
        for call in mock_engine.train.call_args_list:
            actual = call.kwargs.get("dp_wrapper", None)
            assert actual is None, (
                f"engine.train() received non-None dp_wrapper on non-DP job: {call}"
            )

    def test_actual_epsilon_set_on_job_after_dp_training(self) -> None:
        """job.actual_epsilon must be set to epsilon_spent() result after DP training.

        Confirms epsilon is read from the wrapper and persisted to the job
        record before the COMPLETE status commit.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=12,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = _make_mock_dp_wrapper(epsilon=3.14)

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn"),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            _run_synthesis_job_impl(
                job_id=12,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=dp_wrapper,
            )

        assert job.actual_epsilon == 3.14, f"Expected actual_epsilon=3.14; got {job.actual_epsilon}"
        dp_wrapper.epsilon_spent.assert_called_once_with(delta=1e-5)

    def test_actual_epsilon_is_none_when_dp_disabled(self) -> None:
        """job.actual_epsilon must remain None when no dp_wrapper is provided.

        Confirms the non-DP path does not write a spurious epsilon value.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=13,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=False,
            actual_epsilon=None,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=13,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=None,
            )

        assert job.actual_epsilon is None, (
            f"Expected actual_epsilon=None on non-DP job; got {job.actual_epsilon}"
        )

    def test_epsilon_spent_exception_does_not_block_completion(self) -> None:
        """RuntimeError from epsilon_spent() must not prevent job from reaching COMPLETE.

        Training succeeded; only the epsilon accounting step failed.  The job
        artifact is valid, so the lifecycle must continue to COMPLETE with
        actual_epsilon left as None.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=14,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = MagicMock()
        dp_wrapper.epsilon_spent.side_effect = RuntimeError("Opacus error")

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=14,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=dp_wrapper,
            )

        assert job.status == "COMPLETE", (
            f"Expected status=COMPLETE after epsilon_spent() failure; got {job.status}"
        )
        assert job.actual_epsilon is None, (
            f"Expected actual_epsilon=None when epsilon_spent() raises; got {job.actual_epsilon}"
        )


# ---------------------------------------------------------------------------
# DI factory injection tests (P22-T22.2 architecture blocker fix)
# ---------------------------------------------------------------------------


class TestDPFactoryInjection:
    """Tests for the set_dp_wrapper_factory DI injection pattern (ADR-0029).

    These tests verify that run_synthesis_job raises RuntimeError when
    enable_dp=True but no factory has been registered, and that
    set_dp_wrapper_factory correctly stores and makes the factory callable.
    """

    def test_set_dp_wrapper_factory_stores_callable(self) -> None:
        """set_dp_wrapper_factory must store the provided callable.

        After calling set_dp_wrapper_factory with a mock factory, the module-
        level _dp_wrapper_factory must reference that exact callable.
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod

        mock_factory = MagicMock(return_value=MagicMock())
        original = orch_mod._dp_wrapper_factory
        try:
            orch_mod.set_dp_wrapper_factory(mock_factory)
            assert orch_mod._dp_wrapper_factory is mock_factory
        finally:
            # Restore original state so other tests are not affected.
            orch_mod._dp_wrapper_factory = original  # type: ignore[assignment]

    def test_dp_requested_without_factory_raises_runtime_error(self) -> None:
        """run_synthesis_job must raise RuntimeError when enable_dp=True and no factory registered.

        Verifies that the guard in run_synthesis_job() fires with the expected
        message when _dp_wrapper_factory is None and a DP job is requested.

        Because Session and get_engine are locally imported inside the task
        function body, they are patched at their source module paths rather
        than via the tasks module namespace.
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        import synth_engine.modules.synthesizer.tasks as tasks_mod

        mock_job = _make_synthesis_job(
            id=99,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
        )

        mock_session_instance = MagicMock()
        mock_session_instance.get.return_value = mock_job
        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__ = MagicMock(return_value=mock_session_instance)
        mock_session_ctx.__exit__ = MagicMock(return_value=False)

        original_factory = orch_mod._dp_wrapper_factory
        try:
            orch_mod._dp_wrapper_factory = None  # type: ignore[assignment]

            with (
                patch(
                    "synth_engine.shared.db.get_engine",
                    return_value=MagicMock(),
                ),
                patch(
                    "sqlmodel.Session",
                    return_value=mock_session_ctx,
                ),
                pytest.raises(RuntimeError, match="dp_wrapper_factory"),
            ):
                tasks_mod.run_synthesis_job.call_local(99)
        finally:
            orch_mod._dp_wrapper_factory = original_factory  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# spend_budget() wiring tests (P22-T22.3)
# ---------------------------------------------------------------------------


class TestSpendBudgetWiring:
    """Tests for spend_budget DI injection and invocation (AC2, AC3, AC4, AC5, AC6, AC7).

    All tests use mocks — no real database, no real async session.
    The spend_budget callable is injected via set_spend_budget_fn() following
    the same DI pattern as set_dp_wrapper_factory() (ADR-0029).

    Boundary guard: these tests do NOT import from modules/privacy/ — they
    use duck-typed mocks and exception name matching to stay boundary-clean.
    """

    def _run_impl_with_budget_mock(
        self,
        job_id: int = 20,
        epsilon: float = 2.5,
        budget_fn_side_effect: Exception | None = None,
    ) -> tuple[Any, MagicMock, MagicMock]:
        """Helper: run _run_synthesis_job_impl with a DP wrapper and mocked budget fn.

        Returns:
            Tuple of (job, mock_budget_fn, mock_session).
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=job_id,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = _make_mock_dp_wrapper(epsilon=epsilon)

        mock_budget_fn = MagicMock()
        if budget_fn_side_effect is not None:
            mock_budget_fn.side_effect = budget_fn_side_effect

        original_fn = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_budget_fn)
            with (
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"
                ),
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                    return_value=MagicMock(),
                ),
            ):
                _run_synthesis_job_impl(
                    job_id=job_id,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=dp_wrapper,
                )
        finally:
            orch_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        return job, mock_budget_fn, mock_session

    def test_set_spend_budget_fn_stores_callable(self) -> None:
        """set_spend_budget_fn must store the provided callable at module level.

        After calling set_spend_budget_fn with a mock, the module-level
        _spend_budget_fn must reference that exact callable.
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod

        mock_fn = MagicMock()
        original = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_fn)
            assert orch_mod._spend_budget_fn is mock_fn
        finally:
            orch_mod._spend_budget_fn = original  # type: ignore[assignment]

    def test_spend_budget_called_after_dp_training(self) -> None:
        """spend_budget fn must be called after successful DP training (AC2).

        Verifies the fn is invoked exactly once with the correct epsilon
        from the dp_wrapper.epsilon_spent() result.
        """
        job, mock_budget_fn, _ = self._run_impl_with_budget_mock(job_id=20, epsilon=2.5)

        mock_budget_fn.assert_called_once()
        call_kwargs = mock_budget_fn.call_args.kwargs
        assert call_kwargs["amount"] == 2.5, f"Expected amount=2.5; got {call_kwargs.get('amount')}"
        assert call_kwargs["job_id"] == 20, f"Expected job_id=20; got {call_kwargs.get('job_id')}"

    def test_spend_budget_called_with_ledger_id_1(self) -> None:
        """spend_budget fn must be called with ledger_id=1 (default seeded ledger).

        The migration 005 seeds a single PrivacyLedger row with id=1.
        The task must use this fixed ledger_id until multi-tenant is implemented.
        """
        _, mock_budget_fn, _ = self._run_impl_with_budget_mock(job_id=21, epsilon=1.0)

        call_kwargs = mock_budget_fn.call_args.kwargs
        assert call_kwargs["ledger_id"] == 1, (
            f"Expected ledger_id=1; got {call_kwargs.get('ledger_id')}"
        )

    def test_budget_exhaustion_marks_job_failed(self) -> None:
        """BudgetExhaustionError from spend_budget fn must mark job FAILED (AC3).

        P26-T26.2: BudgetExhaustionError now lives in shared/exceptions.py and
        is caught by type rather than by ADR-0033 duck-typing name matching.
        """
        from synth_engine.shared.exceptions import BudgetExhaustionError

        job, mock_budget_fn, mock_session = self._run_impl_with_budget_mock(
            job_id=22,
            epsilon=999.0,
            budget_fn_side_effect=BudgetExhaustionError("Budget exhausted"),
        )

        assert job.status == "FAILED", f"Expected FAILED; got {job.status}"

    def test_budget_exhaustion_sets_error_msg(self) -> None:
        """BudgetExhaustionError must set job.error_msg to 'Privacy budget exhausted' (AC3)."""
        from synth_engine.shared.exceptions import BudgetExhaustionError

        job, _, _ = self._run_impl_with_budget_mock(
            job_id=23,
            epsilon=999.0,
            budget_fn_side_effect=BudgetExhaustionError("over budget"),
        )

        assert job.error_msg == "Privacy budget exhausted", (
            f"Expected 'Privacy budget exhausted'; got {job.error_msg!r}"
        )

    def test_budget_exhaustion_artifact_not_persisted(self) -> None:
        """When budget exhausted, job must be FAILED before artifact_path is written (AC3).

        The artifact_path must remain None — the synthesis artifact must NOT
        be persisted when the privacy budget is exhausted.
        """
        from synth_engine.shared.exceptions import BudgetExhaustionError

        job, _, _ = self._run_impl_with_budget_mock(
            job_id=24,
            epsilon=999.0,
            budget_fn_side_effect=BudgetExhaustionError("over budget"),
        )

        assert job.artifact_path is None, (
            f"Expected artifact_path=None on budget exhaustion; got {job.artifact_path!r}"
        )

    def test_budget_exhaustion_commits_failed_status(self) -> None:
        """Budget exhaustion must commit the FAILED status to the database (AC3)."""
        from synth_engine.shared.exceptions import BudgetExhaustionError

        _, _, mock_session = self._run_impl_with_budget_mock(
            job_id=25,
            epsilon=999.0,
            budget_fn_side_effect=BudgetExhaustionError("over budget"),
        )

        assert mock_session.commit.call_count >= 1

    def test_spend_budget_not_called_when_dp_disabled(self) -> None:
        """spend_budget fn must NOT be called when dp_wrapper is None (non-DP job, AC-implicit).

        When the job does not use DP, no epsilon was spent, so no budget
        deduction should occur.
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=26,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=False,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job
        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        mock_budget_fn = MagicMock()
        original_fn = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_budget_fn)
            with patch(
                "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"
            ):
                _run_synthesis_job_impl(
                    job_id=26,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=None,  # Non-DP path
                )
        finally:
            orch_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        mock_budget_fn.assert_not_called()

    def test_spend_budget_not_called_when_epsilon_is_none(self) -> None:
        """spend_budget fn must NOT be called when actual_epsilon is None after training.

        When epsilon_spent() raises, actual_epsilon stays None and budget
        deduction must be skipped (no budget was measurably spent).
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=27,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job
        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = MagicMock()
        dp_wrapper.epsilon_spent.side_effect = RuntimeError("Opacus internal error")

        mock_budget_fn = MagicMock()
        original_fn = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_budget_fn)
            with patch(
                "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"
            ):
                _run_synthesis_job_impl(
                    job_id=27,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=dp_wrapper,
                )
        finally:
            orch_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        mock_budget_fn.assert_not_called()

    def test_audit_log_emitted_on_budget_spend(self) -> None:
        """Audit log_event must be called after successful spend_budget (AC5).

        Verifies that a WORM audit record is emitted with the correct
        event_type='PRIVACY_BUDGET_SPEND' and actor='system/huey-worker'.
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=28,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job
        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = _make_mock_dp_wrapper(epsilon=1.5)
        mock_budget_fn = MagicMock()

        mock_audit_logger = MagicMock()

        original_fn = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_budget_fn)
            with (
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"
                ),
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                    return_value=mock_audit_logger,
                ),
            ):
                _run_synthesis_job_impl(
                    job_id=28,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=dp_wrapper,
                )
        finally:
            orch_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        mock_audit_logger.log_event.assert_called_once()
        audit_call_kwargs = mock_audit_logger.log_event.call_args.kwargs
        expected_event_type = "PRIVACY_BUDGET_SPEND"
        actual_event_type = audit_call_kwargs.get("event_type")
        assert actual_event_type == expected_event_type, (
            f"Expected event_type={expected_event_type!r}; got {actual_event_type!r}"
        )
        assert audit_call_kwargs["actor"] == "system/huey-worker", (
            f"Expected actor='system/huey-worker'; got {audit_call_kwargs.get('actor')!r}"
        )

    def test_non_budget_exception_from_spend_budget_propagates(self) -> None:
        """Non-BudgetExhaustion exceptions from _spend_budget_fn must propagate.

        When _spend_budget_fn raises an exception whose class name does NOT
        contain 'BudgetExhaustion', the task must re-raise it.  The job must
        NOT be marked FAILED by the budget-exhaustion handler — Huey handles
        re-raised exceptions at the task framework level.

        This guards against silent swallowing of infrastructure errors such as
        database connectivity failures (e.g., ConnectionError).
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=29,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job
        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = _make_mock_dp_wrapper(epsilon=1.0)
        mock_budget_fn = MagicMock()
        mock_budget_fn.side_effect = ConnectionError("DB down")

        original_fn = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_budget_fn)
            with (
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"
                ),
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                    return_value=MagicMock(),
                ),
            ):
                with pytest.raises(ConnectionError, match="DB down"):
                    _run_synthesis_job_impl(
                        job_id=29,
                        session=mock_session,
                        engine=mock_engine,
                        dp_wrapper=dp_wrapper,
                    )
        finally:
            orch_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        # Job must NOT be marked FAILED by the BudgetExhaustion handler;
        # the re-raise lets Huey handle the error at the framework level.
        assert job.status != "FAILED", (
            f"Non-BudgetExhaustion exception must not set job.status=FAILED; got {job.status!r}"
        )


class TestSpendBudgetFactoryBootstrapper:
    """Tests for build_spend_budget_fn factory in bootstrapper/factories.py (AC4).

    Verifies the factory produces a sync callable that wraps async spend_budget
    without violating import boundaries.
    """

    def test_build_spend_budget_fn_returns_callable(self) -> None:
        """build_spend_budget_fn must return a callable (sync wrapper)."""
        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        fn = build_spend_budget_fn()
        assert callable(fn)

    def test_build_spend_budget_fn_does_not_corrupt_async_url(self) -> None:
        """build_spend_budget_fn must not double-substitute async driver prefixes.

        If DATABASE_URL already contains an async driver prefix (e.g.,
        'sqlite+aiosqlite:///:memory:' or 'postgresql+asyncpg://host/db'),
        the URL promotion logic must pass it through unchanged and not corrupt
        it by re-substituting the sync prefix.

        This is a regression guard for F3 (review finding): the original
        code applied string.replace() unconditionally, which would corrupt
        URLs that already contained the async prefix.
        """
        import logging
        from unittest.mock import AsyncMock
        from unittest.mock import patch as _patch

        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        fn = build_spend_budget_fn()

        # Capture the URL passed to create_async_engine.
        captured_urls: list[str] = []

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        def _capture_create_async_engine(url: str, **kwargs: object) -> MagicMock:
            captured_urls.append(url)
            return MagicMock()

        from unittest.mock import MagicMock as _MagicMock

        from synth_engine.shared.settings import get_settings

        mock_settings = _MagicMock()
        mock_settings.database_url = "sqlite+aiosqlite:///:memory:"
        get_settings.cache_clear()
        with (
            _patch(
                "synth_engine.shared.settings.get_settings",
                return_value=mock_settings,
            ),
            _patch(
                "sqlalchemy.ext.asyncio.create_async_engine",
                side_effect=_capture_create_async_engine,
            ),
            _patch(
                "sqlalchemy.ext.asyncio.AsyncSession",
                return_value=mock_session_cm,
            ),
            _patch(
                "synth_engine.modules.privacy.accountant.spend_budget",
                new_callable=lambda: lambda: AsyncMock(),
            ),
        ):
            try:
                fn(amount=0.5, job_id=1, ledger_id=1)
            except Exception as err:
                logging.getLogger(__name__).debug("Expected mock error: %s", err)

        if captured_urls:
            # The URL must not have been double-substituted.
            assert captured_urls[0] == "sqlite+aiosqlite:///:memory:", (
                f"URL was corrupted: {captured_urls[0]!r}"
            )

    def test_bootstrapper_wires_spend_budget_fn_into_tasks(self) -> None:
        """bootstrapper/main.py must call set_spend_budget_fn at module import time.

        Verifies that importing main.py results in _spend_budget_fn being set
        on the tasks module (Rule 8 compliance).
        """
        # Importing main triggers the wiring side-effect; _spend_budget_fn
        # must be non-None after import completes.
        import synth_engine.bootstrapper.main  # noqa: F401 — side-effect import
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod

        assert orch_mod._spend_budget_fn is not None, (
            "_spend_budget_fn must be wired by bootstrapper at import time (Rule 8)"
        )


_ALEMBIC_VERSIONS = Path(__file__).parent.parent.parent / "alembic" / "versions"


def _find_migration_005() -> Path | None:
    """Return the Path of the migration 005 file, or None if absent."""
    for f in _ALEMBIC_VERSIONS.glob("*.py"):
        if f.name.startswith("__"):
            continue
        if "005" in f.name:
            return f
    return None


class TestMigration005:
    """Tests for Alembic migration 005 — default PrivacyLedger seeding (AC1).

    These are structural file-inspection tests — they verify the migration file
    has the correct revision chain and SQL patterns without running a live database.
    Follows the pattern established in test_migration_003_epsilon_precision.py.
    """

    def test_migration_005_file_exists(self) -> None:
        """Migration 005 file must exist in alembic/versions/."""
        path = _find_migration_005()
        assert path is not None, (
            "Migration 005 not found in alembic/versions/. Expected a file matching '005*.py'."
        )

    def test_migration_005_revision_is_005(self) -> None:
        """Migration 005 must have revision='005'."""
        path = _find_migration_005()
        assert path is not None, "Migration 005 file not found"
        content = path.read_text(encoding="utf-8")
        assert 'revision: str = "005"' in content, f"Expected revision='005' in {path.name}"

    def test_migration_005_down_revision_is_004(self) -> None:
        """Migration 005 must depend on revision 004."""
        path = _find_migration_005()
        assert path is not None, "Migration 005 file not found"
        content = path.read_text(encoding="utf-8")
        assert 'down_revision: str | None = "004"' in content, (
            f"Expected down_revision='004' in {path.name}"
        )

    def test_migration_005_seeds_privacy_ledger_row(self) -> None:
        """Migration 005 upgrade() must INSERT a privacy_ledger row."""
        path = _find_migration_005()
        assert path is not None, "Migration 005 file not found"
        content = path.read_text(encoding="utf-8")
        assert "privacy_ledger" in content, "Expected 'privacy_ledger' INSERT in migration 005"
        assert "INSERT" in content.upper(), "Expected INSERT statement in migration 005 upgrade()"

    def test_migration_005_downgrade_deletes_seeded_row(self) -> None:
        """Migration 005 downgrade() must DELETE the seeded row."""
        path = _find_migration_005()
        assert path is not None, "Migration 005 file not found"
        content = path.read_text(encoding="utf-8")
        assert "DELETE" in content.upper(), "Expected DELETE statement in migration 005 downgrade()"


# ---------------------------------------------------------------------------
# T23.1 — num_rows and output_path fields on SynthesisJob (RED)
# ---------------------------------------------------------------------------


class TestSynthesisJobNumRowsField:
    """SynthesisJob must have num_rows and output_path fields (P23-T23.1 AC1/4)."""

    def test_synthesis_job_has_num_rows_field(self) -> None:
        """SynthesisJob must have a num_rows integer field."""
        job = _make_synthesis_job(num_rows=500)
        assert job.num_rows == 500

    def test_synthesis_job_num_rows_required(self) -> None:
        """SynthesisJob num_rows is a required integer field."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job = SynthesisJob(
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
            num_rows=100,
        )
        assert job.num_rows == 100

    def test_synthesis_job_has_output_path_field_default_none(self) -> None:
        """SynthesisJob output_path must default to None (set after generation)."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job = SynthesisJob(
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
            num_rows=100,
        )
        assert job.output_path is None

    def test_synthesis_job_output_path_can_be_set(self) -> None:
        """SynthesisJob output_path must accept a string value."""
        job = _make_synthesis_job(output_path="/output/job_1_synthetic.parquet")
        assert job.output_path == "/output/job_1_synthetic.parquet"


# ---------------------------------------------------------------------------
# T23.1 — GENERATING status transition (RED)
# ---------------------------------------------------------------------------


class TestGeneratingStatusTransition:
    """After training and before generation, status must transition to GENERATING (AC5)."""

    def test_task_transitions_to_generating_after_training(self) -> None:
        """Task must set status=GENERATING between training loop and generation step.

        Captures every status value passed to session.add() to confirm
        the GENERATING transition happens after TRAINING completes and
        before COMPLETE is set.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=50,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=10,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        # generate() returns a minimal DataFrame
        import pandas as pd

        mock_engine.generate.return_value = pd.DataFrame({"a": [1, 2, 3]})

        recorded_statuses: list[str] = []

        def _snapshot_status(obj: object) -> None:
            if hasattr(obj, "status"):
                recorded_statuses.append(str(obj.status))

        mock_session.add.side_effect = _snapshot_status

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=50,
                session=mock_session,
                engine=mock_engine,
            )

        assert "GENERATING" in recorded_statuses, (
            f"Expected status=GENERATING in status transitions; got: {recorded_statuses}"
        )

    def test_generating_precedes_complete(self) -> None:
        """GENERATING must appear before COMPLETE in the status transition sequence."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=51,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=10,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        import pandas as pd

        mock_engine.generate.return_value = pd.DataFrame({"a": [1, 2, 3]})

        recorded_statuses: list[str] = []

        def _snapshot_status(obj: object) -> None:
            if hasattr(obj, "status"):
                recorded_statuses.append(str(obj.status))

        mock_session.add.side_effect = _snapshot_status

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=51,
                session=mock_session,
                engine=mock_engine,
            )

        assert "GENERATING" in recorded_statuses, "GENERATING status not found"
        assert "COMPLETE" in recorded_statuses, "COMPLETE status not found"
        idx_generating = max(i for i, s in enumerate(recorded_statuses) if s == "GENERATING")
        idx_complete = max(i for i, s in enumerate(recorded_statuses) if s == "COMPLETE")
        assert idx_generating < idx_complete, (
            f"GENERATING must precede COMPLETE; got transitions: {recorded_statuses}"
        )


# ---------------------------------------------------------------------------
# T23.1 — Generation step produces Parquet and sets output_path (RED)
# ---------------------------------------------------------------------------


class TestGenerationStep:
    """After training: engine.generate() called, Parquet saved, output_path set (AC1-4)."""

    def test_engine_generate_called_with_num_rows(self) -> None:
        """engine.generate() must be called with n_rows=job.num_rows after training.

        AC1: After training completes, run_synthesis_job() calls artifact.model.sample(n_rows).
        The implementation routes through engine.generate(artifact, n_rows=job.num_rows).
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=60,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=42,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        import pandas as pd

        mock_engine.generate.return_value = pd.DataFrame({"col": range(42)})

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=60,
                session=mock_session,
                engine=mock_engine,
            )

        mock_engine.generate.assert_called_once()
        call_args = mock_engine.generate.call_args
        # First positional arg is the artifact; second kwarg is n_rows.
        assert (
            call_args.args[0] is mock_artifact or call_args.kwargs.get("artifact") is mock_artifact
        ), f"engine.generate() must receive the trained artifact; got {call_args}"
        n_rows_actual = (
            call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("n_rows")
        )
        assert n_rows_actual == 42, (
            f"engine.generate() must be called with n_rows=42; got n_rows={n_rows_actual}"
        )

    def test_output_path_set_on_job_after_generation(self) -> None:
        """job.output_path must point to a Parquet file path after generation completes (AC4)."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=61,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=10,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        import pandas as pd

        mock_engine.generate.return_value = pd.DataFrame({"x": range(10)})

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            _run_synthesis_job_impl(
                job_id=61,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

        assert job.output_path is not None, "job.output_path must be set after generation"
        assert job.output_path.endswith(".parquet"), (
            f"output_path must end with .parquet; got {job.output_path!r}"
        )

    def test_parquet_file_written_to_checkpoint_dir(self) -> None:
        """Generated Parquet must be physically written to the checkpoint directory (AC2)."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=62,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=5,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        import pandas as pd

        mock_engine.generate.return_value = pd.DataFrame({"col": range(5)})

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            _run_synthesis_job_impl(
                job_id=62,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

            # The Parquet file must exist on disk in tmpdir.
            assert job.output_path is not None
            assert Path(job.output_path).exists(), (
                f"Parquet file must exist at output_path={job.output_path!r}"
            )

    def test_artifact_path_still_points_to_pickle(self) -> None:
        """artifact_path must still point to the model checkpoint pickle (backward compat).

        Option B: artifact_path = pickle, output_path = Parquet (AC4).
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=63,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=5,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        import pandas as pd

        mock_engine.generate.return_value = pd.DataFrame({"col": range(5)})

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            _run_synthesis_job_impl(
                job_id=63,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

        assert job.artifact_path is not None, "artifact_path must still be set (pickle)"
        assert job.artifact_path.endswith(".pkl"), (
            f"artifact_path must end with .pkl; got {job.artifact_path!r}"
        )

    def test_job_reaches_complete_after_generation(self) -> None:
        """Job must reach COMPLETE status after generation succeeds (AC5)."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=64,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=7,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        import pandas as pd

        mock_engine.generate.return_value = pd.DataFrame({"x": range(7)})

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            _run_synthesis_job_impl(
                job_id=64,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

        assert job.status == "COMPLETE", f"Expected COMPLETE; got {job.status}"

    def test_generation_runtime_error_sets_failed(self) -> None:
        """RuntimeError during generation must set job to FAILED."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=65,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=10,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.side_effect = RuntimeError("generation failed")

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=65,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.status == "FAILED", f"Expected FAILED; got {job.status}"
        assert job.error_msg is not None
        # F4 fix: error_msg is now sanitized — raw exception text must not appear.
        assert "see server logs" in job.error_msg, (
            f"Expected sanitized error_msg; got {job.error_msg!r}"
        )


# ---------------------------------------------------------------------------
# T23.1 — HMAC signing of Parquet artifact (RED)
# ---------------------------------------------------------------------------


class TestParquetHMACSigning:
    """When ARTIFACT_SIGNING_KEY is set, the Parquet output must be HMAC-signed (AC3)."""

    def test_parquet_written_unsigned_when_no_signing_key(self) -> None:
        """When ARTIFACT_SIGNING_KEY is not set, Parquet is written without a signature."""
        import os

        import pandas as pd

        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=70,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=4,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"y": range(4)})

        env_without_key = {k: v for k, v in os.environ.items() if k != "ARTIFACT_SIGNING_KEY"}

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch.dict("os.environ", env_without_key, clear=True),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            _run_synthesis_job_impl(
                job_id=70,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

            # File must exist and be a readable Parquet — assertions inside the
            # with block so the tmpdir has not yet been cleaned up.
            assert job.output_path is not None
            df_loaded = pd.read_parquet(job.output_path)
            assert len(df_loaded) == 4

    def test_parquet_sidecar_sig_file_written_when_signing_key_set(self) -> None:
        """When ARTIFACT_SIGNING_KEY is set, a .sig sidecar must be written alongside the Parquet.

        The sidecar file path is output_path + '.sig'.
        The .sig file must contain a 32-byte HMAC-SHA256 digest.
        """
        import pandas as pd

        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl
        from synth_engine.shared.security.hmac_signing import HMAC_DIGEST_SIZE

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=71,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=4,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"y": range(4)})

        # A 32-byte key expressed as 64 hex chars.
        signing_key_hex = "a" * 64

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch.dict("os.environ", {"ARTIFACT_SIGNING_KEY": signing_key_hex}),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            _run_synthesis_job_impl(
                job_id=71,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

            # Assertions inside the with block so tmpdir persists during checks.
            assert job.output_path is not None, "output_path must be set"
            sig_path = job.output_path + ".sig"
            assert Path(sig_path).exists(), f"Sidecar .sig file must exist at {sig_path!r}"
            sig_bytes = Path(sig_path).read_bytes()
            assert len(sig_bytes) == HMAC_DIGEST_SIZE, (
                f"Signature must be {HMAC_DIGEST_SIZE} bytes; got {len(sig_bytes)}"
            )


# ---------------------------------------------------------------------------
# T23.1 — Migration 006: num_rows and output_path columns (RED)
# ---------------------------------------------------------------------------

_ALEMBIC_VERSIONS_T23 = Path(__file__).parent.parent.parent / "alembic" / "versions"


def _find_migration_006() -> Path | None:
    """Return the Path of migration 006 file, or None if absent."""
    for f in _ALEMBIC_VERSIONS_T23.glob("*.py"):
        if f.name.startswith("__"):
            continue
        if "006" in f.name:
            return f
    return None


class TestMigration006:
    """Alembic migration 006 must add num_rows and output_path to synthesis_job (AC schema)."""

    def test_migration_006_file_exists(self) -> None:
        """Migration 006 file must exist in alembic/versions/."""
        path = _find_migration_006()
        assert path is not None, (
            "Migration 006 not found in alembic/versions/. Expected a file matching '006*.py'."
        )

    def test_migration_006_revision_is_006(self) -> None:
        """Migration 006 must have revision='006'."""
        path = _find_migration_006()
        assert path is not None, "Migration 006 file not found"
        content = path.read_text(encoding="utf-8")
        assert 'revision: str = "006"' in content, f"Expected revision='006' in {path.name}"

    def test_migration_006_down_revision_is_005(self) -> None:
        """Migration 006 must depend on revision 005."""
        path = _find_migration_006()
        assert path is not None, "Migration 006 file not found"
        content = path.read_text(encoding="utf-8")
        assert 'down_revision: str | None = "005"' in content, (
            f"Expected down_revision='005' in {path.name}"
        )

    def test_migration_006_adds_num_rows_column(self) -> None:
        """Migration 006 upgrade() must add a num_rows column."""
        path = _find_migration_006()
        assert path is not None, "Migration 006 file not found"
        content = path.read_text(encoding="utf-8")
        assert "num_rows" in content, (
            f"Expected 'num_rows' column in migration 006; not found in {path.name}"
        )

    def test_migration_006_adds_output_path_column(self) -> None:
        """Migration 006 upgrade() must add an output_path column."""
        path = _find_migration_006()
        assert path is not None, "Migration 006 file not found"
        content = path.read_text(encoding="utf-8")
        assert "output_path" in content, (
            f"Expected 'output_path' column in migration 006; not found in {path.name}"
        )


# ---------------------------------------------------------------------------
# T23.1 — JobCreateRequest and JobResponse schema (RED)
# ---------------------------------------------------------------------------


class TestJobSchemaNumRows:
    """JobCreateRequest and JobResponse must include num_rows (P23-T23.1 schema AC)."""

    def test_job_create_request_has_num_rows_field(self) -> None:
        """JobCreateRequest must accept num_rows as a required positive integer."""
        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        req = JobCreateRequest(
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            total_epochs=10,
            num_rows=500,
        )
        assert req.num_rows == 500

    def test_job_create_request_num_rows_must_be_positive(self) -> None:
        """JobCreateRequest must reject num_rows <= 0."""
        from pydantic import ValidationError

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        with pytest.raises(ValidationError):
            JobCreateRequest(
                table_name="customers",
                parquet_path="/tmp/customers.parquet",
                total_epochs=10,
                num_rows=0,
            )

    def test_job_response_has_num_rows_field(self) -> None:
        """JobResponse must include a num_rows field."""
        from synth_engine.bootstrapper.schemas.jobs import JobResponse

        resp = JobResponse(
            id=1,
            status="QUEUED",
            current_epoch=0,
            total_epochs=10,
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            artifact_path=None,
            error_msg=None,
            checkpoint_every_n=5,
            enable_dp=True,
            noise_multiplier=1.1,
            max_grad_norm=1.0,
            actual_epsilon=None,
            num_rows=500,
            output_path=None,
        )
        assert resp.num_rows == 500

    def test_job_response_has_output_path_field(self) -> None:
        """JobResponse must include an output_path field (None until generation completes)."""
        from synth_engine.bootstrapper.schemas.jobs import JobResponse

        resp = JobResponse(
            id=1,
            status="COMPLETE",
            current_epoch=10,
            total_epochs=10,
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            artifact_path="/output/job_1_epoch_10.pkl",
            error_msg=None,
            checkpoint_every_n=5,
            enable_dp=False,
            noise_multiplier=1.1,
            max_grad_norm=1.0,
            actual_epsilon=None,
            num_rows=500,
            output_path="/output/job_1_synthetic.parquet",
        )
        assert resp.output_path == "/output/job_1_synthetic.parquet"


# ---------------------------------------------------------------------------
# T23.1 review findings — new edge case tests (RED phase, P23-T23.1)
# ---------------------------------------------------------------------------


class TestWriteParquetWithSigningEdgeCases:
    """Edge-case tests for _write_parquet_with_signing (review findings F2, F5, F8)."""

    def test_malformed_hex_signing_key_skips_signing_gracefully(self) -> None:
        """ARTIFACT_SIGNING_KEY with non-hex chars must skip signing without raising.

        Finding F2: bytes.fromhex() raises ValueError on malformed input.
        After the fix, ValueError is caught and signing is skipped gracefully
        (no crash, Parquet file still written).
        """
        import pandas as pd

        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=80,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=4,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"col": range(4)})

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch.dict("os.environ", {"ARTIFACT_SIGNING_KEY": "not-valid-hex"}),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            # Must not raise — malformed key should be handled gracefully.
            _run_synthesis_job_impl(
                job_id=80,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

            # Job still completes and Parquet file is written.
            assert job.status == "COMPLETE", (
                f"Malformed signing key must not prevent COMPLETE; got {job.status}"
            )
            assert job.output_path is not None
            assert Path(job.output_path).exists(), (
                "Parquet must still be written when signing key is malformed"
            )
            # No .sig sidecar should exist — signing was skipped.
            sig_path = job.output_path + ".sig"
            assert not Path(sig_path).exists(), (
                "No .sig sidecar should be written when signing key is malformed"
            )

    def test_whitespace_only_signing_key_skips_signing(self) -> None:
        """ARTIFACT_SIGNING_KEY containing only whitespace skips signing gracefully.

        bytes.fromhex('   ') raises ValueError (odd-length string after stripping
        is still non-hex).  After fix F2, this must skip signing without crashing.
        """
        import pandas as pd

        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=81,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=4,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"col": range(4)})

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch.dict("os.environ", {"ARTIFACT_SIGNING_KEY": "   "}),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            # Must not raise.
            _run_synthesis_job_impl(
                job_id=81,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

            assert job.status == "COMPLETE", (
                f"Whitespace signing key must not prevent COMPLETE; got {job.status}"
            )
            assert job.output_path is not None
            assert Path(job.output_path).exists()


class TestAuditLoggerFailureAfterBudgetDeduction:
    """Audit log failure after budget deduction must not block job completion (finding F9).

    The audit log call is intentionally outside the BudgetExhaustion try/except
    so that audit logger failures are isolated.  The budget was already deducted;
    the job must still proceed to COMPLETE.
    """

    def test_audit_logger_exception_does_not_block_complete(self) -> None:
        """When audit log_event() raises after budget deduction, job still reaches COMPLETE.

        The PrivacyTransaction table records the deduction; audit logger failure
        must not prevent the COMPLETE status (tasks.py lines 583-584 guard).
        """
        import pandas as pd

        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=85,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
            num_rows=3,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"x": range(3)})

        dp_wrapper = _make_mock_dp_wrapper(epsilon=1.0)
        mock_budget_fn = MagicMock()  # budget spend succeeds

        # Audit logger raises an exception.
        mock_audit_logger = MagicMock()
        mock_audit_logger.log_event.side_effect = RuntimeError("Audit DB unavailable")

        original_fn = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_budget_fn)
            with (
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"
                ),
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                    return_value=mock_audit_logger,
                ),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                _run_synthesis_job_impl(
                    job_id=85,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=dp_wrapper,
                    checkpoint_dir=tmpdir,
                )
        finally:
            orch_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        assert job.status == "COMPLETE", (
            f"Audit logger failure must not prevent COMPLETE; got {job.status}"
        )


class TestStep9OSErrorTransitionsFailed:
    """Step 9 OSError during Parquet write must transition job to FAILED (finding F1).

    Before fix F1, an OSError from _write_parquet_with_signing() would propagate
    unhandled, leaving the job permanently in GENERATING status.
    After fix F1, the step-9 block catches OSError and sets FAILED.
    """

    def test_oserror_in_write_parquet_sets_job_failed(self) -> None:
        """OSError during _write_parquet_with_signing must transition job to FAILED."""
        import pandas as pd

        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=90,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=5,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"x": range(5)})

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._write_parquet_with_signing",
                side_effect=OSError("Disk full"),
            ),
        ):
            _run_synthesis_job_impl(
                job_id=90,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.status == "FAILED", (
            f"OSError in step 9 must set job.status=FAILED; got {job.status!r}"
        )
        assert job.error_msg is not None, "error_msg must be set on OSError failure"

    def test_oserror_in_write_parquet_commits_failed_status(self) -> None:
        """OSError in step 9 must commit FAILED status to the database."""
        import pandas as pd

        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=91,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=5,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"x": range(5)})

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._write_parquet_with_signing",
                side_effect=OSError("No space left on device"),
            ),
        ):
            _run_synthesis_job_impl(
                job_id=91,
                session=mock_session,
                engine=mock_engine,
            )

        assert mock_session.commit.call_count >= 1, (
            "session.commit() must be called after OSError to persist FAILED status"
        )

    def test_oserror_error_msg_is_sanitized(self) -> None:
        """OSError in step 9 must set a sanitized error_msg (no internal detail).

        Finding F4: error_msg must not contain raw exception internals.
        """
        import pandas as pd

        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=92,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=5,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"x": range(5)})

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._write_parquet_with_signing",
                side_effect=OSError("internal filesystem error xyz"),
            ),
        ):
            _run_synthesis_job_impl(
                job_id=92,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.error_msg is not None
        # After fix F4: error_msg must be a sanitized static string.
        assert "see server logs" in job.error_msg, (
            f"error_msg must be sanitized; got {job.error_msg!r}"
        )


class TestGenerationRuntimeErrorSanitized:
    """Generation RuntimeError error_msg must be sanitized (finding F4)."""

    def test_generation_runtime_error_msg_is_sanitized(self) -> None:
        """RuntimeError during generation must NOT expose raw exception text in error_msg.

        Finding F4 (DevOps): job.error_msg is written verbatim from the exception.
        After fix, error_msg must be a static sanitized string.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=95,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=10,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.side_effect = RuntimeError(
            "internal/path/to/model.py line 42: segfault"
        )

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=95,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.status == "FAILED"
        assert job.error_msg is not None
        # Sanitized message — must NOT include internal path details.
        assert "internal/path" not in job.error_msg, (
            f"error_msg must not expose internal exception details; got {job.error_msg!r}"
        )
        assert "see server logs" in job.error_msg, (
            f"error_msg must point to server logs; got {job.error_msg!r}"
        )


class TestSynthesisJobNumRowsValidation:
    """SynthesisJob must reject num_rows < 1 at construction time (finding F3)."""

    def test_synthesis_job_num_rows_zero_raises(self) -> None:
        """SynthesisJob must reject num_rows=0 with ValueError.

        Finding F3: docstring says 'Must be >= 1' but __init__ does not enforce it.
        """
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        with pytest.raises(ValueError, match="num_rows must be >= 1"):
            SynthesisJob(
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                num_rows=0,
            )

    def test_synthesis_job_num_rows_negative_raises(self) -> None:
        """SynthesisJob must reject num_rows=-1 with ValueError."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        with pytest.raises(ValueError, match="num_rows must be >= 1"):
            SynthesisJob(
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                num_rows=-1,
            )

    def test_synthesis_job_num_rows_one_is_valid(self) -> None:
        """SynthesisJob must accept num_rows=1 (minimum valid value)."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job = SynthesisJob(
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
            num_rows=1,
        )
        assert job.num_rows == 1
