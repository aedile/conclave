"""Vulture whitelist for the Air-Gapped Synthetic Data Generation Engine.

This file suppresses known false positives from `vulture src/ --min-confidence 60`.
All entries here are intentionally "unused" from vulture's static-analysis perspective
but are legitimately consumed at runtime through one of these mechanisms:

  - FastAPI decorator-based route registration (@router.get, @router.post, etc.)
  - Starlette middleware protocol (dispatch() called by the ASGI framework)
  - FastAPI dependency injection (Depends(), lifespan hooks)
  - Pydantic model fields and validators (consumed by Pydantic's metaclass machinery)
  - SQLAlchemy TypeDecorator protocol (process_bind_param, process_result_value, impl,
    cache_ok are called by SQLAlchemy's column type system)
  - Test-isolation utilities (reset functions used by test fixtures — not production paths)

Usage:
    vulture src/ .vulture_whitelist.py --min-confidence 60
"""

# ---------------------------------------------------------------------------
# Category A — FastAPI route handler functions
# These are registered via @router.get() / @router.post() / @router.delete()
# decorators.  Vulture cannot trace decorator-based registration.
# ---------------------------------------------------------------------------

subset  # unused function — FastAPI route handler (bootstrapper/cli.py)
list_connections  # unused function — FastAPI route handler (routers/connections.py)
create_connection  # unused function — FastAPI route handler (routers/connections.py)
get_connection  # unused function — FastAPI route handler (routers/connections.py)
delete_connection  # unused function — FastAPI route handler (routers/connections.py)
list_jobs  # unused function — FastAPI route handler (routers/jobs.py)
create_job  # unused function — FastAPI route handler (routers/jobs.py)
get_job  # unused function — FastAPI route handler (routers/jobs.py)
start_job  # unused function — FastAPI route handler (routers/jobs.py)
stream_job  # unused function — FastAPI route handler (routers/jobs.py)
get_license_challenge  # unused function — FastAPI route handler (routers/licensing.py)
post_license_activate  # unused function — FastAPI route handler (routers/licensing.py)
shred_vault  # unused function — FastAPI route handler (routers/security.py)
rotate_keys  # unused function — FastAPI route handler (routers/security.py)
list_settings  # unused function — FastAPI route handler (routers/settings.py)
upsert_setting  # unused function — FastAPI route handler (routers/settings.py)
get_setting  # unused function — FastAPI route handler (routers/settings.py)
delete_setting  # unused function — FastAPI route handler (routers/settings.py)
health_check  # unused function — FastAPI route handler (bootstrapper/lifecycle.py)
unseal_vault  # unused function — FastAPI route handler (bootstrapper/lifecycle.py)
get_budget  # unused function — FastAPI route handler (routers/privacy.py)
refresh_budget  # unused function — FastAPI route handler (routers/privacy.py)

# ---------------------------------------------------------------------------
# Category B — Starlette middleware dispatch() methods
# Starlette's BaseHTTPMiddleware protocol calls dispatch() on every request.
# Vulture cannot trace ASGI protocol-based dispatch.
# ---------------------------------------------------------------------------

dispatch  # unused method — Starlette middleware protocol (CSPMiddleware)
# Note: 'dispatch' appears in multiple middleware classes; one whitelist entry
# covers all of them since vulture deduplicates by name.

# ---------------------------------------------------------------------------
# Category C — FastAPI dependency-injection factory functions
# Called via Depends() or wired into FastAPI's DI container / lifespan hooks.
# Vulture cannot trace string-based or Depends()-based injection.
# ---------------------------------------------------------------------------

get_current_user  # unused function — FastAPI Depends() (dependencies/auth.py)
require_unsealed  # unused function — FastAPI Depends() (dependencies/vault.py)
build_synthesis_engine  # unused function — DI factory (bootstrapper/factories.py)
build_dp_wrapper  # unused function — DI factory (bootstrapper/factories.py)
build_ephemeral_storage_client  # unused function — DI factory (bootstrapper/main.py)
_cycle_detection_error_handler  # unused function — FastAPI exception handler (router_registry.py)
get_async_engine  # unused function — FastAPI Depends() (shared/db.py)
get_session  # unused function — FastAPI Depends() (shared/db.py)
get_async_session  # unused function — FastAPI Depends() (shared/db.py)
create_access_token  # unused function — JWT utility called by auth routes (shared/auth/jwt.py)

# ---------------------------------------------------------------------------
# Category D — Pydantic model fields and validators
# Pydantic's metaclass processes field definitions and validator decorators at
# class-creation time; direct attribute access is never the call site.
# ---------------------------------------------------------------------------

model_config  # unused variable — Pydantic ConfigDict field (multiple schemas)
hardware_id  # unused variable — Pydantic field (schemas/licensing.py)
app_version  # unused variable — Pydantic field (schemas/licensing.py)
qr_code  # unused variable — Pydantic field (schemas/licensing.py)
licensee  # unused variable — Pydantic field (schemas/licensing.py)
tier  # unused variable — Pydantic field (schemas/licensing.py)
artifact_path  # unused variable — Pydantic field (schemas/jobs.py, synthesizer/job_models.py)
output_path  # unused variable — Pydantic/SQLModel field (schemas/jobs.py, synthesizer/job_models.py, tasks.py)
validate_parquet_path  # unused method — Pydantic field_validator (schemas/jobs.py)
remaining_epsilon  # unused variable — Pydantic field (schemas/privacy.py)
is_exhausted  # unused variable — Pydantic field (schemas/privacy.py)

