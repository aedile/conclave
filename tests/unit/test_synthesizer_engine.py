"""Unit tests for SynthesisEngine, ModelArtifact, and FK post-processing.

Tests follow TDD Red/Green/Refactor.  All tests are isolated (no SDV calls,
no network I/O) and assert return values — not just that no exception is raised.

Pattern guards applied:
- Return-value assertion pattern: every test asserts the return value of the
  function under test, not just absence of exceptions.
- Version-pin hallucination: SDV pinned to verified PyPI version 1.34.3.
- ADV-037 BLOCKER: EphemeralStorageClient + SynthesisEngine wired in bootstrapper.
- Pickle compatibility: MagicMock is NOT picklable in Python 3.14; save/load
  round-trip tests use _PicklableModelStub instead.  MagicMock is retained for
  tests that do NOT call save/load (generate calls, training mock assertions).
"""

from __future__ import annotations

import pickle
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from synth_engine.modules.synthesizer.models import ModelArtifact


class _PicklableModelStub:
    """Minimal picklable stand-in for a CTGANSynthesizer used in save/load tests.

    Python 3.14 changed MagicMock pickling behaviour — MagicMock instances are
    NOT picklable because the class identity check fails across the pickling
    boundary.  This stub is a plain class that can be pickled and whose
    ``sample()`` method returns a predictable DataFrame.
    """

    def __init__(self, some_param: str = "test_value") -> None:
        self.some_param = some_param

    def sample(self, num_rows: int = 1) -> pd.DataFrame:
        """Return a minimal DataFrame with one column."""
        return pd.DataFrame({"id": list(range(num_rows))})


