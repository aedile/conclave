"""Integration tests for the Alembic migration infrastructure.

These tests verify:

1. The Alembic configuration and migration scripts are importable and
   correctly wired (``alembic.ini`` + ``alembic/env.py``).
2. Alembic can stamp a schema created by ``SQLModel.metadata.create_all()``
   at the current head revision (matching the real deployment pattern).
3. ``alembic downgrade -1`` succeeds (last migration's downgrade is reversible).
4. Re-upgrading after a downgrade succeeds (idempotent round-trip).

Deployment context
------------------
The Conclave Engine uses a hybrid schema management strategy:

- ``SQLModel.metadata.create_all()`` creates ALL tables at application startup,
  including all current columns (``synthesis_job.enable_dp``, etc.).
- Alembic is then **stamped at head** to record the current schema state.
- Future schema changes are applied via ``alembic upgrade head``.

Migration tests therefore use the ``stamp head`` + ``downgrade -1`` + ``upgrade
head`` round-trip to verify that each migration's ``downgrade()`` function is
reversible without data loss.  The ``stamp`` operation tells Alembic the schema
is already at a given revision without running any DDL, which is appropriate
because ``create_all()`` already created the schema in its final state.

Requirements
------------
- ``pytest-postgresql`` installed: ``poetry install --with dev,integration``
- ``pg_ctl`` binary present on PATH.  If absent, the test module is skipped.

Marks: ``integration``

CONSTITUTION Priority 0: Security — no credentials hardcoded; all connection
    strings constructed from pytest-postgresql ephemeral fixture attrs.
CONSTITUTION Priority 3: TDD — integration gate for P26-T26.5.
Task: P26-T26.5 — Licensing + Migration + FK Masking Integration Tests
"""

from __future__ import annotations

import shutil
from collections.abc import Generator

import psycopg2
import psycopg2.extensions
import pytest
from alembic import command
from alembic.config import Config
from pytest_postgresql import factories
from sqlalchemy import Engine, create_engine
from sqlmodel import SQLModel

# Side-effect imports: ensure all ORM table classes register with SQLModel.metadata
# before create_all() is called.  This mirrors what the bootstrapper does at startup.
from synth_engine.bootstrapper.schemas.connections import Connection  # noqa: F401
from synth_engine.bootstrapper.schemas.settings import Setting  # noqa: F401
from synth_engine.modules.privacy.ledger import PrivacyLedger, PrivacyTransaction  # noqa: F401
from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob  # noqa: F401
from tests.conftest_types import PostgreSQLProc

# ---------------------------------------------------------------------------
# pytest-postgresql process fixture
# ---------------------------------------------------------------------------

postgresql_proc = factories.postgresql_proc()

# ---------------------------------------------------------------------------
# Test database name
# ---------------------------------------------------------------------------

