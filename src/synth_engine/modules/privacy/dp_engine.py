"""Differential Privacy training wrapper using Opacus PrivacyEngine.

Provides :class:`DPTrainingWrapper`, a standalone class that wraps a PyTorch
optimizer, model, and DataLoader with Opacus DP-SGD mechanisms.  Also defines
:exc:`BudgetExhaustionError`, raised when per-run Epsilon budget is exhausted.

Boundary constraints (import-linter enforced):
  - Must NOT import from ``modules/synthesizer/``.
  - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
    ``modules/profiler/``, or ``modules/subsetting/``.
  - Cross-module data transfer uses only generic PyTorch types (``Any``).

Design note — SDV integration deferred (ADR-0017 risk):
  SDV's :class:`CTGANSynthesizer` manages its own PyTorch training loop via
  ``fit()``.  The optimizer, model, and DataLoader are created and destroyed
  internally; they are not exposed as public arguments.  Wrapping SDV's
  internal optimizer with Opacus therefore requires either accessing CTGAN
  private attributes (fragile) or implementing a custom CTGAN training loop
  (significant scope).  ADR-0017 acknowledges this as a risk and defers the
  concrete wiring to a future task when SDV exposes training hooks, or when a
  custom training loop replaces SDV's ``fit()``.

  This module delivers the *full public API* — :meth:`DPTrainingWrapper.wrap`,
  :meth:`~DPTrainingWrapper.epsilon_spent`, :meth:`~DPTrainingWrapper.check_budget`
  — tested against raw PyTorch objects.  :class:`SynthesisEngine` accepts the
  wrapper as an ``Any``-typed parameter and logs an advisory when the wrapper
  is provided but cannot be applied to SDV's internal loop.

Task: P4-T4.3b — DP Engine Wiring
ADR: ADR-0017 (CTGAN + Opacus; RDP accountant for Epsilon tracking)
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Opacus import — deferred to module-level try/except so that environments
# without the synthesizer group do not encounter ModuleNotFoundError at
# import time.  The synthesizer group must be installed for DP training.
#
# PrivacyEngine is bound at module scope for unit-test patching:
#   patch('synth_engine.modules.privacy.dp_engine.PrivacyEngine')
# ---------------------------------------------------------------------------
try:
    from opacus import PrivacyEngine  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover — only triggered if synthesizer group absent
    PrivacyEngine = None  # Opacus not installed; DP training unavailable


class BudgetExhaustionError(Exception):
    """Raised when cumulative Epsilon spend reaches or exceeds the allocated budget.

    Attributes:
        message: Human-readable description including spent and allocated Epsilon.

    Example::

        raise BudgetExhaustionError(
            f"DP budget exhausted: epsilon_spent={1.1:.4f} >= "
            f"allocated_epsilon={1.0:.4f} (delta={1e-5:.0e})"
        )
    """


class DPTrainingWrapper:
    """Wraps a PyTorch training setup with Opacus Differential Privacy.

    Encapsulates the Opacus :class:`~opacus.PrivacyEngine` lifecycle:
    construction, optimizer wrapping via :meth:`wrap`, per-epoch Epsilon
    query via :meth:`epsilon_spent`, and budget enforcement via
    :meth:`check_budget`.

    A single :class:`DPTrainingWrapper` instance is single-use — calling
    :meth:`wrap` twice raises :exc:`RuntimeError`.  This prevents accidental
    double-wrapping that would corrupt Epsilon accounting.

    Usage::

        wrapper = DPTrainingWrapper()
        dp_optimizer = wrapper.wrap(
            optimizer=optimizer,
            model=model,
            dataloader=train_loader,
            max_grad_norm=1.0,
            noise_multiplier=1.1,
        )

        for epoch in range(epochs):
            # ... training step using dp_optimizer ...
            wrapper.check_budget(allocated_epsilon=1.0, delta=1e-5)

    Boundary note:
        This class does NOT import from ``modules/synthesizer/``.  The caller
        (bootstrapper or orchestrator) is responsible for constructing the
        wrapper and passing it into ``SynthesisEngine.train()``.
    """

    def __init__(self) -> None:
        """Initialise an unwrapped DPTrainingWrapper.

        The wrapper starts in a "not-wrapped" state.  :meth:`wrap` must be
        called before :meth:`epsilon_spent` or :meth:`check_budget`.
        """
        self._privacy_engine: Any = None  # set by wrap()
        self._wrapped: bool = False

    def wrap(
        self,
        optimizer: Any,
        model: Any,
        dataloader: Any,
        *,
        max_grad_norm: float,
        noise_multiplier: float,
    ) -> Any:
        """Wrap optimizer with Opacus PrivacyEngine for DP-SGD training.

        Constructs an Opacus :class:`~opacus.PrivacyEngine`, calls
        ``make_private()`` to replace the standard optimizer with a
        DP-aware optimizer, and stores the engine for later Epsilon queries.

        Args:
            optimizer: A PyTorch optimizer (e.g. ``torch.optim.Adam``).
            model: The ``nn.Module`` to be trained.
            dataloader: The ``DataLoader`` providing training batches.
            max_grad_norm: Maximum L2 norm for per-sample gradient clipping.
                Controls the sensitivity of the gradients.  Typical value: 1.0.
            noise_multiplier: Ratio of Gaussian noise std to max_grad_norm.
                Higher values → stronger privacy, lower utility.  Typical: 1.1.

        Returns:
            The DP-wrapped optimizer returned by
            ``PrivacyEngine.make_private()``.  Use this optimizer in the
            training loop instead of the original one.

        Note:
            Opacus ``PrivacyEngine.make_private()`` internally returns a
            3-tuple (dp_model, dp_optimizer, dp_dataloader).  This method
            surfaces only the dp_optimizer to the caller — the DP-wrapped
            model and dataloader are consumed internally by Opacus to instrument
            gradient hooks and batch accounting.  Callers do not need to handle
            the model or dataloader replacements; only the returned optimizer
            must replace the original in the training loop.

        Raises:
            RuntimeError: If this wrapper has already been used to wrap a
                training setup (single-use constraint).
            ImportError: If ``opacus`` is not installed (synthesizer group
                not present).
        """
        if self._wrapped:
            raise RuntimeError(
                "DPTrainingWrapper has already wrapped a training setup. "
                "Each DPTrainingWrapper instance is single-use — create a "
                "new instance for each training run."
            )

        if PrivacyEngine is None:  # pragma: no cover
            raise ImportError(
                "The 'opacus' package is required for DP training. "
                "Install it with: poetry install --with synthesizer"
            )

        _logger.info(
            "Initialising Opacus PrivacyEngine (max_grad_norm=%.2f, noise_multiplier=%.2f).",
            max_grad_norm,
            noise_multiplier,
        )

        privacy_engine = PrivacyEngine()
        _dp_model, dp_optimizer, _dp_dataloader = privacy_engine.make_private(
            module=model,
            optimizer=optimizer,
            data_loader=dataloader,
            max_grad_norm=max_grad_norm,
            noise_multiplier=noise_multiplier,
        )

        self._privacy_engine = privacy_engine
        self._wrapped = True

        _logger.info("Opacus PrivacyEngine active — DP-SGD optimizer installed.")
        return dp_optimizer

    def epsilon_spent(self, *, delta: float) -> float:
        """Return the cumulative Epsilon spent so far in this training run.

        Queries the Opacus RDP accountant for the current (Epsilon, Delta)-DP
        guarantee.  Returns 0.0 before :meth:`wrap` is called (no training
        has occurred yet).

        Args:
            delta: The Delta value (probability of privacy failure) to use
                when computing the Epsilon guarantee.  Typical value: 1e-5.

        Returns:
            Cumulative Epsilon spent since the start of this training run.
            0.0 if the wrapper has not yet been activated via :meth:`wrap`.
        """
        if self._privacy_engine is None:
            return 0.0

        epsilon: float = self._privacy_engine.get_epsilon(delta=delta)
        return epsilon

    def check_budget(self, *, allocated_epsilon: float, delta: float) -> None:
        """Raise BudgetExhaustionError if Epsilon spend has reached the allocation.

        Call this after each training epoch to enforce per-run budget.

        Args:
            allocated_epsilon: The maximum Epsilon budget for this training run.
                Training must stop if ``epsilon_spent >= allocated_epsilon``.
            delta: The Delta value used to compute the current Epsilon spend.

        Returns:
            None — if the budget has not been exhausted.

        Raises:
            RuntimeError: If :meth:`wrap` has not been called yet (no Opacus
                engine is active to query).
            BudgetExhaustionError: If ``epsilon_spent(delta) >= allocated_epsilon``.
        """
        if not self._wrapped:
            raise RuntimeError(
                "DPTrainingWrapper is not wrapped yet. Call wrap() before calling check_budget()."
            )

        spent = self.epsilon_spent(delta=delta)

        if spent >= allocated_epsilon:
            raise BudgetExhaustionError(
                f"DP budget exhausted: epsilon_spent={spent:.6f} >= "
                f"allocated_epsilon={allocated_epsilon:.6f} (delta={delta:.0e}). "
                "Training halted to protect privacy guarantee."
            )

        _logger.debug(
            "Budget check: epsilon_spent=%.6f < allocated_epsilon=%.6f (delta=%.0e). OK.",
            spent,
            allocated_epsilon,
            delta,
        )
