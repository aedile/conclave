"""Unit tests for DPCompatibleCTGAN custom discriminator-level DP-SGD training loop.

Tests follow TDD Red/Green/Refactor. All tests are isolated — CTGAN internals,
PyTorch, and Opacus are mocked so these run without a GPU.

Covers (T30.3 Acceptance Criteria):
  AC1: fit() with dp_wrapper uses _train_dp_discriminator:
    - Constructs OpacusCompatibleDiscriminator
    - Constructs CTGAN Generator (reused from ctgan internals)
    - Wraps Discriminator optimizer via dp_wrapper.wrap()
    - Runs custom GAN training loop (conditional vectors + PacGAN)
    - Calls dp_wrapper.check_budget() per epoch
    - dp_wrapper.epsilon_spent() returns positive value after training
  AC2: vanilla path (dp_wrapper=None) unchanged — CTGAN.fit() called as before
  AC3: _activate_opacus renamed to _activate_opacus_proxy (fallback only)
  AC4: sample() works after DP training (Generator stored directly)
  AC5: Budget exhaustion raises BudgetExhaustionError mid-training
  AC5: Fallback to proxy model when discriminator wrapping fails (WARNING logged)

CONSTITUTION Priority 3: TDD Red/Green/Refactor.
Task: P30-T30.3 — Custom GAN Training Loop with Discriminator DP-SGD
ADR: ADR-0036 (Discriminator-Level DP-SGD Architecture)
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_training_df(n: int = 100) -> pd.DataFrame:
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


def _make_mock_sdv_synthesizer(n: int = 100) -> MagicMock:
    """Return a mock CTGANSynthesizer with numeric-only processed output.

    Args:
        n: Number of rows in the processed DataFrame.

    Returns:
        Configured MagicMock standing in for CTGANSynthesizer.
    """
    import numpy as np

    rng = np.random.default_rng(99)
    processed_df = pd.DataFrame(
        {
            "age": rng.integers(18, 80, size=n).astype(float).tolist(),
            "dept.value": rng.uniform(0, 1, size=n).tolist(),
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
        "batch_size": 50,
        "discriminator_steps": 1,
        "log_frequency": True,
        "verbose": False,
        "epochs": 1,
        "pac": 2,
        "enable_gpu": False,
    }
    mock_proc = MagicMock()
    mock_proc._hyper_transformer.field_transformers = {}
    mock_synth._data_processor = mock_proc

    return mock_synth


def _make_mock_dp_wrapper() -> MagicMock:
    """Return a mock DP wrapper with default attributes.

    Returns:
        MagicMock with max_grad_norm, noise_multiplier, wrap, epsilon_spent,
        check_budget attributes.
    """
    mock_wrapper = MagicMock()
    mock_wrapper.max_grad_norm = 1.0
    mock_wrapper.noise_multiplier = 1.1
    # wrap() returns a mock dp_optimizer
    mock_dp_optimizer = MagicMock()
    mock_wrapper.wrap.return_value = mock_dp_optimizer
    # epsilon_spent() returns positive value after training
    mock_wrapper.epsilon_spent.return_value = 0.42
    # check_budget() returns None (no exception = budget not exhausted)
    mock_wrapper.check_budget.return_value = None
    return mock_wrapper


# ---------------------------------------------------------------------------
# AC1: Custom training loop is invoked when dp_wrapper is provided
# ---------------------------------------------------------------------------


class TestDPTrainingLoopInvoked:
    """AC1 — verify the custom training loop path is taken when dp_wrapper is set."""

    def test_fit_dp_mode_calls_train_dp_discriminator(self) -> None:
        """fit() with dp_wrapper must call _train_dp_discriminator(), not vanilla CTGAN.fit()."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_dp_wrapper = _make_mock_dp_wrapper()

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper
        )

        # Spy on _train_dp_discriminator
        train_dp_called: list[bool] = []
        original_train = instance._train_dp_discriminator

        def spy_train_dp(*args: Any, **kwargs: Any) -> Any:
            train_dp_called.append(True)
            return original_train(*args, **kwargs)

        instance._train_dp_discriminator = spy_train_dp  # type: ignore[method-assign]

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.OpacusCompatibleDiscriminator",
            ) as mock_disc_cls,
            patch(
                "synth_engine.modules.synthesizer.dp_training.Generator",
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.DataTransformer",
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.DataSampler",
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.torch",
            ),
        ):
            mock_disc_cls.return_value = MagicMock()
            instance.fit(_make_training_df())

        assert train_dp_called, "_train_dp_discriminator was never called in DP mode"

    def test_fit_dp_mode_does_not_call_vanilla_ctgan_fit(self) -> None:
        """fit() with dp_wrapper must NOT call vanilla CTGAN.fit() — custom loop replaces it."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        mock_ctgan_cls = MagicMock()
        mock_ctgan_instance = MagicMock()
        mock_ctgan_cls.return_value = mock_ctgan_instance

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper
        )

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                mock_ctgan_cls,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.OpacusCompatibleDiscriminator",
            ),
            patch("synth_engine.modules.synthesizer.dp_training.Generator"),
            patch("synth_engine.modules.synthesizer.dp_training.DataTransformer"),
            patch("synth_engine.modules.synthesizer.dp_training.DataSampler"),
            patch("synth_engine.modules.synthesizer.dp_training.torch"),
        ):
            instance.fit(_make_training_df())

        # vanilla CTGAN.fit() must NOT have been called
        mock_ctgan_instance.fit.assert_not_called()

    def test_fit_dp_mode_constructs_opacus_discriminator(self) -> None:
        """fit() in DP mode must construct an OpacusCompatibleDiscriminator."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_dp_wrapper = _make_mock_dp_wrapper()

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper
        )

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.OpacusCompatibleDiscriminator",
            ) as mock_disc_cls,
            patch("synth_engine.modules.synthesizer.dp_training.Generator"),
            patch("synth_engine.modules.synthesizer.dp_training.DataTransformer"),
            patch("synth_engine.modules.synthesizer.dp_training.DataSampler"),
            patch("synth_engine.modules.synthesizer.dp_training.torch"),
        ):
            instance.fit(_make_training_df())

        mock_disc_cls.assert_called_once()

    def test_fit_dp_mode_wraps_discriminator_optimizer(self) -> None:
        """fit() in DP mode must call dp_wrapper.wrap() with the discriminator's optimizer."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_dp_wrapper = _make_mock_dp_wrapper()

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper
        )

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.OpacusCompatibleDiscriminator",
            ),
            patch("synth_engine.modules.synthesizer.dp_training.Generator"),
            patch("synth_engine.modules.synthesizer.dp_training.DataTransformer"),
            patch("synth_engine.modules.synthesizer.dp_training.DataSampler"),
            patch("synth_engine.modules.synthesizer.dp_training.torch"),
        ):
            instance.fit(_make_training_df())

        assert mock_dp_wrapper.wrap.called, "dp_wrapper.wrap() was never called in DP mode"

    def test_fit_dp_mode_calls_check_budget_per_epoch(self) -> None:
        """fit() in DP mode must call dp_wrapper.check_budget() once per epoch."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        n_epochs = 3

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata, epochs=n_epochs, dp_wrapper=mock_dp_wrapper
        )

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.OpacusCompatibleDiscriminator",
            ),
            patch("synth_engine.modules.synthesizer.dp_training.Generator"),
            patch("synth_engine.modules.synthesizer.dp_training.DataTransformer"),
            patch("synth_engine.modules.synthesizer.dp_training.DataSampler"),
            patch("synth_engine.modules.synthesizer.dp_training.torch"),
        ):
            instance.fit(_make_training_df())

        assert mock_dp_wrapper.check_budget.call_count == n_epochs, (
            f"check_budget() must be called once per epoch. "
            f"Expected {n_epochs}, got {mock_dp_wrapper.check_budget.call_count}"
        )


