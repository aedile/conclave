"""Shared task infrastructure — abstract repository and reaper business logic.

This package provides:
- :mod:`~synth_engine.shared.tasks.repository` — Abstract base class for task
  repositories (no concrete DB dependency, safe for ``shared/``).
- :mod:`~synth_engine.shared.tasks.reaper` — Pure business logic for orphan
  task detection and remediation.

Boundary constraints (import-linter enforced):
    ``shared/tasks`` must NOT import from ``modules/`` or ``bootstrapper/``.

Task: T45.2 — Reintroduce Orphan Task Reaper (TBD-08)
"""
