"""Strategy classes for CTGAN training (VanillaCtganStrategy, DpCtganStrategy).

Extracted from ``dp_training.py`` (T35.2 decomposition) to separate the
training loop orchestration from the GAN coordinator.

The frozen dataclasses (``GanHyperparams``, ``TrainingConfig``, ``Optimizers``)
and the ``build_proxy_dataloader`` builder have been moved into
``ctgan_types.py`` (P35 ARCH-F1 finding) to give this module a single
responsibility: the two training-path strategy classes.

All four symbols are re-exported here so existing imports from
``training_strategies`` continue to work without modification.

Import boundary (ADR-0025 / ADR-0001):
  This module stays within ``modules/synthesizer/``.  No imports from
  ``modules/privacy/``.  The ``dp_wrapper`` field on :class:`DpCtganStrategy`
  is typed as ``Any`` for the same reason as in ``dp_training.py``.

Task: T35.2 — Split dp_training.py Into Strategy Classes
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import pandas as pd

from synth_engine.modules.synthesizer.training.ctgan_types import (
    GanHyperparams as GanHyperparams,
)
from synth_engine.modules.synthesizer.training.ctgan_types import (
    Optimizers as Optimizers,
)
from synth_engine.modules.synthesizer.training.ctgan_types import (
    TrainingConfig as TrainingConfig,
)
from synth_engine.modules.synthesizer.training.ctgan_types import (
    build_proxy_dataloader as build_proxy_dataloader,
)

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Warning pattern constants (mirror of dp_training.py — both modules need them)
# ---------------------------------------------------------------------------
_OPACUS_SECURE_RNG_PATTERN = ".*Secure RNG turned off.*"
_OPACUS_BATCH_PATTERN = ".*Expected.*batch.*"


# ---------------------------------------------------------------------------
# Strategy classes — encapsulate the two training paths
# ---------------------------------------------------------------------------


class VanillaCtganStrategy:
    """Training strategy for vanilla (non-DP) CTGAN.

    Constructs a ``CTGAN`` model from the SDV synthesizer's kwargs and
    calls ``CTGAN.fit()`` with the preprocessed DataFrame.

    This strategy is called by :class:`~dp_training.DPCompatibleCTGAN`
    when ``dp_wrapper`` is ``None``.
    """

    def run(
        self,
        sdv_synth: Any,
        processed_df: pd.DataFrame,
        discrete_columns: list[str],
        *,
        ctgan_cls: Any,
        epochs: int,
    ) -> Any:
        """Execute vanilla CTGAN training.

        Args:
            sdv_synth: ``CTGANSynthesizer`` (after ``preprocess()``).
            processed_df: Preprocessed DataFrame from SDV's DataProcessor.
            discrete_columns: List of discrete column names.
            ctgan_cls: The ``CTGAN`` class (injected for testability).
            epochs: Number of training epochs.

        Returns:
            The fitted ``CTGAN`` model instance.
        """
        model_kwargs: dict[str, Any] = dict(sdv_synth._model_kwargs)
        model_kwargs["epochs"] = epochs
        ctgan_model = ctgan_cls(**model_kwargs)

        _logger.info("VanillaCtganStrategy: starting CTGAN.fit().")
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning)
            warnings.filterwarnings("ignore", category=UserWarning)
            ctgan_model.fit(processed_df, discrete_columns=discrete_columns)

        return ctgan_model


class DpCtganStrategy:
    """Training strategy for discriminator-level DP-SGD CTGAN.

    Constructs an ``OpacusCompatibleDiscriminator`` and CTGAN ``Generator``,
    wraps the Discriminator optimizer via ``dp_wrapper.wrap()``, and runs
    the custom WGAN training loop.  Delegates per-epoch iteration to
    :class:`~dp_training.DPCompatibleCTGAN._run_gan_epoch` (which still
    lives on the coordinator class because existing tests call it directly
    on the instance).

    This strategy object is instantiated and called by
    :class:`~dp_training.DPCompatibleCTGAN` when ``dp_wrapper`` is not None.

    Args:
        dp_wrapper: Object implementing the dp_wrapper duck-type contract
            (see :class:`~dp_training.DPCompatibleCTGAN` docstring).
    """

    def __init__(self, dp_wrapper: Any) -> None:
        self._dp_wrapper = dp_wrapper

    def run(
        self,
        coordinator: Any,
        processed_df: pd.DataFrame,
        model_kwargs: dict[str, Any],
    ) -> None:
        """Execute the discriminator-level DP-SGD training loop.

        Delegates all orchestration to ``coordinator._train_dp_discriminator()``
        so that the existing test suite — which patches and calls that method
        directly on the coordinator instance — continues to work unchanged.

        Args:
            coordinator: The :class:`~dp_training.DPCompatibleCTGAN` instance
                that owns the training state.
            processed_df: Preprocessed DataFrame from SDV's DataProcessor.
            model_kwargs: CTGAN hyperparameters from ``_get_model_kwargs()``.
        """
        coordinator._train_dp_discriminator(processed_df, model_kwargs)