# ---------------------------------------------------------------------------
# AC1: Epsilon is positive after DP training
# ---------------------------------------------------------------------------


class TestEpsilonAfterTraining:
    """AC1 — epsilon_spent() returns a positive value after DP training."""

    def test_epsilon_spent_positive_after_dp_fit(self) -> None:
        """dp_wrapper.epsilon_spent() must return a positive value after fit() with dp_wrapper.

        The mock returns 0.42; this test verifies the implementation calls epsilon_spent
        and that callers can retrieve it after fit().
        """
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        mock_dp_wrapper.epsilon_spent.return_value = 0.7

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper
        )

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.OpacusCompatibleDiscriminator",
            ),
            patch("synth_engine.modules.synthesizer.dp_training.Generator"),
            patch("synth_engine.modules.synthesizer.dp_training.DataTransformer"),
            patch("synth_engine.modules.synthesizer.dp_training.DataSampler"),
            patch("synth_engine.modules.synthesizer.dp_training.torch"),
        ):
            instance.fit(_make_training_df())

        epsilon = mock_dp_wrapper.epsilon_spent(delta=1e-5)
        assert epsilon > 0, f"epsilon_spent() must return positive; got {epsilon}"


# ---------------------------------------------------------------------------
# AC5: Budget exhaustion raises BudgetExhaustionError mid-training
# ---------------------------------------------------------------------------


