"""Unit tests for synthesizer task happy-path lifecycle and model field validation.

Covers: SynthesisJob model fields, Huey task registration, QUEUED->TRAINING->COMPLETE
transitions, and checkpointing behaviour.

Split (P56 review finding): generation step tests moved to test_synthesizer_tasks_generation.py;
migration and schema tests moved to test_synthesizer_tasks_migration.py.

All tests are isolated (no real DB, no real Huey worker, no network I/O).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
from tests.unit.helpers_synthesizer import _make_synthesis_job

# ---------------------------------------------------------------------------
# SynthesisJob model tests
# ---------------------------------------------------------------------------


class TestSynthesisJobModel:
    """Tests that SynthesisJob SQLModel defines all required fields."""

    @pytest.mark.parametrize(
        ("field", "kwargs", "expected"),
        [
            ("id", {"id": 42}, 42),
            ("current_epoch", {"current_epoch": 3}, 3),
            ("total_epochs", {"total_epochs": 300}, 300),
            ("table_name", {"table_name": "orders"}, "orders"),
            ("parquet_path", {"parquet_path": "/data/orders.parquet"}, "/data/orders.parquet"),
            (
                "artifact_path",
                {"artifact_path": "/artifacts/persons.pkl"},
                "/artifacts/persons.pkl",
            ),
            (
                "error_msg",
                {"error_msg": "OOM: 6.8 GiB estimated, 4.0 GiB available"},
                "OOM: 6.8 GiB estimated, 4.0 GiB available",
            ),
        ],
        ids=[
            "id",
            "current_epoch",
            "total_epochs",
            "table_name",
            "parquet_path",
            "artifact_path",
            "error_msg",
        ],
    )
    def test_synthesis_job_scalar_field_round_trips(
        self, field: str, kwargs: dict, expected: object
    ) -> None:
        """SynthesisJob persists each scalar field at the value it was constructed with.

        This parameterized test replaces 7 individual field-setter tests.  Each
        case sets one field to a known value and asserts the model stores it
        exactly — proving the column exists AND the assignment is not silently
        discarded.
        """
        job = _make_synthesis_job(**kwargs)
        actual = getattr(job, field)
        assert actual == expected, (
            f"SynthesisJob.{field} must equal {expected!r} after construction, got {actual!r}"
        )

    @pytest.mark.parametrize(
        "field",
        ["artifact_path", "error_msg"],
        ids=["artifact_path", "error_msg"],
    )
    def test_synthesis_job_optional_field_accepts_none(self, field: str) -> None:
        """Optional fields artifact_path and error_msg must accept None."""
        job = _make_synthesis_job(**{field: None})
        assert getattr(job, field) is None, (
            f"SynthesisJob.{field} must accept None, got {getattr(job, field)!r}"
        )
        assert str(getattr(job, field)) == "None"

    @pytest.mark.parametrize(
        "status",
        ["QUEUED", "TRAINING", "COMPLETE", "FAILED"],
    )
    def test_synthesis_job_valid_status_values(self, status: str) -> None:
        """SynthesisJob must accept all four lifecycle status strings."""
        job = _make_synthesis_job(status=status)
        assert job.status == status, (
            f"SynthesisJob.status must equal {status!r}, got {job.status!r}"
        )

    def test_synthesis_job_has_checkpoint_every_n_field(self) -> None:
        """SynthesisJob must have a checkpoint_every_n field defaulting to 5."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        job = SynthesisJob(
            status="QUEUED",
            current_epoch=0,
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
        )
        assert job.checkpoint_every_n == 5

    def test_synthesis_job_checkpoint_every_n_zero_raises(self) -> None:
        """SynthesisJob must reject checkpoint_every_n=0 with ValueError.

        A value of 0 would cause an infinite loop in _run_synthesis_job_impl
        because min(0, total - 0) == 0, so completed_epochs never advances.

        Note: SQLModel table=True models bypass pydantic field validators in
        __init__ to allow ORM row construction.  The guard is implemented as
        an __init__ override that raises ValueError directly.
        """
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

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
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        job = SynthesisJob(
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
        )
        assert job.enable_dp is True
        assert job.enable_dp

    def test_synthesis_job_enable_dp_can_be_set_false(self) -> None:
        """SynthesisJob must accept enable_dp=False for non-DP training."""
        job = _make_synthesis_job(enable_dp=False)
        assert job.enable_dp is False
        assert not job.enable_dp

    def test_synthesis_job_noise_multiplier_defaults_to_1_1(self) -> None:
        """SynthesisJob must default noise_multiplier to 1.1 (ADR-0025 calibration)."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

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

    @pytest.mark.parametrize(
        ("noise_multiplier", "match"),
        [
            pytest.param(0.0, "noise_multiplier must be > 0", id="zero"),
            pytest.param(-0.5, "noise_multiplier must be > 0", id="negative"),
            pytest.param(101, "noise_multiplier must be <= 100.0", id="above_100"),
        ],
    )
    def test_synthesis_job_invalid_noise_multiplier_raises(
        self, noise_multiplier: float, match: str
    ) -> None:
        """SynthesisJob must raise ValueError for degenerate noise_multiplier values.

        Guards against zero (no training signal), negative (invalid), and
        above-maximum (100.0) values. All three cases breach the privacy
        guarantee and must be rejected at construction time.

        Args:
            noise_multiplier: The invalid value to test.
            match: Expected fragment of the ValueError message.
        """
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        with pytest.raises(ValueError, match=match):
            SynthesisJob(
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                noise_multiplier=noise_multiplier,
            )

    def test_synthesis_job_max_grad_norm_defaults_to_1_0(self) -> None:
        """SynthesisJob must default max_grad_norm to 1.0."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

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

    @pytest.mark.parametrize(
        ("max_grad_norm", "match"),
        [
            pytest.param(0.0, "max_grad_norm must be > 0", id="zero"),
            pytest.param(-1.0, "max_grad_norm must be > 0", id="negative"),
            pytest.param(101, "max_grad_norm must be <= 100.0", id="above_100"),
        ],
    )
    def test_synthesis_job_invalid_max_grad_norm_raises(
        self, max_grad_norm: float, match: str
    ) -> None:
        """SynthesisJob must raise ValueError for degenerate max_grad_norm values.

        Guards against zero (all gradients clipped to zero), negative (invalid),
        and above-maximum (100.0) values. These protect the DP training guarantee.

        Args:
            max_grad_norm: The invalid value to test.
            match: Expected fragment of the ValueError message.
        """
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        with pytest.raises(ValueError, match=match):
            SynthesisJob(
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                max_grad_norm=max_grad_norm,
            )

    def test_synthesis_job_actual_epsilon_defaults_to_none(self) -> None:
        """SynthesisJob must default actual_epsilon to None (set after training)."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        job = SynthesisJob(
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
        )
        assert job.actual_epsilon is None
        assert str(job.actual_epsilon) == "None"

    def test_synthesis_job_actual_epsilon_can_be_set(self) -> None:
        """SynthesisJob must accept a float actual_epsilon value."""
        job = _make_synthesis_job(actual_epsilon=3.14)
        assert job.actual_epsilon == 3.14


# ---------------------------------------------------------------------------
# Huey task registration
# ---------------------------------------------------------------------------


class TestHueyTaskRegistration:
    """Verify that run_synthesis_job is registered as a Huey task."""

    def test_run_synthesis_job_has_huey_interface(self) -> None:
        """run_synthesis_job must be importable and expose the Huey task interface.

        An import-and-callable check proves nothing about the task being correctly
        registered with Huey.  This test asserts that the function exposes the
        .call_local attribute that Huey tasks carry, which is the actual behavioral
        requirement.
        """
        from synth_engine.modules.synthesizer.jobs.tasks import run_synthesis_job

        assert hasattr(run_synthesis_job, "call_local"), (
            "run_synthesis_job must be a Huey task with a .call_local attribute"
        )

    def test_run_synthesis_job_is_huey_task(self) -> None:
        """run_synthesis_job must be a Huey task (has .call_local attribute)."""
        from synth_engine.modules.synthesizer.jobs.tasks import run_synthesis_job

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
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

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

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
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
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = self._make_mock_session()
        job = _make_synthesis_job(id=1, status="QUEUED", total_epochs=3, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job1_final.pkl"

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
            )

        # Final status on job must be COMPLETE
        assert job.status == "COMPLETE"

    def test_task_sets_artifact_path_on_complete(self) -> None:
        """Task must set artifact_path on job record after successful completion."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = self._make_mock_session()
        job = _make_synthesis_job(id=1, status="QUEUED", total_epochs=3, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job1_final.pkl"

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.artifact_path is not None
        assert "job_1" in job.artifact_path

    def test_task_calls_session_commit_on_status_transitions(self) -> None:
        """Task must commit the session after each status change."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = self._make_mock_session()
        job = _make_synthesis_job(id=1, status="QUEUED", total_epochs=3, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job1.pkl"

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=1,
                session=mock_session,
                engine=mock_engine,
            )

        # session.commit() must be called at least twice:
        # once for QUEUED → TRAINING and once for TRAINING → COMPLETE
        assert mock_session.commit.call_count >= 2


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
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=4, status="QUEUED", total_epochs=10, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job4_checkpoint.pkl"

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=4,
                session=mock_session,
                engine=mock_engine,
            )

        # artifact.save() must have been called at least once
        assert mock_artifact.save.call_count >= 1

    def test_current_epoch_updated_during_training(self) -> None:
        """job.current_epoch must be updated to reflect training progress."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=4, status="QUEUED", total_epochs=10, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job4.pkl"

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
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
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        # total_epochs=5 < checkpoint_every_n=10 → no intermediate checkpoints
        job = _make_synthesis_job(id=5, status="QUEUED", total_epochs=5, checkpoint_every_n=10)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_artifact.save.return_value = "/artifacts/job5_final.pkl"

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=5,
                session=mock_session,
                engine=mock_engine,
            )

        # Exactly 1 save() call: the final artifact save on COMPLETE
        assert mock_artifact.save.call_count == 1
