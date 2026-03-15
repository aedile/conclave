"""Unit tests for SynthesisEngine, ModelArtifact, and FK post-processing.

Tests follow TDD Red/Green/Refactor.  All tests are isolated (no SDV calls,
no network I/O) and assert return values — not just that no exception is raised.

Pattern guards applied:
- Return-value assertion pattern: every test asserts the return value of the
  function under test, not just absence of exceptions.
- Version-pin hallucination: SDV pinned to verified PyPI version 1.34.3.
- ADV-037 BLOCKER: EphemeralStorageClient + SynthesisEngine wired in bootstrapper.
"""

from __future__ import annotations

import pickle
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from synth_engine.modules.synthesizer.models import ModelArtifact


class TestModelArtifactRoundTrip:
    """Unit tests for ModelArtifact save/load round-trip."""

    def _make_artifact(self, table_name: str = "persons") -> ModelArtifact:
        """Create a minimal ModelArtifact with a mock synthesizer model."""
        mock_model = MagicMock()
        mock_model.some_param = "test_value"
        return ModelArtifact(
            table_name=table_name,
            model=mock_model,
            column_names=["id", "name", "age"],
            column_dtypes={"id": "int64", "name": "object", "age": "int64"},
        )

    def test_save_returns_path(self) -> None:
        """save() must return the path it was saved to."""
        artifact = self._make_artifact()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "artifact.pkl"
            result = artifact.save(str(save_path))
            assert result == str(save_path)

    def test_save_creates_file(self) -> None:
        """save() must create a file at the given path."""
        artifact = self._make_artifact()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "artifact.pkl"
            artifact.save(str(save_path))
            assert save_path.exists()

    def test_load_returns_model_artifact(self) -> None:
        """load() must return a ModelArtifact instance."""
        artifact = self._make_artifact()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "artifact.pkl"
            artifact.save(str(save_path))
            loaded = ModelArtifact.load(str(save_path))
            assert isinstance(loaded, ModelArtifact)

    def test_round_trip_preserves_table_name(self) -> None:
        """load(save(artifact)) must preserve the table_name field."""
        artifact = self._make_artifact(table_name="orders")
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "artifact.pkl"
            artifact.save(str(save_path))
            loaded = ModelArtifact.load(str(save_path))
            assert loaded.table_name == "orders"

    def test_round_trip_preserves_column_names(self) -> None:
        """load(save(artifact)) must preserve column_names."""
        artifact = self._make_artifact()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "artifact.pkl"
            artifact.save(str(save_path))
            loaded = ModelArtifact.load(str(save_path))
            assert loaded.column_names == ["id", "name", "age"]

    def test_round_trip_preserves_column_dtypes(self) -> None:
        """load(save(artifact)) must preserve column_dtypes."""
        artifact = self._make_artifact()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "artifact.pkl"
            artifact.save(str(save_path))
            loaded = ModelArtifact.load(str(save_path))
            assert loaded.column_dtypes == {"id": "int64", "name": "object", "age": "int64"}

    def test_load_nonexistent_file_raises_file_not_found(self) -> None:
        """load() on a missing path must raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            ModelArtifact.load("/tmp/does_not_exist_artifact.pkl")  # noqa: S108


class TestFkPostProcessing:
    """Unit tests for the FK post-processing step.

    The post-processor replaces any FK value in the child table that is not
    present in the synthetic parent PK set with a uniformly sampled value from
    that set.  After processing, zero orphan FKs must remain.
    """

    def test_no_orphans_unchanged(self) -> None:
        """Rows with valid FK values must be left unchanged."""
        from synth_engine.modules.synthesizer.engine import apply_fk_post_processing

        parent_pks = {1, 2, 3}
        child_df = pd.DataFrame({"id": [10, 11, 12], "parent_id": [1, 2, 3]})
        result = apply_fk_post_processing(
            child_df=child_df,
            fk_column="parent_id",
            valid_parent_pks=parent_pks,
            rng_seed=42,
        )
        assert list(result["parent_id"]) == [1, 2, 3]

    def test_orphan_fk_replaced(self) -> None:
        """Orphan FK values must be replaced with values from parent PK set."""
        from synth_engine.modules.synthesizer.engine import apply_fk_post_processing

        parent_pks = {1, 2, 3}
        child_df = pd.DataFrame({"id": [10, 11], "parent_id": [999, 888]})
        result = apply_fk_post_processing(
            child_df=child_df,
            fk_column="parent_id",
            valid_parent_pks=parent_pks,
            rng_seed=42,
        )
        # All FK values in result must be in parent_pks
        assert set(result["parent_id"]).issubset(parent_pks)

    def test_orphan_fk_count_preserved(self) -> None:
        """Row count must be unchanged after FK post-processing."""
        from synth_engine.modules.synthesizer.engine import apply_fk_post_processing

        parent_pks = {1, 2, 3}
        child_df = pd.DataFrame({"id": [10, 11, 12], "parent_id": [999, 2, 888]})
        result = apply_fk_post_processing(
            child_df=child_df,
            fk_column="parent_id",
            valid_parent_pks=parent_pks,
            rng_seed=42,
        )
        assert len(result) == 3

    def test_mixed_valid_orphan_fks(self) -> None:
        """Valid FK rows must be preserved; only orphan rows must be resampled."""
        from synth_engine.modules.synthesizer.engine import apply_fk_post_processing

        parent_pks = {1, 2, 3}
        child_df = pd.DataFrame({"id": [10, 11, 12], "parent_id": [1, 999, 3]})
        result = apply_fk_post_processing(
            child_df=child_df,
            fk_column="parent_id",
            valid_parent_pks=parent_pks,
            rng_seed=42,
        )
        # Row 0 and Row 2 are valid FK values — must be unchanged
        assert result.iloc[0]["parent_id"] == 1
        assert result.iloc[2]["parent_id"] == 3
        # Row 1 was orphan — must now be in parent_pks
        assert result.iloc[1]["parent_id"] in parent_pks

    def test_empty_dataframe_returns_empty(self) -> None:
        """Empty child DataFrame must return empty DataFrame without error."""
        from synth_engine.modules.synthesizer.engine import apply_fk_post_processing

        parent_pks = {1, 2, 3}
        child_df = pd.DataFrame({"id": pd.Series([], dtype="int64"), "parent_id": pd.Series([], dtype="int64")})
        result = apply_fk_post_processing(
            child_df=child_df,
            fk_column="parent_id",
            valid_parent_pks=parent_pks,
            rng_seed=42,
        )
        assert len(result) == 0

    def test_empty_parent_pks_raises_value_error(self) -> None:
        """Empty parent_pks set must raise ValueError (nowhere to resample)."""
        from synth_engine.modules.synthesizer.engine import apply_fk_post_processing

        child_df = pd.DataFrame({"id": [10], "parent_id": [999]})
        with pytest.raises(ValueError, match="parent_pks"):
            apply_fk_post_processing(
                child_df=child_df,
                fk_column="parent_id",
                valid_parent_pks=set(),
                rng_seed=42,
            )

    def test_returns_dataframe(self) -> None:
        """apply_fk_post_processing must return a pd.DataFrame."""
        from synth_engine.modules.synthesizer.engine import apply_fk_post_processing

        parent_pks = {1, 2}
        child_df = pd.DataFrame({"id": [10], "parent_id": [1]})
        result = apply_fk_post_processing(
            child_df=child_df,
            fk_column="parent_id",
            valid_parent_pks=parent_pks,
            rng_seed=42,
        )
        assert isinstance(result, pd.DataFrame)


class TestSynthesisEngineTrain:
    """Unit tests for SynthesisEngine.train() using a mocked CTGAN model."""

    def _make_persons_df(self, n: int = 10) -> pd.DataFrame:
        """Build a minimal persons DataFrame for training tests."""
        import numpy as np
        rng = np.random.default_rng(42)
        return pd.DataFrame(
            {
                "id": range(1, n + 1),
                "age": rng.integers(18, 80, size=n).tolist(),
                "salary": rng.integers(30000, 100000, size=n).tolist(),
            }
        )

    def test_train_returns_model_artifact(self) -> None:
        """train() must return a ModelArtifact instance."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine
        from synth_engine.modules.synthesizer.models import ModelArtifact

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("synth_engine.modules.synthesizer.engine.CTGANSynthesizer") as mock_ctgan,
        ):
            mock_instance = MagicMock()
            mock_ctgan.return_value = mock_instance

            df = self._make_persons_df()
            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            result = engine.train(table_name="persons", parquet_path=parquet_path)

            assert isinstance(result, ModelArtifact)

    def test_train_calls_fit_on_model(self) -> None:
        """train() must call fit() on the CTGANSynthesizer with the loaded DataFrame."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("synth_engine.modules.synthesizer.engine.CTGANSynthesizer") as mock_ctgan,
        ):
            mock_instance = MagicMock()
            mock_ctgan.return_value = mock_instance

            df = self._make_persons_df()
            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            engine.train(table_name="persons", parquet_path=parquet_path)

            mock_instance.fit.assert_called_once()

    def test_train_artifact_has_correct_table_name(self) -> None:
        """train() must set the table_name on the returned ModelArtifact."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("synth_engine.modules.synthesizer.engine.CTGANSynthesizer") as mock_ctgan,
        ):
            mock_instance = MagicMock()
            mock_ctgan.return_value = mock_instance

            df = self._make_persons_df()
            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            result = engine.train(table_name="orders", parquet_path=parquet_path)

            assert result.table_name == "orders"

    def test_train_artifact_preserves_column_names(self) -> None:
        """train() must record all column names in the ModelArtifact."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("synth_engine.modules.synthesizer.engine.CTGANSynthesizer") as mock_ctgan,
        ):
            mock_instance = MagicMock()
            mock_ctgan.return_value = mock_instance

            df = self._make_persons_df()
            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            result = engine.train(table_name="persons", parquet_path=parquet_path)

            assert sorted(result.column_names) == sorted(["id", "age", "salary"])

    def test_train_missing_parquet_raises_file_not_found(self) -> None:
        """train() must raise FileNotFoundError for non-existent parquet_path."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        with patch("synth_engine.modules.synthesizer.engine.CTGANSynthesizer"):
            engine = SynthesisEngine()
            with pytest.raises(FileNotFoundError):
                engine.train(table_name="persons", parquet_path="/tmp/nonexistent.parquet")  # noqa: S108