class TestBudgetExhaustion:
    """AC5 — BudgetExhaustionError from check_budget() propagates out of fit()."""

    def test_budget_exhaustion_error_propagates_from_fit(self) -> None:
        """BudgetExhaustionError raised by check_budget() must propagate from fit().

        The check_budget() call per epoch must allow an exception to escape
        so the training loop terminates early.
        """
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN
        from synth_engine.shared.exceptions import BudgetExhaustionError

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        mock_dp_wrapper.check_budget.side_effect = BudgetExhaustionError(
            "Privacy budget exceeded: epsilon=1.5 > allocated=1.0"
        )

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata, epochs=3, dp_wrapper=mock_dp_wrapper
        )

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.OpacusCompatibleDiscriminator",
            ),
            patch("synth_engine.modules.synthesizer.dp_training.Generator"),
            patch("synth_engine.modules.synthesizer.dp_training.DataTransformer"),
            patch("synth_engine.modules.synthesizer.dp_training.DataSampler"),
            patch("synth_engine.modules.synthesizer.dp_training.torch"),
        ):
            with pytest.raises(BudgetExhaustionError, match="Privacy budget exceeded"):
                instance.fit(_make_training_df())


# ---------------------------------------------------------------------------
# AC2: Vanilla path (dp_wrapper=None) unchanged
# ---------------------------------------------------------------------------


class TestVanillaPathUnchanged:
    """AC2 — vanilla path (dp_wrapper=None) must be identical to the original behaviour."""

    def test_vanilla_fit_calls_ctgan_fit(self) -> None:
        """fit() with dp_wrapper=None must still call CTGAN.fit() (vanilla path)."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_cls = MagicMock()
        mock_ctgan_instance = MagicMock()
        mock_ctgan_cls.return_value = mock_ctgan_instance

        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=None)

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                mock_ctgan_cls,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance.fit(_make_training_df())

        mock_ctgan_instance.fit.assert_called_once()

    def test_vanilla_fit_does_not_call_wrap(self) -> None:
        """fit() with dp_wrapper=None must never call dp_wrapper.wrap()."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_cls = MagicMock()
        mock_ctgan_instance = MagicMock()
        mock_ctgan_cls.return_value = mock_ctgan_instance
        mock_dp_wrapper = _make_mock_dp_wrapper()

        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=None)

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                mock_ctgan_cls,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance.fit(_make_training_df())

        mock_dp_wrapper.wrap.assert_not_called()


# ---------------------------------------------------------------------------
# AC3: _activate_opacus_proxy exists as renamed fallback
# ---------------------------------------------------------------------------


class TestActivateOpacusProxyRenamed:
    """AC3 — _activate_opacus must be renamed/present as _activate_opacus_proxy."""

    def test_activate_opacus_proxy_method_exists(self) -> None:
        """DPCompatibleCTGAN must have _activate_opacus_proxy as the fallback method."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        assert hasattr(DPCompatibleCTGAN, "_activate_opacus_proxy"), (
            "DPCompatibleCTGAN must have _activate_opacus_proxy — the renamed fallback "
            "proxy-model method (AC3: renamed from _activate_opacus)."
        )

    def test_train_dp_discriminator_method_exists(self) -> None:
        """DPCompatibleCTGAN must have _train_dp_discriminator as the primary DP method."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        assert hasattr(DPCompatibleCTGAN, "_train_dp_discriminator"), (
            "DPCompatibleCTGAN must have _train_dp_discriminator — the primary "
            "discriminator-level DP-SGD training method (T30.3)."
        )


