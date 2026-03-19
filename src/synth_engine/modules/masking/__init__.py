"""Masking — Deterministic format-preserving transformation rules.

Public API:

- :class:`~synth_engine.modules.masking.registry.MaskingRegistry` — maps
  column types to masking algorithms with collision prevention.
- :class:`~synth_engine.modules.masking.registry.ColumnType` — enum of
  supported PII column types (NAME, EMAIL, SSN, etc.).
- :exc:`~synth_engine.modules.masking.registry.CollisionError` — raised when
  collision prevention encounters an unexpected internal state.
- :func:`~synth_engine.modules.masking.deterministic.mask_value` — low-level
  deterministic masking primitive (HMAC-seeded Faker).
- :func:`~synth_engine.modules.masking.deterministic.deterministic_hash` —
  HMAC-SHA256 based deterministic hash for domain separation.

Task: T36.4 — Add __all__ (standardise module exports)
"""

from synth_engine.modules.masking.deterministic import deterministic_hash, mask_value
from synth_engine.modules.masking.registry import CollisionError, ColumnType, MaskingRegistry

__all__ = [
    "CollisionError",
    "ColumnType",
    "MaskingRegistry",
    "deterministic_hash",
    "mask_value",
]
