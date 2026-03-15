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
list_jobs  # FastAPI GET /jobs route (bootstrapper/routers/jobs.py) — registered via @router decorator
create_job  # FastAPI POST /jobs route (bootstrapper/routers/jobs.py)
get_job  # FastAPI GET /jobs/{id} route (bootstrapper/routers/jobs.py)
start_job  # FastAPI POST /jobs/{id}/start route (bootstrapper/routers/jobs.py)
stream_job  # FastAPI GET /jobs/{id}/stream route (bootstrapper/routers/jobs.py)
list_connections  # FastAPI GET /connections route (bootstrapper/routers/connections.py)
create_connection  # FastAPI POST /connections route (bootstrapper/routers/connections.py)
get_connection  # FastAPI GET /connections/{id} route (bootstrapper/routers/connections.py)
delete_connection  # FastAPI DELETE /connections/{id} route (bootstrapper/routers/connections.py)
list_settings  # FastAPI GET /settings route (bootstrapper/routers/settings.py)
upsert_setting  # FastAPI PUT /settings/{key} route (bootstrapper/routers/settings.py)
get_setting  # FastAPI GET /settings/{key} route (bootstrapper/routers/settings.py)
delete_setting  # FastAPI DELETE /settings/{key} route (bootstrapper/routers/settings.py)
get_license_challenge  # FastAPI GET /license/challenge route (bootstrapper/routers/licensing.py)
post_license_activate  # FastAPI POST /license/activate route (bootstrapper/routers/licensing.py)
problem_detail  # Public RFC 7807 helper (bootstrapper/errors.py) — used by routers and tests
register_error_handlers  # Public error handler registration (bootstrapper/errors.py)
RFC7807Middleware  # Starlette middleware (bootstrapper/errors.py) — registered at startup
get_db_session  # FastAPI dependency (bootstrapper/dependencies/db.py) — used in routers
safe_error_msg  # Public sanitization helper (shared/errors.py) — used by SSE and error handler
job_event_stream  # Public SSE generator (bootstrapper/sse.py) — used by jobs router
_uuid_str  # UUID string factory (bootstrapper/schemas/connections.py) — default_factory

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
# synthesizer — public storage API (wired in Phase 4 bootstrapper; T4.1)
# Engine and model API wired in bootstrapper in T4.2b (ADV-037 drain)
# ---------------------------------------------------------------------------
build_ephemeral_storage_client  # Factory fn for EphemeralStorageClient (bootstrapper/main.py)
build_synthesis_engine  # Factory fn for SynthesisEngine (bootstrapper/main.py)
_log_device_selection  # Module-level GPU/CPU detection fn (synthesizer/storage.py)
MinioStorageBackend  # Concrete MinIO backend (synthesizer/storage.py) — injected at runtime
EphemeralStorageClient  # Public ephemeral storage client (synthesizer/storage.py)
_.upload_parquet  # Public upload method on EphemeralStorageClient
_.download_parquet  # Public download method on EphemeralStorageClient

# ---------------------------------------------------------------------------
# privacy — DP engine and Privacy Accountant public API (T4.3b, T4.4)
# ---------------------------------------------------------------------------
DPTrainingWrapper  # Public DP training wrapper (privacy/dp_engine.py) — injected at runtime
BudgetExhaustionError  # Public exception (privacy/dp_engine.py) — raised on budget exhaustion
_.wrap  # Public wrap method on DPTrainingWrapper (privacy/dp_engine.py)
_.epsilon_spent  # Public epsilon_spent method on DPTrainingWrapper
_.check_budget  # Public check_budget method on DPTrainingWrapper
PrivacyLedger  # SQLModel table — global epsilon budget tracker (privacy/ledger.py)
PrivacyTransaction  # SQLModel table — epsilon expenditure audit log (privacy/ledger.py)
spend_budget  # Async function — atomically deducts epsilon with FOR UPDATE (privacy/accountant.py)
get_async_engine  # Async SQLAlchemy engine factory (shared/db.py)
get_async_session  # Async context manager yielding AsyncSession (shared/db.py)
last_updated  # SQLModel timestamp field on PrivacyLedger (privacy/ledger.py)

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
get_active_public_key  # Public key resolver (shared/security/licensing.py) — used by tests and router
LicenseState  # Public license state class (shared/security/licensing.py) — used by middleware and router
LicenseError  # Public license exception (shared/security/licensing.py) — used by router
LicenseGateMiddleware  # Starlette middleware (bootstrapper/dependencies/licensing.py) — registered at startup
