"""Schema reflection module for the Conclave Engine mapping pipeline.

Reflects a PostgreSQL database schema into a :class:`DirectedAcyclicGraph`
using SQLAlchemy's ``inspect()`` API.  Both explicit foreign-key relationships
defined in the database schema and user-supplied Virtual Foreign Keys (VFKs)
are represented as edges.  VFKs are merged with physical FKs before the DAG
is built so that topological sort and traversal work identically for both.

Architecture note
-----------------
This module lives in ``synth_engine.modules.mapping`` and may only import from
sibling files within that package and the Python standard library.
Cross-module imports are forbidden by import-linter contracts defined in
``pyproject.toml``.

ADV-012 compliance:
- ``get_columns()`` passes SQLAlchemy's ``primary_key`` integer values through
  unchanged.  PK columns have ``primary_key >= 1``; composite PKs use
  incrementing integers (1, 2, ...).  Callers MUST use ``>= 1``, not ``== 1``,
  to identify PK membership.

ADV-021 fix (T20.1): Added ``get_pk_constraint()`` method.  The bootstrapper's
``_load_topology()`` previously used ``col.get('primary_key', 0)`` which is
unreliable because some SQLAlchemy backends (notably PostgreSQL via psycopg2)
do not include a ``primary_key`` key in column dicts.  The fix uses
``get_pk_constraint()`` to obtain the definitive list of PK column names.

ADV-023 fix: The SQLAlchemy inspector is now cached in ``__init__`` via a
single ``inspect(engine)`` call.  The three methods ``get_tables()``,
``get_columns()``, and ``get_foreign_keys()`` share ``self._inspector`` rather
than creating a new inspector on each invocation.

ADR-0013: Relational Mapping DAG and Topological Sort Design.
CONSTITUTION Priority 0: Security -- no external calls, no PII exposure.
Task: P3-T3.2 -- Relational Mapping & Topological Sort
Task: P3.5-T3.5.2 -- Module Cohesion Refactor (moved from modules/ingestion/)
Task: P3.5-T3.5.3 -- Virtual Foreign Key (VFK) support
Task: P20-T20.1 -- ADV-021 FK Traversal Fix (get_pk_constraint method)
ADV-023, ADV-024: Inspector caching and type-ignore justifications (T3.4).
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import Engine, inspect
from sqlalchemy.engine import Inspector

from synth_engine.modules.mapping.graph import DirectedAcyclicGraph

#: Type alias for a single VFK config dict supplied by the caller.
_VfkDict = dict[str, str]


class SchemaReflector:
    """Reflects a PostgreSQL schema into a :class:`DirectedAcyclicGraph`.

    Uses SQLAlchemy's ``inspect()`` to extract tables, columns, data types,
    and explicit foreign keys from the connected database.  Optional
    Virtual Foreign Keys (VFKs) — user-supplied logical FK mappings for
    databases without physical FK constraints — are merged with physical FKs
    before DAG construction so that topological sort and traversal behave
    identically for both FK types.

    The SQLAlchemy inspector is cached in ``__init__`` (ADV-023 fix) to avoid
    redundant round-trips when all three reflection methods are called across
    many tables in a tight loop.

    Args:
        engine: A connected SQLAlchemy :class:`~sqlalchemy.Engine`.
        virtual_foreign_keys: Optional list of VFK config dicts.  Each dict
            must contain the keys ``"table"``, ``"column"``,
            ``"references_table"``, and ``"references_column"``.  VFKs
            reference tables that must exist in the reflected schema;
            referencing unknown tables raises :exc:`ValueError`.

    Example::

        engine = create_engine("postgresql+psycopg2://user:pw@host/db")
        reflector = SchemaReflector(engine)
        dag = reflector.reflect()
        order = dag.topological_sort()

        # With virtual FKs (no physical constraint in DB):
        vfks = [{"table": "txn", "column": "acct_id",
                 "references_table": "accounts", "references_column": "id"}]
        reflector = SchemaReflector(engine, virtual_foreign_keys=vfks)
        dag = reflector.reflect()
    """

    def __init__(
        self,
        engine: Engine,
        *,
        virtual_foreign_keys: list[_VfkDict] | None = None,
    ) -> None:
        """Initialise with a SQLAlchemy engine and cache its inspector.

        The inspector is created once here rather than per-method call
        (ADV-023: caching reduces redundant round-trips on large schemas).

        Args:
            engine: A connected SQLAlchemy :class:`~sqlalchemy.Engine`.
            virtual_foreign_keys: Optional list of VFK config dicts.  Each
                dict must have keys ``"table"``, ``"column"``,
                ``"references_table"``, and ``"references_column"``.
        """
        self._inspector: Inspector = inspect(engine)
        self._virtual_foreign_keys: list[_VfkDict] = virtual_foreign_keys or []

    def reflect(self, schema: str = "public") -> DirectedAcyclicGraph:
        """Build and return a DAG from the connected database schema.

        Iterates over all tables in the given schema, registers each as a
        node, then adds directed edges for each FK relationship — both
        explicit physical FKs from the database and any Virtual Foreign Keys
        supplied at construction time.

        VFKs are validated against the reflected table set before being merged:
        if any VFK references an unknown table, :exc:`ValueError` is raised
        with a message identifying the offending table name.  Duplicate edges
        (a VFK identical to an existing physical FK) are silently deduplicated
        by the :class:`DirectedAcyclicGraph` (idempotent ``add_edge``).

        Args:
            schema: PostgreSQL schema name to reflect. Defaults to
                ``"public"``.

        Returns:
            A :class:`DirectedAcyclicGraph` with one node per table and one
            edge per FK relationship (physical + virtual), in the direction
            ``parent -> child``.

        Raises:
            ValueError: If a VFK references a table name not present in the
                reflected schema.
        """
        dag = DirectedAcyclicGraph()
        tables = self.get_tables(schema=schema)
        all_tables: set[str] = set(tables)

        for table in tables:
            dag.add_node(table)

        # --- Validate VFKs against the reflected table set ---
        # VFK table/column names are user-supplied and must be validated
        # against the schema before use; they are never interpolated into SQL.
        for vfk in self._virtual_foreign_keys:
            for field_name in ("table", "references_table"):
                table_name = vfk[field_name]
                if table_name not in all_tables:
                    raise ValueError(
                        f"Virtual FK references unknown table: {table_name!r}. "
                        f"Known tables: {sorted(all_tables)}"
                    )

        # --- Build edge set from physical FKs ---
        # Track physical FK edges as (parent, child, constrained_col, referred_col)
        # tuples so we can deduplicate against VFKs below.
        physical_edges: set[tuple[str, str, str, str]] = set()

        for table in tables:
            for fk in self.get_foreign_keys(table, schema=schema):
                parent = fk["referred_table"]
                # constrained_columns / referred_columns are lists; we use the
                # first element for deduplication keying (composite FK support
                # is not in scope here).
                constrained_col: str = (
                    fk["constrained_columns"][0] if fk.get("constrained_columns") else ""
                )
                referred_col: str = fk["referred_columns"][0] if fk.get("referred_columns") else ""
                physical_edges.add((table, constrained_col, parent, referred_col))
                dag.add_edge(parent, table)

        # --- Merge VFK edges ---
        # A VFK that exactly duplicates a physical FK edge is silently skipped
        # (the DAG's add_edge is itself idempotent, but explicit deduplication
        # here keeps the intent visible).
        for vfk in self._virtual_foreign_keys:
            child = vfk["table"]
            col = vfk["column"]
            parent = vfk["references_table"]
            ref_col = vfk["references_column"]
            edge_key = (child, col, parent, ref_col)
            if edge_key not in physical_edges:
                dag.add_edge(parent, child)

        return dag

    def get_tables(self, schema: str = "public") -> list[str]:
        """Return a list of table names in the given schema.

        Args:
            schema: PostgreSQL schema name. Defaults to ``"public"``.

        Returns:
            List of table name strings visible to the current user.
        """
        return self._inspector.get_table_names(schema=schema)

    def get_columns(self, table: str, schema: str = "public") -> list[dict[str, Any]]:
        """Return column metadata for the given table.

        Each dict contains at minimum ``name``, ``type``, ``nullable``, and
        ``primary_key`` keys.  The ``primary_key`` value is an integer:
        ``0`` means not part of a PK; values ``>= 1`` indicate PK membership
        (ADV-012: use ``>= 1``, not ``== 1``, to support composite PKs).

        Note (ADV-021): the ``primary_key`` field is present in SQLAlchemy's
        column dicts for SQLite but may be absent for PostgreSQL backends.
        Callers that need reliable PK detection must use
        :meth:`get_pk_constraint` and cross-reference the column name against
        the ``constrained_columns`` list.

        Args:
            table: Unquoted table name in the target schema.
            schema: PostgreSQL schema name. Defaults to ``"public"``.

        Returns:
            List of column descriptor dicts from SQLAlchemy reflection.
        """
        return cast(list[dict[str, Any]], self._inspector.get_columns(table, schema=schema))

    def get_foreign_keys(self, table: str, schema: str = "public") -> list[dict[str, Any]]:
        """Return foreign key metadata for the given table.

        Args:
            table: Unquoted table name in the target schema.
            schema: PostgreSQL schema name. Defaults to ``"public"``.

        Returns:
            List of FK descriptor dicts from SQLAlchemy reflection.
            Each dict contains ``constrained_columns``, ``referred_table``,
            and ``referred_columns`` keys at minimum.
        """
        return cast(list[dict[str, Any]], self._inspector.get_foreign_keys(table, schema=schema))

    def get_pk_constraint(self, table: str, schema: str = "public") -> dict[str, Any]:
        """Return the primary key constraint for the given table.

        Uses SQLAlchemy's ``Inspector.get_pk_constraint()`` which reliably
        returns PK column names regardless of the database backend — unlike
        ``get_columns()``, which may or may not include a ``primary_key`` key
        depending on the backend (ADV-021).

        Args:
            table: Unquoted table name in the target schema.
            schema: PostgreSQL schema name. Defaults to ``"public"``.

        Returns:
            A dict with at minimum a ``constrained_columns`` key containing
            a list of column name strings that form the primary key.  Returns
            ``{"constrained_columns": []}`` for tables with no PK constraint.
        """
        return cast(
            dict[str, Any],
            self._inspector.get_pk_constraint(table, schema=schema),
        )
