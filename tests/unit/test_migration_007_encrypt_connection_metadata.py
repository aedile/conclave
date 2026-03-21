"""Unit tests for Alembic migration 007: connection metadata encryption (T39.4).

Verifies that:
  1. upgrade() encrypts existing plaintext host, database, and schema_name values
     so that stored values are Fernet tokens (not readable plaintext).
  2. downgrade() decrypts Fernet tokens back to the original plaintext values.
  3. Empty table path: upgrade() and downgrade() are no-ops when the connection
     table has no rows (no Fernet key required).
  4. None-guarded rows are skipped gracefully in both directions.

These tests use an in-memory SQLite engine and mock the ALE Fernet key via
the ``ALE_KEY`` environment variable so no vault is required.

No external services are required.  These are pure unit tests.

CONSTITUTION Priority 0: Security — sensitive connection fields must be
    encrypted at rest.
CONSTITUTION Priority 4: Comprehensive testing — migration data path must
    have explicit coverage.
Task: T39.4 — Encrypt Connection Metadata with ALE
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    Path(__file__).parent.parent.parent
    / "alembic"
    / "versions"
    / "007_encrypt_connection_metadata.py"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATE_CONNECTION_TABLE = """
CREATE TABLE IF NOT EXISTS connection (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    host        TEXT NOT NULL,
    port        INTEGER NOT NULL,
    "database"  TEXT NOT NULL,
    schema_name TEXT NOT NULL
)
"""

# Nullable variant — used by NULL-guard tests to bypass SQLite NOT NULL
# enforcement so we can simulate rows that existed before the NOT NULL
# constraint was added (e.g. manual inserts or future schema changes).
_CREATE_CONNECTION_TABLE_NULLABLE = """
CREATE TABLE IF NOT EXISTS connection (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    host        TEXT,
    port        INTEGER,
    "database"  TEXT,
    schema_name TEXT
)
"""


def _make_engine() -> Any:
    """Return a fresh in-memory SQLite engine with the connection table.

    Returns:
        A SQLAlchemy engine with the connection table created.
    """
    engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(sa.text(_CREATE_CONNECTION_TABLE))
        conn.commit()
    return engine


def _make_nullable_engine() -> Any:
    """Return a fresh in-memory SQLite engine with nullable connection table.

    The table schema omits NOT NULL constraints so that NULL values can be
    inserted directly via raw SQL, simulating rows that pre-date strict
    constraint enforcement (or were created by manual database operations).

    Returns:
        A SQLAlchemy engine with a nullable connection table created.
    """
    engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(sa.text(_CREATE_CONNECTION_TABLE_NULLABLE))
        conn.commit()
    return engine


def _insert_row(
    conn: Any,
    *,
    host: str,
    database: str,
    schema_name: str,
) -> str:
    """Insert a single plaintext row and return its id.

    Args:
        conn: An active SQLAlchemy connection.
        host: Plaintext host value.
        database: Plaintext database value.
        schema_name: Plaintext schema_name value.

    Returns:
        The UUID string used as the row's primary key.
    """
    row_id = str(uuid.uuid4())
    conn.execute(
        sa.text(
            'INSERT INTO connection (id, name, host, port, "database", schema_name) '
            "VALUES (:id, :name, :host, :port, :database, :schema_name)"
        ),
        {
            "id": row_id,
            "name": "test-conn",
            "host": host,
            "port": 5432,
            "database": database,
            "schema_name": schema_name,
        },
    )
    conn.commit()
    return row_id


def _insert_null_row(conn: Any) -> str:
    """Insert a row with NULL values for host, database, and schema_name.

    Bypasses ORM-level NOT NULL constraints by using a raw SQL insert against
    a table created without NOT NULL clauses (see ``_make_nullable_engine``).
    This simulates rows that could exist due to manual database operations or
    pre-constraint-era data.

    Args:
        conn: An active SQLAlchemy connection backed by a nullable-schema engine.

    Returns:
        The UUID string used as the row's primary key.
    """
    row_id = str(uuid.uuid4())
    conn.execute(
        sa.text(
            'INSERT INTO connection (id, name, host, port, "database", schema_name) '
            "VALUES (:id, :name, NULL, 5432, NULL, NULL)"
        ),
        {"id": row_id, "name": "null-test-conn"},
    )
    conn.commit()
    return row_id


def _fetch_row(conn: Any, row_id: str) -> Any:
    """Return the connection row as a named-tuple-style row object.

    Args:
        conn: An active SQLAlchemy connection.
        row_id: The primary key of the row to fetch.

    Returns:
        A SQLAlchemy Row with host, database, and schema_name attributes.
    """
    return conn.execute(
        sa.text('SELECT host, "database", schema_name FROM connection WHERE id = :id'),
        {"id": row_id},
    ).fetchone()


def _is_fernet_token(value: str) -> bool:
    """Return True if *value* looks like a Fernet token (base64url, starts with 'gAAA').

    Fernet tokens always start with the bytes ``b"\\x80"`` which base64url-encode
    to ``"gAAA"`` in the first four characters.

    Args:
        value: The string value to inspect.

    Returns:
        True if the value matches the Fernet token format.
    """
    return value.startswith("gAAA") and len(value) > 40


def _import_migration_007() -> Any:
    """Import the migration module from alembic/versions/007_encrypt_connection_metadata.py.

    The module name contains a leading digit which makes it invalid as a
    Python identifier, so we use ``importlib`` for direct file loading.

    Returns:
        The loaded migration module.
    """
    spec = importlib.util.spec_from_file_location("migration_007", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fernet_key() -> str:
    """Generate a fresh Fernet key for test isolation.

    Returns:
        A URL-safe base64-encoded Fernet key string.
    """
    return Fernet.generate_key().decode()


@pytest.fixture
def fernet_instance(fernet_key: str) -> Fernet:
    """Return a Fernet instance using the test key.

    Args:
        fernet_key: The key fixture.

    Returns:
        A configured Fernet instance.
    """
    return Fernet(fernet_key.encode())


# ---------------------------------------------------------------------------
# Test: empty table — no-op paths
# ---------------------------------------------------------------------------


class TestMigration007EmptyTable:
    """upgrade() and downgrade() on an empty table must be no-ops."""

    def test_upgrade_empty_table_is_noop(self) -> None:
        """upgrade() on an empty connection table must succeed without ALE key."""
        engine = _make_engine()
        module = _import_migration_007()
        mock_op = MagicMock()

        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with patch.object(module, "op", mock_op):
                module.upgrade()  # Must not raise — no rows, no ALE key needed.

    def test_downgrade_empty_table_is_noop(self) -> None:
        """downgrade() on an empty connection table must succeed without ALE key."""
        engine = _make_engine()
        module = _import_migration_007()
        mock_op = MagicMock()

        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with patch.object(module, "op", mock_op):
                module.downgrade()  # Must not raise — no rows, no ALE key needed.


# ---------------------------------------------------------------------------
# Test: upgrade encrypts plaintext rows
# ---------------------------------------------------------------------------


class TestMigration007Upgrade:
    """upgrade() must convert plaintext values to Fernet tokens."""

    def test_upgrade_encrypts_host(self, fernet_instance: Fernet) -> None:
        """After upgrade(), the stored host value must be a Fernet token."""
        engine = _make_engine()
        module = _import_migration_007()

        with engine.connect() as conn:
            row_id = _insert_row(
                conn,
                host="prod-postgres.internal",
                database="mydb",
                schema_name="public",
            )

        mock_op = MagicMock()
        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with (
                patch.object(module, "op", mock_op),
                patch.object(module, "_get_fernet", return_value=fernet_instance),
            ):
                module.upgrade()
            bind.commit()

        with engine.connect() as conn:
            row = _fetch_row(conn, row_id)

        assert row is not None
        assert _is_fernet_token(row.host), (
            f"After upgrade(), host must be a Fernet token, got: {row.host!r}"
        )
        assert row.host != "prod-postgres.internal"

    def test_upgrade_encrypts_database(self, fernet_instance: Fernet) -> None:
        """After upgrade(), the stored database value must be a Fernet token."""
        engine = _make_engine()
        module = _import_migration_007()

        with engine.connect() as conn:
            row_id = _insert_row(
                conn,
                host="localhost",
                database="sensitive_db",
                schema_name="public",
            )

        mock_op = MagicMock()
        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with (
                patch.object(module, "op", mock_op),
                patch.object(module, "_get_fernet", return_value=fernet_instance),
            ):
                module.upgrade()
            bind.commit()

        with engine.connect() as conn:
            row = _fetch_row(conn, row_id)

        assert row is not None
        assert _is_fernet_token(row.database), (
            f"After upgrade(), database must be a Fernet token, got: {row.database!r}"
        )
        assert row.database != "sensitive_db"

    def test_upgrade_encrypts_schema_name(self, fernet_instance: Fernet) -> None:
        """After upgrade(), the stored schema_name value must be a Fernet token."""
        engine = _make_engine()
        module = _import_migration_007()

        with engine.connect() as conn:
            row_id = _insert_row(
                conn,
                host="localhost",
                database="mydb",
                schema_name="analytics",
            )

        mock_op = MagicMock()
        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with (
                patch.object(module, "op", mock_op),
                patch.object(module, "_get_fernet", return_value=fernet_instance),
            ):
                module.upgrade()
            bind.commit()

        with engine.connect() as conn:
            row = _fetch_row(conn, row_id)

        assert row is not None
        assert _is_fernet_token(row.schema_name), (
            f"After upgrade(), schema_name must be a Fernet token, got: {row.schema_name!r}"
        )
        assert row.schema_name != "analytics"


# ---------------------------------------------------------------------------
# Test: downgrade decrypts back to plaintext
# ---------------------------------------------------------------------------


class TestMigration007Downgrade:
    """downgrade() must restore Fernet tokens to original plaintext."""

    def test_downgrade_restores_plaintext_host(self, fernet_instance: Fernet) -> None:
        """After upgrade() + downgrade(), host must equal the original plaintext."""
        engine = _make_engine()
        module = _import_migration_007()
        original_host = "prod-postgres.internal"

        with engine.connect() as conn:
            row_id = _insert_row(
                conn,
                host=original_host,
                database="mydb",
                schema_name="public",
            )

        mock_op = MagicMock()
        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with (
                patch.object(module, "op", mock_op),
                patch.object(module, "_get_fernet", return_value=fernet_instance),
            ):
                module.upgrade()
            bind.commit()

        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with (
                patch.object(module, "op", mock_op),
                patch.object(module, "_get_fernet", return_value=fernet_instance),
            ):
                module.downgrade()
            bind.commit()

        with engine.connect() as conn:
            row = _fetch_row(conn, row_id)

        assert row is not None
        assert row.host == original_host, (
            f"After downgrade(), host must be restored to '{original_host}', got: {row.host!r}"
        )

    def test_downgrade_restores_plaintext_database(self, fernet_instance: Fernet) -> None:
        """After upgrade() + downgrade(), database must equal the original plaintext."""
        engine = _make_engine()
        module = _import_migration_007()
        original_db = "warehouse_prod"

        with engine.connect() as conn:
            row_id = _insert_row(
                conn,
                host="localhost",
                database=original_db,
                schema_name="public",
            )

        mock_op = MagicMock()
        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with (
                patch.object(module, "op", mock_op),
                patch.object(module, "_get_fernet", return_value=fernet_instance),
            ):
                module.upgrade()
            bind.commit()

        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with (
                patch.object(module, "op", mock_op),
                patch.object(module, "_get_fernet", return_value=fernet_instance),
            ):
                module.downgrade()
            bind.commit()

        with engine.connect() as conn:
            row = _fetch_row(conn, row_id)

        assert row is not None
        assert row.database == original_db, (
            f"After downgrade(), database must be restored to '{original_db}', "
            f"got: {row.database!r}"
        )

    def test_downgrade_restores_plaintext_schema_name(self, fernet_instance: Fernet) -> None:
        """After downgrade(), schema_name must equal the original plaintext."""
        engine = _make_engine()
        module = _import_migration_007()
        original_schema = "finance"

        with engine.connect() as conn:
            row_id = _insert_row(
                conn,
                host="localhost",
                database="mydb",
                schema_name=original_schema,
            )

        mock_op = MagicMock()
        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with (
                patch.object(module, "op", mock_op),
                patch.object(module, "_get_fernet", return_value=fernet_instance),
            ):
                module.upgrade()
            bind.commit()

        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with (
                patch.object(module, "op", mock_op),
                patch.object(module, "_get_fernet", return_value=fernet_instance),
            ):
                module.downgrade()
            bind.commit()

        with engine.connect() as conn:
            row = _fetch_row(conn, row_id)

        assert row is not None
        assert row.schema_name == original_schema, (
            f"After downgrade(), schema_name must be restored to '{original_schema}', "
            f"got: {row.schema_name!r}"
        )


# ---------------------------------------------------------------------------
# Test: revision metadata
# ---------------------------------------------------------------------------


class TestMigration007Metadata:
    """Migration 007 must declare correct revision and chain from 006."""

    def test_revision_is_007(self) -> None:
        """Migration file must declare revision = '007'."""
        module = _import_migration_007()
        assert module.revision == "007", f"Expected revision '007', got {module.revision!r}"

    def test_down_revision_is_006(self) -> None:
        """Migration file must declare down_revision = '006'."""
        module = _import_migration_007()
        assert module.down_revision == "006", (
            f"Expected down_revision '006', got {module.down_revision!r}"
        )

    def test_intentional_import_comment_present(self) -> None:
        """The intentional-import comment must be present to document the coupling."""
        content = _MIGRATION_PATH.read_text(encoding="utf-8")
        assert "intentionally imports synth_engine" in content, (
            "Migration 007 must contain the intentional-import comment explaining "
            "why it imports from synth_engine (unlike DDL-only migrations 001-006)."
        )


# ---------------------------------------------------------------------------
# Test: NULL guard — rows with NULL fields are skipped gracefully
# ---------------------------------------------------------------------------


class TestMigration007NullGuard:
    """upgrade() and downgrade() must skip rows where any encrypted field is NULL.

    Columns have been NOT NULL since migration 002, but the guard protects
    against manual inserts or future constraint changes.  The NULL row must
    be left untouched — its values must remain NULL after both operations.

    A nullable-schema engine is used so raw SQL can insert NULL values
    without triggering SQLite NOT NULL enforcement.
    """

    def test_upgrade_skips_null_row_without_raising(self, fernet_instance: Fernet) -> None:
        """upgrade() must not raise when a row has NULL host/database/schema_name."""
        engine = _make_nullable_engine()
        module = _import_migration_007()

        with engine.connect() as conn:
            null_id = _insert_null_row(conn)

        mock_op = MagicMock()
        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with (
                patch.object(module, "op", mock_op),
                patch.object(module, "_get_fernet", return_value=fernet_instance),
            ):
                # Must not raise even though the row has NULL encrypted fields.
                module.upgrade()
            bind.commit()

        with engine.connect() as conn:
            row = _fetch_row(conn, null_id)

        assert row is not None
        assert row.host is None, f"upgrade() must leave NULL host untouched, got: {row.host!r}"
        assert row.database is None, (
            f"upgrade() must leave NULL database untouched, got: {row.database!r}"
        )
        assert row.schema_name is None, (
            f"upgrade() must leave NULL schema_name untouched, got: {row.schema_name!r}"
        )

    def test_downgrade_skips_null_row_without_raising(self, fernet_instance: Fernet) -> None:
        """downgrade() must not raise when a row has NULL host/database/schema_name."""
        engine = _make_nullable_engine()
        module = _import_migration_007()

        with engine.connect() as conn:
            null_id = _insert_null_row(conn)

        mock_op = MagicMock()
        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with (
                patch.object(module, "op", mock_op),
                patch.object(module, "_get_fernet", return_value=fernet_instance),
            ):
                # Must not raise even though the row has NULL encrypted fields.
                module.downgrade()
            bind.commit()

        with engine.connect() as conn:
            row = _fetch_row(conn, null_id)

        assert row is not None
        assert row.host is None, f"downgrade() must leave NULL host untouched, got: {row.host!r}"
        assert row.database is None, (
            f"downgrade() must leave NULL database untouched, got: {row.database!r}"
        )
        assert row.schema_name is None, (
            f"downgrade() must leave NULL schema_name untouched, got: {row.schema_name!r}"
        )

    def test_upgrade_encrypts_non_null_row_alongside_null_row(
        self, fernet_instance: Fernet
    ) -> None:
        """upgrade() must encrypt the non-NULL row and leave the NULL row untouched.

        When the table contains both a NULL row and a valid row, the NULL row
        must be skipped while the valid row is fully encrypted.
        """
        engine = _make_nullable_engine()
        module = _import_migration_007()

        with engine.connect() as conn:
            null_id = _insert_null_row(conn)
            valid_id = _insert_row(
                conn,
                host="db.internal",
                database="prod_db",
                schema_name="public",
            )

        mock_op = MagicMock()
        with engine.connect() as bind:
            mock_op.get_bind.return_value = bind
            with (
                patch.object(module, "op", mock_op),
                patch.object(module, "_get_fernet", return_value=fernet_instance),
            ):
                module.upgrade()
            bind.commit()

        with engine.connect() as conn:
            null_row = _fetch_row(conn, null_id)
            valid_row = _fetch_row(conn, valid_id)

        assert null_row is not None
        assert null_row.host is None, (
            f"upgrade() must leave NULL host untouched, got: {null_row.host!r}"
        )
        assert null_row.database is None, (
            f"upgrade() must leave NULL database untouched, got: {null_row.database!r}"
        )
        assert null_row.schema_name is None, (
            f"upgrade() must leave NULL schema_name untouched, got: {null_row.schema_name!r}"
        )

        assert valid_row is not None
        assert _is_fernet_token(valid_row.host), (
            f"upgrade() must encrypt non-NULL host, got: {valid_row.host!r}"
        )
        assert _is_fernet_token(valid_row.database), (
            f"upgrade() must encrypt non-NULL database, got: {valid_row.database!r}"
        )
        assert _is_fernet_token(valid_row.schema_name), (
            f"upgrade() must encrypt non-NULL schema_name, got: {valid_row.schema_name!r}"
        )