# ---------------------------------------------------------------------------
# Category D (continued) — SQLAlchemy model timestamp fields
# ORM columns accessed via query results, not direct Python attribute reads.
# ---------------------------------------------------------------------------

created_at  # unused variable — SQLAlchemy ORM column (shared/db.py)
updated_at  # unused variable — SQLAlchemy ORM column (shared/db.py)

# ---------------------------------------------------------------------------
# Category D (continued) — shared schema topology fields
# DataColumn.nullable is serialised into profile JSON consumed downstream.
# ---------------------------------------------------------------------------

nullable  # unused variable — DataColumn field (shared/schema_topology.py)

# ---------------------------------------------------------------------------
# Category D (continued) — JWT payload fields
# exp and iat are standard JWT claims written into the token dict; the dict
# is serialised as a whole — neither field is read back individually in src/.
# ---------------------------------------------------------------------------

exp  # unused variable — JWT claim field (shared/auth/jwt.py)
iat  # unused variable — JWT claim field (shared/auth/jwt.py)

# ---------------------------------------------------------------------------
# Category D (continued) — task model fields
# OrphanTaskReaper model columns; read by SQLAlchemy ORM, not by direct access.
# ---------------------------------------------------------------------------

started_at  # unused variable — ORM column (shared/tasks/reaper.py)
locked_by  # unused variable — ORM column (shared/tasks/reaper.py)

# ---------------------------------------------------------------------------
# Category D (continued) — PrivacyLedger ORM column
# last_updated is a SQLAlchemy column populated by server_onupdate; not read
# by application code directly.
# ---------------------------------------------------------------------------

last_updated  # unused variable — SQLAlchemy ORM column (modules/privacy/ledger.py)

# ---------------------------------------------------------------------------
# Category E — SQLAlchemy TypeDecorator protocol methods
# SQLAlchemy calls process_bind_param and process_result_value on custom column
# types; impl and cache_ok are required TypeDecorator class attributes.
# ---------------------------------------------------------------------------

impl  # unused variable — SQLAlchemy TypeDecorator required attribute (shared/security/ale.py)
cache_ok  # unused variable — SQLAlchemy TypeDecorator required attribute (shared/security/ale.py)
process_bind_param  # unused method — SQLAlchemy TypeDecorator protocol (shared/security/ale.py)
process_result_value  # unused method — SQLAlchemy TypeDecorator protocol (shared/security/ale.py)

# ---------------------------------------------------------------------------
# Category F — Domain classes/methods used only via DI, integration tests,
# or Huey task queue (runtime late-binding that vulture cannot trace).
# ---------------------------------------------------------------------------

PostgresIngestionAdapter  # unused class — used in integration tests and CLI ingestion path
preflight_check  # unused method — PostgresIngestionAdapter.preflight_check (ingestion)
stream_table  # unused method — PostgresIngestionAdapter.stream_table (ingestion)
get_schema_inspector  # unused method — PostgresIngestionAdapter.get_schema_inspector (ingestion)
nodes  # unused method — DependencyGraph.nodes property (modules/mapping/graph.py)
edges  # unused method — DependencyGraph.edges property (modules/mapping/graph.py)
has_cycle  # unused method — DependencyGraph.has_cycle (modules/mapping/graph.py)
MaskingRegistry  # unused class — wired via bootstrapper DI (modules/masking/registry.py)
profile  # unused method — StatisticalProfiler.profile called in synthesis pipeline
compare  # unused method — StatisticalProfiler.compare called in synthesis pipeline
written_tables  # unused property — EgressWriter.written_tables used in Saga rollback
generate  # unused method — SynthesisEngine.generate called by Huey task queue
upload_parquet  # unused method — EphemeralStorageClient.upload_parquet (synthesis pipeline)
download_parquet  # unused method — EphemeralStorageClient.download_parquet (synthesis pipeline)
load  # unused method — SynthesisModel.load called by SynthesisEngine (synthesizer/models.py)
_log_device_selection  # unused function — device logging utility (synthesizer/storage.py)
artifact_path  # unused attribute — Huey task result attribute (synthesizer/tasks.py)
generate_ale_key  # unused function — ALE key factory called by vault rotation (shared/security/ale.py)
verify_event  # unused method — AuditLogger.verify_event used in audit chain verification
deactivate  # unused method — LicenseManager.deactivate (shared/security/licensing.py)
get_claims  # unused method — LicenseManager.get_claims (shared/security/licensing.py)
OrphanTaskReaper  # unused class — wired into Huey task scheduler (shared/tasks/reaper.py)
reap  # unused method — OrphanTaskReaper.reap called by Huey scheduled task
IdempotencyMiddleware  # unused class — registered in ASGI middleware stack (shared/middleware/idempotency.py)

# ---------------------------------------------------------------------------
# Category G — Test-isolation utilities
# These are only called from test fixtures to reset global singletons between
# test cases.  They are deliberately NOT called from production paths.
# ---------------------------------------------------------------------------

reset  # unused method — VaultState.reset() / MaskingRegistry.reset() test-isolation helpers
check_budget  # unused method — DPTrainingWrapper.check_budget() exercised in unit + integration tests
reset_audit_logger  # unused function — test-isolation helper (shared/security/audit.py)
_reset_fernet_cache  # unused function — test-isolation helper (shared/security/ale.py)
dispose_engines  # unused function — engine cache teardown utility (shared/db.py); used in test teardown and app shutdown; T19.1
