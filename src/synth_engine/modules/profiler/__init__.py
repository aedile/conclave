"""profiler module — Statistical distributions and latent patterns (Phase 4, T4.2a).

Public API:
    StatisticalProfiler: Computes per-column statistics and pairwise covariance.
    TableProfile: Frozen snapshot of a table's statistical shape.
    ColumnProfile: Frozen snapshot of a single column's statistics.
    ProfileDelta: Comparison result between baseline and synthetic profiles.
    ColumnDelta: Per-column drift metrics.
"""

from synth_engine.modules.profiler.models import (
    ColumnDelta,
    ColumnProfile,
    ProfileDelta,
    TableProfile,
)
from synth_engine.modules.profiler.profiler import StatisticalProfiler

__all__ = [
    "ColumnDelta",
    "ColumnProfile",
    "ProfileDelta",
    "StatisticalProfiler",
    "TableProfile",
]
