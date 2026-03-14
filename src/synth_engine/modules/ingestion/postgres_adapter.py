"""PostgreSQL ingestion adapter for the Conclave Engine.

Provides a read-only, streaming interface to a source PostgreSQL database.
Key security guarantees:

- **Pre-flight check**: Detects superuser status and write privileges
  (INSERT, UPDATE, DELETE) before any data access. Raises
  :class:`PrivilegeEscalationError` immediately on violation.
- **Server-side cursors**: ``stream_results=True`` + ``fetchmany()`` ensures
  large tables are never fully loaded into memory.
- **Table name allowlist**: All SQL referencing a caller-supplied table name
  is guarded by :meth:`PostgresIngestionAdapter._validate_table_name`, which
  checks the name against ``SchemaInspector.get_tables()`` before any SQL is
  constructed (ADV-013 compliance).
- **No f-string SQL**: Streaming uses reflected ``Table`` objects so that
  SQLAlchemy generates all SQL with correctly quoted identifiers.

Architecture note
-----------------
This module may only import from ``synth_engine.shared`` and the Python
standard library. Cross-module imports are forbidden by import-linter
contracts defined in ``pyproject.toml``.

CONSTITUTION Priority 0: Security — privilege check is the primary gate.
Task: P3-T3.1 — Target Ingestion Engine
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, MetaData, Table, create_engine, inspect, text

from synth_engine.modules.ingestion.validators import validate_connection_string


class PrivilegeEscalationError(Exception):
    """Raised when the ingestion user has write privileges on the source database.

    This exception is the hard stop for the pre-flight check: if the connected
    user can INSERT, UPDATE, or DELETE — or if they are a superuser — ingestion
    is refused entirely to protect source data integrity.
    """


class SchemaInspector:
    """Wraps SQLAlchemy ``inspect()`` to expose tables, columns, and FKs.

    Provides a thin, mockable façade over the SQLAlchemy reflection API so
    that adapter code never interacts with ``inspect()`` directly.

    Args:
        engine: A connected SQLAlchemy :class:`~sqlalchemy.Engine`.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_tables(self, schema: str = "public") -> list[str]:
        """Return a list of table names in the given schema.

        Args:
            schema: PostgreSQL schema name. Defaults to ``"public"``.

        Returns:
            Sorted list of table name strings visible to the current user.
        """
        inspector = inspect(self._engine)
        return inspector.get_table_names(schema=schema)

    def get_columns(self, table_name: str, schema: str = "public") -> list[dict[str, Any]]:
        """Return column metadata for the given table.

        Each dict contains at minimum ``name``, ``type``, ``nullable``, and
        ``primary_key`` keys.  The ``primary_key`` value is an integer: ``0``
        means not part of a PK; values ``>= 1`` indicate PK membership
        (ADV-012: use ``>= 1``, not ``== 1``, to support composite PKs).

        Args:
            table_name: Unquoted table name in the target schema.
            schema: PostgreSQL schema name. Defaults to ``"public"``.

        Returns:
            List of column descriptor dicts from SQLAlchemy reflection.
        """
        inspector = inspect(self._engine)
        return inspector.get_columns(table_name, schema=schema)  # type: ignore[return-value]

    def get_foreign_keys(self, table_name: str, schema: str = "public") -> list[dict[str, Any]]:
        """Return foreign key metadata for the given table.

        Args:
            table_name: Unquoted table name in the target schema.
            schema: PostgreSQL schema name. Defaults to ``"public"``.

        Returns:
            List of FK descriptor dicts from SQLAlchemy reflection.
        """
        inspector = inspect(self._engine)
        return inspector.get_foreign_keys(table_name, schema=schema)  # type: ignore[return-value]


