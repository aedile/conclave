"""Privacy — Epsilon/Delta differential privacy accountant ledger.

Public API exports:
    - :class:`~synth_engine.modules.privacy.dp_engine.DPTrainingWrapper`:
      Wraps a PyTorch optimizer/model/dataloader with Opacus PrivacyEngine
      for DP-SGD training.  Exposes ``wrap()``, ``epsilon_spent()``, and
      ``check_budget()``.
    - :exc:`~synth_engine.modules.privacy.dp_engine.BudgetExhaustionError`:
      Raised when per-run Epsilon spend reaches or exceeds the allocated budget.

Task: P4-T4.3b — DP Engine Wiring (BudgetExhaustionError, DPTrainingWrapper)
"""

from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError, DPTrainingWrapper

__all__ = ["BudgetExhaustionError", "DPTrainingWrapper"]
