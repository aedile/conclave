"""Neutral schema topology value object for cross-module data handoff.

Produced by the bootstrapper from SchemaReflector output and injected into
downstream modules (SubsettingEngine, Profiler, Synthesizer) via constructor.

Direct import of SchemaReflector or DirectedAcyclicGraph by any module other
than modules/ingestion/ is forbidden (import-linter enforcement).

Per ADR-0001 (bootstrapper-as-orchestrator), ADR-0012 §Cross-module,
ADR-0013 §5.

CONSTITUTION Priority 0: Security — no external calls, no PII stored.
Task: P3-T3.4 -- Subsetting & Materialization Core
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ColumnInfo:
    """Immutable descriptor for a single database column.

    Attributes:
        name: Column name as reflected from the database schema.
        type: SQLAlchemy type name string, e.g. ``"VARCHAR"``, ``"INTEGER"``.
        primary_key: ``0`` = not part of the PK; ``>= 1`` = PK member
            (supports composite PKs using incrementing integers 1, 2, ...).
            Callers MUST use ``>= 1``, not ``== 1``, to identify PK membership
            (ADV-012 compliance).
        nullable: Whether the column accepts NULL values.
    """

    name: str
    type: str
    primary_key: int  # 0 = not PK; >= 1 = PK (composite PKs use 1, 2, ...)
    nullable: bool


@dataclass(frozen=True)
class ForeignKeyInfo:
    """Immutable descriptor for a single foreign key constraint.

    Attributes:
        constrained_columns: Column name(s) on the child (constrained) side.
        referred_table: Name of the parent (referenced) table.
        referred_columns: Column name(s) on the parent (referred) side.
    """

    constrained_columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]


@dataclass(frozen=True)
class SchemaTopology:
    """Immutable snapshot of database schema topology for downstream modules.

    This value object is the only legal way for downstream modules
    (SubsettingEngine, Profiler, Synthesizer) to consume schema information.
    The bootstrapper constructs it from ``SchemaReflector`` output and injects
    it via constructor — direct import of ingestion-module types by other
    modules is forbidden by import-linter contracts.

    Attributes:
        table_order: Tables in topological (parent-before-child) processing
            order, as produced by ``DirectedAcyclicGraph.topological_sort()``.
        columns: Mapping of table name to a tuple of :class:`ColumnInfo`
            descriptors for all columns in that table.
        foreign_keys: Mapping of table name to a tuple of
            :class:`ForeignKeyInfo` descriptors for FK constraints where that
            table is the child (constrained) side.
    """

    table_order: tuple[str, ...]
    columns: dict[str, tuple[ColumnInfo, ...]] = field(default_factory=dict)
    foreign_keys: dict[str, tuple[ForeignKeyInfo, ...]] = field(default_factory=dict)
