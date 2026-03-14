# ruff: noqa — vulture whitelist uses bare name expressions which ruff cannot parse as normal Python
"""Vulture whitelist — suppress known false positives at --min-confidence 60.

All entries here are genuine public API, framework-registered hooks, or
test-support utilities that vulture cannot see as "used" via static analysis:

- FastAPI route handlers: @app.get/@app.post/@app.exception_handler decorators
  register functions with the ASGI router at startup; vulture sees no callers.
- Click CLI commands: @click.command/@click.option register functions with the
  Click group at import time; vulture sees no callers.
- Starlette middleware dispatch: required ASGI interface; never called directly.
- SQLAlchemy TypeDecorator hooks: called by SQLAlchemy ORM at bind/result time.
- SQLModel model fields: accessed via ORM attribute access, not direct Python.
- Public API functions/classes: wired in bootstrapper (Phase 4, not yet done)
  or exported for use by external callers (integration tests, CLI consumers).

Do NOT add names here for code that is genuinely dead. Fix or remove that code.

See: https://github.com/jendrikseipp/vulture#whitelists
Generated with: poetry run python -m vulture src/ --min-confidence 60 --make-whitelist
All entries manually verified as false positives before inclusion.
"""

# ---------------------------------------------------------------------------
# bootstrapper — FastAPI route handlers and CLI commands
# ---------------------------------------------------------------------------
subset  # Click CLI command (bootstrapper/cli.py) — registered via @click decorators
get_current_user  # FastAPI dependency (bootstrapper/dependencies/auth.py)
_.dispatch  # Starlette middleware dispatch (bootstrapper/dependencies/vault.py)
require_unsealed  # FastAPI dependency (bootstrapper/dependencies/vault.py)
_cycle_detection_error_handler  # FastAPI exception handler (bootstrapper/main.py)
health_check  # FastAPI GET /health route (bootstrapper/main.py)
unseal_vault  # FastAPI POST /unseal route (bootstrapper/main.py)

# ---------------------------------------------------------------------------
# ingestion — public adapter API (wired in Phase 4 bootstrapper)
# ---------------------------------------------------------------------------
PostgresIngestionAdapter  # Public ingestion adapter class (ingestion/postgres_adapter.py)
_.preflight_check  # Public method on PostgresIngestionAdapter
_.stream_table  # Public method on PostgresIngestionAdapter
_.get_schema_inspector  # Public method on PostgresIngestionAdapter

# ---------------------------------------------------------------------------
# mapping — public graph methods
# ---------------------------------------------------------------------------
_.nodes  # Public property on DirectedAcyclicGraph (mapping/graph.py)
_.edges  # Public property on DirectedAcyclicGraph (mapping/graph.py)
_.has_cycle  # Public method on DirectedAcyclicGraph (mapping/graph.py)

# ---------------------------------------------------------------------------
# masking — public registry API
# ---------------------------------------------------------------------------
MaskingRegistry  # Public masking registry class (masking/registry.py)
_.reset  # Test-support reset on MaskingRegistry and VaultClient (masking/registry.py, vault.py)

# ---------------------------------------------------------------------------
# profiler — public StatisticalProfiler API (wired in Phase 4 bootstrapper)
# ---------------------------------------------------------------------------
_.profile  # Public profile method on StatisticalProfiler (profiler/profiler.py)
_.compare  # Public compare method on StatisticalProfiler (profiler/profiler.py)

# ---------------------------------------------------------------------------
# subsetting — public EgressWriter property
# ---------------------------------------------------------------------------
_.written_tables  # Public property on EgressWriter (subsetting/egress.py)

# ---------------------------------------------------------------------------
# shared — auth, db, middleware, schema_topology, security, tasks
# ---------------------------------------------------------------------------
sub  # JWT payload field (shared/auth/jwt.py)
exp  # JWT payload field (shared/auth/jwt.py)
iat  # JWT payload field (shared/auth/jwt.py)
create_access_token  # Public JWT factory (shared/auth/jwt.py)
get_engine  # SQLAlchemy engine DI factory (shared/db.py)
get_session  # SQLAlchemy session DI factory (shared/db.py)
model_config  # SQLModel model configuration field (shared/db.py)
created_at  # SQLModel timestamp field (shared/db.py)
updated_at  # SQLModel timestamp field (shared/db.py)
IdempotencyMiddleware  # Starlette middleware registered at startup (shared/middleware)
_.dispatch  # Starlette ASGI dispatch method (shared/middleware/idempotency.py)
nullable  # ColumnInfo dataclass field (shared/schema_topology.py)
generate_ale_key  # ALE key factory (shared/security/ale.py)
_reset_fernet_cache  # Test-support cache reset (shared/security/ale.py)
EncryptedString  # SQLAlchemy TypeDecorator public type (shared/security/ale.py)
impl  # SQLAlchemy TypeDecorator internal field (shared/security/ale.py)
cache_ok  # SQLAlchemy TypeDecorator internal field (shared/security/ale.py)
_.process_bind_param  # SQLAlchemy TypeDecorator hook (shared/security/ale.py)
_.process_result_value  # SQLAlchemy TypeDecorator hook (shared/security/ale.py)
_.verify_event  # Public audit verification method (shared/security/audit.py)
reset_audit_logger  # Test-support audit logger reset (shared/security/audit.py)
status  # SQLModel task field (shared/tasks/reaper.py)
started_at  # SQLModel task field (shared/tasks/reaper.py)
locked_by  # SQLModel task field (shared/tasks/reaper.py)
OrphanTaskReaper  # Public task reaper class (shared/tasks/reaper.py)
_.reap  # Public reap method on OrphanTaskReaper (shared/tasks/reaper.py)
