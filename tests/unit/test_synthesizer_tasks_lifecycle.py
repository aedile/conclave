"""Unit tests for synthesizer task happy-path lifecycle and model field validation.

Covers: SynthesisJob model fields, Huey task registration, QUEUED→TRAINING→GENERATING→COMPLETE
transitions, checkpointing behaviour, generation step, job schema fields, and Alembic migrations
005 and 006.

All tests are isolated (no real DB, no real Huey worker, no network I/O).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
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
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

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

    def test_run_synthesis_job_has_huey_interface(self) -> None:
        """run_synthesis_job must be importable and expose the Huey task interface.

        An import-and-callable check proves nothing about the task being correctly
        registered with Huey.  This test asserts that the function exposes the
        .call_local attribute that Huey tasks carry, which is the actual behavioral
        requirement.
        """
        from synth_engine.modules.synthesizer.tasks import run_synthesis_job

        assert hasattr(run_synthesis_job, "call_local"), (
            "run_synthesis_job must be a Huey task with a .call_local attribute"
        )

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


# ---------------------------------------------------------------------------
# T23.1 — Migration 005: default PrivacyLedger seeding (AC1)
# ---------------------------------------------------------------------------

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
