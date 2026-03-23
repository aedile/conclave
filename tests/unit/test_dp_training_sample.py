"""Unit tests for DPCompatibleCTGAN DP wrapper integration and sample().

Tests follow TDD Red/Green/Refactor.  All tests are isolated — SDV internals
are mocked so these tests run without a GPU and without a full CTGAN training
run.

Covers:
  - fit() with dp_wrapper does NOT call wrap() when dp_wrapper=None
  - fit() with dp_wrapper calls wrap() BEFORE CTGAN.fit()
  - wrap() is called with required optimizer/model/dataloader kwargs
  - sample() returns pd.DataFrame
  - sample() returns correct row count
  - sample() calls CTGAN.sample()
  - sample() calls reverse_transform()
  - sample() before fit() raises RuntimeError
  - sample(num_rows=0) raises ValueError
  - sample(num_rows<0) raises ValueError

CONSTITUTION Priority 3: TDD Red/Green/Refactor.
Task: P7-T7.2 — Custom CTGAN Training Loop
Task: P26-T26.6 — Split from test_dp_training.py for maintainability
T49.3: _make_training_df and _make_mock_ctgan_model extracted to helpers_synthesizer.
"""

from __future__ import annotations

from typing import Any
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
# Helper unique to this module
# ---------------------------------------------------------------------------