class TestModelArtifactRoundTrip:
    """Unit tests for ModelArtifact save/load round-trip."""

    def _make_artifact(self, table_name: str = "persons") -> ModelArtifact:
        """Create a minimal ModelArtifact with a picklable synthesizer stub."""
        return ModelArtifact(
            table_name=table_name,
            model=_PicklableModelStub(),
            column_names=["id", "name", "age"],
            column_dtypes={"id": "int64", "name": "object", "age": "int64"},
            column_nullables={"id": False, "name": False, "age": False},
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

    def test_round_trip_preserves_column_nullables(self) -> None:
        """load(save(artifact)) must preserve column_nullables."""
        artifact = ModelArtifact(
            table_name="test_table",
            model=_PicklableModelStub(),
            column_names=["id", "opt_field"],
            column_dtypes={"id": "int64", "opt_field": "object"},
            column_nullables={"id": False, "opt_field": True},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "artifact.pkl"
            artifact.save(str(save_path))
            loaded = ModelArtifact.load(str(save_path))
            assert loaded.column_nullables == {"id": False, "opt_field": True}

    def test_load_nonexistent_file_raises_file_not_found(self) -> None:
        """load() on a missing path must raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            ModelArtifact.load("/tmp/does_not_exist_artifact.pkl")


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
        child_df = pd.DataFrame(
            {
                "id": pd.Series([], dtype="int64"),
                "parent_id": pd.Series([], dtype="int64"),
            }
        )
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

    def test_missing_fk_column_raises_key_error(self) -> None:
        """apply_fk_post_processing must raise KeyError if fk_column is absent."""
        from synth_engine.modules.synthesizer.engine import apply_fk_post_processing

        parent_pks = {1, 2, 3}
        child_df = pd.DataFrame({"id": [10, 11], "parent_id": [1, 2]})
        with pytest.raises(KeyError):
            apply_fk_post_processing(
                child_df=child_df,
                fk_column="nonexistent_column",
                valid_parent_pks=parent_pks,
                rng_seed=42,
            )

    def test_original_df_not_mutated(self) -> None:
        """apply_fk_post_processing must not mutate the original child_df."""
        from synth_engine.modules.synthesizer.engine import apply_fk_post_processing

        parent_pks = {1, 2, 3}
        original_values = [999, 888, 777]
        child_df = pd.DataFrame({"id": [10, 11, 12], "parent_id": original_values.copy()})

        apply_fk_post_processing(
            child_df=child_df,
            fk_column="parent_id",
            valid_parent_pks=parent_pks,
            rng_seed=42,
        )

        # Original DataFrame must be unchanged
        assert list(child_df["parent_id"]) == original_values


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

    def test_train_artifact_preserves_column_dtypes(self) -> None:
        """train() must record all column dtypes in the ModelArtifact."""
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

            # column_dtypes must be a dict with all columns present
            assert set(result.column_dtypes.keys()) == {"id", "age", "salary"}
            # Each value must be a non-empty string dtype representation
            for col, dtype_str in result.column_dtypes.items():
                assert isinstance(dtype_str, str), f"dtype for '{col}' must be str"
                assert dtype_str, f"dtype string for '{col}' must not be empty"

    def test_train_missing_parquet_raises_file_not_found(self) -> None:
        """train() must raise FileNotFoundError for non-existent parquet_path."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        with patch("synth_engine.modules.synthesizer.engine.CTGANSynthesizer"):
            engine = SynthesisEngine()
            with pytest.raises(FileNotFoundError):
                engine.train(table_name="persons", parquet_path="/tmp/nonexistent.parquet")


class TestSynthesisEngineGenerate:
    """Unit tests for SynthesisEngine.generate() using mocked CTGAN.

    Uses MagicMock here because generate() does NOT pickle the model —
    it only calls model.sample().  MagicMock is safe for non-pickle usage.
    """

    def _make_artifact(self) -> ModelArtifact:
        """Create a ModelArtifact backed by a mock CTGAN model."""
        mock_model = MagicMock()
        mock_model.sample.return_value = pd.DataFrame(
            {"id": [1, 2, 3], "age": [25, 30, 35], "salary": [50000, 60000, 70000]}
        )
        return ModelArtifact(
            table_name="persons",
            model=mock_model,
            column_names=["id", "age", "salary"],
            column_dtypes={"id": "int64", "age": "int64", "salary": "int64"},
            column_nullables={"id": False, "age": False, "salary": False},
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
    """Tests that ModelArtifact serialises using pickle (not torch.save).

    Uses _PicklableModelStub instead of MagicMock because Python 3.14 changed
    MagicMock pickling behaviour — MagicMock is no longer picklable.
    """

    def test_saved_file_is_valid_pickle(self) -> None:
        """ModelArtifact.save() must produce a valid pickle file."""
        artifact = ModelArtifact(
            table_name="test",
            model=_PicklableModelStub(),
            column_names=["a"],
            column_dtypes={"a": "int64"},
            column_nullables={"a": False},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "artifact.pkl"
            artifact.save(str(save_path))
            # Verify the file can be read as valid pickle
            with open(save_path, "rb") as f:
                loaded = pickle.load(f)  # noqa: S301 — test-only deserialization of self-produced artifact
            assert isinstance(loaded, ModelArtifact)


class TestSynthesisEngineWithDPWrapper:
    """Unit tests for SynthesisEngine.train() with optional dp_wrapper parameter.

    Task: P4-T4.3b — DP Engine Wiring
    The dp_wrapper parameter is accepted as Any to avoid import-linter boundary
    violations between modules/synthesizer and modules/privacy.
    """

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

    def test_train_accepts_dp_wrapper_kwarg(self) -> None:
        """train() must accept an optional dp_wrapper keyword argument without error.

        T7.3: When dp_wrapper is provided, DPCompatibleCTGAN is used instead of
        CTGANSynthesizer.  Both are patched so no real SDV calls occur.
        """
        from synth_engine.modules.synthesizer.engine import SynthesisEngine
        from synth_engine.modules.synthesizer.models import ModelArtifact

        mock_dp_wrapper = MagicMock()
        mock_dp_wrapper.check_budget.return_value = None
        mock_dp_wrapper.max_grad_norm = 1.0
        mock_dp_wrapper.noise_multiplier = 1.1

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("synth_engine.modules.synthesizer.engine.CTGANSynthesizer") as mock_ctgan,
            patch("synth_engine.modules.synthesizer.engine.DPCompatibleCTGAN") as mock_dp_ctgan,
        ):
            mock_ctgan.return_value = MagicMock()
            mock_dp_instance = MagicMock()
            mock_dp_instance.fit.return_value = mock_dp_instance
            mock_dp_ctgan.return_value = mock_dp_instance

            df = self._make_persons_df()
            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            result = engine.train(
                table_name="persons",
                parquet_path=parquet_path,
                dp_wrapper=mock_dp_wrapper,
            )

        assert isinstance(result, ModelArtifact)
        assert mock_dp_ctgan.called, "DPCompatibleCTGAN must be used when dp_wrapper is provided."

    def test_train_without_dp_wrapper_still_works(self) -> None:
        """train() without dp_wrapper must behave identically to pre-T4.3b behavior."""
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

    def test_train_with_dp_wrapper_routes_to_dp_compatible_ctgan(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """train() with dp_wrapper must route to DPCompatibleCTGAN (T7.3 wiring).

        T4.3b logged a deferral warning because SDV's CTGANSynthesizer.fit()
        did not expose its optimizer for Opacus wrapping.  T7.3 replaces that
        warning path with actual routing to DPCompatibleCTGAN.

        This test verifies:
        - DPCompatibleCTGAN is constructed with the dp_wrapper argument.
        - DPCompatibleCTGAN.fit() is called.
        - CTGANSynthesizer is NOT used when dp_wrapper is provided.
        """
        import logging

        from synth_engine.modules.synthesizer.engine import SynthesisEngine
        from synth_engine.modules.synthesizer.models import ModelArtifact

        mock_dp_wrapper = MagicMock()
        mock_dp_wrapper.check_budget.return_value = None
        mock_dp_wrapper.max_grad_norm = 1.0
        mock_dp_wrapper.noise_multiplier = 1.1

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("synth_engine.modules.synthesizer.engine.CTGANSynthesizer") as mock_ctgan,
            patch("synth_engine.modules.synthesizer.engine.DPCompatibleCTGAN") as mock_dp_ctgan,
            caplog.at_level(logging.INFO, logger="synth_engine.modules.synthesizer.engine"),
        ):
            mock_ctgan.return_value = MagicMock()
            mock_dp_instance = MagicMock()
            mock_dp_instance.fit.return_value = mock_dp_instance
            mock_dp_ctgan.return_value = mock_dp_instance

            df = self._make_persons_df()
            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            result = engine.train(
                table_name="persons",
                parquet_path=parquet_path,
                dp_wrapper=mock_dp_wrapper,
            )

        assert isinstance(result, ModelArtifact)
        # T7.3: DPCompatibleCTGAN must be called when dp_wrapper is provided
        assert mock_dp_ctgan.called, "DPCompatibleCTGAN must be used when dp_wrapper is not None."
        # T7.3: CTGANSynthesizer must NOT be called in the DP path
        assert not mock_ctgan.called, (
            "CTGANSynthesizer must NOT be called when dp_wrapper is provided."
        )
        # T7.3: dp_wrapper must be passed to DPCompatibleCTGAN constructor
        _, kwargs = mock_dp_ctgan.call_args
        assert kwargs.get("dp_wrapper") is mock_dp_wrapper, (
            "DPCompatibleCTGAN must receive the dp_wrapper argument."
        )

    def test_train_dp_wrapper_none_default(self) -> None:
        """train() dp_wrapper parameter must default to None."""
        import inspect

        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        sig = inspect.signature(SynthesisEngine.train)
        assert "dp_wrapper" in sig.parameters
        param = sig.parameters["dp_wrapper"]
        assert param.default is None


# ---------------------------------------------------------------------------
# T25.1 — synthesis_ms_per_row Histogram metric tests
# ---------------------------------------------------------------------------


class TestSynthesisMsPerRowHistogram:
    """Tests for the synthesis_ms_per_row Histogram instrument (T25.1).

    Verifies that:
    - The histogram is incremented after a successful train() call.
    - The correct model_type label is used ("vanilla" vs "dp").
    - The correct row_count_bucket label is derived from source row count.
    - The histogram is accessible as a module-level attribute.
    """

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

    def test_synthesis_ms_per_row_histogram_is_module_attribute(self) -> None:
        """engine module must expose synthesis_ms_per_row as a module-level name."""
        import synth_engine.modules.synthesizer.engine as engine_mod

        assert hasattr(engine_mod, "SYNTHESIS_MS_PER_ROW"), (
            "engine module must expose SYNTHESIS_MS_PER_ROW Histogram."
        )

    def test_synthesis_ms_per_row_is_histogram_instance(self) -> None:
        """SYNTHESIS_MS_PER_ROW must be a prometheus_client.Histogram instance."""
        from prometheus_client import Histogram

        from synth_engine.modules.synthesizer.engine import SYNTHESIS_MS_PER_ROW

        assert isinstance(SYNTHESIS_MS_PER_ROW, Histogram)

    def test_train_increments_histogram_for_vanilla_model(self) -> None:
        """train() must observe a value in the histogram after vanilla CTGAN fit."""
        import prometheus_client

        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        # Read the pre-call sample count using REGISTRY
        before = prometheus_client.REGISTRY.get_sample_value(
            "synthesis_ms_per_row_count",
            {"model_type": "vanilla", "row_count_bucket": "1-100"},
        )
        before_count = before if before is not None else 0.0

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("synth_engine.modules.synthesizer.engine.CTGANSynthesizer") as mock_ctgan,
        ):
            mock_instance = MagicMock()
            mock_ctgan.return_value = mock_instance

            df = self._make_persons_df(n=10)
            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            engine.train(table_name="persons", parquet_path=parquet_path)

        after = prometheus_client.REGISTRY.get_sample_value(
            "synthesis_ms_per_row_count",
            {"model_type": "vanilla", "row_count_bucket": "1-100"},
        )
        after_count = after if after is not None else 0.0
        assert after_count == before_count + 1.0, (
            f"Expected histogram count to increment by 1. "
            f"Before={before_count}, After={after_count}"
        )

    def test_train_increments_histogram_for_dp_model(self) -> None:
        """train() with dp_wrapper must use model_type='dp' label."""
        import prometheus_client

        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        before = prometheus_client.REGISTRY.get_sample_value(
            "synthesis_ms_per_row_count",
            {"model_type": "dp", "row_count_bucket": "1-100"},
        )
        before_count = before if before is not None else 0.0

        mock_dp_wrapper = MagicMock()
        mock_dp_wrapper.max_grad_norm = 1.0
        mock_dp_wrapper.noise_multiplier = 1.1

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("synth_engine.modules.synthesizer.engine.CTGANSynthesizer") as mock_ctgan,
            patch("synth_engine.modules.synthesizer.engine.DPCompatibleCTGAN") as mock_dp_ctgan,
        ):
            mock_ctgan.return_value = MagicMock()
            mock_dp_instance = MagicMock()
            mock_dp_ctgan.return_value = mock_dp_instance

            df = self._make_persons_df(n=10)
            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            engine.train(
                table_name="persons",
                parquet_path=parquet_path,
                dp_wrapper=mock_dp_wrapper,
            )

        after = prometheus_client.REGISTRY.get_sample_value(
            "synthesis_ms_per_row_count",
            {"model_type": "dp", "row_count_bucket": "1-100"},
        )
        after_count = after if after is not None else 0.0
        assert after_count == before_count + 1.0, (
            f"Expected dp histogram count to increment by 1. "
            f"Before={before_count}, After={after_count}"
        )


class TestRowCountBucketLogic:
    """Tests for the _row_count_bucket() helper function (T25.1).

    Verifies that row counts are bucketed into the correct label strings.
    """

    def test_bucket_1_row(self) -> None:
        """1 row must fall in '1-100' bucket."""
        from synth_engine.modules.synthesizer.engine import _row_count_bucket

        assert _row_count_bucket(1) == "1-100"

    def test_bucket_100_rows(self) -> None:
        """100 rows must fall in '1-100' bucket."""
        from synth_engine.modules.synthesizer.engine import _row_count_bucket

        assert _row_count_bucket(100) == "1-100"

    def test_bucket_101_rows(self) -> None:
        """101 rows must fall in '101-1000' bucket."""
        from synth_engine.modules.synthesizer.engine import _row_count_bucket

        assert _row_count_bucket(101) == "101-1000"

    def test_bucket_1000_rows(self) -> None:
        """1000 rows must fall in '101-1000' bucket."""
        from synth_engine.modules.synthesizer.engine import _row_count_bucket

        assert _row_count_bucket(1000) == "101-1000"

    def test_bucket_1001_rows(self) -> None:
        """1001 rows must fall in '1001-10000' bucket."""
        from synth_engine.modules.synthesizer.engine import _row_count_bucket

        assert _row_count_bucket(1001) == "1001-10000"

    def test_bucket_10000_rows(self) -> None:
        """10000 rows must fall in '1001-10000' bucket."""
        from synth_engine.modules.synthesizer.engine import _row_count_bucket

        assert _row_count_bucket(10000) == "1001-10000"

    def test_bucket_10001_rows(self) -> None:
        """10001 rows must fall in '10001+' bucket."""
        from synth_engine.modules.synthesizer.engine import _row_count_bucket

        assert _row_count_bucket(10001) == "10001+"

    def test_bucket_zero_rows(self) -> None:
        """0 rows must fall in '1-100' bucket (degenerate case)."""
        from synth_engine.modules.synthesizer.engine import _row_count_bucket

        assert _row_count_bucket(0) == "1-100"


class TestGrafanaDashboardPanels:
    """Tests verifying Grafana dashboard JSON includes the T25.1 KPI panels (T25.1).

    Both panels must be present in grafana/provisioning/dashboards/synth_engine.json.
    """

    def _load_dashboard(self) -> dict:  # type: ignore[type-arg]
        """Load and return the parsed Grafana dashboard JSON."""
        import json
        from pathlib import Path

        dashboard_path = (
            Path(__file__).parent.parent.parent
            / "grafana"
            / "provisioning"
            / "dashboards"
            / "synth_engine.json"
        )
        return json.loads(dashboard_path.read_text())  # type: ignore[return-value]

    def test_dashboard_has_synthesis_ms_per_row_panel(self) -> None:
        """Dashboard must contain a panel for synthesis_ms_per_row."""
        dashboard = self._load_dashboard()
        panels = dashboard.get("panels", [])
        titles = [p.get("title", "") for p in panels]
        assert any("synthesis_ms_per_row" in t.lower() or "ms per row" in t.lower() for t in titles), (
            f"Expected a synthesis_ms_per_row panel in dashboard. Found panels: {titles}"
        )

    def test_dashboard_has_epsilon_spent_panel(self) -> None:
        """Dashboard must contain a panel for epsilon_spent_total."""
        dashboard = self._load_dashboard()
        panels = dashboard.get("panels", [])
        titles = [p.get("title", "") for p in panels]
        assert any("epsilon" in t.lower() for t in titles), (
            f"Expected an epsilon_spent panel in dashboard. Found panels: {titles}"
        )

    def test_dashboard_synthesis_panel_uses_histogram_query(self) -> None:
        """synthesis_ms_per_row panel must use a histogram_quantile or rate query."""
        import json

        dashboard = self._load_dashboard()
        panels = dashboard.get("panels", [])
        synthesis_panels = [
            p
            for p in panels
            if "synthesis_ms_per_row" in p.get("title", "").lower()
            or "ms per row" in p.get("title", "").lower()
        ]
        assert synthesis_panels, "synthesis_ms_per_row panel must exist"
        panel = synthesis_panels[0]
        targets = panel.get("targets", [])
        assert targets, "synthesis_ms_per_row panel must have at least one target query"
        exprs = [t.get("expr", "") for t in targets]
        assert any("synthesis_ms_per_row" in e for e in exprs), (
            f"synthesis_ms_per_row panel must reference synthesis_ms_per_row metric. Exprs: {exprs}"
        )

    def test_dashboard_epsilon_panel_uses_counter_query(self) -> None:
        """epsilon_spent_total panel must reference epsilon_spent_total metric."""
        dashboard = self._load_dashboard()
        panels = dashboard.get("panels", [])
        epsilon_panels = [p for p in panels if "epsilon" in p.get("title", "").lower()]
        assert epsilon_panels, "epsilon panel must exist"
        panel = epsilon_panels[0]
        targets = panel.get("targets", [])
        assert targets, "epsilon panel must have at least one target query"
        exprs = [t.get("expr", "") for t in targets]
        assert any("epsilon_spent_total" in e for e in exprs), (
            f"epsilon panel must reference epsilon_spent_total metric. Exprs: {exprs}"
        )
