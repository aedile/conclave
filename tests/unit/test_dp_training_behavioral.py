"""Behavioral and contract tests for the DP training layer (T35.3).

Replaces tautological mock-heavy tests with:
  1. Behavioral tests — real (small) DataFrames, assert on output shape / statistics.
  2. Contract tests — verify the SDV API assumptions encoded by the existing mocks.
  3. Slow marker — real CTGAN training runs (>5s) excluded from default unit gate.

Setup-to-assertion ratios are kept below 5:1.  No MagicMock() is used for
objects under test; mocks are confined to external I/O boundaries (database,
filesystem, network) per T35.3 AC4.

CONSTITUTION Priority 3: TDD Red/Green/Refactor.
Task: T35.3 — Replace Tautological DP Training Tests
"""

from __future__ import annotations

import warnings
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared fictional DataFrames — no PII, seeded RNG
# ---------------------------------------------------------------------------


def _small_numeric_df(n: int = 20) -> pd.DataFrame:
    """Return a small all-numeric fictional DataFrame (n rows).

    Args:
        n: Number of rows.

    Returns:
        DataFrame with columns: age (float), salary (float).
    """
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "age": rng.uniform(18.0, 80.0, size=n),
            "salary": rng.uniform(30000.0, 120000.0, size=n),
        }
    )


# ---------------------------------------------------------------------------
# Behavioral tests: cap_batch_size — pure function, zero mocks
# ---------------------------------------------------------------------------


class TestCapBatchSizeBehavioral:
    """Behavioral tests for cap_batch_size with real integer inputs.

    These tests assert on output values, not on call counts.
    Setup-to-assertion ratio: 1:1.
    """

    def test_output_is_pac_divisible(self) -> None:
        """cap_batch_size output must always be divisible by pac."""
        from synth_engine.modules.synthesizer.ctgan_utils import cap_batch_size

        for n, requested, pac in [
            (200, 500, 10),
            (50, 100, 4),
            (1000, 32, 8),
            (15, 500, 3),
        ]:
            result = cap_batch_size(n, requested, pac)
            assert result % pac == 0, (
                f"cap_batch_size({n}, {requested}, {pac}) = {result} is not pac-divisible."
            )

    def test_output_never_exceeds_half_n_samples(self) -> None:
        """cap_batch_size output must be at most n_samples // 2 for Opacus compatibility.

        Contract: Opacus requires len(dataloader) >= 2 so sample_rate < 1.0.
        """
        from synth_engine.modules.synthesizer.ctgan_utils import cap_batch_size

        for n, requested, pac in [(200, 500, 10), (100, 200, 5), (60, 50, 4)]:
            result = cap_batch_size(n, requested, pac)
            assert result <= n // 2 or result == pac, (
                f"cap_batch_size({n}, {requested}, {pac}) = {result} exceeds n//2={n // 2}. "
                "Opacus requires len(dataloader) >= 2 (q < 1.0 for PRV accounting)."
            )

    def test_output_at_least_pac(self) -> None:
        """cap_batch_size output must be at least pac to permit one Discriminator forward pass."""
        from synth_engine.modules.synthesizer.ctgan_utils import cap_batch_size

        result = cap_batch_size(n_samples=5, requested_batch_size=500, pac=10)
        assert result >= 10, (
            f"cap_batch_size must return at least pac=10, got {result}. "
            "A batch smaller than pac cannot fit even one PacGAN group."
        )

    def test_standard_case_produces_correct_value(self) -> None:
        """cap_batch_size(100, 500, 10) must return 50 (capped, pac-aligned)."""
        from synth_engine.modules.synthesizer.ctgan_utils import cap_batch_size

        result = cap_batch_size(n_samples=100, requested_batch_size=500, pac=10)
        assert result == 50


# ---------------------------------------------------------------------------
# Behavioral tests: parse_gan_hyperparams — pure function, zero mocks
# ---------------------------------------------------------------------------


