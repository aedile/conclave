"""Unit tests for the PostgreSQL ingestion adapter.

These tests verify the adapter's privilege-check logic, schema inspection,
table name validation, and streaming interface using mocked connections.
They do NOT require a real PostgreSQL instance — see tests/integration/ for
live database tests.

CONSTITUTION Priority 0: Security — PrivilegeEscalationError enforces read-only access.
CONSTITUTION Priority 3: TDD — RED phase for P3-T3.1.
Task: P3-T3.1 — Target Ingestion Engine
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from synth_engine.modules.ingestion.postgres_adapter import (
    PostgresIngestionAdapter,
    PrivilegeEscalationError,
    SchemaInspector,
)

# ---------------------------------------------------------------------------
# PrivilegeEscalationError
# ---------------------------------------------------------------------------


class TestPrivilegeEscalationError:
    """Tests for :class:`PrivilegeEscalationError`."""

    def test_privilege_escalation_error_is_exception(self) -> None:
        """PrivilegeEscalationError must be a subclass of Exception."""
        assert issubclass(PrivilegeEscalationError, Exception)

    def test_privilege_escalation_error_can_be_raised(self) -> None:
        """PrivilegeEscalationError can be raised and caught as Exception."""
        with pytest.raises(PrivilegeEscalationError):
            raise PrivilegeEscalationError("write privilege detected")

    def test_privilege_escalation_error_message_preserved(self) -> None:
        """PrivilegeEscalationError preserves the message string."""
        msg = "INSERT privilege detected on table users"
        err = PrivilegeEscalationError(msg)
        assert str(err) == msg


# ---------------------------------------------------------------------------
# SchemaInspector
# ---------------------------------------------------------------------------


class TestSchemaInspector:
    """Tests for :class:`SchemaInspector`."""

    def test_schema_inspector_get_tables_returns_list(self) -> None:
        """get_tables() returns a list of table name strings from inspect()."""
        mock_engine = MagicMock()
        mock_inspect = MagicMock()
        mock_inspect.get_table_names.return_value = ["users", "orders", "products"]

        with patch(
            "synth_engine.modules.ingestion.postgres_adapter.inspect",
            return_value=mock_inspect,
        ):
            inspector = SchemaInspector(mock_engine)
            tables = inspector.get_tables()

        assert tables == ["users", "orders", "products"]
        assert isinstance(tables, list)

    def test_schema_inspector_get_columns_returns_list(self) -> None:
        """get_columns() returns a list of column dicts from inspect()."""
        mock_engine = MagicMock()
        mock_cols = [
            {"name": "id", "type": "INTEGER", "primary_key": 1, "nullable": False},
            {"name": "email", "type": "VARCHAR", "primary_key": 0, "nullable": True},
        ]
        mock_inspect = MagicMock()
        mock_inspect.get_columns.return_value = mock_cols

        with patch(
            "synth_engine.modules.ingestion.postgres_adapter.inspect",
            return_value=mock_inspect,
        ):
            inspector = SchemaInspector(mock_engine)
            cols = inspector.get_columns("users")

        assert cols == mock_cols
        assert isinstance(cols, list)

    def test_schema_inspector_get_columns_composite_pk(self) -> None:
        """get_columns() correctly includes all PK columns for composite PKs.

        ADV-012: primary_key >= 1 (not == 1) to support composite primary keys.
        """
        mock_engine = MagicMock()
        mock_cols = [
            {"name": "order_id", "type": "INTEGER", "primary_key": 1},
            {"name": "product_id", "type": "INTEGER", "primary_key": 2},
            {"name": "qty", "type": "INTEGER", "primary_key": 0},
        ]
        mock_inspect = MagicMock()
        mock_inspect.get_columns.return_value = mock_cols

        with patch(
            "synth_engine.modules.ingestion.postgres_adapter.inspect",
            return_value=mock_inspect,
        ):
            inspector = SchemaInspector(mock_engine)
            cols = inspector.get_columns("order_items")

        pk_cols = [c for c in cols if c["primary_key"] >= 1]
        assert len(pk_cols) == 2, "Both composite PK columns must be identified"

    def test_schema_inspector_get_foreign_keys_returns_list(self) -> None:
        """get_foreign_keys() returns a list of FK dicts from inspect()."""
        mock_engine = MagicMock()
        mock_fks = [
            {
                "constrained_columns": ["user_id"],
                "referred_table": "users",
                "referred_columns": ["id"],
            }
        ]
        mock_inspect = MagicMock()
        mock_inspect.get_foreign_keys.return_value = mock_fks

        with patch(
            "synth_engine.modules.ingestion.postgres_adapter.inspect",
            return_value=mock_inspect,
        ):
            inspector = SchemaInspector(mock_engine)
            fks = inspector.get_foreign_keys("orders")

        assert fks == mock_fks
        assert isinstance(fks, list)


# ---------------------------------------------------------------------------
# PostgresIngestionAdapter._validate_table_name (ADV-013)
# ---------------------------------------------------------------------------


class TestValidateTableName:
    """Tests for the internal table name allowlist check (ADV-013 compliance)."""

    def _make_adapter_with_tables(self, tables: list[str]) -> PostgresIngestionAdapter:
        """Build an adapter whose SchemaInspector returns ``tables``."""
        adapter = PostgresIngestionAdapter.__new__(PostgresIngestionAdapter)
        mock_engine = MagicMock()
        adapter._engine = mock_engine  # type: ignore[attr-defined]  # test-only direct assignment

        mock_inspector = MagicMock(spec=SchemaInspector)
        mock_inspector.get_tables.return_value = tables
        adapter._schema_inspector = mock_inspector  # type: ignore[attr-defined]

        return adapter

    def test_validate_table_name_raises_for_unknown_table(self) -> None:
        """_validate_table_name raises ValueError for a table not in the schema."""
        adapter = self._make_adapter_with_tables(["users", "orders"])
        with pytest.raises(ValueError, match="not found in schema"):
            adapter._validate_table_name("secrets")  # type: ignore[attr-defined]

    def test_validate_table_name_passes_for_known_table(self) -> None:
        """_validate_table_name passes silently for a known table."""
        adapter = self._make_adapter_with_tables(["users", "orders"])
        adapter._validate_table_name("users")  # type: ignore[attr-defined]  # must not raise


# ---------------------------------------------------------------------------
# PostgresIngestionAdapter.stream_table
# ---------------------------------------------------------------------------


def _make_stream_conn_mock(rows: list[MagicMock], batch_size: int) -> MagicMock:
    """Build a mock connection whose execute returns ``rows`` in batches.

    Args:
        rows: The row mocks to be returned across batches.
        batch_size: Simulated batch size for fetchmany calls.

    Returns:
        A context-manager-compatible MagicMock connection.
    """
    # Split rows into batches plus a trailing empty list to signal end of stream.
    batches: list[list[MagicMock]] = []
    for i in range(0, len(rows), batch_size):
        batches.append(rows[i : i + batch_size])
    batches.append([])  # end sentinel

    mock_result = MagicMock()
    mock_result.fetchmany.side_effect = batches

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execution_options.return_value = mock_conn
    mock_conn.execute.return_value = mock_result
    return mock_conn


class TestStreamTable:
    """Tests for :meth:`PostgresIngestionAdapter.stream_table`."""

    def test_stream_table_validates_table_name(self) -> None:
        """stream_table raises ValueError for a table not in the schema (ADV-013)."""
        with patch("synth_engine.modules.ingestion.postgres_adapter.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine

            adapter = PostgresIngestionAdapter("postgresql+psycopg2://user:pass@localhost:5432/db")

            mock_inspector = MagicMock(spec=SchemaInspector)
            mock_inspector.get_tables.return_value = ["users"]
            adapter._schema_inspector = mock_inspector  # type: ignore[attr-defined]

            with pytest.raises(ValueError, match="not found in schema"):
                list(adapter.stream_table("non_existent_table"))

    def test_stream_table_yields_batches(self) -> None:
        """stream_table yields list[dict] batches from the server-side cursor.

        Table reflection (MetaData/Table) is patched so that stream_table
        never tries to connect to a real database during unit testing.
        """
        with (
            patch("synth_engine.modules.ingestion.postgres_adapter.create_engine") as mock_create,
            patch("synth_engine.modules.ingestion.postgres_adapter.Table") as mock_table_cls,
            patch("synth_engine.modules.ingestion.postgres_adapter.MetaData"),
        ):
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine

            # Make Table(...).select() return a sentinel statement object.
            mock_tbl = MagicMock()
            mock_stmt = MagicMock()
            mock_tbl.select.return_value = mock_stmt
            mock_table_cls.return_value = mock_tbl

            adapter = PostgresIngestionAdapter("postgresql+psycopg2://user:pass@localhost:5432/db")

            mock_inspector = MagicMock(spec=SchemaInspector)
            mock_inspector.get_tables.return_value = ["users"]
            adapter._schema_inspector = mock_inspector  # type: ignore[attr-defined]

            row1 = MagicMock()
            row1._mapping = {"id": 1, "name": "Alice"}
            row2 = MagicMock()
            row2._mapping = {"id": 2, "name": "Bob"}

            mock_conn = _make_stream_conn_mock([row1, row2], batch_size=2)
            mock_engine.connect.return_value = mock_conn

            batches = list(adapter.stream_table("users", batch_size=2))

        assert len(batches) == 1  # one non-empty batch
        assert batches[0] == [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]


# ---------------------------------------------------------------------------
# PostgresIngestionAdapter.preflight_check
# ---------------------------------------------------------------------------


class TestPreflightCheck:
    """Tests for :meth:`PostgresIngestionAdapter.preflight_check`."""

    def _make_adapter(self) -> tuple[PostgresIngestionAdapter, MagicMock]:
        """Create an adapter with a mocked engine, returning (adapter, mock_engine)."""
        with patch("synth_engine.modules.ingestion.postgres_adapter.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine
            adapter = PostgresIngestionAdapter("postgresql+psycopg2://user:pass@localhost:5432/db")
        return adapter, mock_engine

    def _build_conn_mock(
        self,
        is_superuser: str,
        write_grants: list[str],
    ) -> MagicMock:
        """Build a context-manager-compatible mock connection.

        Args:
            is_superuser: Value for ``current_setting('is_superuser')``.
            write_grants: List of privilege_type strings returned by
                ``information_schema.role_table_grants``.

        Returns:
            A MagicMock that behaves as a context-managed connection.
        """
        # superuser scalar result
        superuser_result = MagicMock()
        superuser_result.scalar_one.return_value = is_superuser

        # grants result — build row mocks
        grant_rows: list[MagicMock] = []
        for priv in write_grants:
            row = MagicMock()
            row._mapping = {"privilege_type": priv}
            grant_rows.append(row)

        grants_result = MagicMock()
        grants_result.fetchall.return_value = grant_rows

        # SELECT 1 result
        select_one_result = MagicMock()
        select_one_result.scalar_one.return_value = 1

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        # execute is called in order: SELECT 1, superuser check, grants check
        mock_conn.execute.side_effect = [
            select_one_result,
            superuser_result,
            grants_result,
        ]
        return mock_conn

    def test_preflight_raises_for_superuser(self) -> None:
        """preflight_check raises PrivilegeEscalationError when user is superuser."""
        adapter, mock_engine = self._make_adapter()
        mock_conn = self._build_conn_mock(is_superuser="on", write_grants=[])
        mock_engine.connect.return_value = mock_conn

        with pytest.raises(PrivilegeEscalationError, match="superuser"):
            adapter.preflight_check()

    def test_preflight_raises_when_write_grants_exist(self) -> None:
        """preflight_check raises PrivilegeEscalationError when INSERT grant is present."""
        adapter, mock_engine = self._make_adapter()
        mock_conn = self._build_conn_mock(is_superuser="off", write_grants=["INSERT"])
        mock_engine.connect.return_value = mock_conn

        with pytest.raises(PrivilegeEscalationError, match="INSERT"):
            adapter.preflight_check()

    def test_preflight_passes_when_readonly(self) -> None:
        """preflight_check passes silently when user has no write grants and is not superuser."""
        adapter, mock_engine = self._make_adapter()
        mock_conn = self._build_conn_mock(is_superuser="off", write_grants=[])
        mock_engine.connect.return_value = mock_conn

        # Must not raise
        adapter.preflight_check()


# ---------------------------------------------------------------------------
# PostgresIngestionAdapter.get_schema_inspector
# ---------------------------------------------------------------------------


class TestGetSchemaInspector:
    """Tests for :meth:`PostgresIngestionAdapter.get_schema_inspector`."""

    def test_get_schema_inspector_returns_schema_inspector(self) -> None:
        """get_schema_inspector() returns a SchemaInspector instance."""
        with patch("synth_engine.modules.ingestion.postgres_adapter.create_engine"):
            adapter = PostgresIngestionAdapter("postgresql+psycopg2://user:pass@localhost:5432/db")
            inspector = adapter.get_schema_inspector()

        assert isinstance(inspector, SchemaInspector)


# ---------------------------------------------------------------------------
# Type annotation verification
# ---------------------------------------------------------------------------


def test_stream_table_is_generator() -> None:
    """stream_table return type is a Generator (structural check).

    Table reflection (MetaData/Table) is patched so that stream_table
    never tries to connect to a real database during unit testing.
    """
    with (
        patch("synth_engine.modules.ingestion.postgres_adapter.create_engine"),
        patch("synth_engine.modules.ingestion.postgres_adapter.Table") as mock_table_cls,
        patch("synth_engine.modules.ingestion.postgres_adapter.MetaData"),
    ):
        mock_tbl = MagicMock()
        mock_stmt = MagicMock()
        mock_tbl.select.return_value = mock_stmt
        mock_table_cls.return_value = mock_tbl

        adapter = PostgresIngestionAdapter("postgresql+psycopg2://user:pass@localhost:5432/db")

    mock_inspector = MagicMock(spec=SchemaInspector)
    mock_inspector.get_tables.return_value = ["users"]
    adapter._schema_inspector = mock_inspector  # type: ignore[attr-defined]

    row = MagicMock()
    row._mapping = {"id": 1}
    mock_conn = _make_stream_conn_mock([row], batch_size=1)
    adapter._engine.connect.return_value = mock_conn  # type: ignore[attr-defined]

    result = adapter.stream_table("users")
    assert inspect.isgenerator(result)
