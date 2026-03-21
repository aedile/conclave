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
download_job  # unused function — FastAPI route handler (routers/jobs.py)
shred_job  # unused function — FastAPI route handler (routers/jobs.py)
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
post_auth_token  # unused function — FastAPI route handler (routers/auth.py)
set_legal_hold  # unused function — FastAPI route handler (routers/admin.py)
erasure  # unused function — FastAPI route handler (routers/compliance.py)

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

require_unsealed  # unused function — FastAPI Depends() (dependencies/vault.py)
build_synthesis_engine  # unused function — DI factory (bootstrapper/factories.py)
build_ephemeral_storage_client  # unused function — DI factory (bootstrapper/main.py)
_cycle_detection_error_handler  # unused function — FastAPI exception handler (router_registry.py)
_budget_exhaustion_error_handler  # unused function — FastAPI exception handler (router_registry.py)
_oom_guardrail_error_handler  # unused function — FastAPI exception handler (router_registry.py)
_vault_sealed_error_handler  # unused function — FastAPI exception handler (router_registry.py)
_vault_already_unsealed_error_handler  # unused function — FastAPI exception handler (router_registry.py)
_license_error_handler  # unused function — FastAPI exception handler (router_registry.py)
_collision_error_handler  # unused function — FastAPI exception handler (router_registry.py)
_privilege_escalation_error_handler  # unused function — FastAPI exception handler (router_registry.py)
_artifact_tampering_error_handler  # unused function — FastAPI exception handler (router_registry.py)
get_async_engine  # unused function — FastAPI Depends() (shared/db.py)
get_session  # unused function — FastAPI Depends() (shared/db.py)
get_async_session  # unused function — FastAPI Depends() (shared/db.py)

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
validate_parquet_path  # unused method — Pydantic field_validator (schemas/jobs.py)
remaining_epsilon  # unused variable — Pydantic field (schemas/privacy.py)
is_exhausted  # unused variable — Pydantic field (schemas/privacy.py)
access_token  # unused variable — Pydantic field (routers/auth.py TokenResponse)
job_id  # unused variable — Pydantic field (routers/admin.py LegalHoldResponse)
enable  # unused variable — Pydantic field (routers/admin.py LegalHoldRequest)
token_type  # unused variable — Pydantic field (routers/auth.py TokenResponse)

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
# Category D (continued) — PrivacyLedger ORM column
# last_updated is a SQLAlchemy column populated by server_onupdate; not read
# by application code directly.
# ---------------------------------------------------------------------------

last_updated  # unused variable — SQLAlchemy ORM column (modules/privacy/ledger.py)

# ---------------------------------------------------------------------------
# Category D (continued) — HTTP error status_code fields
# status_code is a dataclass field on OperatorErrorEntry (bootstrapper/errors.py)
# and an instance attribute on VaultSealedError (shared/exceptions.py).
# Both are read by FastAPI exception handlers via dynamic attribute access;
# vulture cannot trace dict-lookup or exception-handler attribute reads.
# ---------------------------------------------------------------------------

status_code  # unused variable — error presentation field (bootstrapper/errors.py, shared/exceptions.py)

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
upload_parquet  # unused method — EphemeralStorageClient.upload_parquet (synthesis pipeline)
download_parquet  # unused method — EphemeralStorageClient.download_parquet (synthesis pipeline)
load  # unused method — ModelArtifact.load @classmethod; not called from production src yet — entry point for inference pipeline (synthesizer/models.py)
_log_device_selection  # unused function — device selection utility called by tests; not called from production src directly (synthesizer/storage.py)
generate_ale_key  # unused function — ALE key provisioning utility exported for operator use (shared/security/ale.py); not called from src — intended for one-time key generation at host setup
verify_event  # unused method — AuditLogger.verify_event used in audit chain verification
deactivate  # unused method — LicenseManager.deactivate (shared/security/licensing.py)
RetentionCleanup  # unused class — called by Huey scheduled tasks / CLI (modules/synthesizer/retention.py)
cleanup_expired_jobs  # unused method — RetentionCleanup.cleanup_expired_jobs (retention.py)
cleanup_expired_artifacts  # unused method — RetentionCleanup.cleanup_expired_artifacts (retention.py)
periodic_cleanup_expired_jobs  # unused function — Huey periodic task, registered at 02:00 UTC (retention_tasks.py)
periodic_cleanup_expired_artifacts  # unused function — Huey periodic task, registered at 03:00 UTC (retention_tasks.py)
get_claims  # unused method — LicenseManager.get_claims (shared/security/licensing.py)

# ---------------------------------------------------------------------------
# Category G — Test-isolation utilities
# These are only called from test fixtures to reset global singletons between
# test cases.  They are deliberately NOT called from production paths.
# ---------------------------------------------------------------------------

reset  # unused method — VaultState.reset() / MaskingRegistry.reset() test-isolation helpers
reset_audit_logger  # unused function — test-isolation helper (shared/security/audit.py)
_reset_fernet_cache  # unused function — no-op retained for backward compat with pre-vault-wiring tests (shared/security/ale.py)
dispose_engines  # unused function — engine cache teardown utility (shared/db.py); used in test teardown and app shutdown; T19.1

# ---------------------------------------------------------------------------
# Category H — T30.3 discriminator-level DP-SGD training loop entries
# ---------------------------------------------------------------------------

forward  # unused method — OpacusCompatibleDiscriminator.forward() called by PyTorch nn.Module
calc_gradient_penalty  # unused method — OpacusCompatibleDiscriminator.calc_gradient_penalty() called by unit tests for WGAN gradient penalty validation

# ---------------------------------------------------------------------------
# Category I — ConclaveSettings public API
# is_production() is a public method on ConclaveSettings consumed by
# tests (test_settings.py) and available for production callers that want
# to check deployment mode. Vulture cannot trace method calls on BaseSettings
# instances that are accessed via get_settings() singleton.
# ---------------------------------------------------------------------------

is_production  # unused method — ConclaveSettings.is_production() (shared/settings.py)
audit_retention_days  # unused variable — ConclaveSettings field read by operator tooling (shared/settings.py)
artifact_retention_days  # unused variable — ConclaveSettings field read by operator tooling (shared/settings.py)

# ---------------------------------------------------------------------------
# Category J — T42.2 HTTPS enforcement startup hook
# warn_if_ssl_misconfigured is called from config_validation.validate_config()
# at startup.  Vulture cannot trace cross-module function calls that go
# through a concrete import in config_validation.py.
# ---------------------------------------------------------------------------

warn_if_ssl_misconfigured  # unused function — startup hook called by config_validation.validate_config() (bootstrapper/dependencies/https_enforcement.py)