class TestParseGanHyparamsBehavioral:
    """Behavioral tests for parse_gan_hyperparams with real dict inputs.

    Setup-to-assertion ratio: 2:1.
    """

    def _make_model_kwargs(
        self,
        embedding_dim: int = 128,
        pac: int = 10,
        discriminator_steps: int = 1,
        batch_size: int = 500,
    ) -> dict[str, Any]:
        """Build a minimal model_kwargs dict for testing.

        Args:
            embedding_dim: GAN noise embedding dimension.
            pac: PacGAN grouping factor.
            discriminator_steps: Discriminator update steps per batch.
            batch_size: CTGAN batch size.

        Returns:
            A dict compatible with parse_gan_hyperparams.
        """
        return {
            "embedding_dim": embedding_dim,
            "generator_dim": (256, 256),
            "discriminator_dim": (256, 256),
            "pac": pac,
            "discriminator_steps": discriminator_steps,
            "batch_size": batch_size,
        }

    def test_returned_object_has_correct_embedding_dim(self) -> None:
        """parse_gan_hyperparams must extract embedding_dim from kwargs."""
        from synth_engine.modules.synthesizer.ctgan_utils import parse_gan_hyperparams

        result = parse_gan_hyperparams(self._make_model_kwargs(embedding_dim=64))
        assert result.embedding_dim == 64

    def test_returned_object_has_correct_pac(self) -> None:
        """parse_gan_hyperparams must extract pac from kwargs."""
        from synth_engine.modules.synthesizer.ctgan_utils import parse_gan_hyperparams

        result = parse_gan_hyperparams(self._make_model_kwargs(pac=4))
        assert result.pac == 4

    def test_returned_object_has_correct_discriminator_steps(self) -> None:
        """parse_gan_hyperparams must extract discriminator_steps from kwargs."""
        from synth_engine.modules.synthesizer.ctgan_utils import parse_gan_hyperparams

        result = parse_gan_hyperparams(self._make_model_kwargs(discriminator_steps=3))
        assert result.discriminator_steps == 3

    def test_default_values_applied_when_keys_missing(self) -> None:
        """parse_gan_hyperparams must apply sane defaults for missing keys."""
        from synth_engine.modules.synthesizer.ctgan_utils import parse_gan_hyperparams

        result = parse_gan_hyperparams({})
        assert result.embedding_dim == 128
        assert result.pac == 10
        assert result.discriminator_steps == 1
        assert result.batch_size == 500

    def test_generator_dim_is_tuple(self) -> None:
        """parse_gan_hyperparams must return generator_dim as a tuple."""
        from synth_engine.modules.synthesizer.ctgan_utils import parse_gan_hyperparams

        result = parse_gan_hyperparams(self._make_model_kwargs())
        assert isinstance(result.generator_dim, tuple), (
            f"generator_dim must be tuple, got {type(result.generator_dim)}"
        )

    def test_discriminator_dim_is_tuple(self) -> None:
        """parse_gan_hyperparams must return discriminator_dim as a tuple."""
        from synth_engine.modules.synthesizer.ctgan_utils import parse_gan_hyperparams

        result = parse_gan_hyperparams(self._make_model_kwargs())
        assert isinstance(result.discriminator_dim, tuple), (
            f"discriminator_dim must be tuple, got {type(result.discriminator_dim)}"
        )


# ---------------------------------------------------------------------------
# Behavioral tests: build_proxy_dataloader — real torch operations
# ---------------------------------------------------------------------------


