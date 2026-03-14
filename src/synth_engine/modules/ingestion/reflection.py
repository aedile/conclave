"""Schema reflection module for the Conclave Engine ingestion pipeline.

Reflects a PostgreSQL database schema into a :class:`DirectedAcyclicGraph`
using SQLAlchemy's ``inspect()`` API.  Only explicit foreign-key relationships
defined in the database schema are represented as edges; virtual or
user-inferred FK mappings are deferred (see ADR-0013).

Architecture note
-----------------
This module may only import from ``synth_engine.modules.ingestion`` (sibling
files) and the Python standard library.  Cross-module imports are forbidden by
import-linter contracts defined in ``pyproject.toml``.

ADV-012 compliance:
- ``get_columns()`` passes SQLAlchemy's ``primary_key`` integer values through
  unchanged.  PK columns have ``primary_key >= 1``; composite PKs use
  incrementing integers (1, 2, ...).  Callers MUST use ``>= 1``, not ``== 1``,
  to identify PK membership.

ADR-0013: Relational Mapping DAG and Topological Sort Design.
CONSTITUTION Priority 0: Security -- no external calls, no PII exposure.
Task: P3-T3.2 -- Relational Mapping & Topological Sort
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, inspect

from synth_engine.modules.ingestion.graph import DirectedAcyclicGraph


class SchemaReflector:
    """Reflects a PostgreSQL schema into a :class:`DirectedAcyclicGraph`.

    Uses SQLAlchemy's ``inspect()`` to extract tables, columns, data types,
    and explicit foreign keys from the connected database.  The resulting
    DAG can be topologically sorted to determine the correct table processing
    order for synthetic data generation.

    Only FK relationships explicitly defined in the database schema are
    represented as DAG edges.  Virtual FK support (user-defined mappings) is
    deferred -- see ADR-0013.

    Args:
        engine: A connected SQLAlchemy :class:`~sqlalchemy.Engine`.

    Example::

        engine = create_engine("postgresql+psycopg2://user:pw@host/db")
        reflector = SchemaReflector(engine)
        dag = reflector.reflect()
        order = dag.topological_sort()
    """

    def __init__(self, engine: Engine) -> None:
        """Initialise with a SQLAlchemy engine.

        Args:
            engine: A connected SQLAlchemy :class:`~sqlalchemy.Engine`.
        """
        self._engine = engine

    def reflect(self, schema: str = "public") -> DirectedAcyclicGraph:
        """Build and return a DAG from the connected database schema.

        Iterates over all tables in the given schema, registers each as a
        node, then adds directed edges for each explicit foreign key
        relationship (``referred_table -> constrained_table``).

        Args:
            schema: PostgreSQL schema name to reflect. Defaults to
                ``"public"``.

        Returns:
            A :class:`DirectedAcyclicGraph` with one node per table and one
            edge per explicit FK constraint, in the direction
            ``parent -> child``.
        """
        dag = DirectedAcyclicGraph()
        tables = self.get_tables(schema=schema)

        for table in tables:
            dag.add_node(table)

        for table in tables:
            for fk in self.get_foreign_keys(table, schema=schema):
                parent = fk["referred_table"]
                dag.add_edge(parent, table)

        return dag

    def get_tables(self, schema: str = "public") -> list[str]:
        """Return a list of table names in the given schema.

        Args:
            schema: PostgreSQL schema name. Defaults to ``"public"``.

        Returns:
            List of table name strings visible to the current user.
        """
        inspector = inspect(self._engine)
        return inspector.get_table_names(schema=schema)

    def get_columns(self, table: str, schema: str = "public") -> list[dict[str, Any]]:
        """Return column metadata for the given table.

        Each dict contains at minimum ``name``, ``type``, ``nullable``, and
        ``primary_key`` keys.  The ``primary_key`` value is an integer:
        ``0`` means not part of a PK; values ``>= 1`` indicate PK membership
        (ADV-012: use ``>= 1``, not ``== 1``, to support composite PKs).

        Args:
            table: Unquoted table name in the target schema.
            schema: PostgreSQL schema name. Defaults to ``"public"``.

        Returns:
            List of column descriptor dicts from SQLAlchemy reflection.
        """
        inspector = inspect(self._engine)
        return inspector.get_columns(table, schema=schema)  # type: ignore[return-value]

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
        inspector = inspect(self._engine)
        return inspector.get_foreign_keys(table, schema=schema)  # type: ignore[return-value]
