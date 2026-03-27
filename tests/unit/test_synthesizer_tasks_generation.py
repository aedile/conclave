"""Unit tests for synthesizer generation step: num_rows/output_path fields, GENERATING
status transition, and the full generation pipeline (engine.generate, Parquet writing,
artifact path setting).

Split from test_synthesizer_tasks_lifecycle.py (P56 review finding — file exceeded 600 LOC).
Zero test deletion. All test logic is preserved verbatim.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.helpers_synthesizer import _make_synthesis_job

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
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        job = SynthesisJob(
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
            num_rows=100,
        )
        assert job.num_rows == 100

    def test_synthesis_job_has_output_path_field_default_none(self) -> None:
        """SynthesisJob output_path must default to None (set after generation)."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

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
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

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

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
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
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

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

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=51,
                session=mock_session,
                engine=mock_engine,
            )

        assert "GENERATING" in recorded_statuses, "GENERATING must appear in statuses"
        assert "COMPLETE" in recorded_statuses, "COMPLETE must appear in statuses"
        gen_idx = recorded_statuses.index("GENERATING")
        complete_idx = recorded_statuses.index("COMPLETE")
        assert gen_idx < complete_idx, (
            f"GENERATING (index {gen_idx}) must precede COMPLETE (index {complete_idx}); "
            f"got: {recorded_statuses}"
        )

    def test_training_precedes_generating(self) -> None:
        """TRAINING must appear before GENERATING in the status sequence."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=52,
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

        mock_engine.generate.return_value = pd.DataFrame({"a": range(5)})

        recorded_statuses: list[str] = []

        def _snapshot_status(obj: object) -> None:
            if hasattr(obj, "status"):
                recorded_statuses.append(str(obj.status))

        mock_session.add.side_effect = _snapshot_status

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=52,
                session=mock_session,
                engine=mock_engine,
            )

        assert "TRAINING" in recorded_statuses, "TRAINING must appear in statuses"
        assert "GENERATING" in recorded_statuses, "GENERATING must appear in statuses"
        train_idx = recorded_statuses.index("TRAINING")
        gen_idx = recorded_statuses.index("GENERATING")
        assert train_idx < gen_idx, (
            f"TRAINING (index {train_idx}) must precede GENERATING (index {gen_idx}); "
            f"got: {recorded_statuses}"
        )

    def test_status_sequence_is_training_generating_complete(self) -> None:
        """Full status sequence must be TRAINING → GENERATING → COMPLETE (AC5)."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=53,
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

        mock_engine.generate.return_value = pd.DataFrame({"a": range(5)})

        recorded_statuses: list[str] = []

        def _snapshot_status(obj: object) -> None:
            if hasattr(obj, "status"):
                recorded_statuses.append(str(obj.status))

        mock_session.add.side_effect = _snapshot_status

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=53,
                session=mock_session,
                engine=mock_engine,
            )

        # Must contain all three phases in order
        for status in ["TRAINING", "GENERATING", "COMPLETE"]:
            assert status in recorded_statuses, (
                f"Expected {status!r} in recorded statuses; got: {recorded_statuses}"
            )

        training_idx = recorded_statuses.index("TRAINING")
        generating_idx = recorded_statuses.index("GENERATING")
        complete_idx = recorded_statuses.index("COMPLETE")

        assert training_idx < generating_idx < complete_idx, (
            f"Expected TRAINING < GENERATING < COMPLETE ordering; "
            f"got indices {training_idx}, {generating_idx}, {complete_idx} "
            f"in {recorded_statuses}"
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
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

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

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
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
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

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
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
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
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

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
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
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
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

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
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
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
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

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
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            _run_synthesis_job_impl(
                job_id=64,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

        assert job.status == "COMPLETE", f"Expected COMPLETE; got {job.status}"

    @pytest.mark.parametrize("num_rows", [1, 10, 100])
    def test_engine_generate_called_with_correct_num_rows_parametrized(self, num_rows: int) -> None:
        """engine.generate() must be called with the exact num_rows value from the job."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=65,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=num_rows,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        import pandas as pd

        mock_engine.generate.return_value = pd.DataFrame({"col": range(num_rows)})

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=65,
                session=mock_session,
                engine=mock_engine,
            )

        call_args = mock_engine.generate.call_args
        n_rows_actual = (
            call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("n_rows")
        )
        assert n_rows_actual == num_rows, (
            f"engine.generate() must be called with n_rows={num_rows}; got {n_rows_actual}"
        )