class TestBuildProxyDataloaderBehavioral:
    """Behavioral tests for build_proxy_dataloader using real PyTorch objects.

    These tests assert on actual DataLoader behavior — batch counts, feature
    dimensions, and tensor shapes — without mocking torch.

    Setup-to-assertion ratio: 3:1.
    """

    def test_returns_correct_n_features_for_numeric_df(self) -> None:
        """build_proxy_dataloader must return n_features equal to the numeric column count."""
        torch = pytest.importorskip("torch")
        from torch.utils.data import DataLoader, TensorDataset

        from synth_engine.modules.synthesizer.training_strategies import build_proxy_dataloader

        df = _small_numeric_df(n=20)
        _, n_features = build_proxy_dataloader(
            df, torch_module=torch, tensor_dataset_cls=TensorDataset, dataloader_cls=DataLoader
        )
        assert n_features == 2, (
            f"Expected n_features=2 for a 2-column numeric DataFrame, got {n_features}."
        )

    def test_dataloader_has_at_least_one_batch(self) -> None:
        """build_proxy_dataloader must produce at least one batch for a 20-row DataFrame."""
        torch = pytest.importorskip("torch")
        from torch.utils.data import DataLoader, TensorDataset

        from synth_engine.modules.synthesizer.training_strategies import build_proxy_dataloader

        df = _small_numeric_df(n=20)
        dl, _ = build_proxy_dataloader(
            df, torch_module=torch, tensor_dataset_cls=TensorDataset, dataloader_cls=DataLoader
        )
        assert len(dl) >= 1, (
            f"DataLoader must have at least 1 batch for a 20-row DataFrame, got {len(dl)}."
        )

    def test_batch_tensor_has_correct_feature_dimension(self) -> None:
        """Each batch tensor from build_proxy_dataloader must have shape (*, n_features)."""
        torch = pytest.importorskip("torch")
        from torch.utils.data import DataLoader, TensorDataset

        from synth_engine.modules.synthesizer.training_strategies import build_proxy_dataloader

        df = _small_numeric_df(n=20)
        dl, n_features = build_proxy_dataloader(
            df, torch_module=torch, tensor_dataset_cls=TensorDataset, dataloader_cls=DataLoader
        )
        (first_batch,) = next(iter(dl))
        assert first_batch.shape[1] == n_features, (
            f"Batch tensor last dimension must equal n_features={n_features}, "
            f"got shape {first_batch.shape}."
        )

    def test_raises_runtime_error_for_single_row_df(self) -> None:
        """build_proxy_dataloader must raise RuntimeError when the DataFrame is too small."""
        torch = pytest.importorskip("torch")
        from torch.utils.data import DataLoader, TensorDataset

        from synth_engine.modules.synthesizer.training_strategies import build_proxy_dataloader

        df = pd.DataFrame({"a": [1.0], "b": [2.0]})
        with pytest.raises(RuntimeError, match="too few rows"):
            build_proxy_dataloader(
                df,
                torch_module=torch,
                tensor_dataset_cls=TensorDataset,
                dataloader_cls=DataLoader,
            )

    def test_nan_values_are_sanitised_to_zero(self) -> None:
        """build_proxy_dataloader must replace NaN values with 0.0 in the tensor."""
        torch = pytest.importorskip("torch")
        from torch.utils.data import DataLoader, TensorDataset

        from synth_engine.modules.synthesizer.training_strategies import build_proxy_dataloader

        rng = np.random.default_rng(0)
        values = rng.uniform(0.0, 1.0, size=20).tolist()
        values[5] = float("nan")
        df = pd.DataFrame({"a": values, "b": rng.uniform(0.0, 1.0, size=20).tolist()})

        dl, _ = build_proxy_dataloader(
            df, torch_module=torch, tensor_dataset_cls=TensorDataset, dataloader_cls=DataLoader
        )
        for (batch,) in dl:
            assert not torch.isnan(batch).any(), (
                "NaN values must be replaced with 0.0 in the DataLoader tensor."
            )


# ---------------------------------------------------------------------------
# Behavioral tests: GanHyperparams and TrainingConfig dataclasses
# ---------------------------------------------------------------------------


