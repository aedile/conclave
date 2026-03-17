"""Minimal CLI entrypoint for the conclave-subset command.

Provides the ``conclave-subset`` command that subsets a source PostgreSQL
database into a target database with optional deterministic masking applied.

This is a functional entrypoint — not the polished API (that is T5.1).
Its purpose is to close T3.5 AC2: a real user can run a complete subset
job from the command line today.

Security notes
--------------
- Connection strings are validated via :func:`validate_connection_string`
  before any connection is attempted.
- Connection strings are NEVER echoed in output or error messages
  (they may contain credentials).
- The ``--seed-query`` is validated to start with SELECT to prevent
  accidental destructive SQL execution.

Architecture note
-----------------
This module lives in ``bootstrapper/`` as the CLI entry point — analogous
to ``bootstrapper/main.py`` for the HTTP layer.  It wires the masking
registry into the subsetting engine via the ``row_transformer`` IoC hook,
the same pattern used in the HTTP bootstrapper.

This module imports from ``modules/masking`` intentionally: ``cli.py`` is
inside ``bootstrapper/``, which is the wiring layer responsible for
composing modules together.  The import-linter contracts apply only to the
``modules/`` and ``shared/`` namespaces; ``bootstrapper/`` is explicitly
allowed to import from any module.

CONSTITUTION Priority 0: Security — no credential echo, SELECT-only validation.
Task: P3.5-T3.5.4 — Bootstrapper Wiring & Minimal CLI Entrypoint
Task: P21-T21.1 — Fix CLI masking config to match sample data schema (customers)
Task: P21-T21.2 — Masking algorithm split: first_name, last_name, address
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from typing import Any

import click
from sqlalchemy import create_engine

from synth_engine.modules.ingestion.validators import validate_connection_string
from synth_engine.modules.mapping.reflection import SchemaReflector
from synth_engine.modules.masking.algorithms import (
    mask_address,
    mask_email,
    mask_first_name,
    mask_last_name,
    mask_name,
    mask_phone,
    mask_ssn,
)
from synth_engine.modules.subsetting.core import SubsettingEngine
from synth_engine.modules.subsetting.egress import EgressWriter
from synth_engine.shared.schema_topology import ColumnInfo, ForeignKeyInfo, SchemaTopology

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Masking transformer factory
# ---------------------------------------------------------------------------

# Salt used by the CLI masking transformer.
# Per ADR note on ADV-027: deterministic-across-deployments without a secret
# is the current design for the CLI.
#
# ADV-035: Read from MASKING_SALT env var when set; fall back to the hardcoded
# development value with a warning so operators are directed to inject the
# production salt from Vault or the environment.
_CLI_MASKING_SALT = os.environ.get("MASKING_SALT", "")
if not _CLI_MASKING_SALT:
    _logger.warning(
        "MASKING_SALT env var not set; using hardcoded CLI fallback. "
        "Set MASKING_SALT for production use."
    )
    _CLI_MASKING_SALT = "conclave-cli-v1"

# Column-level masking configuration: table → {column → masking function}.
# Only PII columns are listed; all others pass through unchanged.
#
# NOTE: This config is sample-data-specific (matches the schema in sample_data/).
# Production deployments should use schema-driven masking config (future task).
# The sample data schema has a 'customers' table with the PII columns below.
#
# P21-T21.2: first_name and last_name use mask_first_name/mask_last_name
# (Faker.first_name()/last_name()) rather than mask_name (Faker.name()),
# which produces "First Last" (two words) — incorrect for single-component columns.
# address uses mask_address (Faker.address()) rather than mask_name.
#
# Type reflects call-site usage: only (value, salt) are passed through the dict.
# Registered functions may accept additional optional params (e.g. max_length)
# which are unused in this CLI path.
_COLUMN_MASKS: dict[str, dict[str, Callable[[str, str], str]]] = {
    "customers": {
        "first_name": mask_first_name,
        "last_name": mask_last_name,
        "email": mask_email,
        "ssn": mask_ssn,
        "phone": mask_phone,
        "address": mask_address,
    },
    "persons": {
        "full_name": mask_name,
        "email": mask_email,
        "ssn": mask_ssn,
    },
}


def _build_masking_transformer() -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """Build and return a deterministic row-masking transformer.

    The transformer is the ``row_transformer`` callback injected into
    :class:`~synth_engine.modules.subsetting.core.SubsettingEngine`.  It
    applies format-preserving masking to known PII columns and passes all
    other columns through unchanged.

    Returns:
        A callable with signature
        ``(table_name: str, row: dict[str, Any]) -> dict[str, Any]``.
    """

    def _mask_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
        """Apply deterministic masking to PII columns in the given row.

        Non-PII tables and non-PII columns are returned unchanged.  The
        input dict is never mutated — a new dict is returned.

        Args:
            table: The source table name; used to look up masking config.
            row: A single row dict fetched from the source database.

        Returns:
            A new row dict with PII columns replaced by deterministic
            masked values.  Non-PII values are copied unchanged.
        """
        masks = _COLUMN_MASKS.get(table, {})
        if not masks:
            return row
        result = dict(row)
        for col, fn in masks.items():
            if col in result and result[col] is not None:
                result[col] = fn(str(result[col]), _CLI_MASKING_SALT)
        return result

    return _mask_row


# ---------------------------------------------------------------------------
# Topology loader
# ---------------------------------------------------------------------------


def _load_topology(source_dsn: str) -> SchemaTopology:
    """Reflect the schema topology from the source database.

    Uses :class:`~synth_engine.modules.mapping.reflection.SchemaReflector`
    to build a :class:`~synth_engine.shared.schema_topology.SchemaTopology`
    value object from the live source schema.  The bootstrapper is the sole
    layer permitted to call ``SchemaReflector.reflect()`` and convert the
    resulting :class:`~synth_engine.modules.mapping.graph.DirectedAcyclicGraph`
    into the :class:`~synth_engine.shared.schema_topology.SchemaTopology`
    that downstream modules (SubsettingEngine) consume.

    Args:
        source_dsn: The validated source PostgreSQL connection string.

    Returns:
        A :class:`~synth_engine.shared.schema_topology.SchemaTopology`
        instance describing the source schema.

    Raises:
        Exception: Any SQLAlchemy or reflection error propagates to the
            caller (the ``subset`` command), which wraps it in a clean
            error message.
    """
    engine = create_engine(source_dsn)
    reflector = SchemaReflector(engine=engine)
    dag = reflector.reflect()
    table_order = tuple(dag.topological_sort())

    columns: dict[str, tuple[ColumnInfo, ...]] = {}
    foreign_keys: dict[str, tuple[ForeignKeyInfo, ...]] = {}

    for table in table_order:
        # ADV-021 fix: Use get_pk_constraint() to reliably identify PK columns.
        # Inspector.get_columns() may omit the 'primary_key' key on PostgreSQL
        # backends; get_pk_constraint() is the authoritative source.
        pk_constraint = reflector.get_pk_constraint(table)
        pk_columns: set[str] = set(pk_constraint.get("constrained_columns", []))

        raw_cols = reflector.get_columns(table)
        columns[table] = tuple(
            ColumnInfo(
                name=str(col["name"]),
                type=str(col.get("type", "")),
                # primary_key: 1 if this column is in the PK constraint, 0 otherwise.
                # Using get_pk_constraint() (ADV-021) is reliable across all backends.
                primary_key=1 if str(col["name"]) in pk_columns else 0,
                nullable=bool(col.get("nullable", True)),
            )
            for col in raw_cols
        )
        raw_fks = reflector.get_foreign_keys(table)
        foreign_keys[table] = tuple(
            ForeignKeyInfo(
                constrained_columns=tuple(str(c) for c in fk.get("constrained_columns", [])),
                referred_table=str(fk["referred_table"]),
                referred_columns=tuple(str(c) for c in fk.get("referred_columns", [])),
            )
            for fk in raw_fks
        )

    return SchemaTopology(
        table_order=table_order,
        columns=columns,
        foreign_keys=foreign_keys,
    )


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command()
@click.option("--source", required=True, help="Source PostgreSQL DSN (connection string).")
@click.option("--target", required=True, help="Target PostgreSQL DSN (connection string).")
@click.option("--seed-table", required=True, help="Root table for the subset traversal.")
@click.option(
    "--seed-query",
    required=True,
    help="SELECT query that returns the seed rows from --seed-table.",
)
@click.option(
    "--mask/--no-mask",
    default=True,
    show_default=True,
    help="Apply deterministic format-preserving masking to PII columns (default: enabled).",
)
def subset(
    source: str,
    target: str,
    seed_table: str,
    seed_query: str,
    mask: bool,
) -> None:
    """Subset a source PostgreSQL database into a target database.

    Traverses the source schema's foreign-key graph starting from
    SEED_TABLE using SEED_QUERY to select the root rows.  All
    referentially-dependent rows are fetched and written to the target.

    With --mask (default), deterministic format-preserving masking is
    applied to known PII columns before rows are written to the target.
    Use --no-mask to write rows as-is (for non-PII data or debugging).

    Example:\b

        conclave-subset \\
          --source postgresql://user:pass@localhost/prod \\  # pragma: allowlist secret
          --target postgresql://user:pass@localhost/dev \\  # pragma: allowlist secret
          --seed-table customers \\
          --seed-query "SELECT * FROM customers LIMIT 100"
    """
    # --- Input validation (fail fast before touching any database) ---
    try:
        validate_connection_string(source)
    except ValueError as exc:
        # Do NOT include the original DSN — it may contain credentials.
        click.echo(f"Error: invalid --source connection string: {exc}")
        sys.exit(1)

    try:
        validate_connection_string(target)
    except ValueError as exc:
        click.echo(f"Error: invalid --target connection string: {exc}")
        sys.exit(1)

    stripped_query = seed_query.strip()
    if not stripped_query:
        click.echo("Error: --seed-query must not be empty.")
        sys.exit(1)

    if not stripped_query.upper().startswith("SELECT"):
        click.echo(
            "Error: --seed-query must be a SELECT statement. "
            "Only SELECT queries are accepted to prevent accidental data modification."
        )
        sys.exit(1)

    # --- Wire up the subsetting engine ---
    row_transformer: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
    if mask:
        row_transformer = _build_masking_transformer()

    try:
        topology = _load_topology(source)
        src_engine = create_engine(source)
        tgt_engine = create_engine(target)
        egress = EgressWriter(target_engine=tgt_engine)
        engine = SubsettingEngine(
            source_engine=src_engine,
            topology=topology,
            egress=egress,
            row_transformer=row_transformer,
        )
        result = engine.run(seed_table=seed_table, seed_query=seed_query)
    except Exception as exc:
        _logger.exception("subset run failed")
        click.echo("Error: subset run failed — see logs for details.", err=True)
        raise SystemExit(1) from exc

    # --- Summary output ---
    click.echo("Subset complete.")
    for table in result.tables_written:
        count = result.row_counts.get(table, 0)
        click.echo(f"  {table}: {count} rows")