class PostgresIngestionAdapter:
    """Read-only streaming adapter for a source PostgreSQL database.

    Validates the connection string, enforces a read-only pre-flight check,
    and provides a generator-based row streaming interface backed by
    server-side cursors.

    Args:
        connection_url: SQLAlchemy PostgreSQL connection URL. Must pass
            :func:`~synth_engine.modules.ingestion.validators.validate_connection_string`.

    Raises:
        ValueError: If the connection URL fails validation.
    """

    def __init__(self, connection_url: str) -> None:
        validate_connection_string(connection_url)
        self._engine: Engine = create_engine(connection_url)
        self._schema_inspector: SchemaInspector = SchemaInspector(self._engine)

    def preflight_check(self) -> None:
        """Execute SELECT 1 and verify the user lacks INSERT/UPDATE/DELETE privileges.

        The check proceeds in two stages:

        1. **Superuser detection**: ``SELECT current_setting('is_superuser')``
           returns ``'on'`` for superusers, who bypass the grants table entirely.
           A superuser result raises :class:`PrivilegeEscalationError` immediately.
        2. **Grants inspection**: ``information_schema.role_table_grants`` is
           queried for INSERT, UPDATE, or DELETE privileges granted to
           ``current_user``. Any result raises :class:`PrivilegeEscalationError`.

        Raises:
            PrivilegeEscalationError: If the user is a superuser or holds any
                write privilege (INSERT, UPDATE, DELETE) on any table in the
                target database.
            sqlalchemy.exc.SQLAlchemyError: If the database connection fails.
        """
        with self._engine.connect() as conn:
            # Stage 0: connectivity check — raises if connection is broken.
            conn.execute(text("SELECT 1"))  # nosec B608

            # Stage 1: Superuser detection.
            superuser_result = conn.execute(
                text("SELECT current_setting('is_superuser')")  # nosec B608
            )
            is_superuser: str = superuser_result.scalar_one()
            if is_superuser == "on":
                raise PrivilegeEscalationError(
                    "Ingestion refused: the connected user is a superuser. "
                    "Superusers bypass all privilege checks — use a dedicated "
                    "read-only account for ingestion."
                )

            # Stage 2: Write-privilege inspection via information_schema.
            grants_result = conn.execute(
                text(  # nosec B608
                    "SELECT privilege_type "
                    "FROM information_schema.role_table_grants "
                    "WHERE grantee = current_user "
                    "AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE')"
                )
            )
            rows = grants_result.fetchall()
            if rows:
                detected = ", ".join(str(row._mapping["privilege_type"]) for row in rows)
                raise PrivilegeEscalationError(
                    f"Ingestion refused: the connected user holds write "
                    f"privilege(s): {detected}. Only SELECT is permitted."
                )

    def _validate_table_name(self, table_name: str, schema: str = "public") -> None:
        """Validate that table_name exists in the schema (ADV-013).

        Checks the supplied name against the allowlist returned by
        ``SchemaInspector.get_tables()`` before any SQL referencing the name
        is constructed.

        Args:
            table_name: The table name to validate.
            schema: The schema to check against. Defaults to ``"public"``.

        Raises:
            ValueError: If ``table_name`` is not in the schema's table list.
        """
        allowed = self._schema_inspector.get_tables(schema=schema)
        if table_name not in allowed:
            raise ValueError(
                f"Table {table_name!r} not found in schema {schema!r}. Allowed tables: {allowed}"
            )

    def stream_table(
        self,
        table_name: str,
        schema: str = "public",
        batch_size: int = 1000,
    ) -> Generator[list[dict[str, Any]]]:
        """Yield batches of rows as dicts using a named server-side cursor.

        Never loads the full table into memory. Each batch is a list of
        ``dict`` objects mapping column name → value.

        Table name is validated against :meth:`get_schema_inspector` before
        any SQL is constructed (ADV-013 compliance). The reflected ``Table``
        object lets SQLAlchemy generate correctly quoted identifier SQL.

        Args:
            table_name: The table to stream. Must exist in ``schema``.
            schema: PostgreSQL schema name. Defaults to ``"public"``.
            batch_size: Number of rows to fetch per round-trip. Defaults to 1000.

        Yields:
            Batches of rows; each batch is ``list[dict[str, Any]]``.

        Raises:
            ValueError: If ``table_name`` is not in the allowed table list.
            sqlalchemy.exc.SQLAlchemyError: If a database error occurs.
        """
        self._validate_table_name(table_name, schema=schema)

        # Reflect the table so SQLAlchemy knows its columns and generates
        # a correct SELECT * with properly quoted identifiers.  The table
        # name is already validated against the allowlist (ADV-013).
        metadata = MetaData()
        tbl = Table(table_name, metadata, autoload_with=self._engine, schema=schema)
        stmt = tbl.select()

        with self._engine.connect() as conn:
            result = conn.execution_options(stream_results=True).execute(stmt)
            while batch := result.fetchmany(batch_size):
                yield [dict(row._mapping) for row in batch]

    def get_schema_inspector(self) -> SchemaInspector:
        """Return a :class:`SchemaInspector` for the connected database.

        Returns:
            A :class:`SchemaInspector` bound to this adapter's engine.
        """
        return self._schema_inspector