class TestSynthesisEngineGenerate:
    """Unit tests for SynthesisEngine.generate() using mocked CTGAN."""

    def _make_artifact(self) -> "ModelArtifact":
        """Create a ModelArtifact backed by a mock CTGAN model."""
        mock_model = MagicMock()
        mock_model.sample.return_value = pd.DataFrame(
            {"id": [1, 2, 3], "age": [25, 30, 35], "salary": [50000, 60000, 70000]}
        )
        from synth_engine.modules.synthesizer.models import ModelArtifact

        return ModelArtifact(
            table_name="persons",
            model=mock_model,
            column_names=["id", "age", "salary"],
            column_dtypes={"id": "int64", "age": "int64", "salary": "int64"},
        )

    def test_generate_returns_dataframe(self) -> None:
        """generate() must return a pd.DataFrame."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        engine = SynthesisEngine()
        artifact = self._make_artifact()
        result = engine.generate(artifact=artifact, n_rows=3)
        assert isinstance(result, pd.DataFrame)

    def test_generate_calls_sample_with_n_rows(self) -> None:
        """generate() must call artifact.model.sample() with the requested row count."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        engine = SynthesisEngine()
        artifact = self._make_artifact()
        engine.generate(artifact=artifact, n_rows=50)
        artifact.model.sample.assert_called_once_with(num_rows=50)

    def test_generate_returns_correct_row_count(self) -> None:
        """generate() must return a DataFrame with n_rows rows."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        engine = SynthesisEngine()
        artifact = self._make_artifact()
        result = engine.generate(artifact=artifact, n_rows=3)
        assert len(result) == 3

    def test_generate_returns_correct_columns(self) -> None:
        """generate() result must contain all columns from the artifact."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        engine = SynthesisEngine()
        artifact = self._make_artifact()
        result = engine.generate(artifact=artifact, n_rows=3)
        assert set(result.columns) == {"id", "age", "salary"}

    def test_generate_zero_rows_raises_value_error(self) -> None:
        """generate() with n_rows=0 must raise ValueError."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        engine = SynthesisEngine()
        artifact = self._make_artifact()
        with pytest.raises(ValueError, match="n_rows"):
            engine.generate(artifact=artifact, n_rows=0)

    def test_generate_negative_rows_raises_value_error(self) -> None:
        """generate() with n_rows<0 must raise ValueError."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        engine = SynthesisEngine()
        artifact = self._make_artifact()
        with pytest.raises(ValueError, match="n_rows"):
            engine.generate(artifact=artifact, n_rows=-5)


class TestModelArtifactPickleFormat:
    """Tests that ModelArtifact serialises using pickle (not torch.save)."""

    def test_saved_file_is_valid_pickle(self) -> None:
        """ModelArtifact.save() must produce a valid pickle file."""
        mock_model = MagicMock()
        from synth_engine.modules.synthesizer.models import ModelArtifact

        artifact = ModelArtifact(
            table_name="test",
            model=mock_model,
            column_names=["a"],
            column_dtypes={"a": "int64"},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "artifact.pkl"
            artifact.save(str(save_path))
            # Verify the file can be read as valid pickle
            with open(save_path, "rb") as f:
                loaded = pickle.load(f)  # noqa: S301 — test-only deserialization of self-produced artifact
            assert isinstance(loaded, ModelArtifact)
