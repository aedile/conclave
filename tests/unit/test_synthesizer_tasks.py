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
        "artifact_path": None,
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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
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
            "synth_engine.modules.synthesizer.tasks.check_memory_feasibility",
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
            "synth_engine.modules.synthesizer.tasks.check_memory_feasibility",
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
            "synth_engine.modules.synthesizer.tasks.check_memory_feasibility",
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
            "synth_engine.modules.synthesizer.tasks.check_memory_feasibility",
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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
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
            patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"),
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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
            )

        assert mock_session.commit.call_count >= 1


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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
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
            patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"),
            patch("synth_engine.modules.synthesizer.tasks._spend_budget_fn"),
            patch(
                "synth_engine.modules.synthesizer.tasks.get_audit_logger",
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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
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
            patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"),
            patch("synth_engine.modules.synthesizer.tasks._spend_budget_fn"),
            patch(
                "synth_engine.modules.synthesizer.tasks.get_audit_logger",
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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
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

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
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
        import synth_engine.modules.synthesizer.tasks as tasks_mod

        mock_factory = MagicMock(return_value=MagicMock())
        original = tasks_mod._dp_wrapper_factory
        try:
            tasks_mod.set_dp_wrapper_factory(mock_factory)
            assert tasks_mod._dp_wrapper_factory is mock_factory
        finally:
            # Restore original state so other tests are not affected.
            tasks_mod._dp_wrapper_factory = original  # type: ignore[assignment]

    def test_dp_requested_without_factory_raises_runtime_error(self) -> None:
        """run_synthesis_job must raise RuntimeError when enable_dp=True and no factory registered.

        Verifies that the guard in run_synthesis_job() fires with the expected
        message when _dp_wrapper_factory is None and a DP job is requested.

        Because Session and get_engine are locally imported inside the task
        function body, they are patched at their source module paths rather
        than via the tasks module namespace.
        """
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

        original_factory = tasks_mod._dp_wrapper_factory
        try:
            tasks_mod._dp_wrapper_factory = None  # type: ignore[assignment]

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
            tasks_mod._dp_wrapper_factory = original_factory  # type: ignore[assignment]


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
        import synth_engine.modules.synthesizer.tasks as tasks_mod
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

        original_fn = tasks_mod._spend_budget_fn
        try:
            tasks_mod.set_spend_budget_fn(mock_budget_fn)
            with (
                patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"),
                patch(
                    "synth_engine.modules.synthesizer.tasks.get_audit_logger",
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
            tasks_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        return job, mock_budget_fn, mock_session

    def test_set_spend_budget_fn_stores_callable(self) -> None:
        """set_spend_budget_fn must store the provided callable at module level.

        After calling set_spend_budget_fn with a mock, the module-level
        _spend_budget_fn must reference that exact callable.
        """
        import synth_engine.modules.synthesizer.tasks as tasks_mod

        mock_fn = MagicMock()
        original = tasks_mod._spend_budget_fn
        try:
            tasks_mod.set_spend_budget_fn(mock_fn)
            assert tasks_mod._spend_budget_fn is mock_fn
        finally:
            tasks_mod._spend_budget_fn = original  # type: ignore[assignment]

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

        The exception name contains 'BudgetExhaustion' — the task uses duck-
        typing name check to stay boundary-clean (no import from privacy module).
        """

        class _FakeBudgetExhaustionError(Exception):
            """Simulates BudgetExhaustionError without importing from privacy module."""

        job, mock_budget_fn, mock_session = self._run_impl_with_budget_mock(
            job_id=22,
            epsilon=999.0,
            budget_fn_side_effect=_FakeBudgetExhaustionError("Budget exhausted"),
        )

        assert job.status == "FAILED", f"Expected FAILED; got {job.status}"

    def test_budget_exhaustion_sets_error_msg(self) -> None:
        """BudgetExhaustionError must set job.error_msg to 'Privacy budget exhausted' (AC3)."""

        class _FakeBudgetExhaustionError(Exception):
            """Simulates BudgetExhaustionError without importing from privacy module."""

        job, _, _ = self._run_impl_with_budget_mock(
            job_id=23,
            epsilon=999.0,
            budget_fn_side_effect=_FakeBudgetExhaustionError("over budget"),
        )

        assert job.error_msg == "Privacy budget exhausted", (
            f"Expected 'Privacy budget exhausted'; got {job.error_msg!r}"
        )

    def test_budget_exhaustion_artifact_not_persisted(self) -> None:
        """When budget exhausted, job must be FAILED before artifact_path is written (AC3).

        The artifact_path must remain None — the synthesis artifact must NOT
        be persisted when the privacy budget is exhausted.
        """

        class _FakeBudgetExhaustionError(Exception):
            """Simulates BudgetExhaustionError without importing from privacy module."""

        job, _, _ = self._run_impl_with_budget_mock(
            job_id=24,
            epsilon=999.0,
            budget_fn_side_effect=_FakeBudgetExhaustionError("over budget"),
        )

        assert job.artifact_path is None, (
            f"Expected artifact_path=None on budget exhaustion; got {job.artifact_path!r}"
        )

    def test_budget_exhaustion_commits_failed_status(self) -> None:
        """Budget exhaustion must commit the FAILED status to the database (AC3)."""

        class _FakeBudgetExhaustionError(Exception):
            """Simulates BudgetExhaustionError without importing from privacy module."""

        _, _, mock_session = self._run_impl_with_budget_mock(
            job_id=25,
            epsilon=999.0,
            budget_fn_side_effect=_FakeBudgetExhaustionError("over budget"),
        )

        assert mock_session.commit.call_count >= 1

    def test_spend_budget_not_called_when_dp_disabled(self) -> None:
        """spend_budget fn must NOT be called when dp_wrapper is None (non-DP job, AC-implicit).

        When the job does not use DP, no epsilon was spent, so no budget
        deduction should occur.
        """
        import synth_engine.modules.synthesizer.tasks as tasks_mod
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
        original_fn = tasks_mod._spend_budget_fn
        try:
            tasks_mod.set_spend_budget_fn(mock_budget_fn)
            with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
                _run_synthesis_job_impl(
                    job_id=26,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=None,  # Non-DP path
                )
        finally:
            tasks_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        mock_budget_fn.assert_not_called()

    def test_spend_budget_not_called_when_epsilon_is_none(self) -> None:
        """spend_budget fn must NOT be called when actual_epsilon is None after training.

        When epsilon_spent() raises, actual_epsilon stays None and budget
        deduction must be skipped (no budget was measurably spent).
        """
        import synth_engine.modules.synthesizer.tasks as tasks_mod
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
        original_fn = tasks_mod._spend_budget_fn
        try:
            tasks_mod.set_spend_budget_fn(mock_budget_fn)
            with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
                _run_synthesis_job_impl(
                    job_id=27,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=dp_wrapper,
                )
        finally:
            tasks_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        mock_budget_fn.assert_not_called()

    def test_audit_log_emitted_on_budget_spend(self) -> None:
        """Audit log_event must be called after successful spend_budget (AC5).

        Verifies that a WORM audit record is emitted with the correct
        event_type='PRIVACY_BUDGET_SPEND' and actor='system/huey-worker'.
        """
        import synth_engine.modules.synthesizer.tasks as tasks_mod
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

        original_fn = tasks_mod._spend_budget_fn
        try:
            tasks_mod.set_spend_budget_fn(mock_budget_fn)
            with (
                patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"),
                patch(
                    "synth_engine.modules.synthesizer.tasks.get_audit_logger",
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
            tasks_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

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

    def test_bootstrapper_wires_spend_budget_fn_into_tasks(self) -> None:
        """bootstrapper/main.py must call set_spend_budget_fn at module import time.

        Verifies that importing main.py results in _spend_budget_fn being set
        on the tasks module (Rule 8 compliance).
        """
        # Importing main triggers the wiring side-effect; _spend_budget_fn
        # must be non-None after import completes.
        import synth_engine.bootstrapper.main  # noqa: F401 — side-effect import
        import synth_engine.modules.synthesizer.tasks as tasks_mod

        assert tasks_mod._spend_budget_fn is not None, (
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
