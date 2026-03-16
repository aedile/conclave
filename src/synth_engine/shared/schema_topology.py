"""Neutral schema topology value object for cross-module data handoff.

Produced by the bootstrapper from SchemaReflector output and injected into
downstream modules (SubsettingEngine, Profiler, Synthesizer) via constructor.

Direct import of SchemaReflector or DirectedAcyclicGraph by any module other
than modules/mapping/ is forbidden (import-linter enforcement).

Per ADR-0001 (bootstrapper-as-orchestrator), ADR-0012 §Cross-module,
ADR-0013 §5.

CONSTITUTION Priority 0: Security — no external calls, no PII stored.
Task: P3-T3.4 -- Subsetting & Materialization Core
Task: P3.5-T3.5.3 -- SchemaTopology immutability (MappingProxyType)
"""

from __future__ import annotations

import types
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ColumnInfo:
    """Immutable descriptor for a single database column.

    Attributes:
        name: Column name as reflected from the database schema.
        type: SQLAlchemy type name string, e.g. ``"VARCHAR"``, ``"INTEGER"``.
        primary_key: ``0`` = not part of the PK; ``>= 1`` = PK member.
            Composite PK members are assigned ``primary_key=1`` (ordering not
            preserved — all members receive the same value of 1).
            Callers MUST use ``>= 1``, not ``== 1``, to identify PK membership
            (ADV-012 compliance).
        nullable: Whether the column accepts NULL values.
    """

    name: str
    type: str
    primary_key: int  # 0 = not PK; >= 1 = PK (composite PK members all assigned 1)
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
    it via constructor — direct import of mapping-module types by other
    modules is forbidden by import-linter contracts.

    The ``columns`` and ``foreign_keys`` fields are wrapped in
    ``types.MappingProxyType`` during ``__post_init__`` to prevent nested
    dict mutation at runtime.  This closes the gap where ``frozen=True``
    prevents field reassignment but NOT mutation of nested mutable containers
    (e.g. ``topology.columns["t"].append("evil")`` would succeed silently
    without this guard).  See T3.5.3 / ADV-028.

    Attributes:
        table_order: Tables in topological (parent-before-child) processing
            order, as produced by ``DirectedAcyclicGraph.topological_sort()``.
        columns: Read-only mapping of table name to a tuple of
            :class:`ColumnInfo` descriptors for all columns in that table.
        foreign_keys: Read-only mapping of table name to a tuple of
            :class:`ForeignKeyInfo` descriptors for FK constraints where that
            table is the child (constrained) side.
    """

    table_order: tuple[str, ...]
    columns: Mapping[str, tuple[ColumnInfo, ...]] = field(default_factory=dict)
    foreign_keys: Mapping[str, tuple[ForeignKeyInfo, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Wrap columns and foreign_keys in MappingProxyType after construction.

        ``frozen=True`` prevents field reassignment but does NOT prevent
        mutation of nested mutable containers.  Wrapping in
        ``types.MappingProxyType`` makes the outer dicts truly read-only at
        runtime — any mutation attempt raises ``TypeError``.

        ``object.__setattr__`` must be used here because the dataclass is
        ``frozen=True``; the normal attribute-assignment path is blocked.
        """
        object.__setattr__(
            self,
            "columns",
            types.MappingProxyType(dict(self.columns)),
        )
        object.__setattr__(
            self,
            "foreign_keys",
            types.MappingProxyType(dict(self.foreign_keys)),
        )
