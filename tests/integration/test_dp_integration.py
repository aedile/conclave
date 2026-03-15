"""Integration tests for DPTrainingWrapper with real Opacus PrivacyEngine.

These tests exercise the DP engine with real PyTorch objects (no mocks for the
core DP path).  CTGAN/SDV is NOT tested here — the SDV integration gap is
documented in engine.py and in ADR-0017's risk section.  These tests verify
that DPTrainingWrapper functions correctly with real PyTorch components.

Test scope:
  1. DPTrainingWrapper.wrap() + epsilon_spent() with real Opacus PrivacyEngine,
     a real nn.Linear model, and a real DataLoader.
  2. BudgetExhaustionError is raised when a tiny budget is exceeded via
     repeated epsilon queries after wrapping (simulating training steps).
  3. import-linter contract: modules/privacy must NOT import from
     modules/synthesizer (run via lint-imports CLI).

Note on the "2 epochs with tiny budget" acceptance criterion:
  The backlog states: "train CTGAN for 2 epochs with a tiny budget
  (max_epsilon=0.01); assert BudgetExhaustionError is raised before training
  completes all epochs."  This requires wiring DPTrainingWrapper into
  CTGANSynthesizer's internal training loop — which is blocked by SDV's
  fit() not exposing its optimizer (ADR-0017 risk).  The integration test
  below fulfils the spirit of this AC using a real PyTorch model + Opacus
  instead of CTGAN+SDV.  The SDV-specific path is logged as an advisory
  in RETRO_LOG.

Task: P4-T4.3b — DP Engine Wiring
ADR: ADR-0017 (CTGAN + Opacus; per-table training with FK post-processing)
"""

from __future__ import annotations

import shutil
import subprocess

import pytest