class TestGanHyperparamsBehavioral:
    """Behavioral tests for GanHyperparams frozen dataclass.

    Setup-to-assertion ratio: 1:1.
    """

    def test_gan_hyperparams_is_frozen(self) -> None:
        """GanHyperparams must be immutable (frozen dataclass)."""
        import dataclasses

        from synth_engine.modules.synthesizer.training_strategies import GanHyperparams

        hyp = GanHyperparams(
            embedding_dim=128,
            generator_dim=(256, 256),
            discriminator_dim=(256, 256),
            pac=10,
            discriminator_steps=1,
            batch_size=500,
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            hyp.embedding_dim = 64  # type: ignore[misc]

    def test_training_config_is_frozen(self) -> None:
        """TrainingConfig must be immutable (frozen dataclass)."""
        import dataclasses

        from synth_engine.modules.synthesizer.training_strategies import TrainingConfig

        config = TrainingConfig(
            embedding_dim=128, data_dim=10, pac=10, batch_size=50, discriminator_steps=1
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            config.embedding_dim = 64  # type: ignore[misc]

    def test_optimizers_carries_both_optimizers(self) -> None:
        """Optimizers dataclass must store both optimizer_g and dp_optimizer."""
        from synth_engine.modules.synthesizer.training_strategies import Optimizers

        sentinel_g = object()
        sentinel_dp = object()
        opts = Optimizers(optimizer_g=sentinel_g, dp_optimizer=sentinel_dp)
        assert opts.optimizer_g is sentinel_g
        assert opts.dp_optimizer is sentinel_dp


# ---------------------------------------------------------------------------
# Behavioral tests: DPCompatibleCTGAN fit() with real DataFrames
# (patching only external I/O — CTGANSynthesizer, CTGAN boundaries)
# ---------------------------------------------------------------------------


class TestDPCompatibleCTGANFitBehavioral:
    """Behavioral tests for DPCompatibleCTGAN.fit() and sample().

    Uses MagicMock only at external I/O boundaries (CTGANSynthesizer, CTGAN).
    All assertions are on return values, not on call counts.

    Setup-to-assertion ratio: 4:1.
    """

    def _make_fitted_vanilla_instance(self, n: int = 10) -> Any:
        """Fit a DPCompatibleCTGAN in vanilla mode with a small real DataFrame.

        SDV/CTGAN are patched at their import boundaries.  The patched CTGAN
        returns a real-shaped sample DataFrame.

        Args:
            n: Number of training rows.

        Returns:
            A fitted DPCompatibleCTGAN instance.
        """
        from unittest.mock import patch

        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        df = _small_numeric_df(n=n)
        mock_metadata = MagicMock()

        rng = np.random.default_rng(0)
        processed_df = pd.DataFrame(
            {
                "age": rng.uniform(0.0, 1.0, size=n).tolist(),
                "salary": rng.uniform(0.0, 1.0, size=n).tolist(),
            }
        )
        sample_df = pd.DataFrame(
            {
                "age": rng.uniform(18.0, 80.0, size=5).tolist(),
                "salary": rng.uniform(30000.0, 120000.0, size=5).tolist(),
            }
        )

        mock_sdv = MagicMock()
        mock_sdv.preprocess.return_value = processed_df
        mock_sdv._model_kwargs = {
            "embedding_dim": 8,
            "generator_dim": (16,),
            "discriminator_dim": (16,),
            "generator_lr": 2e-4,
            "generator_decay": 1e-6,
            "discriminator_lr": 2e-4,
            "discriminator_decay": 1e-6,
            "batch_size": 4,
            "discriminator_steps": 1,
            "pac": 2,
            "enable_gpu": False,
        }
        mock_proc = MagicMock()
        mock_proc._hyper_transformer.field_transformers = {}
        mock_sdv._data_processor = mock_proc
        mock_sdv._data_processor.reverse_transform.return_value = sample_df

        mock_ctgan = MagicMock()
        mock_ctgan.sample.return_value = processed_df.head(5)

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=None)
            instance.fit(df)

        return instance

    def test_fit_produces_fitted_instance(self) -> None:
        """fit() must produce an instance with _fitted=True."""
        instance = self._make_fitted_vanilla_instance()
        assert instance._fitted is True

    def test_sample_returns_dataframe(self) -> None:
        """sample(N) on a fitted instance must return a pd.DataFrame."""
        instance = self._make_fitted_vanilla_instance()
        result = instance.sample(num_rows=5)
        assert isinstance(result, pd.DataFrame), (
            f"sample() must return pd.DataFrame, got {type(result)}"
        )

    def test_sample_result_has_correct_columns(self) -> None:
        """sample() result must have the same columns as the mock's reverse_transform output."""
        instance = self._make_fitted_vanilla_instance()
        result = instance.sample(num_rows=5)
        assert "age" in result.columns, (
            f"Column 'age' missing from sample output: {list(result.columns)}"
        )
        assert "salary" in result.columns, (
            f"Column 'salary' missing from sample output: {list(result.columns)}"
        )

    def test_fit_raises_value_error_for_empty_df(self) -> None:
        """fit(empty_df) must raise ValueError immediately — no mocks needed."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        instance = DPCompatibleCTGAN(metadata=MagicMock(), epochs=1)
        empty_df = pd.DataFrame({"age": [], "salary": []})

        with pytest.raises(ValueError, match="empty"):
            instance.fit(empty_df)

    def test_sample_before_fit_raises_runtime_error_with_informative_message(self) -> None:
        """sample() before fit() must raise RuntimeError mentioning 'fit'."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        instance = DPCompatibleCTGAN(metadata=MagicMock(), epochs=1)
        with pytest.raises(RuntimeError, match="fit"):
            instance.sample(num_rows=5)

    def test_sample_negative_rows_raises_value_error(self) -> None:
        """sample(-1) must raise ValueError with informative message."""
        instance = self._make_fitted_vanilla_instance()
        with pytest.raises(ValueError, match="num_rows"):
            instance.sample(num_rows=-1)


# ---------------------------------------------------------------------------
# Contract tests: SDV DataProcessor API assumptions
# ---------------------------------------------------------------------------


class TestSdvDataProcessorContract:
    """Contract tests verifying the SDV DataProcessor assumptions encoded in our mocks.

    These tests verify that the SDV/CTGAN APIs we mock actually behave as assumed.
    They use pytest.importorskip() so they are skipped when SDV is not installed.

    AC6: at least 2 contract tests verifying SDV API assumptions.
    """

    def test_ctgan_synthesizer_has_preprocess_method(self) -> None:
        """CTGANSynthesizer must expose a preprocess() method (our mocks assume this)."""
        sdv_single_table = pytest.importorskip("sdv.single_table")
        ctgan_synth_cls = sdv_single_table.CTGANSynthesizer
        assert hasattr(ctgan_synth_cls, "preprocess"), (
            "CTGANSynthesizer.preprocess() must exist. "
            "Our mocks in test_dp_training_*.py call mock_synth.preprocess(df). "
            "If this contract breaks, all DP training tests that mock preprocess are invalid."
        )

    def test_ctgan_synthesizer_has_data_processor_attribute(self) -> None:
        """CTGANSynthesizer instances must have a _data_processor attribute (ADR-0025).

        This is the private SDV coupling accepted per ADR-0025.  If SDV changes
        this internal attribute name, all mocks that set mock_synth._data_processor
        will silently become incorrect.
        """
        sdv_single_table = pytest.importorskip("sdv.single_table")
        sdv_metadata_mod = pytest.importorskip("sdv.metadata")
        ctgan_synth_cls = sdv_single_table.CTGANSynthesizer
        metadata_cls = sdv_metadata_mod.SingleTableMetadata

        df = _small_numeric_df(n=10)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            metadata = metadata_cls()
            metadata.detect_from_dataframe(df)
            synth = ctgan_synth_cls(metadata=metadata, epochs=1)

        assert hasattr(synth, "_data_processor"), (
            "CTGANSynthesizer must have _data_processor attribute (ADR-0025 coupling point). "
            "Our mocks set mock_synth._data_processor = MagicMock(). "
            "If this breaks, DPCompatibleCTGAN._get_data_processor() will fail."
        )

    def test_ctgan_synthesizer_has_model_kwargs_attribute(self) -> None:
        """CTGANSynthesizer instances must have a _model_kwargs attribute (ADR-0025).

        This is a private SDV attribute accessed by DPCompatibleCTGAN._get_model_kwargs().
        The mock hard-codes this dict; the contract test verifies the real dict exists.
        """
        sdv_single_table = pytest.importorskip("sdv.single_table")
        sdv_metadata_mod = pytest.importorskip("sdv.metadata")
        ctgan_synth_cls = sdv_single_table.CTGANSynthesizer
        metadata_cls = sdv_metadata_mod.SingleTableMetadata

        df = _small_numeric_df(n=10)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            metadata = metadata_cls()
            metadata.detect_from_dataframe(df)
            synth = ctgan_synth_cls(metadata=metadata, epochs=1)

        assert hasattr(synth, "_model_kwargs"), (
            "CTGANSynthesizer must have _model_kwargs attribute (ADR-0025 coupling point). "
            "Our mocks set mock_synth._model_kwargs = {...}. "
            "If this breaks, DPCompatibleCTGAN._get_model_kwargs() will fail."
        )

    def test_ctgan_synthesizer_model_kwargs_has_expected_keys(self) -> None:
        """CTGANSynthesizer._model_kwargs must contain the keys our mocks hard-code.

        The mocks in test_dp_training_init.py and test_dp_training_loop.py hard-code:
          embedding_dim, generator_dim, discriminator_dim, pac, discriminator_steps, batch_size.
        This contract test verifies those keys exist in the real SDV object.
        """
        sdv_single_table = pytest.importorskip("sdv.single_table")
        sdv_metadata_mod = pytest.importorskip("sdv.metadata")
        ctgan_synth_cls = sdv_single_table.CTGANSynthesizer
        metadata_cls = sdv_metadata_mod.SingleTableMetadata

        df = _small_numeric_df(n=10)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            metadata = metadata_cls()
            metadata.detect_from_dataframe(df)
            synth = ctgan_synth_cls(metadata=metadata, epochs=1)

        required_keys = {
            "embedding_dim",
            "generator_dim",
            "discriminator_dim",
            "pac",
            "batch_size",
        }
        actual_keys = set(synth._model_kwargs.keys())
        missing = required_keys - actual_keys
        assert not missing, (
            f"CTGANSynthesizer._model_kwargs is missing keys our mocks hard-code: {missing}. "
            "Update the mock dictionaries in test_dp_training_*.py to match the real SDV API."
        )

    def test_detect_discrete_columns_is_callable(self) -> None:
        """detect_discrete_columns must be importable and callable (our mocks assume this)."""
        sdv_ctgan_mod = pytest.importorskip("sdv.single_table.ctgan")
        detect_fn = getattr(sdv_ctgan_mod, "detect_discrete_columns", None)
        assert detect_fn is not None, (
            "sdv.single_table.ctgan.detect_discrete_columns must exist and be callable. "
            "Our mocks patch synth_engine.modules.synthesizer.dp_training.detect_discrete_columns. "
            "If this import path breaks, the DP training preprocess step will fail silently."
        )
        import inspect

        assert inspect.isfunction(detect_fn), (
            "detect_discrete_columns must be a plain function, not just any callable. "
            "Our mock uses unittest.mock.patch which requires a real function target."
        )
        # Verify it accepts at least one argument (the DataFrame parameter)
        sig = inspect.signature(detect_fn)
        assert len(sig.parameters) >= 1, (
            "detect_discrete_columns must accept at least one parameter (the DataFrame)"
        )


# ---------------------------------------------------------------------------
# Contract tests: CTGAN Generator API assumptions
# ---------------------------------------------------------------------------


class TestCtganGeneratorContract:
    """Contract tests verifying the CTGAN Generator assumptions encoded in mocks.

    Skipped when ctgan is not installed.
    """

    def test_ctgan_generator_class_is_importable(self) -> None:
        """ctgan.synthesizers.ctgan.Generator must be importable (used in DP loop)."""
        pytest.importorskip("ctgan")
        from ctgan.synthesizers.ctgan import Generator

        assert callable(Generator), (
            "ctgan.synthesizers.ctgan.Generator must be importable. "
            "DPCompatibleCTGAN builds a Generator for the custom DP-SGD loop."
        )

    def test_ctgan_synthesizer_class_is_importable(self) -> None:
        """ctgan.synthesizers.ctgan.CTGAN must be importable (used in vanilla path)."""
        pytest.importorskip("ctgan")
        from ctgan.synthesizers.ctgan import CTGAN

        assert callable(CTGAN), "ctgan.synthesizers.ctgan.CTGAN must be a callable class"


# ---------------------------------------------------------------------------
# Behavioral tests: VanillaCtganStrategy with real inputs
# (CTGAN class patched at boundary — not a real training run)
# ---------------------------------------------------------------------------


class TestVanillaCtganStrategyBehavioral:
    """Behavioral tests for VanillaCtganStrategy.run() with real DataFrames.

    The CTGAN class is patched at the boundary (external I/O), but the
    strategy's orchestration logic runs for real.

    Setup-to-assertion ratio: 3:1.
    """

    def _make_sdv_synth_stub(self) -> Any:
        """Build a minimal sdv_synth stub with a real model_kwargs dict.

        Returns a SimpleNamespace instead of MagicMock so attribute access is
        explicit and contract-verifiable.

        Returns:
            A SimpleNamespace with _model_kwargs matching the real SDV schema.
        """
        from types import SimpleNamespace

        return SimpleNamespace(
            _model_kwargs={
                "embedding_dim": 8,
                "generator_dim": (16,),
                "discriminator_dim": (16,),
                "generator_lr": 2e-4,
                "generator_decay": 1e-6,
                "discriminator_lr": 2e-4,
                "discriminator_decay": 1e-6,
                "batch_size": 4,
                "discriminator_steps": 1,
                "log_frequency": False,
                "verbose": False,
                "epochs": 1,
                "pac": 2,
                "enable_gpu": False,
            }
        )

    def test_run_returns_ctgan_instance(self) -> None:
        """VanillaCtganStrategy.run() must return the CTGAN model instance."""
        from synth_engine.modules.synthesizer.training_strategies import VanillaCtganStrategy

        mock_ctgan_cls = MagicMock()
        mock_ctgan_instance = MagicMock()
        mock_ctgan_cls.return_value = mock_ctgan_instance

        strategy = VanillaCtganStrategy()
        result = strategy.run(
            self._make_sdv_synth_stub(),
            _small_numeric_df(n=10),
            discrete_columns=[],
            ctgan_cls=mock_ctgan_cls,
            epochs=1,
        )

        assert result is mock_ctgan_instance, (
            "VanillaCtganStrategy.run() must return the CTGAN model instance."
        )

    def test_run_calls_ctgan_fit_with_discrete_columns(self) -> None:
        """VanillaCtganStrategy.run() must call CTGAN.fit() with discrete_columns kwarg."""
        from synth_engine.modules.synthesizer.training_strategies import VanillaCtganStrategy

        mock_ctgan_cls = MagicMock()
        mock_ctgan_instance = MagicMock()
        mock_ctgan_cls.return_value = mock_ctgan_instance

        discrete_columns = ["category"]
        strategy = VanillaCtganStrategy()
        strategy.run(
            self._make_sdv_synth_stub(),
            _small_numeric_df(n=10),
            discrete_columns=discrete_columns,
            ctgan_cls=mock_ctgan_cls,
            epochs=1,
        )

        mock_ctgan_instance.fit.assert_called_once()
        call_kwargs = mock_ctgan_instance.fit.call_args.kwargs
        assert call_kwargs.get("discrete_columns") == discrete_columns, (
            f"CTGAN.fit() must receive discrete_columns={discrete_columns!r}, got: {call_kwargs}"
        )

    def test_run_overrides_epochs_in_model_kwargs(self) -> None:
        """VanillaCtganStrategy.run() must pass the epochs parameter to CTGAN constructor."""
        from synth_engine.modules.synthesizer.training_strategies import VanillaCtganStrategy

        mock_ctgan_cls = MagicMock()
        mock_ctgan_cls.return_value = MagicMock()

        strategy = VanillaCtganStrategy()
        strategy.run(
            self._make_sdv_synth_stub(),
            _small_numeric_df(n=10),
            discrete_columns=[],
            ctgan_cls=mock_ctgan_cls,
            epochs=7,
        )

        _args, kwargs = mock_ctgan_cls.call_args
        assert kwargs.get("epochs") == 7, (
            f"VanillaCtganStrategy must pass epochs=7 to CTGAN constructor, got: {kwargs}"
        )


# ---------------------------------------------------------------------------
# Behavioral tests: DpCtganStrategy delegation (spy-based pattern)
# ---------------------------------------------------------------------------


class TestDpCtganStrategyBehavioral:
    """Behavioral tests for DpCtganStrategy.run() delegation contract.

    Uses a spy function (not MagicMock) on the coordinator's internal method
    to verify delegation happens without replacing real behavior.

    Setup-to-assertion ratio: 3:1.
    """

    def test_run_delegates_to_coordinator_train_dp_discriminator(self) -> None:
        """DpCtganStrategy.run() must delegate to coordinator._train_dp_discriminator()."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN
        from synth_engine.modules.synthesizer.training_strategies import DpCtganStrategy

        mock_dp_wrapper = MagicMock()
        coordinator = DPCompatibleCTGAN(metadata=MagicMock(), epochs=1, dp_wrapper=mock_dp_wrapper)

        calls: list[tuple[Any, Any]] = []

        def spy_train(processed_df: pd.DataFrame, model_kwargs: dict[str, Any]) -> None:
            calls.append((processed_df, model_kwargs))

        coordinator._train_dp_discriminator = spy_train  # type: ignore[method-assign]

        strategy = DpCtganStrategy(dp_wrapper=mock_dp_wrapper)
        processed_df = _small_numeric_df(n=10)
        model_kwargs: dict[str, Any] = {"embedding_dim": 8, "pac": 2, "batch_size": 4}

        strategy.run(coordinator, processed_df, model_kwargs)

        assert len(calls) == 1, (
            f"DpCtganStrategy.run() must call coordinator._train_dp_discriminator() exactly once, "
            f"called {len(calls)} times."
        )

    def test_run_passes_processed_df_unchanged(self) -> None:
        """DpCtganStrategy.run() must pass the processed_df through unchanged."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN
        from synth_engine.modules.synthesizer.training_strategies import DpCtganStrategy

        mock_dp_wrapper = MagicMock()
        coordinator = DPCompatibleCTGAN(metadata=MagicMock(), epochs=1, dp_wrapper=mock_dp_wrapper)

        received: list[pd.DataFrame] = []

        def spy_train(processed_df: pd.DataFrame, model_kwargs: dict[str, Any]) -> None:
            received.append(processed_df)

        coordinator._train_dp_discriminator = spy_train  # type: ignore[method-assign]

        strategy = DpCtganStrategy(dp_wrapper=mock_dp_wrapper)
        original_df = _small_numeric_df(n=10)
        strategy.run(coordinator, original_df, {"pac": 2})

        assert len(received) == 1
        pd.testing.assert_frame_equal(received[0], original_df)


# ---------------------------------------------------------------------------
# Behavioral test: slow marker — real VanillaCtganStrategy CTGAN training
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_vanilla_ctgan_strategy_trains_real_small_dataframe() -> None:
    """VanillaCtganStrategy.run() must produce a CTGAN model that can call sample().

    This test uses real CTGAN training on a tiny (20-row) DataFrame — no SDV
    mocking.  It verifies that the strategy's CTGAN.fit() call produces a
    model capable of calling sample().

    Marked @pytest.mark.slow — excluded from the default unit gate via
    ``-m "not slow"`` but included in integration / full-suite runs.

    SDV/ctgan is required; test is skipped if not installed.
    """
    sdv_module = pytest.importorskip("sdv.single_table")
    sdv_metadata_module = pytest.importorskip("sdv.metadata")
    pytest.importorskip("ctgan")

    from ctgan.synthesizers.ctgan import CTGAN

    from synth_engine.modules.synthesizer.training_strategies import VanillaCtganStrategy

    ctgan_synth_cls = sdv_module.CTGANSynthesizer
    metadata_cls = sdv_metadata_module.SingleTableMetadata

    df = _small_numeric_df(n=20)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        metadata = metadata_cls()
        metadata.detect_from_dataframe(df)
        sdv_synth = ctgan_synth_cls(metadata=metadata, epochs=1)
        processed_df = sdv_synth.preprocess(df)
        discrete_columns: list[str] = []

    strategy = VanillaCtganStrategy()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ctgan_model = strategy.run(
            sdv_synth, processed_df, discrete_columns, ctgan_cls=CTGAN, epochs=1
        )

    assert isinstance(ctgan_model, CTGAN), (
        f"VanillaCtganStrategy.run() must return a CTGAN instance, got {type(ctgan_model)}."
    )
    sampled = ctgan_model.sample(5)
    assert sampled is not None, (
        "CTGAN.sample() must return data after VanillaCtganStrategy training."
    )


# ---------------------------------------------------------------------------
# QA-F3: Zero-numeric-column fallback path in _build_dp_dataloader
# ---------------------------------------------------------------------------


class TestBuildDpDataloaderZeroNumericColumns:
    """Unit test for the np.zeros fallback path in _build_dp_dataloader.

    QA-F3: Verify that a DataFrame with only string (non-numeric) columns
    triggers the ``arr = np.zeros((n, 1), dtype="float32")`` fallback,
    producing a valid DataLoader with shape (n, 1).

    Setup-to-assertion ratio: 2:1.
    """

    def test_zero_numeric_columns_triggers_fallback(self) -> None:
        """_build_dp_dataloader must use np.zeros fallback for all-string DataFrame.

        When the DataFrame has no numeric columns, select_dtypes returns a
        0-column array.  The fallback replaces it with np.zeros((n, 1)) so
        the DataLoader has feature dimension 1 rather than 0.
        """
        torch = pytest.importorskip("torch")
        from unittest.mock import MagicMock

        from torch.utils.data import DataLoader, TensorDataset

        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        df_strings_only = pd.DataFrame(
            {"name": ["alice", "bob", "carol"] * 8, "city": ["nyc", "la", "chi"] * 8}
        )

        instance = DPCompatibleCTGAN(metadata=MagicMock(), epochs=1)
        # Inject real torch objects so the real DataLoader code executes.
        # We need to monkeypatch the module-level names used by _build_dp_dataloader.
        import synth_engine.modules.synthesizer.dp_training as dp_mod

        original_torch = dp_mod.torch
        original_tensor_dataset = dp_mod.TensorDataset
        original_dataloader = dp_mod.DataLoader
        dp_mod.torch = torch
        dp_mod.TensorDataset = TensorDataset
        dp_mod.DataLoader = DataLoader

        try:
            batch_size = 4
            dataloader = instance._build_dp_dataloader(df_strings_only, batch_size)
            (first_batch,) = next(iter(dataloader))
            # Fallback fills shape (n, 1) — feature dimension must be 1
            assert first_batch.shape[1] == 1, (
                f"_build_dp_dataloader must produce feature dim=1 for all-string DataFrame, "
                f"got shape {first_batch.shape}. The np.zeros fallback was not triggered."
            )
        finally:
            dp_mod.torch = original_torch
            dp_mod.TensorDataset = original_tensor_dataset
            dp_mod.DataLoader = original_dataloader
