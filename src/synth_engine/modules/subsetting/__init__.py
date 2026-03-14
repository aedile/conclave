"""Subsetting — DAG traversal, subsetting orchestration, and Saga egress.

Public API:

- :class:`~synth_engine.modules.subsetting.traversal.DagTraversal`
- :class:`~synth_engine.modules.subsetting.core.SubsettingEngine`
- :class:`~synth_engine.modules.subsetting.core.SubsetResult`
- :class:`~synth_engine.modules.subsetting.egress.EgressWriter`

Architecture note
-----------------
``modules/subsetting`` is responsible for a single coherent domain: extracting
a referentially-intact subset from a source database and writing it to a target
database with Saga-pattern rollback guarantees.

Import-linter contracts (``pyproject.toml``) enforce:
- ``subsetting`` MAY import from ``synth_engine.modules.mapping`` (it needs
  the DAG structure for traversal).
- ``subsetting`` may NOT import from ``ingestion``, ``masking``, ``profiler``,
  ``privacy``, or ``bootstrapper``.

Task: P3.5-T3.5.2 — Module Cohesion Refactor
"""

from synth_engine.modules.subsetting.core import SubsetResult, SubsettingEngine
from synth_engine.modules.subsetting.egress import EgressWriter
from synth_engine.modules.subsetting.traversal import DagTraversal

__all__ = [
    "DagTraversal",
    "EgressWriter",
    "SubsetResult",
    "SubsettingEngine",
]
