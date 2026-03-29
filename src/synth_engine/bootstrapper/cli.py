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

#: Development fallback salt used when MASKING_SALT is not set.
#: Per ADV-027: deterministic-across-deployments without a secret is the
#: design for CLI development mode.
_CLI_MASKING_SALT_FALLBACK: str = "conclave-cli-v1"


def _get_cli_masking_salt() -> str:
    """Return the CLI masking salt, reading from settings at call time.

    ADV-035: Reads from :attr:`ConclaveSettings.masking_salt` when set;
    falls back to the hardcoded development value with a warning.

    Returns:
        The masking salt string to use for deterministic HMAC masking.
    """
    from synth_engine.shared.settings import get_settings

    _salt_secret = get_settings().masking_salt
    salt = _salt_secret.get_secret_value() if _salt_secret is not None else None
    if not salt:
        _logger.warning(
            "MASKING_SALT env var not set; using hardcoded CLI fallback. "
            "Set MASKING_SALT for production use."
        )
        return _CLI_MASKING_SALT_FALLBACK
    return salt


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
                result[col] = fn(str(result[col]), _get_cli_masking_salt())
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
        sqlalchemy.exc.SQLAlchemyError: If the database connection cannot be
            established or schema reflection fails.
    """  # noqa: DOC502
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
    except Exception as exc:  # Broad catch intentional: CLI converts all errors to SystemExit(1)
        _logger.exception("subset run failed")
        click.echo("Error: subset run failed — see logs for details.", err=True)
        raise SystemExit(1) from exc

    # --- Summary output ---
    click.echo("Subset complete.")
    for table in result.tables_written:
        count = result.row_counts.get(table, 0)
        click.echo(f"  {table}: {count} rows")


# ---------------------------------------------------------------------------
# Audit CLI group — T71.2
# Provides `conclave audit` with `migrate-signatures` and `log-event` subcommands.
#
# Security notes:
# - AUDIT_KEY is read EXCLUSIVELY from the environment variable (not from CLI
#   args), so it is never visible in process argv or shell history.
# - --details must be valid JSON; rejected with exit 1 if unparseable.
# - --input is validated: must exist, be a regular file, and be non-empty.
# - --output uses atomic write (temp file + os.rename) to prevent partial output.
# - This group loads ONLY AUDIT_KEY from env — NOT the full ConclaveSettings —
#   so it works on audit workstations without a database connection.
#
# Task: T71.2 — Wire audit CLI commands (ADV-P70-02)
# ---------------------------------------------------------------------------


def _load_audit_key_from_env() -> bytes:
    """Load the AUDIT_KEY from the environment — NOT from CLI args.

    Checks ``AUDIT_KEY`` and ``CONCLAVE_AUDIT_KEY`` (preferred alias, T63.2).
    Exits non-zero with a user-friendly message if neither is set.

    Returns:
        Raw 32-byte HMAC key decoded from the hex-encoded env var.

    Raises:
        SystemExit: If AUDIT_KEY is absent, empty, or not valid hex.
    """
    import os

    key_hex = os.environ.get("CONCLAVE_AUDIT_KEY") or os.environ.get("AUDIT_KEY")
    if not key_hex or not key_hex.strip():
        click.echo(
            "Error: AUDIT_KEY environment variable is required but not set. "
            "Set AUDIT_KEY=<hex-encoded-32-byte-key> before running this command.",
            err=True,
        )
        raise SystemExit(1)
    try:
        return bytes.fromhex(key_hex.strip())
    except ValueError as exc:
        click.echo(
            "Error: AUDIT_KEY is not valid hexadecimal. "
            "It must be a hex-encoded 32-byte (64-character) key.",
            err=True,
        )
        raise SystemExit(1) from exc


@click.group(name="audit")
def audit_group() -> None:
    """Audit log management commands for the Conclave Engine.

    Subcommands for migrating audit log signatures and manually emitting
    audit events for operational and forensic purposes.

    AUDIT_KEY is read exclusively from the AUDIT_KEY environment variable.
    It must never be passed as a CLI argument.
    """


@audit_group.command(name="migrate-signatures")
@click.option(
    "--input",
    "input_path",
    required=True,
    help="Path to the source JSONL audit log file.",
)
@click.option(
    "--output",
    "output_path",
    required=True,
    help="Path to write the migrated JSONL audit log file.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help=(
        "Read and verify input, count what would be migrated, print summary — "
        "do NOT write any output file."
    ),
)
def migrate_signatures(
    input_path: str,
    output_path: str,
    dry_run: bool,
) -> None:
    """Re-sign v1/v2 audit log entries as v3 format.

    Reads INPUT line-by-line, verifies each entry's existing signature, and
    writes a v3-signed copy to OUTPUT.  Entries with tampered v1/v2 signatures
    are skipped (not written).

    Use --dry-run to inspect the input and count what would be migrated without
    writing anything to disk.

    AUDIT_KEY is read from the AUDIT_KEY environment variable.
    """
    import json
    import os
    import tempfile
    from pathlib import Path

    from synth_engine.shared.security.audit_migrations import migrate_audit_signatures

    # --- Validate input file ---
    input_file = Path(input_path)
    if not input_file.exists():
        click.echo(f"Error: input file not found: {input_path}", err=True)
        raise SystemExit(1)
    if not input_file.is_file():
        click.echo(f"Error: input path is not a regular file: {input_path}", err=True)
        raise SystemExit(1)
    if input_file.stat().st_size == 0:
        click.echo(f"Error: input file is empty: {input_path}", err=True)
        raise SystemExit(1)

    # --- Load audit key from environment only ---
    audit_key = _load_audit_key_from_env()

    if dry_run:
        # Parse and count without writing output.
        lines = input_file.read_text().splitlines()
        total = len(lines)
        parseable = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
                parseable += 1
            except json.JSONDecodeError:
                pass
        click.echo("Dry-run summary:")
        click.echo(f"  Input file:    {input_path}")
        click.echo(f"  Total lines:   {total}")
        click.echo(f"  Valid JSON:    {parseable}")
        click.echo("  Output:        (skipped — dry-run mode)")
        return

    # --- Atomic write: write to temp file, then rename to output_path ---
    output_file = Path(output_path)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(output_file.parent),
        prefix=".conclave-migrate-tmp-",
        suffix=".jsonl",
    )
    os.close(tmp_fd)
    try:
        migrate_audit_signatures(
            input_path=input_path,
            output_path=tmp_path,
            audit_key=audit_key,
        )
        os.rename(tmp_path, output_path)
    except Exception as exc:
        # Clean up temp file on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        click.echo(f"Error: migration failed: {exc}", err=True)
        raise SystemExit(1) from exc

    click.echo(f"Migration complete. Output written to: {output_path}")


@audit_group.command(name="log-event")
@click.option("--type", "event_type", required=True, help="Audit event type (e.g. MANUAL_ENTRY).")
@click.option("--actor", required=True, help="Actor identifier (operator or system).")
@click.option("--resource", required=True, help="Resource identifier (e.g. system/config).")
@click.option("--action", required=True, help="Action performed (e.g. update).")
@click.option(
    "--details",
    default="{}",
    help="JSON object with additional event details (default: {}).",
)
def log_event(
    event_type: str,
    actor: str,
    resource: str,
    action: str,
    details: str,
) -> None:
    """Manually emit a WORM audit event.

    Use this command for operational annotations, forensic notes, or to record
    manual administrative actions that are not captured by the API.

    AUDIT_KEY is read from the AUDIT_KEY environment variable.

    --details must be a valid JSON object string.  Use ``'{}'`` for no details.
    """
    import json

    from synth_engine.shared.security.audit_logger import AuditLogger

    # --- Validate --details is parseable JSON ---
    try:
        details_dict = json.loads(details)
        if not isinstance(details_dict, dict):
            click.echo(
                'Error: --details must be a JSON object (e.g. \'{"key": "value"}\'), '
                f"got {type(details_dict).__name__}.",
                err=True,
            )
            raise SystemExit(1)
    except json.JSONDecodeError as exc:
        click.echo(
            f"Error: --details is not valid JSON: {exc}",
            err=True,
        )
        raise SystemExit(1) from exc

    # --- Load audit key from environment only ---
    audit_key = _load_audit_key_from_env()

    # --- Emit the audit event ---
    try:
        audit_logger = AuditLogger(audit_key=audit_key)
        audit_logger.log_event(
            event_type=event_type,
            actor=actor,
            resource=resource,
            action=action,
            details=details_dict,
        )
    except Exception as exc:
        click.echo(f"Error: failed to emit audit event: {exc}", err=True)
        raise SystemExit(1) from exc

    click.echo(f"Audit event emitted: type={event_type} actor={actor} resource={resource}")