class TestDPTrainingWrapperRealOpacus:
    """Integration tests: real Opacus PrivacyEngine (no mocks for the DP path)."""

    @pytest.fixture
    def simple_pytorch_setup(self) -> dict[object, object]:
        """Provide a minimal real PyTorch model, optimizer, and DataLoader.

        Uses a single nn.Linear layer — sufficient to exercise Opacus
        PrivacyEngine without CTGAN/SDV complexity.

        Returns:
            Dict with keys: model, optimizer, dataloader.

        Raises:
            pytest.skip: If torch is not installed (synthesizer group absent).
        """
        try:
            import torch
            import torch.nn as nn
            from torch.utils.data import DataLoader, TensorDataset
        except ImportError:
            pytest.skip("torch not installed — synthesizer group required")

        # Minimal 4-feature → 1-output linear model
        model = nn.Linear(4, 1)
        optimizer = torch.optim.SGD(model.parameters(), lr=0.01)

        # 50 synthetic samples, batch size 8
        x = torch.randn(50, 4)
        y = torch.randn(50, 1)
        dataset = TensorDataset(x, y)
        dataloader = DataLoader(dataset, batch_size=8)

        return {"model": model, "optimizer": optimizer, "dataloader": dataloader}

    def test_wrap_returns_dp_optimizer_with_real_engine(
        self, simple_pytorch_setup: dict[object, object]
    ) -> None:
        """wrap() with real Opacus must return a DP-wrapped optimizer instance."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper()
        dp_optimizer = wrapper.wrap(
            optimizer=simple_pytorch_setup["optimizer"],
            model=simple_pytorch_setup["model"],
            dataloader=simple_pytorch_setup["dataloader"],
            max_grad_norm=1.0,
            noise_multiplier=1.1,
        )

        # The returned optimizer must not be None
        assert dp_optimizer is not None

    def test_epsilon_spent_returns_positive_after_training_step(
        self, simple_pytorch_setup: dict[object, object]
    ) -> None:
        """epsilon_spent() must return a positive float after at least one training step.

        This test simulates one epoch of training to advance the Opacus
        accountant, then checks that epsilon_spent() returns a positive value.
        """
        try:
            import torch.nn as nn
        except ImportError:
            pytest.skip("torch not installed — synthesizer group required")

        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        model = simple_pytorch_setup["model"]
        dataloader = simple_pytorch_setup["dataloader"]

        wrapper = DPTrainingWrapper()
        dp_optimizer = wrapper.wrap(
            optimizer=simple_pytorch_setup["optimizer"],
            model=model,
            dataloader=dataloader,
            max_grad_norm=1.0,
            noise_multiplier=1.1,
        )

        # Run one mini-batch of training to advance the Opacus accountant
        loss_fn = nn.MSELoss()
        dp_optimizer.zero_grad()
        for x_batch, y_batch in dataloader:
            pred = model(x_batch)
            loss = loss_fn(pred, y_batch)
            loss.backward()
            dp_optimizer.step()
            break  # one batch is enough to register steps with Opacus

        epsilon = wrapper.epsilon_spent(delta=1e-5)

        # After at least one training step, epsilon must be a positive float
        assert isinstance(epsilon, float)
        assert epsilon > 0.0, f"Expected epsilon > 0.0 after one training step, got {epsilon!r}"

    def test_check_budget_raises_with_tiny_budget(
        self, simple_pytorch_setup: dict[object, object]
    ) -> None:
        """check_budget() must raise BudgetExhaustionError with a tiny budget (0.01).

        Simulates the backlog AC: "train for 2 epochs with max_epsilon=0.01;
        assert BudgetExhaustionError is raised before training completes".
        Uses a real PyTorch model instead of CTGAN (SDV integration deferred
        per ADR-0017 risk note and engine.py advisory warning).
        """
        try:
            import torch.nn as nn
        except ImportError:
            pytest.skip("torch not installed — synthesizer group required")

        from synth_engine.modules.privacy.dp_engine import (
            BudgetExhaustionError,
            DPTrainingWrapper,
        )

        model = simple_pytorch_setup["model"]
        dataloader = simple_pytorch_setup["dataloader"]

        wrapper = DPTrainingWrapper()
        dp_optimizer = wrapper.wrap(
            optimizer=simple_pytorch_setup["optimizer"],
            model=model,
            dataloader=dataloader,
            max_grad_norm=1.0,
            noise_multiplier=1.1,
        )

        # Run 2 epochs of training, checking budget with max_epsilon=0.01.
        # With noise_multiplier=1.1 and 50 samples, epsilon rises quickly.
        loss_fn = nn.MSELoss()
        budget_exhausted = False
        max_epsilon = 0.01

        for _epoch in range(2):
            for x_batch, y_batch in dataloader:
                dp_optimizer.zero_grad()
                pred = model(x_batch)
                loss = loss_fn(pred, y_batch)
                loss.backward()
                dp_optimizer.step()

            # Check budget after each epoch
            try:
                wrapper.check_budget(allocated_epsilon=max_epsilon, delta=1e-5)
            except BudgetExhaustionError:
                budget_exhausted = True
                break

        # With max_epsilon=0.01 (very tight) and standard noise params,
        # budget should be exhausted within 2 epochs
        assert budget_exhausted, (
            f"Expected BudgetExhaustionError with max_epsilon={max_epsilon} "
            f"after 2 epochs, but budget was not exhausted. "
            f"Current epsilon: {wrapper.epsilon_spent(delta=1e-5):.6f}"
        )

    def test_epsilon_spent_before_wrap_returns_zero(self) -> None:
        """epsilon_spent() before wrap() must return 0.0 (no training has occurred)."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper()
        epsilon = wrapper.epsilon_spent(delta=1e-5)

        assert epsilon == 0.0
        assert isinstance(epsilon, float)


class TestImportBoundaryPrivacySynthesizer:
    """Verify that modules/privacy does NOT import from modules/synthesizer.

    Per backlog AC: "Run poetry run python -m importlinter — modules/privacy
    must NOT import from modules/synthesizer."

    This enforces the independence contract defined in pyproject.toml.
    """

    def test_privacy_does_not_import_from_synthesizer(self) -> None:
        """import-linter must confirm privacy has no synthesizer dependency.

        Uses the ``lint-imports`` CLI entry point installed by import-linter.
        """
        lint_imports = shutil.which("lint-imports")
        if lint_imports is None:
            pytest.skip("lint-imports binary not found — import-linter not installed")

        result = subprocess.run(  # noqa: S603
            [lint_imports],
            capture_output=True,
            text=True,
        )
        # lint-imports exits 0 on success, non-zero on contract violation
        assert result.returncode == 0, (
            f"import-linter found contract violations:\n{result.stdout}\n{result.stderr}"
        )
