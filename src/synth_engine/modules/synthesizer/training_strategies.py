"""Strategy classes and configuration dataclasses for CTGAN training.

Extracted from ``dp_training.py`` (T35.2 decomposition) to separate the
training loop orchestration from the GAN coordinator.

Contains:
  - :class:`GanHyperparams` — frozen dataclass for GAN architecture params.
  - :class:`TrainingConfig` — frozen dataclass replacing the 12-parameter
    ``_run_gan_epoch()`` signature (AC5 of T35.2).
  - :class:`VanillaCtganStrategy` — runs plain CTGAN.fit() (no DP).
  - :class:`DpCtganStrategy` — runs the custom discriminator-level DP-SGD loop.

Import boundary (ADR-0025 / ADR-0001):
  This module stays within ``modules/synthesizer/``.  No imports from
  ``modules/privacy/``.  The ``dp_wrapper`` field on :class:`DpCtganStrategy`
  is typed as ``Any`` for the same reason as in ``dp_training.py``.

Task: T35.2 — Split dp_training.py Into Strategy Classes
"""

from __future__ import annotations

import dataclasses
import logging
import warnings
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    pass

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Warning pattern constants (mirror of dp_training.py — both modules need them)
# ---------------------------------------------------------------------------
_OPACUS_SECURE_RNG_PATTERN = ".*Secure RNG turned off.*"
_OPACUS_BATCH_PATTERN = ".*Expected.*batch.*"


# ---------------------------------------------------------------------------
# Frozen dataclasses — pure data carriers (AC5 / Neutral value object rule)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class GanHyperparams:
    """Frozen carrier for GAN architecture hyperparameters.

    Replaces six individual scalar arguments extracted from ``model_kwargs``
    in ``_parse_gan_hyperparams()``.

    Attributes:
        embedding_dim: Noise embedding dimension for the Generator.
        generator_dim: Hidden layer sizes for the Generator.
        discriminator_dim: Hidden layer sizes for the Discriminator.
        pac: PacGAN grouping factor.
        discriminator_steps: Number of Discriminator updates per batch.
        batch_size: Requested batch size (before pac-alignment).
    """

    embedding_dim: int
    generator_dim: tuple[int, ...]
    discriminator_dim: tuple[int, ...]
    pac: int
    discriminator_steps: int
    batch_size: int


@dataclasses.dataclass(frozen=True)
class Optimizers:
    """Frozen carrier for the two GAN optimizer objects.

    Bundles Generator and Discriminator optimizers to keep _run_gan_epoch()
    under the 5-parameter limit (AC3 of T35.2).

    Attributes:
        optimizer_g: Generator's Adam optimizer (not DP-wrapped).
        dp_optimizer: Discriminator's Opacus-wrapped DP optimizer.
    """

    optimizer_g: Any
    dp_optimizer: Any


@dataclasses.dataclass(frozen=True)
class TrainingConfig:
    """Frozen configuration for a single GAN training epoch.

    Replaces the 10-parameter ``_run_gan_epoch()`` signature (AC5 of T35.2).
    Carries the scalar configuration values so that the method signature
    stays at <= 5 parameters (generator, discriminator, dataloader,
    optimizer_g, dp_optimizer all remain as positional args; the scalars
    are grouped here).

    Attributes:
        embedding_dim: Noise vector dimension for the Generator.
        data_dim: Number of processed feature columns.
        pac: PacGAN grouping factor (batch must be divisible by pac).
        batch_size: Effective batch size (for Generator noise shape).
        discriminator_steps: Number of Discriminator updates per batch.
    """

    embedding_dim: int
    data_dim: int
    pac: int
    batch_size: int
    discriminator_steps: int


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
    """

    def __init__(self, dp_wrapper: Any) -> None:
        """Initialise DpCtganStrategy with the DP wrapper.

        Args:
            dp_wrapper: Object implementing the dp_wrapper duck-type contract
                (see :class:`~dp_training.DPCompatibleCTGAN` docstring).
        """
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


# ---------------------------------------------------------------------------
# Proxy DataLoader builder — extracted from dp_training._build_proxy_dataloader
# ---------------------------------------------------------------------------


def build_proxy_dataloader(
    processed_df: pd.DataFrame,
    *,
    torch_module: Any,
    tensor_dataset_cls: Any,
    dataloader_cls: Any,
) -> tuple[Any, int]:
    """Build the DataLoader and batch size for proxy model training.

    Extracted from ``DPCompatibleCTGAN._build_proxy_dataloader`` (T35.2).
    Converts numeric columns to a float32 tensor, sanitises non-finite
    values, and constructs a DataLoader with a proxy-appropriate batch
    size (min(64, n_rows // 2), clamped to at least 2).

    Args:
        processed_df: Preprocessed (VGM-normalized) DataFrame.
        torch_module: The ``torch`` module (injected to allow test patching).
        tensor_dataset_cls: ``TensorDataset`` class (injected for testability).
        dataloader_cls: ``DataLoader`` class (injected for testability).

    Returns:
        A 2-tuple ``(dataloader, n_features)`` where ``dataloader`` is a
        DataLoader wrapping the processed data and ``n_features`` is the
        number of numeric feature columns (at least 1).

    Raises:
        RuntimeError: If the DataFrame is too small to form even one batch.
    """
    arr = processed_df.select_dtypes(include=[float, int]).values.astype("float32")
    if arr.shape[1] == 0:
        arr = np.zeros((len(processed_df), 1), dtype="float32")
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)

    batch_size = min(64, max(1, len(arr) // 2))
    batch_size = max(2, batch_size)

    tensor_data = torch_module.tensor(arr)
    dataset = tensor_dataset_cls(tensor_data)
    dataloader = dataloader_cls(
        dataset,
        batch_size=batch_size,
        shuffle=True,
        drop_last=True,
    )

    if len(dataloader) == 0:
        raise RuntimeError(
            "DPCompatibleCTGAN: processed_df has too few rows for Opacus "
            "DataLoader (need >= 2*batch_size rows for DP-SGD). "
            "A too-small dataset would produce a false DP guarantee "
            "(epsilon_spent() would return 0.0 with no actual accounting). "
            "Ensure the training DataFrame has at least 4 rows."
        )

    n_features: int = arr.shape[1]
    return dataloader, n_features