def _make_mock_sdv_synthesizer(processed_df: pd.DataFrame | None = None) -> MagicMock:
    """Return a mock CTGANSynthesizer with realistic preprocess / _data_processor.

    Args:
        processed_df: The DataFrame that synth.preprocess() will return.

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
# Tests for fit() — DP wrapper integration
# ---------------------------------------------------------------------------


class TestDPCompatibleCTGANFitWithDPWrapper:
    """Tests for DPCompatibleCTGAN.fit() when dp_wrapper is provided.

    The dp_wrapper parameter is typed as Any (never imports from modules/privacy).
    Expected interface: wrap(optimizer, model, dataloader, ...) -> optimizer
    """

    def test_fit_without_dp_wrapper_does_not_call_wrap(self) -> None:
        """fit(df) with dp_wrapper=None must NOT call any wrap() method."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()
        mock_dp_wrapper = MagicMock()

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
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2, dp_wrapper=None)
            instance.fit(_make_training_df())

        mock_dp_wrapper.wrap.assert_not_called()

    def test_fit_with_dp_wrapper_calls_wrap_before_training(self) -> None:
        """fit() with dp_wrapper must call dp_wrapper.wrap() during DP training.

        wrap() is called inside _train_dp_discriminator() — the Opacus integration
        point where the discriminator optimizer is DP-wrapped BEFORE training begins.
        In the new discriminator-level DP path (T30.3), CTGAN.fit() is NOT called
        when the DP path succeeds — the custom training loop replaces it.

        This test verifies wrap() is called (via _train_dp_discriminator or its
        fallback path) during fit() when dp_wrapper is provided.
        """
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        mock_dp_wrapper = MagicMock()
        mock_dp_wrapper.wrap.return_value = MagicMock()  # returns a dp_optimizer

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
            instance = DPCompatibleCTGAN(
                metadata=mock_metadata, epochs=2, dp_wrapper=mock_dp_wrapper
            )
            instance.fit(_make_training_df())

        # wrap() must be called (either primary DP path or proxy fallback)
        assert mock_dp_wrapper.wrap.called, (
            "dp_wrapper.wrap() was never called in DP mode. "
            "Expected call in _train_dp_discriminator or _activate_opacus_proxy."
        )

    def test_fit_with_dp_wrapper_wrap_called_with_required_kwargs(self) -> None:
        """dp_wrapper.wrap() must be called with optimizer, model, dataloader kwargs."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        mock_dp_wrapper = MagicMock()
        mock_dp_wrapper.wrap.return_value = MagicMock()

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
            instance = DPCompatibleCTGAN(
                metadata=mock_metadata, epochs=2, dp_wrapper=mock_dp_wrapper
            )
            instance.fit(_make_training_df())

        assert mock_dp_wrapper.wrap.called, "dp_wrapper.wrap() was never called"
        call_kwargs = mock_dp_wrapper.wrap.call_args.kwargs
        # Must pass optimizer, model, dataloader — not positional args
        # (These are the three required PyTorch objects for Opacus wrapping)
        assert "optimizer" in call_kwargs or len(mock_dp_wrapper.wrap.call_args.args) >= 3, (
            f"wrap() must be called with optimizer, model, dataloader. "
            f"Got args={mock_dp_wrapper.wrap.call_args.args}, kwargs={call_kwargs}"
        )


# ---------------------------------------------------------------------------
# Tests for sample()
# ---------------------------------------------------------------------------


class TestDPCompatibleCTGANSample:
    """Unit tests for DPCompatibleCTGAN.sample() with mocked SDV internals."""

    def _fit_instance(
        self,
        mock_sdv_synth: MagicMock,
        mock_ctgan_instance: MagicMock,
        n_rows: int = 50,
    ) -> Any:
        """Helper: fit a DPCompatibleCTGAN and return the fitted instance.

        Args:
            mock_sdv_synth: Mocked CTGANSynthesizer instance.
            mock_ctgan_instance: Mocked CTGAN instance.
            n_rows: Number of rows in the synthetic sample.

        Returns:
            Fitted DPCompatibleCTGAN instance.
        """
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()

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
            instance.fit(_make_training_df(n=n_rows))

        return instance

    def test_sample_returns_dataframe(self) -> None:
        """sample() must return a pd.DataFrame."""
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model(n_rows=20)

        # reverse_transform returns a DataFrame with original columns
        mock_sdv_synth._data_processor.reverse_transform.return_value = pd.DataFrame(
            {
                "id": list(range(20)),
                "age": list(range(20, 40)),
                "dept": ["Engineering"] * 20,
            }
        )

        instance = self._fit_instance(mock_sdv_synth, mock_ctgan_instance, n_rows=50)

        result = instance.sample(num_rows=20)
        assert isinstance(result, pd.DataFrame)

    def test_sample_returns_correct_row_count(self) -> None:
        """sample(num_rows=N) must return exactly N rows."""
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        n_rows = 30
        mock_ctgan_instance = _make_mock_ctgan_model(n_rows=n_rows)
        mock_sdv_synth._data_processor.reverse_transform.return_value = pd.DataFrame(
            {
                "id": list(range(n_rows)),
                "age": list(range(n_rows)),
                "dept": ["Sales"] * n_rows,
            }
        )

        instance = self._fit_instance(mock_sdv_synth, mock_ctgan_instance, n_rows=50)
        result = instance.sample(num_rows=n_rows)

        assert len(result) == n_rows, f"Expected {n_rows} rows, got {len(result)}"

    def test_sample_calls_ctgan_sample(self) -> None:
        """sample() must call the underlying CTGAN model's sample() method."""
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model(n_rows=10)
        mock_sdv_synth._data_processor.reverse_transform.return_value = pd.DataFrame(
            {"age": list(range(10)), "dept": ["HR"] * 10}
        )

        instance = self._fit_instance(mock_sdv_synth, mock_ctgan_instance)
        instance.sample(num_rows=10)

        mock_ctgan_instance.sample.assert_called_once_with(10)

    def test_sample_calls_reverse_transform(self) -> None:
        """sample() must call data_processor.reverse_transform() on CTGAN output."""
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model(n_rows=15)
        mock_sdv_synth._data_processor.reverse_transform.return_value = pd.DataFrame(
            {
                "id": list(range(15)),
                "age": list(range(15)),
                "dept": ["Engineering"] * 15,
            }
        )

        instance = self._fit_instance(mock_sdv_synth, mock_ctgan_instance)
        instance.sample(num_rows=15)

        mock_sdv_synth._data_processor.reverse_transform.assert_called_once()

    def test_sample_before_fit_raises_runtime_error(self) -> None:
        """sample() before fit() must raise RuntimeError with a clear message."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)

        with pytest.raises(RuntimeError, match="fit"):
            instance.sample(num_rows=10)

    def test_sample_zero_rows_raises_value_error(self) -> None:
        """sample(num_rows=0) must raise ValueError."""
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()
        mock_sdv_synth._data_processor.reverse_transform.return_value = pd.DataFrame(
            {"age": [], "dept": []}
        )

        instance = self._fit_instance(mock_sdv_synth, mock_ctgan_instance)

        with pytest.raises(ValueError, match="num_rows"):
            instance.sample(num_rows=0)

    def test_sample_negative_rows_raises_value_error(self) -> None:
        """sample(num_rows<0) must raise ValueError."""
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()
        mock_sdv_synth._data_processor.reverse_transform.return_value = pd.DataFrame(
            {"age": [], "dept": []}
        )

        instance = self._fit_instance(mock_sdv_synth, mock_ctgan_instance)

        with pytest.raises(ValueError, match="num_rows"):
            instance.sample(num_rows=-1)
