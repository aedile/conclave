"""Privacy — Epsilon/Delta differential privacy accountant ledger.

Public API exports:
    - :class:`~synth_engine.modules.privacy.dp_engine.DPTrainingWrapper`:
      Wraps a PyTorch optimizer/model/dataloader with Opacus PrivacyEngine
      for DP-SGD training.  Exposes ``wrap()``, ``epsilon_spent()``, and
      ``check_budget()``.
    - :exc:`~synth_engine.modules.privacy.dp_engine.BudgetExhaustionError`:
      Raised when per-run Epsilon spend reaches or exceeds the allocated budget.
      Also raised by ``spend_budget()`` when the global budget is exhausted.
    - :class:`~synth_engine.modules.privacy.ledger.PrivacyLedger`:
      SQLModel table tracking total allocated and spent epsilon globally.
    - :class:`~synth_engine.modules.privacy.ledger.PrivacyTransaction`:
      SQLModel table recording each individual epsilon expenditure.
    - :func:`~synth_engine.modules.privacy.accountant.spend_budget`:
      Async function that atomically deducts epsilon from the global ledger
      using ``SELECT ... FOR UPDATE`` pessimistic locking.

Task: P4-T4.3b — DP Engine Wiring (BudgetExhaustionError, DPTrainingWrapper)
Task: P4-T4.4 — Privacy Accountant (PrivacyLedger, PrivacyTransaction, spend_budget)
"""

from synth_engine.modules.privacy.accountant import spend_budget
from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError, DPTrainingWrapper
from synth_engine.modules.privacy.ledger import PrivacyLedger, PrivacyTransaction

__all__ = [
    "BudgetExhaustionError",
    "DPTrainingWrapper",
    "PrivacyLedger",
    "PrivacyTransaction",
    "spend_budget",
]
