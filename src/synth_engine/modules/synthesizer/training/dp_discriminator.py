"""Opacus-compatible CTGAN Discriminator wrapper.

Provides :class:`OpacusCompatibleDiscriminator` — a stand-alone reimplementation
of CTGAN's internal ``Discriminator`` class that:

1. Mirrors the exact CTGAN Discriminator architecture (confirmed by source
   inspection of ``ctgan.synthesizers.ctgan.Discriminator``): a ``Sequential``
   stack of ``Linear → LeakyReLU(0.2) → Dropout(0.5)`` blocks repeated for each
   layer in ``discriminator_dim``, followed by a final ``Linear(..., 1)``
   output layer.

2. Passes ``opacus.validators.ModuleValidator.validate()`` with **zero errors**.
   This was validated against the actual CTGAN Discriminator source: the class
   uses only ``nn.Linear``, ``nn.LeakyReLU``, and ``nn.Dropout`` — no
   ``BatchNorm1d``. The ``BatchNorm1d → GroupNorm`` substitution anticipated in
   ADR-0036's initial draft was *not* required.

3. Decouples the synthesizer module from CTGAN's internal (non-public) API. The
   ``ctgan.synthesizers.ctgan.Discriminator`` class is not part of CTGAN's stable
   public surface. This wrapper gives the T30.3 training loop a stable, tested,
   owned entrypoint — if CTGAN's internals change, only this file needs updating.

4. Exposes ``calc_gradient_penalty()`` with the same signature as CTGAN's
   original method, required by the T30.3 custom training loop (WGAN-GP loss).

Architecture note — PacGAN packing:
    CTGAN uses PacGAN (Lin et al., 2018) to stabilize discriminator training.
    ``pac`` real samples are concatenated before being fed to the discriminator,
    so the effective input dimension is ``input_dim * pac``. The ``forward()``
    method reshapes the batch accordingly and asserts divisibility. The
    ``pacdim`` attribute records ``input_dim * pac`` for use by calling code.

Import boundary (ADR-0001 / ADR-0036):
    This module MUST NOT import from ``modules/privacy/``. Opacus is a direct
    dependency of the synthesizer module group and may be imported here. The
    ``ModuleValidator`` import is deferred to avoid hard-failing at module load
    time in environments where Opacus is absent (the import is only invoked
    during validation, not during normal forward/backward passes).

Task: P30-T30.2 — Opacus-Compatible Discriminator Wrapper
ADR: ADR-0036 (Discriminator-Level DP-SGD Architecture)
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Optional PyTorch imports — resolved centrally in _optional_deps.py (T43.2).
# Bound at module scope for unit-test patching.
# ---------------------------------------------------------------------------
from synth_engine.modules.synthesizer.training._optional_deps import nn, torch


class OpacusCompatibleDiscriminator(nn.Module):
    """Opacus-validated CTGAN Discriminator.

    A stand-alone reimplementation of ``ctgan.synthesizers.ctgan.Discriminator``
    that mirrors the exact architecture and passes
    ``opacus.validators.ModuleValidator.validate()`` with zero errors.

    Architecture (per CTGAN source inspection, no BatchNorm1d present):

    .. code-block:: text

        For each hidden_dim in discriminator_dim:
            Linear(prev_dim → hidden_dim)
            LeakyReLU(negative_slope=0.2)
            Dropout(p=0.5)
        Linear(last_hidden_dim → 1)

    Where ``prev_dim`` starts at ``input_dim * pac`` (PacGAN packed dimension).

    PacGAN packing:
        The discriminator receives ``pac`` real samples concatenated into a single
        input vector of size ``input_dim * pac``. The ``forward()`` method reshapes
        the input batch to pack ``pac`` adjacent rows before passing through
        ``self.seq``.

    Args:
        input_dim: Raw feature dimension of a single training sample.
        discriminator_dim: Tuple of hidden layer widths, e.g. ``(256, 256)``.
        pac: PacGAN packing factor. Must divide the batch size evenly during
            training. Defaults to 10 (matching CTGAN's default).

    Example::

        disc = OpacusCompatibleDiscriminator(
            input_dim=100, discriminator_dim=(256, 256), pac=10
        )
        # Validate with Opacus before wrapping
        from opacus.validators import ModuleValidator
        assert ModuleValidator.validate(disc) == []
    """

    def __init__(
        self,
        input_dim: int,
        discriminator_dim: tuple[int, ...],
        pac: int = 10,
    ) -> None:
        super().__init__()

        self.pac: int = pac
        self.pacdim: int = input_dim * pac

        dim = self.pacdim
        layers: list[nn.Module] = []
        for hidden_dim in discriminator_dim:
            layers += [
                nn.Linear(dim, hidden_dim),
                nn.LeakyReLU(0.2),
                nn.Dropout(0.5),
            ]
            dim = hidden_dim
        layers += [nn.Linear(dim, 1)]

        self.seq: nn.Sequential = nn.Sequential(*layers)

    def forward(self, input_: Any) -> Any:
        """Apply the discriminator to a packed batch.

        Reshapes the input by grouping ``pac`` adjacent rows into a single packed
        vector of size ``pacdim``, then passes through ``self.seq``.

        Args:
            input_: Tensor of shape ``(batch_size, input_dim)`` where
                ``batch_size`` must be divisible by ``self.pac``.

        Returns:
            Tensor of shape ``(batch_size // pac, 1)`` — the discriminator score
            for each packed group of samples.

        Raises:
            RuntimeError: If ``batch_size`` is not divisible by ``self.pac``
                (T57.2: replaces bare assert that is stripped by ``python -O``).

        """
        # T57.2: RuntimeError replaces assert (asserts stripped by python -O)
        batch_size = input_.size()[0]
        if batch_size % self.pac != 0:
            raise RuntimeError(
                f"Input batch size ({batch_size}) must be divisible by pac ({self.pac})"
            )
        return self.seq(input_.view(-1, self.pacdim))

    def calc_gradient_penalty(
        self,
        real_data: Any,
        fake_data: Any,
        device: str = "cpu",
        pac: int = 10,
        lambda_: float = 10,
    ) -> Any:
        """Compute the WGAN-GP gradient penalty.

        Implements the gradient penalty from Gulrajani et al. (2017), adapted for
        PacGAN packing. Interpolates between real and fake data, computes
        discriminator scores on interpolated samples, and penalises the norm of
        the gradients with respect to the interpolated inputs.

        Required by the T30.3 custom WGAN-GP training loop. Signature matches
        ``ctgan.synthesizers.ctgan.Discriminator.calc_gradient_penalty()``.

        Args:
            real_data: Tensor of real training samples,
                shape ``(batch_size, input_dim)``.
            fake_data: Tensor of generated samples,
                shape ``(batch_size, input_dim)``.
            device: Device string (e.g. ``"cpu"`` or ``"cuda"``).
            pac: PacGAN packing factor. Must match ``self.pac`` during training.
            lambda_: Gradient penalty coefficient. Defaults to 10.

        Returns:
            Scalar tensor — the gradient penalty loss term, non-negative.
        """
        alpha = torch.rand(real_data.size(0) // pac, 1, 1, device=device)
        alpha = alpha.repeat(1, pac, real_data.size(1))
        alpha = alpha.view(-1, real_data.size(1))

        interpolates = alpha * real_data + ((1 - alpha) * fake_data)

        disc_interpolates = self(interpolates)

        gradients = torch.autograd.grad(
            outputs=disc_interpolates,
            inputs=interpolates,
            grad_outputs=torch.ones(disc_interpolates.size(), device=device),
            create_graph=True,
            retain_graph=True,
            only_inputs=True,
        )[0]

        gradients_view = gradients.view(-1, pac * real_data.size(1)).norm(2, dim=1) - 1
        gradient_penalty = ((gradients_view) ** 2).mean() * lambda_

        return gradient_penalty
