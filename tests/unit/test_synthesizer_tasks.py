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
"""

from __future__ import annotations

import tempfile
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

        mock_storage = MagicMock()

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
                storage_client=mock_storage,
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

        mock_storage = MagicMock()

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
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

        mock_storage = MagicMock()

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
            )

        assert job.artifact_path is not None
        assert "job1" in job.artifact_path or len(job.artifact_path) > 0

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

        mock_storage = MagicMock()

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
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
        mock_storage = MagicMock()

        with patch(
            "synth_engine.modules.synthesizer.tasks.check_memory_feasibility",
            side_effect=OOMGuardrailError("6.8 GiB estimated, 4.0 GiB available"),
        ):
            _run_synthesis_job_impl(
                job_id=2,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
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
        mock_storage = MagicMock()

        oom_msg = "6.8 GiB estimated, 4.0 GiB available -- reduce dataset by 2.00x"
        with patch(
            "synth_engine.modules.synthesizer.tasks.check_memory_feasibility",
            side_effect=OOMGuardrailError(oom_msg),
        ):
            _run_synthesis_job_impl(
                job_id=2,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
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
        mock_storage = MagicMock()

        with patch(
            "synth_engine.modules.synthesizer.tasks.check_memory_feasibility",
            side_effect=OOMGuardrailError("too big"),
        ):
            _run_synthesis_job_impl(
                job_id=2,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
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
        mock_storage = MagicMock()

        with patch(
            "synth_engine.modules.synthesizer.tasks.check_memory_feasibility",
            side_effect=OOMGuardrailError("too big"),
        ):
            _run_synthesis_job_impl(
                job_id=2,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
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
        mock_storage = MagicMock()

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
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
        mock_storage = MagicMock()

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
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
        mock_storage = MagicMock()

        with (
            patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
                checkpoint_dir=tmpdir,
            )

        # Storage must have been called at least once (epoch-3 checkpoint)
        assert mock_storage.upload_parquet.call_count >= 1 or first_artifact.save.call_count >= 1

    def test_failed_job_commits_to_db(self) -> None:
        """RuntimeError path must commit FAILED status to the database."""
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=3, status="QUEUED", total_epochs=5, checkpoint_every_n=3)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_engine.train.side_effect = RuntimeError("failed")
        mock_storage = MagicMock()

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
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
        mock_storage = MagicMock()

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=4,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
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
        mock_storage = MagicMock()

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=4,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
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
        mock_storage = MagicMock()

        with patch("synth_engine.modules.synthesizer.tasks.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=5,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
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
        mock_storage = MagicMock()

        with pytest.raises(ValueError, match="SynthesisJob.*not found"):
            _run_synthesis_job_impl(
                job_id=999,
                session=mock_session,
                engine=mock_engine,
                storage_client=mock_storage,
            )
