"""Mapping — Relational schema DAG construction and reflection.

Public API:

- :class:`~synth_engine.modules.mapping.graph.CycleDetectionError`
- :class:`~synth_engine.modules.mapping.graph.DirectedAcyclicGraph`
- :class:`~synth_engine.modules.mapping.reflection.SchemaReflector`

Architecture note
-----------------
``modules/mapping`` is responsible for a single coherent domain: building and
interrogating a directed acyclic graph that represents a database schema's
foreign-key topology.

Import-linter contracts (``pyproject.toml``) enforce:
- ``mapping`` may NOT import from ``ingestion``, ``subsetting``, ``masking``,
  ``profiler``, ``privacy``, or ``bootstrapper``.

Task: P3.5-T3.5.2 — Module Cohesion Refactor
"""

from synth_engine.modules.mapping.graph import CycleDetectionError, DirectedAcyclicGraph
from synth_engine.modules.mapping.reflection import SchemaReflector

__all__ = [
    "CycleDetectionError",
    "DirectedAcyclicGraph",
    "SchemaReflector",
]
