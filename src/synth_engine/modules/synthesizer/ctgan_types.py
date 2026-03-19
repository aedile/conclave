"""Frozen dataclasses and the proxy DataLoader builder for CTGAN training.

Extracted from ``training_strategies.py`` (P35 ARCH-F1 review finding) to
give each file a single responsibility:

  - :class:`GanHyperparams` — frozen carrier for GAN architecture params.
  - :class:`TrainingConfig` — frozen carrier replacing the old 12-parameter
    ``_run_gan_epoch()`` signature.
  - :class:`Optimizers` — frozen carrier bundling the two GAN optimizers.
  - :func:`build_proxy_dataloader` — standalone builder for the Opacus proxy
    DataLoader used by the fallback training path.

``training_strategies.py`` re-exports all four symbols so existing imports
from that module continue to work without modification.

Import boundary (ADR-0025 / ADR-0001):
  This module stays within ``modules/synthesizer/``.  No imports from
  ``modules/privacy/``.

Task: P35 ARCH-F1 — Extract frozen dataclasses and builder out of
      training_strategies.py to eliminate mixed responsibilities.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Frozen dataclasses — pure data carriers (Neutral value object rule)
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
# Proxy DataLoader builder
# ---------------------------------------------------------------------------


def build_proxy_dataloader(
    processed_df: pd.DataFrame,
    *,
    torch_module: Any,
    tensor_dataset_cls: Any,
    dataloader_cls: Any,
) -> tuple[Any, int]:
    """Build the DataLoader and batch size for proxy model training.

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