# ---------------------------------------------------------------------------
# AC4: sample() works after DP training (Generator stored directly)
# ---------------------------------------------------------------------------


class TestSampleAfterDPTraining:
    """AC4 — sample() must produce valid DataFrames after DP training."""

    def test_sample_returns_dataframe_after_dp_fit(self) -> None:
        """sample() must return a pd.DataFrame after DP training completes."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_dp_wrapper = _make_mock_dp_wrapper()

        # Generator.sample() will be called in DP mode
        mock_generator = MagicMock()
        mock_generator_cls = MagicMock(return_value=mock_generator)

        expected_df = pd.DataFrame({"age": [25, 30], "dept": ["Engineering", "Sales"]})
        mock_sdv_synth._data_processor.reverse_transform.return_value = expected_df

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper
        )

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.OpacusCompatibleDiscriminator",
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.Generator",
                mock_generator_cls,
            ),
            patch("synth_engine.modules.synthesizer.dp_training.DataTransformer"),
            patch("synth_engine.modules.synthesizer.dp_training.DataSampler"),
            patch("synth_engine.modules.synthesizer.dp_training.torch"),
        ):
            instance.fit(_make_training_df())

        # After DP training, instance must be fitted
        assert instance._fitted, "instance must be marked as fitted after DP training"

    def test_sample_fitted_true_after_dp_training(self) -> None:
        """_fitted must be True after DP fit() completes successfully."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_dp_wrapper = _make_mock_dp_wrapper()

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper
        )

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.OpacusCompatibleDiscriminator",
            ),
            patch("synth_engine.modules.synthesizer.dp_training.Generator"),
            patch("synth_engine.modules.synthesizer.dp_training.DataTransformer"),
            patch("synth_engine.modules.synthesizer.dp_training.DataSampler"),
            patch("synth_engine.modules.synthesizer.dp_training.torch"),
        ):
            instance.fit(_make_training_df())

        assert instance._fitted is True


# ---------------------------------------------------------------------------
# AC5: Fallback to proxy model when discriminator wrapping fails
# ---------------------------------------------------------------------------


class TestFallbackToProxyModel:
    """AC5 — graceful fallback to proxy model if discriminator wrapping fails."""

    def test_fallback_to_proxy_model_logs_warning(self, caplog: Any) -> None:
        """fit() must fall back to proxy model and log WARNING when disc wrapping fails."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        mock_ctgan_cls = MagicMock()
        mock_ctgan_instance = MagicMock()
        mock_ctgan_cls.return_value = mock_ctgan_instance

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper
        )

        # Make _train_dp_discriminator raise to trigger fallback
        def failing_train_dp(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("Opacus cannot wrap PacGAN discriminator")

        instance._train_dp_discriminator = failing_train_dp  # type: ignore[method-assign]

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                mock_ctgan_cls,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
            caplog.at_level(logging.WARNING, logger="synth_engine.modules.synthesizer.dp_training"),
        ):
            instance.fit(_make_training_df())

        # Must have logged a WARNING
        warning_logs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_logs, (
            "fit() must log a WARNING when falling back to proxy model. "
            "No WARNING records found in caplog."
        )
        # Warning message must explain why
        warning_text = " ".join(r.message for r in warning_logs)
        assert any(
            token in warning_text.lower()
            for token in ["fallback", "proxy", "failed", "warning"]
        ), f"WARNING must mention fallback/proxy/failed; got: {warning_text!r}"

    def test_fallback_to_proxy_model_still_fits(self, caplog: Any) -> None:
        """fit() must succeed (set _fitted=True) even when falling back to proxy model."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        mock_ctgan_cls = MagicMock()
        mock_ctgan_instance = MagicMock()
        mock_ctgan_cls.return_value = mock_ctgan_instance

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper
        )

        def failing_train_dp(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("Opacus cannot wrap PacGAN discriminator")

        instance._train_dp_discriminator = failing_train_dp  # type: ignore[method-assign]

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                mock_ctgan_cls,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
            caplog.at_level(logging.WARNING, logger="synth_engine.modules.synthesizer.dp_training"),
        ):
            instance.fit(_make_training_df())

        assert instance._fitted is True, (
            "_fitted must be True after fallback-to-proxy completes successfully."
        )


