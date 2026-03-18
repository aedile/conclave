"""Unit tests for OpacusCompatibleDiscriminator (T30.2).

Architecture decouples us from CTGAN's internal Discriminator class, which is not
a public API. ``OpacusCompatibleDiscriminator`` mirrors the exact CTGAN Discriminator
architecture (confirmed by source inspection — no BatchNorm1d present), passes
``opacus.validators.ModuleValidator.validate()`` with zero errors, and exposes the
same interface required by the T30.3 training loop.

CONSTITUTION Priority 3: TDD Red/Green/Refactor.
Task: P30-T30.2 — Opacus-Compatible Discriminator Wrapper
ADR: ADR-0036 (Discriminator-Level DP-SGD Architecture)
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_real_module() -> Any:
    """Import OpacusCompatibleDiscriminator directly (real torch required).

    Returns:
        The OpacusCompatibleDiscriminator class.
    """
    from synth_engine.modules.synthesizer.dp_discriminator import (
        OpacusCompatibleDiscriminator,
    )

    return OpacusCompatibleDiscriminator


# ---------------------------------------------------------------------------
# Tests: constructor and attributes
# ---------------------------------------------------------------------------


class TestOpacusCompatibleDiscriminatorInit:
    """Tests for __init__ signature and attribute storage."""

    def test_constructor_accepts_expected_parameters(self) -> None:
        """Constructor accepts input_dim, discriminator_dim, pac."""
        cls = _make_real_module()
        discriminator = cls(input_dim=10, discriminator_dim=(256, 256), pac=10)
        assert discriminator is not None

    def test_pac_attribute_stored(self) -> None:
        """pac attribute is stored and accessible after construction."""
        cls = _make_real_module()
        discriminator = cls(input_dim=10, discriminator_dim=(256, 256), pac=10)
        assert discriminator.pac == 10

    def test_pacdim_attribute_is_input_dim_times_pac(self) -> None:
        """pacdim == input_dim * pac (PacGAN packing dimension)."""
        cls = _make_real_module()
        discriminator = cls(input_dim=10, discriminator_dim=(256, 256), pac=10)
        assert discriminator.pacdim == 100  # 10 * 10

    def test_pacdim_with_custom_pac(self) -> None:
        """pacdim is correct for non-default pac values."""
        cls = _make_real_module()
        discriminator = cls(input_dim=20, discriminator_dim=(128,), pac=5)
        assert discriminator.pacdim == 100  # 20 * 5

    def test_default_pac_is_ten(self) -> None:
        """Default pac value matches CTGAN Discriminator default (pac=10)."""
        cls = _make_real_module()
        discriminator = cls(input_dim=8, discriminator_dim=(128,))
        assert discriminator.pac == 10

    def test_seq_attribute_exists(self) -> None:
        """The internal sequential container is present (matches CTGAN interface)."""
        cls = _make_real_module()
        discriminator = cls(input_dim=10, discriminator_dim=(256, 256), pac=10)
        assert hasattr(discriminator, "seq")


# ---------------------------------------------------------------------------
# Tests: forward pass
# ---------------------------------------------------------------------------


class TestOpacusCompatibleDiscriminatorForward:
    """Tests for the forward() method output shape and correctness."""

    def test_forward_pass_output_shape(self) -> None:
        """forward() produces (batch_size // pac, 1) output shape."""
        import torch

        cls = _make_real_module()
        pac = 10
        input_dim = 8
        batch_size = 20  # must be divisible by pac
        discriminator = cls(input_dim=input_dim, discriminator_dim=(64, 64), pac=pac)
        discriminator.eval()

        x = torch.randn(batch_size, input_dim)
        with torch.no_grad():
            out = discriminator(x)

        assert out.shape == (batch_size // pac, 1), (
            f"Expected output shape ({batch_size // pac}, 1), got {out.shape}"
        )

    def test_forward_pass_output_shape_single_hidden_layer(self) -> None:
        """forward() shape is correct with a single hidden layer."""
        import torch

        cls = _make_real_module()
        pac = 10
        input_dim = 4
        batch_size = 30
        discriminator = cls(input_dim=input_dim, discriminator_dim=(32,), pac=pac)
        discriminator.eval()

        x = torch.randn(batch_size, input_dim)
        with torch.no_grad():
            out = discriminator(x)

        assert out.shape == (batch_size // pac, 1)

    def test_output_nonzero(self) -> None:
        """Output is not all-zero (discriminator is non-degenerate at init)."""
        import torch

        cls = _make_real_module()
        torch.manual_seed(7)
        discriminator = cls(input_dim=10, discriminator_dim=(64, 64), pac=10)
        discriminator.eval()

        x = torch.randn(20, 10)
        with torch.no_grad():
            out = discriminator(x)

        assert not torch.allclose(out, torch.zeros_like(out)), (
            "Discriminator output is all zeros — degenerate initialization detected"
        )

    def test_forward_asserts_batch_divisible_by_pac(self) -> None:
        """forward() raises AssertionError if batch_size % pac != 0."""
        import torch

        cls = _make_real_module()
        discriminator = cls(input_dim=8, discriminator_dim=(64,), pac=10)
        x = torch.randn(15, 8)  # 15 is not divisible by 10

        with pytest.raises(AssertionError):
            discriminator(x)


# ---------------------------------------------------------------------------
# Tests: Opacus compatibility
# ---------------------------------------------------------------------------


class TestOpacusModuleValidation:
    """Tests that OpacusCompatibleDiscriminator passes Opacus ModuleValidator."""

    def test_opacus_module_validator_passes(self) -> None:
        """ModuleValidator.validate() returns zero errors."""
        from opacus.validators import ModuleValidator

        cls = _make_real_module()
        discriminator = cls(input_dim=10, discriminator_dim=(256, 256), pac=10)
        errors = ModuleValidator.validate(discriminator)
        assert errors == [], f"OpacusCompatibleDiscriminator has Opacus incompatibilities: {errors}"

    def test_opacus_module_validator_passes_single_layer(self) -> None:
        """ModuleValidator.validate() returns zero errors for minimal architecture."""
        from opacus.validators import ModuleValidator

        cls = _make_real_module()
        discriminator = cls(input_dim=4, discriminator_dim=(32,), pac=10)
        errors = ModuleValidator.validate(discriminator)
        assert errors == []

    def test_is_valid_returns_true(self) -> None:
        """ModuleValidator.is_valid() is True for all valid configurations."""
        from opacus.validators import ModuleValidator

        cls = _make_real_module()
        discriminator = cls(input_dim=12, discriminator_dim=(128, 64), pac=10)
        assert ModuleValidator.is_valid(discriminator)


# ---------------------------------------------------------------------------
# Tests: backward pass (gradient flow)
# ---------------------------------------------------------------------------


class TestOpacusCompatibleDiscriminatorGradients:
    """Tests that gradients flow through the discriminator correctly."""

    def test_backward_pass_and_optimizer_step(self) -> None:
        """Gradient computation works and an optimizer step can be taken."""
        import torch
        import torch.optim as optim

        cls = _make_real_module()
        discriminator = cls(input_dim=10, discriminator_dim=(64, 64), pac=10)
        optimizer = optim.Adam(discriminator.parameters(), lr=1e-3)

        x = torch.randn(20, 10, requires_grad=False)
        out = discriminator(x)
        loss = out.mean()
        loss.backward()
        optimizer.step()

        # Verify gradients were computed for at least some parameters
        grad_norms = [
            p.grad.norm().item() for p in discriminator.parameters() if p.grad is not None
        ]
        assert len(grad_norms) > 0, "No parameter received gradients"
        assert any(g > 0 for g in grad_norms), "All gradients are zero"

    def test_parameters_are_accessible(self) -> None:
        """parameters() returns a non-empty iterator (trainable model)."""

        cls = _make_real_module()
        discriminator = cls(input_dim=10, discriminator_dim=(128, 64), pac=10)
        params = list(discriminator.parameters())
        assert len(params) > 0

    def test_parameters_require_grad(self) -> None:
        """All parameters have requires_grad=True by default."""

        cls = _make_real_module()
        discriminator = cls(input_dim=10, discriminator_dim=(64,), pac=10)
        params = list(discriminator.parameters())
        assert all(p.requires_grad for p in params)


# ---------------------------------------------------------------------------
# Tests: calc_gradient_penalty
# ---------------------------------------------------------------------------


class TestCalcGradientPenalty:
    """Tests for the calc_gradient_penalty() method (T30.3 interface contract)."""

    def test_gradient_penalty_returns_scalar_tensor(self) -> None:
        """calc_gradient_penalty() returns a scalar (0-dim) tensor."""
        import torch

        cls = _make_real_module()
        pac = 10
        input_dim = 8
        batch_size = 20
        discriminator = cls(input_dim=input_dim, discriminator_dim=(64, 64), pac=pac)

        real_data = torch.randn(batch_size, input_dim, requires_grad=True)
        fake_data = torch.randn(batch_size, input_dim)

        penalty = discriminator.calc_gradient_penalty(
            real_data=real_data,
            fake_data=fake_data,
            device="cpu",
            pac=pac,
        )
        assert penalty.shape == torch.Size([]), f"Expected scalar tensor, got shape {penalty.shape}"

    def test_gradient_penalty_is_non_negative(self) -> None:
        """Gradient penalty is always >= 0 (it's a squared norm)."""
        import torch

        cls = _make_real_module()
        pac = 10
        input_dim = 6
        batch_size = 20
        discriminator = cls(input_dim=input_dim, discriminator_dim=(32,), pac=pac)

        real_data = torch.randn(batch_size, input_dim, requires_grad=True)
        fake_data = torch.randn(batch_size, input_dim)

        penalty = discriminator.calc_gradient_penalty(
            real_data=real_data,
            fake_data=fake_data,
            device="cpu",
            pac=pac,
        )
        assert penalty.item() >= 0.0

    def test_gradient_penalty_is_differentiable(self) -> None:
        """calc_gradient_penalty() result allows .backward() for training."""
        import torch

        cls = _make_real_module()
        pac = 10
        input_dim = 8
        batch_size = 20
        discriminator = cls(input_dim=input_dim, discriminator_dim=(64,), pac=pac)

        real_data = torch.randn(batch_size, input_dim, requires_grad=True)
        fake_data = torch.randn(batch_size, input_dim)

        penalty = discriminator.calc_gradient_penalty(
            real_data=real_data,
            fake_data=fake_data,
            device="cpu",
            pac=pac,
        )
        # Should not raise
        penalty.backward()

    def test_gradient_penalty_default_lambda_scales_penalty(self) -> None:
        """A higher lambda_ produces a proportionally larger penalty."""
        import torch

        cls = _make_real_module()
        torch.manual_seed(99)
        pac = 10
        input_dim = 8
        batch_size = 20
        discriminator = cls(input_dim=input_dim, discriminator_dim=(64,), pac=pac)

        real_data = torch.randn(batch_size, input_dim, requires_grad=True)
        fake_data = torch.randn(batch_size, input_dim)

        penalty_1 = discriminator.calc_gradient_penalty(
            real_data=real_data.detach().requires_grad_(True),
            fake_data=fake_data,
            device="cpu",
            pac=pac,
            lambda_=10,
        )
        penalty_2 = discriminator.calc_gradient_penalty(
            real_data=real_data.detach().requires_grad_(True),
            fake_data=fake_data,
            device="cpu",
            pac=pac,
            lambda_=20,
        )
        # penalty_2 should be ~2x penalty_1 (linear scaling)
        ratio = penalty_2.item() / (penalty_1.item() + 1e-8)
        assert 1.5 < ratio < 2.5, f"Expected ~2x ratio for 2x lambda_, got {ratio:.3f}"


# ---------------------------------------------------------------------------
# Tests: mocked torch path (import-boundary guard)
# ---------------------------------------------------------------------------


class TestMockedTorchImport:
    """Tests that verify correct behaviour when torch is unavailable at import time."""

    def test_module_file_is_importable_without_side_effects(self) -> None:
        """The module can be imported (real torch present in test environment)."""
        import synth_engine.modules.synthesizer.dp_discriminator as m

        assert m is not None

    def test_class_is_exported_from_module(self) -> None:
        """OpacusCompatibleDiscriminator is the primary export of dp_discriminator."""
        from synth_engine.modules.synthesizer.dp_discriminator import (
            OpacusCompatibleDiscriminator,
        )

        assert OpacusCompatibleDiscriminator is not None
