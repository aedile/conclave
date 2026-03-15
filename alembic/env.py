"""Alembic migration environment for the Conclave Engine.

Supports both offline (SQL generation) and online (live database) migration
modes.  The target metadata is sourced from :class:`synth_engine.shared.db.BaseModel`
so that all SQLModel table classes are automatically included when any module
imports this environment.

CONSTITUTION Priority 0: Security — credentials are sourced from the
environment at runtime via ``alembic.ini``; no hardcoded values here.
Task: P2-T2.2 — Secure Database Layer
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from synth_engine.bootstrapper.schemas.connections import (
    Connection,  # noqa: F401  (T5.1 — ADV-049 convention)
)
from synth_engine.bootstrapper.schemas.settings import (
    Setting,  # noqa: F401  (T5.1 — ADV-049 convention)
)

# Side-effect imports: register all SQLModel tables that extend SQLModel directly
# (rather than BaseModel) so that target_metadata is complete for autogenerate.
from synth_engine.modules.privacy.ledger import PrivacyLedger, PrivacyTransaction  # noqa: F401

# Import BaseModel to register all SQLModel table metadata.  The act of
# importing this module causes all concrete table classes (imported transitively
# by the application) to register with SQLModel.metadata.
from synth_engine.shared.db import BaseModel

# ---------------------------------------------------------------------------
# Alembic configuration object — provides access to values in alembic.ini
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate support
target_metadata = BaseModel.metadata


# ---------------------------------------------------------------------------
# Offline migration (generates SQL without a live DB connection)
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations in offline mode.

    Configures the context with a URL only and without an Engine.  The
    resulting SQL is written to stdout or a file rather than executed against
    a live database.  Useful for auditing or applying migrations via a DBA.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migration (executes against a live database)
# ---------------------------------------------------------------------------


def run_migrations_online() -> None:
    """Run migrations in online mode.

    Creates an Engine from the ``alembic.ini`` configuration and establishes
    a connection, then runs all pending migrations within a transaction.
    ``NullPool`` is used deliberately to avoid leaving idle connections open
    after the migration process exits — Alembic migrations are short-lived
    administrative tasks, not long-running application processes.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


# ---------------------------------------------------------------------------
# Entry point — select mode based on Alembic's runtime context
# ---------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
