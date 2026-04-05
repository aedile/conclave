"""Shared SQLModel table models used across multiple modules.

This subpackage holds data-carrier models (frozen dataclasses and SQLModel
tables) that are consumed by two or more modules and therefore cannot live
in any single module without creating import boundary violations.

Per CLAUDE.md "Neutral value object exception":
    A file that is a pure data-carrier and is consumed by two or more modules
    belongs in ``shared/`` rather than any single module.

Models in this package:
- :mod:`~synth_engine.shared.models.organization` — Organization table
  (multi-tenant org registry, Phase 79 T79.1)
- :mod:`~synth_engine.shared.models.user` — User table
  (multi-tenant user registry with FK to Organization, Phase 79 T79.1)

Alembic discovery
-----------------
``alembic/env.py`` imports this package so that ``Organization`` and ``User``
register with ``SQLModel.metadata`` before ``target_metadata`` is referenced
during autogenerate and migration operations.

CONSTITUTION Priority 0: Security — org isolation anchored in these models
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Phase: 79 — Multi-Tenancy Foundation (T79.0b)
"""

from __future__ import annotations

from synth_engine.shared.models.organization import Organization as Organization
from synth_engine.shared.models.user import User as User

__all__ = ["Organization", "User"]
