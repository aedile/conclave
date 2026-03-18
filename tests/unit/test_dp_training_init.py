"""Unit tests for DPCompatibleCTGAN.__init__ and fit() core behaviour.

Tests follow TDD Red/Green/Refactor.  All tests are isolated — SDV internals
are mocked so these tests run without a GPU and without a full CTGAN training
run.  Every test asserts on the *return value* of the function under test, not
merely on the absence of an exception.

Covers:
  - __init__ signature and parameter defaults
  - __init__ stores epochs, metadata, dp_wrapper, _fitted attributes
  - fit() returns self (method chaining)
  - fit() marks instance as fitted
  - fit() calls preprocess, stores data processor, calls CTGAN.fit
  - fit() passes discrete_columns to CTGAN.fit

CONSTITUTION Priority 3: TDD Red/Green/Refactor.
Task: P7-T7.2 — Custom CTGAN Training Loop
Task: P26-T26.6 — Split from test_dp_training.py for maintainability
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_training_df(n: int = 50) -> pd.DataFrame:
    """Return a simple fictional training DataFrame for test fixtures.

    Uses a seeded NumPy RNG — deterministic, no PII.

    Args:
        n: Number of rows.

    Returns:
        DataFrame with columns: id (int), age (int), dept (str).
    """
    import numpy as np

    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "id": range(1, n + 1),
            "age": rng.integers(18, 80, size=n).tolist(),
            "dept": rng.choice(["Engineering", "Marketing", "Sales"], size=n).tolist(),
        }
    )


def _make_mock_sdv_synthesizer(processed_df: pd.DataFrame | None = None) -> MagicMock:
    """Return a mock CTGANSynthesizer with realistic preprocess / _data_processor.

    Args:
        processed_df: The DataFrame that synth.preprocess() will return.
            Defaults to a 50-row DataFrame with columns ['age', 'dept'].

    Returns:
        Configured MagicMock standing in for CTGANSynthesizer.
    """
    if processed_df is None:
        import numpy as np

        rng = np.random.default_rng(99)
        processed_df = pd.DataFrame(
            {
                "age": rng.integers(18, 80, size=50).tolist(),
                "dept": rng.choice(["Engineering", "Marketing", "Sales"], size=50).tolist(),
            }
        )

    mock_synth = MagicMock()
    mock_synth.preprocess.return_value = processed_df
    mock_synth._model_kwargs = {
        "embedding_dim": 128,
        "generator_dim": (256, 256),
        "discriminator_dim": (256, 256),
        "generator_lr": 2e-4,
        "generator_decay": 1e-6,
        "discriminator_lr": 2e-4,
        "discriminator_decay": 1e-6,
        "batch_size": 500,
        "discriminator_steps": 1,
        "log_frequency": True,
        "verbose": False,
        "epochs": 2,
        "pac": 10,
        "enable_gpu": True,
    }
    # _data_processor for reverse_transform
    mock_proc = MagicMock()
    mock_proc._hyper_transformer.field_transformers = {}
    mock_synth._data_processor = mock_proc

    return mock_synth


def _make_mock_ctgan_model(n_rows: int = 50) -> MagicMock:
    """Return a mock CTGAN model that produces a known sample DataFrame.

    Args:
        n_rows: Number of rows CTGAN.sample() will return.

    Returns:
        MagicMock standing in for ctgan.synthesizers.ctgan.CTGAN.
    """
    mock_ctgan = MagicMock()
    mock_ctgan.sample.return_value = pd.DataFrame(
        {
            "age": list(range(n_rows)),
            "dept": ["Engineering"] * n_rows,
        }
    )
    return mock_ctgan


# ---------------------------------------------------------------------------
# Tests for __init__
# ---------------------------------------------------------------------------


class TestDPCompatibleCTGANInit:
    """Unit tests for DPCompatibleCTGAN.__init__ signature and defaults."""

    def test_init_accepts_metadata_epochs_dp_wrapper(self) -> None:
        """__init__ must accept metadata, epochs, and dp_wrapper parameters."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = MagicMock()

        # Should not raise
        instance = DPCompatibleCTGAN(
            metadata=mock_metadata,
            epochs=5,
            dp_wrapper=mock_dp_wrapper,
        )
        assert instance is not None

    def test_init_dp_wrapper_defaults_to_none(self) -> None:
        """dp_wrapper must default to None — vanilla mode."""
        import inspect

        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        sig = inspect.signature(DPCompatibleCTGAN.__init__)
        assert "dp_wrapper" in sig.parameters
        assert sig.parameters["dp_wrapper"].default is None

    def test_init_stores_epochs(self) -> None:
        """__init__ must store the epochs parameter as an instance attribute."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=7)
        assert instance._epochs == 7

    def test_init_stores_metadata(self) -> None:
        """__init__ must store the metadata parameter as an instance attribute."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
        assert instance._metadata is mock_metadata

    def test_init_stores_dp_wrapper(self) -> None:
        """__init__ must store dp_wrapper as _dp_wrapper."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_wrapper = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2, dp_wrapper=mock_wrapper)
        assert instance._dp_wrapper is mock_wrapper

    def test_init_dp_wrapper_none_stored_correctly(self) -> None:
        """__init__ with dp_wrapper=None must store None."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2, dp_wrapper=None)
        assert instance._dp_wrapper is None

    def test_init_not_fitted_initially(self) -> None:
        """A freshly created instance must not be marked as fitted."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
        assert instance._fitted is False


# ---------------------------------------------------------------------------
# Tests for fit() — with mocked SDV and CTGAN internals
# ---------------------------------------------------------------------------


class TestDPCompatibleCTGANFit:
    """Unit tests for DPCompatibleCTGAN.fit() with mocked SDV internals."""

    def _patch_sdv_and_ctgan(self, mock_sdv_synth: MagicMock, mock_ctgan_cls: MagicMock) -> Any:
        """Return the combined patch context manager for SDV and CTGAN.

        Args:
            mock_sdv_synth: Pre-configured CTGANSynthesizer mock instance.
            mock_ctgan_cls: Mock for ctgan.synthesizers.ctgan.CTGAN class.

        Returns:
            A context manager string for use in with-statement patching.
        """
        return mock_sdv_synth

    def test_fit_returns_self(self) -> None:
        """fit() must return self to allow method chaining."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            df = _make_training_df()
            result = instance.fit(df)

        assert result is instance

    def test_fit_marks_instance_as_fitted(self) -> None:
        """fit() must set _fitted=True after successful training."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df())

        assert instance._fitted is True

    def test_fit_calls_sdv_preprocess(self) -> None:
        """fit() must call CTGANSynthesizer.preprocess() to transform the input DataFrame."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            df = _make_training_df()
            instance.fit(df)

        mock_sdv_synth.preprocess.assert_called_once_with(df)

    def test_fit_stores_data_processor(self) -> None:
        """fit() must store _data_processor from the SDV synth for later reverse_transform."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df())

        assert instance._data_processor is mock_sdv_synth._data_processor

    def test_fit_calls_ctgan_fit(self) -> None:
        """fit() must call CTGAN.fit() on the underlying model."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=["dept"],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df())

        mock_ctgan_instance.fit.assert_called_once()

    def test_fit_passes_discrete_columns_to_ctgan(self) -> None:
        """fit() must pass the discrete_columns detected by SDV to CTGAN.fit()."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        detected_discrete = ["dept"]

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=detected_discrete,
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df())

        _call_args = mock_ctgan_instance.fit.call_args
        assert _call_args is not None
        # discrete_columns must be passed as keyword arg
        assert _call_args.kwargs.get("discrete_columns") == detected_discrete
