"""Unit tests for DPCompatibleCTGAN.__init__ and fit() core behaviour.

Tests follow TDD Red/Green/Refactor.

Classes:
  TestDPCompatibleCTGANInit — Behavioral tests for __init__ (no external patches;
    only boundary mocks for metadata and dp_wrapper, which are injected dependencies).

  TestDPCompatibleCTGANFitWiring — Wiring tests for fit().  These tests patch
    CTGANSynthesizer, CTGAN, and detect_discrete_columns to verify that fit()
    calls the correct SDV/CTGAN interfaces in the correct order.  They do NOT
    test that SDV or CTGAN work correctly — they test that DPCompatibleCTGAN
    wires them together correctly.  Named *Wiring per T40.2 AC4.

Background (T40.2): Wiring tests (3+ patches on the module under test) are
legitimate but MUST be labeled *Wiring so reviewers understand the scope.
Behavioral correctness of SDV/CTGAN is delegated to @pytest.mark.synthesizer
tests in test_dp_training_behavioral.py.

CONSTITUTION Priority 3: TDD Red/Green/Refactor.
Task: P7-T7.2 — Custom CTGAN Training Loop
Task: P26-T26.6 — Split from test_dp_training.py for maintainability
Task: P40-T40.2 — Replace Mock-Heavy Tests With Behavioral Tests (wiring labeling)
T49.3: _make_training_df and _make_mock_ctgan_model extracted to helpers_synthesizer.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tests.unit.helpers_synthesizer import make_mock_ctgan_model, make_training_df

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Module-level aliases for backward-compatible call sites in this file.
# These avoid renaming every call site while eliminating the duplicate definitions.
# ---------------------------------------------------------------------------
_make_training_df = make_training_df
_make_mock_ctgan_model = make_mock_ctgan_model


# ---------------------------------------------------------------------------
# Helpers unique to this module
# ---------------------------------------------------------------------------


def _make_mock_sdv_synthesizer(processed_df: pd.DataFrame | None = None) -> MagicMock:
    """Return a mock CTGANSynthesizer with realistic preprocess / _data_processor.

    Boundary mock: replaces the external SDV library at its interface boundary.
    Used only in *Wiring tests that verify wiring, not SDV correctness.

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
    mock_proc = MagicMock()
    mock_proc._hyper_transformer.field_transformers = {}
    mock_synth._data_processor = mock_proc

    return mock_synth


# ---------------------------------------------------------------------------
# Behavioral tests for __init__
# ---------------------------------------------------------------------------


class TestDPCompatibleCTGANInit:
    """Behavioral tests for DPCompatibleCTGAN.__init__ signature and defaults.

    These tests use real Python objects — MagicMock is used only for injected
    dependencies (metadata, dp_wrapper) which are boundary objects, not the
    code under test.  No external library patching is performed.

    Setup-to-assertion ratio: 1:1 for each test.
    """

    def test_init_accepts_metadata_epochs_dp_wrapper(self) -> None:
        """__init__ must accept metadata, epochs, and dp_wrapper parameters."""
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = MagicMock()

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata,
            epochs=5,
            dp_wrapper=mock_dp_wrapper,
        )
        assert isinstance(instance, DPCompatibleCTGAN), (
            f"Expected DPCompatibleCTGAN instance, got {type(instance)}"
        )

    def test_init_dp_wrapper_defaults_to_none(self) -> None:
        """dp_wrapper must default to None — vanilla mode."""
        import inspect

        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        sig = inspect.signature(DPCompatibleCTGAN.__init__)
        assert "dp_wrapper" in sig.parameters
        assert sig.parameters["dp_wrapper"].default is None

    def test_init_stores_epochs(self) -> None:
        """__init__ must store the epochs parameter as an instance attribute."""
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=7)
        assert instance._epochs == 7

    def test_init_stores_metadata(self) -> None:
        """__init__ must store the metadata parameter as an instance attribute."""
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
        assert instance._metadata is mock_metadata

    def test_init_stores_dp_wrapper(self) -> None:
        """__init__ must store dp_wrapper as _dp_wrapper."""
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_wrapper = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2, dp_wrapper=mock_wrapper)
        assert instance._dp_wrapper is mock_wrapper

    def test_init_dp_wrapper_none_stored_correctly(self) -> None:
        """__init__ with dp_wrapper=None must store None."""
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2, dp_wrapper=None)
        assert instance._dp_wrapper is None

    def test_init_not_fitted_initially(self) -> None:
        """A freshly created instance must not be marked as fitted."""
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
        assert instance._fitted is False


# ---------------------------------------------------------------------------
# Wiring tests for fit() — verify SDV/CTGAN interface wiring
# ---------------------------------------------------------------------------


class TestDPCompatibleCTGANFitWiring:
    """Wiring tests for DPCompatibleCTGAN.fit().

    SCOPE: These tests verify that fit() wires SDV and CTGAN together correctly:
    - Calls CTGANSynthesizer.preprocess()
    - Stores _data_processor
    - Calls CTGAN.fit()
    - Passes discrete_columns to CTGAN.fit()

    They do NOT test SDV correctness or CTGAN correctness — those are external
    libraries tested by their own suites.  Named *Wiring per T40.2 AC4 because
    each test patches 3 things in the module under test:
    CTGANSynthesizer, CTGAN, detect_discrete_columns.
    """

    def test_fit_returns_self(self) -> None:
        """fit() must return self to allow method chaining."""
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            df = _make_training_df()
            result = instance.fit(df)

        assert result is instance

    def test_fit_marks_instance_as_fitted(self) -> None:
        """fit() must set _fitted=True after successful training."""
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df())

        assert instance._fitted is True

    def test_fit_calls_sdv_preprocess(self) -> None:
        """fit() must call CTGANSynthesizer.preprocess() to transform the input DataFrame."""
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            df = _make_training_df()
            instance.fit(df)

        mock_sdv_synth.preprocess.assert_called_once_with(df)

    def test_fit_stores_data_processor(self) -> None:
        """fit() must store _data_processor from the SDV synth for later reverse_transform."""
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df())

        assert instance._data_processor is mock_sdv_synth._data_processor

    def test_fit_calls_ctgan_fit(self) -> None:
        """fit() must call CTGAN.fit() on the underlying model."""
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.detect_discrete_columns",
                return_value=["dept"],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df())

        mock_ctgan_instance.fit.assert_called_once()

    def test_fit_passes_discrete_columns_to_ctgan(self) -> None:
        """fit() must pass the discrete_columns detected by SDV to CTGAN.fit()."""
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        detected_discrete = ["dept"]

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.detect_discrete_columns",
                return_value=detected_discrete,
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df())

        _call_args = mock_ctgan_instance.fit.call_args
        assert _call_args is not None
        assert _call_args.kwargs.get("discrete_columns") == detected_discrete