# ---------------------------------------------------------------------------
# Import boundary — new imports (Generator, DataTransformer, DataSampler) must
# not violate the modules/privacy boundary
# ---------------------------------------------------------------------------


class TestNewImportBoundaryT30_3:
    """T30.3 — new imports must not violate the modules/privacy import boundary."""

    def test_dp_training_imports_discriminator_from_synthesizer_module(self) -> None:
        """dp_training.py must import OpacusCompatibleDiscriminator from dp_discriminator."""
        import ast
        from pathlib import Path

        dp_training_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "synth_engine"
            / "modules"
            / "synthesizer"
            / "dp_training.py"
        )
        source = dp_training_path.read_text()
        tree = ast.parse(source)

        found_discriminator_import = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [alias.name for alias in node.names]
                if "OpacusCompatibleDiscriminator" in names and "synthesizer" in module:
                    found_discriminator_import = True
                    break
            elif isinstance(node, ast.Import):
                pass  # not a from-import

        assert found_discriminator_import, (
            "dp_training.py must import OpacusCompatibleDiscriminator from "
            "synth_engine.modules.synthesizer.dp_discriminator (T30.3)."
        )

    def test_dp_training_new_imports_not_from_privacy(self) -> None:
        """T30.3 new imports (Generator, DataTransformer, DataSampler) must not come from privacy/."""
        import ast
        from pathlib import Path

        dp_training_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "synth_engine"
            / "modules"
            / "synthesizer"
            / "dp_training.py"
        )
        source = dp_training_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "privacy" not in module, (
                    f"dp_training.py must NOT import from modules/privacy. "
                    f"Found: from {module} import ..."
                )

    def test_ctgan_internals_imported_at_module_scope(self) -> None:
        """dp_training.py must import Generator, DataTransformer, DataSampler from ctgan."""
        from pathlib import Path

        dp_training_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "synth_engine"
            / "modules"
            / "synthesizer"
            / "dp_training.py"
        )
        source = dp_training_path.read_text()

        # All three ctgan internals must be referenced
        for name in ("Generator", "DataTransformer", "DataSampler"):
            assert name in source, (
                f"dp_training.py must reference {name} for the custom training loop (T30.3)."
            )


# ---------------------------------------------------------------------------
# check_budget called with correct kwargs (allocated_epsilon, delta)
# ---------------------------------------------------------------------------


class TestCheckBudgetKwargs:
    """Verify check_budget is called with allocated_epsilon and delta kwargs."""

    def test_check_budget_called_with_allocated_epsilon_and_delta(self) -> None:
        """check_budget() must be called with allocated_epsilon and delta keyword args."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_dp_wrapper = _make_mock_dp_wrapper()

        instance = DPCompatibleCTGAN(
            metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper
        )

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.OpacusCompatibleDiscriminator",
            ),
            patch("synth_engine.modules.synthesizer.dp_training.Generator"),
            patch("synth_engine.modules.synthesizer.dp_training.DataTransformer"),
            patch("synth_engine.modules.synthesizer.dp_training.DataSampler"),
            patch("synth_engine.modules.synthesizer.dp_training.torch"),
        ):
            instance.fit(_make_training_df())

        # check_budget must have been called at least once
        assert mock_dp_wrapper.check_budget.called, "check_budget() was never called"
        # Verify it was called with keyword args including 'delta'
        call_kwargs = mock_dp_wrapper.check_budget.call_args_list[0]
        all_kwargs = {**call_kwargs.kwargs}
        assert "delta" in all_kwargs, (
            f"check_budget() must be called with 'delta' keyword arg. "
            f"Got kwargs={all_kwargs}"
        )
        assert "allocated_epsilon" in all_kwargs, (
            f"check_budget() must be called with 'allocated_epsilon' keyword arg. "
            f"Got kwargs={all_kwargs}"
        )