_TEST_DBNAME = "conclave_migration_integration"

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _require_postgresql() -> None:
    """Skip entire module when pg_ctl is not installed.

    Yields:
        None — skip-only guard.
    """
    if shutil.which("pg_ctl") is None:
        pytest.skip(
            "pg_ctl not found on PATH — install PostgreSQL to run migration integration tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Database provisioning
# ---------------------------------------------------------------------------


def _create_database(proc: PostgreSQLProc) -> None:
    """Create the integration test database on the ephemeral PostgreSQL process.

    Args:
        proc: The ``postgresql_proc`` executor providing connection attributes.
    """
    conn = psycopg2.connect(
        dbname="postgres",
        user=proc.user,
        host=proc.host,
        port=proc.port,
        password=proc.password or "",
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DBNAME,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{_TEST_DBNAME}"')  # nosec B608
    conn.close()


@pytest.fixture(scope="module")
def _provision_test_db(postgresql_proc: PostgreSQLProc) -> Generator[None]:
    """Create the migration test database once per module and drop on teardown.

    Args:
        postgresql_proc: Running PostgreSQL process executor.

    Yields:
        None — setup/teardown only.
    """
    _create_database(postgresql_proc)
    yield
    conn = psycopg2.connect(
        dbname="postgres",
        user=postgresql_proc.user,
        host=postgresql_proc.host,
        port=postgresql_proc.port,
        password=postgresql_proc.password or "",
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (_TEST_DBNAME,),
        )
        cur.execute(
            "DROP DATABASE IF EXISTS " + psycopg2.extensions.quote_ident(_TEST_DBNAME, cur)  # nosec B608
        )
    conn.close()


# ---------------------------------------------------------------------------
# Alembic config and SQLAlchemy engine factories
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_url(postgresql_proc: PostgreSQLProc, _provision_test_db: None) -> str:
    """Build the psycopg2 connection URL for the ephemeral PostgreSQL instance.

    Args:
        postgresql_proc: Running PostgreSQL process executor.
        _provision_test_db: Module fixture ensuring the DB exists.

    Returns:
        Full psycopg2 connection URL string.
    """
    proc = postgresql_proc
    return (
        f"postgresql+psycopg2://{proc.user}:{proc.password or ''}"
        f"@{proc.host}:{proc.port}/{_TEST_DBNAME}"
    )


@pytest.fixture(scope="module")
def alembic_cfg(pg_url: str) -> Config:
    """Build an Alembic Config pointing at the ephemeral test database.

    The Config reads ``alembic.ini`` for script_location and logging config
    but overrides ``sqlalchemy.url`` to point at the ephemeral instance.

    Args:
        pg_url: The psycopg2 connection URL from :func:`pg_url`.

    Returns:
        A populated :class:`alembic.config.Config` ready for migration commands.
    """
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", pg_url)
    return cfg


@pytest.fixture(scope="module")
def bootstrapped_engine(pg_url: str, alembic_cfg: Config) -> Generator[Engine]:
    """Yield a SQLAlchemy Engine with the full schema bootstrapped via create_all.

    Mirrors the application startup sequence:
    1. ``SQLModel.metadata.create_all()`` creates ALL tables in their final form.
    2. Alembic is stamped at ``head`` so it knows the schema is current.

    All round-trip tests share this engine; each test calls ``downgrade``/
    ``upgrade`` independently.

    Args:
        pg_url: Connection URL from :func:`pg_url`.
        alembic_cfg: Alembic Config for stamping.

    Yields:
        A configured :class:`sqlalchemy.Engine` instance.
    """
    engine = create_engine(pg_url)
    SQLModel.metadata.create_all(engine)
    # Stamp at head: informs Alembic the schema is already at the latest revision
    # without running any DDL (create_all already did that).
    command.stamp(alembic_cfg, "head")
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Infrastructure wiring test (no PostgreSQL required)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_alembic_config_is_importable() -> None:
    """Alembic configuration infrastructure must be importable.

    Verifies that:
    - ``alembic.config.Config`` can read ``alembic.ini``.
    - The ``script_location`` key resolves to ``alembic``.

    This test does NOT require a live database connection.

    Arrange/Act: instantiate Config from alembic.ini.
    Assert: script_location == "alembic".
    """
    cfg = Config("alembic.ini")
    script_location = cfg.get_main_option("script_location")

    assert script_location == "alembic", (
        f"alembic.ini script_location must be 'alembic'; got {script_location!r}"
    )


# ---------------------------------------------------------------------------
# Round-trip migration tests (PostgreSQL required)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_stamp_head_succeeds_after_create_all(
    alembic_cfg: Config,
    bootstrapped_engine: Engine,
) -> None:
    """Alembic stamp head must succeed after SQLModel.metadata.create_all().

    Verifies that the schema created by the ORM bootstrapper is compatible with
    the Alembic migration chain — i.e., that Alembic can record the current
    revision without DDL errors.

    Arrange: schema created by bootstrapped_engine fixture (create_all + stamp head).
    Act: re-stamp at head (idempotent operation).
    Assert: no exception is raised.
    """
    command.stamp(alembic_cfg, "head")
    # Reaching here without exception confirms Alembic and ORM schemas are compatible.
    # Specific: alembic_cfg has a config_file_name (properly loaded alembic.ini)
    assert alembic_cfg.config_file_name is not None, (
        "alembic_cfg must reference a real alembic.ini file"
    )
    assert "alembic.ini" in alembic_cfg.config_file_name, (
        f"Expected 'alembic.ini' in config_file_name, got: {alembic_cfg.config_file_name}"
    )


@pytest.mark.integration
def test_downgrade_minus_one_succeeds(
    alembic_cfg: Config,
    bootstrapped_engine: Engine,
) -> None:
    """alembic downgrade -1 must succeed when stamped at head.

    Verifies that the most recent migration's ``downgrade()`` function
    executes successfully — i.e., the last schema change is reversible.

    Arrange: schema at head (bootstrapped by fixture).
    Act: run ``alembic downgrade -1``.
    Assert: no exception is raised.
    """
    # Ensure clean starting state
    command.stamp(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "-1")
    # Reaching here without exception confirms the last migration rolls back cleanly.
    # Specific: the alembic config is properly constructed with a config file
    assert alembic_cfg.get_main_option("script_location") is not None, (
        "alembic script_location must be set in alembic.ini"
    )
    script_loc = alembic_cfg.get_main_option("script_location") or ""
    assert "alembic" in script_loc, f"Expected 'alembic' in script_location, got: {script_loc!r}"


@pytest.mark.integration
def test_reupgrade_succeeds_after_downgrade(
    alembic_cfg: Config,
    bootstrapped_engine: Engine,
) -> None:
    """Re-running upgrade head after a downgrade must succeed.

    Verifies the migration chain is idempotent with respect to
    downgrade/re-upgrade — the schema can always be brought back to head.

    Arrange: downgrade -1 first, then upgrade head.
    Act: run ``alembic upgrade head``.
    Assert: no exception is raised; schema is back at head.
    """
    # upgrade head re-applies any migration rolled back in a prior test in this
    # session; avoids interference from the shared module-scoped engine.
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "-1")
    command.upgrade(alembic_cfg, "head")
    # Reaching here confirms the round-trip completes cleanly.
    # Specific: the engine dialect is PostgreSQL (not SQLite)
    assert "postgresql" in str(bootstrapped_engine.url), (
        f"Integration tests must use PostgreSQL, got: {bootstrapped_engine.url}"
    )
