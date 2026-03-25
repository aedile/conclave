"""Utility functions for CTGAN training loop configuration.

Pure, stateless helpers shared by both DP and vanilla CTGAN training paths.
Extracted from ``dp_training.py`` (T35.2 decomposition) to reduce cognitive
load and enable isolated unit testing.

Import boundary (ADR-0025 / ADR-0001):
  This module stays within ``modules/synthesizer/``.  No imports from
  ``modules/privacy/``.

Task: T35.2 — Split dp_training.py Into Strategy Classes
"""

from __future__ import annotations

from typing import Any

from synth_engine.modules.synthesizer.training.ctgan_types import GanHyperparams


def cap_batch_size(n_samples: int, requested_batch_size: int, pac: int) -> int:
    """Clamp batch size to satisfy Opacus sample-rate and pac-divisibility constraints.

    Opacus's ``DPDataLoader.from_data_loader`` sets
    ``sample_rate = 1 / len(data_loader)``.  When ``len(data_loader) == 1``
    (i.e. ``batch_size >= n_rows``), ``sample_rate == 1.0`` and Opacus's PRV
    accountant raises a ``RuntimeWarning`` on ``log(1 - q)`` for ``q = 1.0``
    (promoted to an error under ``-W error`` in tests).

    Fix: cap ``batch_size <= n_samples // 2`` so ``len(data_loader) >= 2``
    and ``q <= 0.5``.  Then enforce pac-divisibility (pac is the minimum
    grouping for one Discriminator pass).

    Args:
        n_samples: Number of rows in the training DataFrame.
        requested_batch_size: Raw batch size from CTGAN model kwargs.
        pac: PacGAN grouping factor from model kwargs.

    Returns:
        A valid batch size that is pac-divisible and satisfies the
        Opacus minimum-batch constraint.
    """
    batch_size = min(requested_batch_size, n_samples // 2)
    batch_size = max(pac, batch_size)
    batch_size = (batch_size // pac) * pac
    return batch_size


def parse_gan_hyperparams(model_kwargs: dict[str, Any]) -> GanHyperparams:
    """Extract GAN architecture hyperparameters from CTGAN model kwargs.

    Centralises ``model_kwargs`` extraction into a typed value object so
    ``_train_dp_discriminator`` can receive a single ``GanHyperparams``
    instead of six individual scalar arguments.

    Args:
        model_kwargs: CTGAN hyperparameter dict from ``_get_model_kwargs()``.

    Returns:
        A :class:`GanHyperparams` dataclass carrying embedding_dim,
        generator_dim, discriminator_dim, pac, discriminator_steps,
        and batch_size.
    """
    return GanHyperparams(
        embedding_dim=int(model_kwargs.get("embedding_dim", 128)),
        generator_dim=tuple(model_kwargs.get("generator_dim", (256, 256))),
        discriminator_dim=tuple(model_kwargs.get("discriminator_dim", (256, 256))),
        pac=int(model_kwargs.get("pac", 10)),
        discriminator_steps=int(model_kwargs.get("discriminator_steps", 1)),
        batch_size=int(model_kwargs.get("batch_size", 500)),
    )
