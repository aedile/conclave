"""Settings sub-models and field mixin for the Conclave Engine.

Contains:
- Six structured view sub-models (TLSSettings, RateLimitSettings, etc.)
  used by the T70.4 ``@property`` accessors on ``ConclaveSettings``.
- ``ConclaveSettingsFields`` — a Pydantic ``BaseModel`` mixin that holds
  all field declarations and validator methods extracted from ``settings.py``
  (T74.3, ADV-P70-01).  ``ConclaveSettings`` in ``settings.py`` inherits from
  both this mixin and ``pydantic_settings.BaseSettings``, keeping
  ``settings.py`` well under 300 LOC.

**Zero-import constraint**: this module MUST NOT import from
``synth_engine.shared.settings`` — doing so would create a circular
dependency (settings.py imports from this file; this file must not
import back).

All six sub-models were previously defined inline in ``settings.py``
(T70.4) and extracted here verbatim at T71.4 with no behaviour changes.
``ConclaveSettingsFields`` was extracted in T74.3 — all field declarations
and validators moved here verbatim, no behaviour changes.

Backward compatibility: ``settings.py`` re-exports all names from this
module so existing callers that import sub-model names from
``shared.settings`` continue to work unchanged.

CONSTITUTION Priority 0: Security — centralized fail-fast configuration
CONSTITUTION Priority 5: Code Quality — file length, maintainability
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator

_logger = logging.getLogger(__name__)

#: Minimum structural length for a bcrypt hash ($2b$NN$<22-char salt><31-char hash>).
#: A full bcrypt output is 60 characters; 59 is the minimum we accept to guard against
#: truncation without calling bcrypt.checkpw() (which is intentionally slow).
_BCRYPT_HASH_PREFIX: str = "$2b$"
_BCRYPT_HASH_MIN_LENGTH: int = 59


# ---------------------------------------------------------------------------
# Structured view sub-models (T70.4, T71.4)
# ---------------------------------------------------------------------------


class TLSSettings(BaseModel):
    """TLS and SSL connection settings.

    Attributes:
        conclave_ssl_required: Enforce sslmode=require for PostgreSQL.
        conclave_tls_cert_path: Path to TLS certificate for health check.
    """

    conclave_ssl_required: bool
    conclave_tls_cert_path: str | None


class RateLimitSettings(BaseModel):
    """Rate limiting settings for all protected endpoints.

    Attributes:
        rate_limit_unseal_per_minute: Max /unseal requests per IP/min.
        rate_limit_auth_per_minute: Max /auth/token requests per IP/min.
        rate_limit_general_per_minute: Max general requests per operator/min.
        rate_limit_download_per_minute: Max download requests per operator/min.
        conclave_rate_limit_fail_open: Fail-open on Redis failure when True.
        conclave_trusted_proxy_count: Number of trusted reverse proxies.
    """

    rate_limit_unseal_per_minute: int
    rate_limit_auth_per_minute: int
    rate_limit_general_per_minute: int
    rate_limit_download_per_minute: int
    conclave_rate_limit_fail_open: bool
    conclave_trusted_proxy_count: int


class WebhookSettings(BaseModel):
    """Webhook delivery and circuit-breaker settings.

    Attributes:
        webhook_max_registrations: Max active registrations per operator.
        webhook_delivery_timeout_seconds: HTTP timeout per delivery attempt.
        webhook_circuit_breaker_threshold: Consecutive failures to trip circuit.
        webhook_circuit_breaker_cooldown_seconds: Cooldown after circuit trips.
    """

    webhook_max_registrations: int
    webhook_delivery_timeout_seconds: int
    webhook_circuit_breaker_threshold: int
    webhook_circuit_breaker_cooldown_seconds: int


class RetentionSettings(BaseModel):
    """Data retention policy settings.

    Attributes:
        job_retention_days: Days to retain synthesis_job records.
        audit_retention_days: Days to retain audit events (archive threshold).
        artifact_retention_days: Days to retain Parquet artifact files.
    """

    job_retention_days: int
    audit_retention_days: int
    artifact_retention_days: int


class ParquetSettings(BaseModel):
    """Parquet memory bounds and path sandbox settings.

    Attributes:
        parquet_max_file_bytes: Maximum Parquet file size in bytes.
        parquet_max_rows: Maximum number of rows in a loaded DataFrame.
        conclave_data_dir: Base directory for the parquet_path sandbox.
    """

    parquet_max_file_bytes: int
    parquet_max_rows: int
    conclave_data_dir: str


class AnchorSettings(BaseModel):
    """Audit trail anchoring settings.

    Attributes:
        anchor_backend: Backend type ('local_file' or 's3_object_lock').
        anchor_file_path: File path for local-file anchor backend.
        anchor_every_n_events: Publish anchor every N audit events.
        anchor_every_seconds: Maximum interval between anchors in seconds.
    """

    anchor_backend: str
    anchor_file_path: str
    anchor_every_n_events: int
    anchor_every_seconds: int


# ---------------------------------------------------------------------------
# Module-level helpers used by ConclaveSettingsFields validators
# ---------------------------------------------------------------------------


def _collect_production_field_errors(settings: ConclaveSettingsFields) -> list[str]:
    """Collect validation errors for production-required fields (T57.3, T63.1).

    Args:
        settings: The partially-constructed ConclaveSettingsFields instance.

    Returns:
        List of human-readable error strings; empty when not in production.
    """
    if settings.conclave_env.lower() != "production":
        return []
    errors: list[str] = []
    _prd = "in production mode (CONCLAVE_ENV=production)."
    if not settings.database_url or not settings.database_url.strip():
        errors.append(
            f"database_url must not be empty {_prd} Set DATABASE_URL to a valid PostgreSQL DSN."
        )
    audit_val = settings.audit_key.get_secret_value()
    if not audit_val or not audit_val.strip():
        errors.append(
            f"audit_key must not be empty {_prd} Set AUDIT_KEY to a hex-encoded 32-byte HMAC key."
        )
    signing_val = (
        settings.artifact_signing_key.get_secret_value()
        if settings.artifact_signing_key is not None
        else ""
    )
    if not (signing_val and signing_val.strip()) and not settings.artifact_signing_keys:
        errors.append(
            f"artifact_signing_key (or artifact_signing_keys) must not be empty {_prd} "
            "Set ARTIFACT_SIGNING_KEY to a hex-encoded HMAC key."
        )
    salt_val = settings.masking_salt.get_secret_value() if settings.masking_salt is not None else ""
    if not salt_val or not salt_val.strip():
        errors.append(
            f"masking_salt must not be empty {_prd} Set MASKING_SALT to a secret salt value."
        )
    jwt_val = settings.jwt_secret_key.get_secret_value()
    if not jwt_val or not jwt_val.strip():
        errors.append(
            f"jwt_secret_key must not be empty {_prd} Set JWT_SECRET_KEY to a random string."
        )
    _check_operator_credentials_hash(settings.operator_credentials_hash, errors)
    return errors


def _check_operator_credentials_hash(hash_value: str | None, errors: list[str]) -> None:
    """Append an error if operator_credentials_hash is absent or malformed.

    Args:
        hash_value: The operator_credentials_hash field value.
        errors: Mutable list to append error strings into.
    """
    if not hash_value:
        errors.append(
            "operator_credentials_hash must not be empty in production mode "
            "(CONCLAVE_ENV=production). Set OPERATOR_CREDENTIALS_HASH to a bcrypt hash."
        )
    elif not (
        hash_value.startswith(_BCRYPT_HASH_PREFIX) and len(hash_value) >= _BCRYPT_HASH_MIN_LENGTH
    ):
        errors.append(
            "OPERATOR_CREDENTIALS_HASH has an invalid format — "
            f"expected a bcrypt hash starting with '{_BCRYPT_HASH_PREFIX}' "
            f"and at least {_BCRYPT_HASH_MIN_LENGTH} characters long."
        )


# ---------------------------------------------------------------------------
# ConclaveSettingsFields — all field declarations and validators (T74.3)
# ---------------------------------------------------------------------------


class ConclaveSettingsFields(BaseModel):
    """Field declarations and validators for ConclaveSettings (T74.3).

    This mixin holds every field and every ``@model_validator`` extracted from
    ``settings.py``.  ``ConclaveSettings`` (in ``settings.py``) inherits from
    both this class and ``pydantic_settings.BaseSettings``, which provides
    environment-variable loading.

    **Why a mixin?**  ``settings.py`` previously exceeded 1,000 LOC.  Extracting
    fields and validators here reduces ``settings.py`` to the composition root
    (model_config, property accessors, ``is_production()``, ``get_settings()``),
    well within the 300-LOC target (ADV-P70-01).

    All field semantics and validators are preserved verbatim from ``settings.py``.
    No behaviour changes in T74.3.
    """

    # -----------------------------------------------------------------------
    # Required in all modes
    # -----------------------------------------------------------------------

    database_url: str = Field(
        default="",
        validation_alias=AliasChoices("CONCLAVE_DATABASE_URL", "DATABASE_URL"),
        description=(
            "Async-compatible PostgreSQL DSN or SQLite URL. "
            "Required at runtime — startup validation enforced by "
            "config_validation.validate_config(). "
            "Accepts env vars: CONCLAVE_DATABASE_URL (preferred, T63.2) or "
            "DATABASE_URL (legacy, still supported)."
        ),
    )
    audit_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("CONCLAVE_AUDIT_KEY", "AUDIT_KEY"),
        description=(
            "Hex-encoded 32-byte HMAC key for audit event signing. "
            "Required at runtime — startup validation enforced by "
            "config_validation.validate_config(). "
            "SecretStr — raw value never exposed in repr or model_dump(). "
            "Accepts env vars: CONCLAVE_AUDIT_KEY (preferred, T63.2) or "
            "AUDIT_KEY (legacy, still supported)."
        ),
    )

    # -----------------------------------------------------------------------
    # Optional secrets (required only in production mode)
    # -----------------------------------------------------------------------

    artifact_signing_key: SecretStr | None = Field(
        default=None,
        description=(
            "Hex-encoded HMAC key for Parquet artifact signing. "
            "Legacy single-key mode — deprecated in favour of "
            "ARTIFACT_SIGNING_KEYS.  Required in production mode only "
            "when ARTIFACT_SIGNING_KEYS is absent. "
            "SecretStr — raw value never exposed in repr or model_dump()."
        ),
    )
    artifact_signing_keys: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "JSON-encoded dict mapping hex key ID strings to hex key strings. "
            "Enables multi-key rotation.  Example: "
            '\'{"00000001": "abcd...ef", "00000002": "1234...56"}\'. '
            "When set, takes precedence over ARTIFACT_SIGNING_KEY."
        ),
    )
    artifact_signing_key_active: str | None = Field(
        default=None,
        description=(
            "Hex key ID string identifying the currently active signing key "
            "in ARTIFACT_SIGNING_KEYS.  New artifacts are signed with this key. "
            "Old artifacts signed with any key in the map remain verifiable."
        ),
    )
    masking_salt: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CONCLAVE_MASKING_SALT", "MASKING_SALT"),
        description=(
            "Secret salt for deterministic HMAC masking. Required in production mode. "
            "SecretStr — raw value never exposed in repr or model_dump(). "
            "Accepts env vars: CONCLAVE_MASKING_SALT (preferred, T63.2) or "
            "MASKING_SALT (legacy, still supported)."
        ),
    )
    license_public_key: str | None = Field(
        default=None,
        description=(
            "PEM-encoded RSA public key for license JWT verification. "
            "Falls back to embedded placeholder when absent."
        ),
    )

    # -----------------------------------------------------------------------
    # Deployment mode
    # -----------------------------------------------------------------------

    conclave_env: str = Field(
        default="production",
        description=(
            "Deployment environment name (e.g. 'production'). "
            "Defaults to 'production' — secure-by-default (T50.3). "
            "Set to 'development' to enable development mode explicitly. "
            "Single source of truth for deployment mode (T57.6). "
            "Takes precedence over the legacy ENV field when both are set."
        ),
    )
    env: str = Field(
        default="",
        description=(
            "Deprecated. Use CONCLAVE_ENV instead. When set, a deprecation warning is logged. "
            "The effective mode is always determined by conclave_env (default: production)."
        ),
    )

    # -----------------------------------------------------------------------
    # TLS / SSL
    # -----------------------------------------------------------------------

    conclave_ssl_required: bool = Field(
        default=True,
        description=(
            "Enforce sslmode=require for PostgreSQL connections. "
            "Defaults to True.  Set to false only on Docker bridge networks."
        ),
    )
    conclave_tls_cert_path: str | None = Field(
        default=None,
        description=(
            "Path to a TLS certificate file used by the TLS-terminating reverse proxy "
            "(nginx, Caddy, or HAProxy).  When set, the T42.2 startup health check treats "
            "TLS as configured and suppresses the CONCLAVE_SSL_REQUIRED misconfiguration "
            "warning.  Maps to the CONCLAVE_TLS_CERT_PATH environment variable."
        ),
    )

    # -----------------------------------------------------------------------
    # Compute device
    # -----------------------------------------------------------------------

    force_cpu: bool = Field(
        default=False,
        description=(
            "Force CPU device selection regardless of CUDA availability. "
            "Set to true in CPU-only environments."
        ),
    )

    # -----------------------------------------------------------------------
    # Telemetry
    # -----------------------------------------------------------------------

    otel_exporter_otlp_endpoint: str | None = Field(
        default=None,
        description=(
            "OTLP gRPC endpoint URL for OpenTelemetry span export. "
            "When absent, falls back to InMemorySpanExporter (air-gap safe)."
        ),
    )

    # -----------------------------------------------------------------------
    # Task queue (Huey)
    # -----------------------------------------------------------------------

    huey_backend: str = Field(
        default="redis",
        description="Huey task queue backend: 'redis' (default) or 'memory'.",
    )
    huey_immediate: bool = Field(
        default=False,
        description=(
            "Execute Huey tasks synchronously in the calling process. "
            "Recommended for integration tests."
        ),
    )
    redis_url: str = Field(
        default="redis://redis:6379/0",
        description="Redis connection URL for the Huey Redis backend.",
    )

    # -----------------------------------------------------------------------
    # JWT Authentication (T39.1)
    # -----------------------------------------------------------------------

    jwt_algorithm: str = Field(
        default="HS256",
        description=(
            "JWT signing algorithm.  Pinned to prevent algorithm confusion attacks. "
            "Defaults to 'HS256'.  Supported: 'HS256', 'HS384', 'HS512'."
        ),
    )
    jwt_expiry_seconds: int = Field(
        default=3600,
        description=(
            "Lifetime of issued JWT tokens in seconds.  Defaults to 3600 (1 hour). "
            "Operators should use short-lived tokens in production."
        ),
    )
    operator_credentials_hash: str = Field(
        default="",
        description=(
            "bcrypt hash of the operator passphrase for POST /auth/token. "
            "Empty string disables token issuance — no operator configured."
        ),
    )
    jwt_secret_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("CONCLAVE_JWT_SECRET_KEY", "JWT_SECRET_KEY"),
        description=(
            "HMAC secret key for JWT signing and verification. "
            "Required when jwt_algorithm is HS256/HS384/HS512. "
            "Must be a cryptographically random string of at least 32 characters. "
            "Empty string only acceptable in development/test environments. "
            "SecretStr — raw value never exposed in repr or model_dump(). "
            "Accepts env vars: CONCLAVE_JWT_SECRET_KEY (preferred, T63.2) or "
            "JWT_SECRET_KEY (legacy, still supported)."
        ),
    )

    # -----------------------------------------------------------------------
    # Rate Limiting (T39.3)
    # -----------------------------------------------------------------------

    rate_limit_unseal_per_minute: int = Field(
        default=5,
        description=(
            "Maximum requests to /unseal per IP per minute. "
            "Brute-force protection for the vault unseal endpoint. "
            "Defaults to 5 per the T39.3 security specification."
        ),
    )
    rate_limit_auth_per_minute: int = Field(
        default=10,
        description=(
            "Maximum requests to /auth/token per IP per minute. "
            "Credential stuffing protection for the authentication endpoint. "
            "Defaults to 10 per the T39.3 security specification."
        ),
    )
    rate_limit_general_per_minute: int = Field(
        default=60,
        description=(
            "Maximum requests per authenticated operator per minute on all other endpoints. "
            "Defaults to 60 per the T39.3 security specification."
        ),
    )
    rate_limit_download_per_minute: int = Field(
        default=10,
        description=(
            "Maximum download requests per authenticated operator per minute. "
            "Bandwidth protection for /jobs/{id}/download. "
            "Defaults to 10 per the T39.3 security specification."
        ),
    )
    conclave_rate_limit_fail_open: bool = Field(
        default=False,
        description=(
            "When True, restores pre-P63 in-memory fallback behavior on Redis failure "
            "(fail-open: requests are allowed through). "
            "When False (default), requests are rejected with 429 after a 5-second grace "
            "period on Redis failure (fail-closed). "
            "WARNING: enabling fail-open in production disables distributed rate limiting "
            "during Redis outages, which allows brute-force and DoS attacks to bypass "
            "per-IP limits. Set CONCLAVE_RATE_LIMIT_FAIL_OPEN=true only for non-production "
            "environments or with explicit security review. "
            "T63.3 — Rate Limiter Fail-Closed on Redis Failure."
        ),
    )
    conclave_trusted_proxy_count: int = Field(
        default=0,
        ge=0,
        le=10,
        description=(
            "Number of trusted reverse proxies in front of the application. "
            "Controls X-Forwarded-For validation in the rate limiter (T66.3). "
            "0 (default, zero-trust): X-Forwarded-For is ignored entirely — the "
            "socket IP is always used as the rate-limit key. "
            "N > 0: the Nth-from-right XFF entry is used as the client IP, "
            "which is the real client IP when exactly N proxies each append their "
            "own IP to the header. If XFF has fewer than N+1 entries, falls back "
            "to the socket IP (fail-closed). Invalid IPs also fall back to the "
            "socket IP to prevent log injection. "
            "WARNING: set this to the exact number of trusted proxies — "
            "undercount allows IP spoofing; overcount strips real client IPs. "
            "Requires CONCLAVE_TRUSTED_PROXY_COUNT environment variable. "
            "T66.3 — Trusted Proxy Validation for X-Forwarded-For."
        ),
    )

    # -----------------------------------------------------------------------
    # Data Retention Policy (T41.1)
    # -----------------------------------------------------------------------

    job_retention_days: int = Field(
        default=90,
        ge=1,
        description=(
            "Number of days to retain synthesis_job records before they are "
            "eligible for routine purge.  Jobs with legal_hold=True are exempt "
            "from purge regardless of this TTL.  Defaults to 90 days."
        ),
    )
    audit_retention_days: int = Field(
        default=1095,
        ge=1,
        description=(
            "Number of days to retain audit events before they may be archived "
            "to cold storage.  Audit events are NEVER deleted during the retention "
            "period — only archived.  Defaults to 1095 (3 years, GDPR minimum). "
            "Set to 2555 (7 years) for financial-services deployments."
        ),
    )
    artifact_retention_days: int = Field(
        default=30,
        ge=1,
        description=(
            "Number of days to retain generated Parquet artifact files before "
            "they are eligible for deletion by the retention cleanup task. "
            "Defaults to 30 days."
        ),
    )

    # -----------------------------------------------------------------------
    # Idempotency (T45.1)
    # -----------------------------------------------------------------------

    idempotency_ttl_seconds: int = Field(
        default=300,
        ge=1,
        description=(
            "Time-to-live in seconds for idempotency keys stored in Redis. "
            "A key is valid for this many seconds after it is first set; "
            "within this window, duplicate requests with the same key receive "
            "HTTP 409.  Must be >= 1.  Defaults to 300 (5 minutes). "
            "Configure to cover the maximum expected request latency plus "
            "any client retry window."
        ),
    )

    # -----------------------------------------------------------------------
    # Orphan Task Reaper (T45.2)
    # -----------------------------------------------------------------------

    reaper_stale_threshold_minutes: int = Field(
        default=60,
        ge=5,
        description=(
            "Number of minutes after which an IN_PROGRESS synthesis job is "
            "considered orphaned and eligible for reaping.  Must be >= 5 to "
            "prevent accidental mass-reaping.  Defaults to 60 minutes."
        ),
    )

    # -----------------------------------------------------------------------
    # Webhook Callbacks (T45.3)
    # -----------------------------------------------------------------------

    webhook_max_registrations: int = Field(
        default=10,
        ge=1,
        description=(
            "Maximum number of active webhook registrations per operator. "
            "Enforced at POST /webhooks time.  Defaults to 10."
        ),
    )
    webhook_delivery_timeout_seconds: int = Field(
        default=10,
        ge=1,
        description=(
            "HTTP timeout in seconds for each webhook delivery attempt. "
            "Applied per-attempt; total time can be up to 3x for 3 retries. "
            "Defaults to 10 seconds."
        ),
    )
    webhook_circuit_breaker_threshold: int = Field(
        default=3,
        ge=1,
        description=(
            "Number of consecutive delivery failures before the webhook circuit "
            "breaker trips for a given callback URL. Defaults to 3."
        ),
    )
    webhook_circuit_breaker_cooldown_seconds: int = Field(
        default=300,
        ge=1,
        description=(
            "Cooldown duration in seconds after the webhook circuit breaker trips. "
            "During this period, deliveries to the affected URL are skipped. "
            "After cooldown, one probe delivery is attempted; if it succeeds the "
            "circuit resets. Defaults to 300 seconds (5 minutes)."
        ),
    )

    # -----------------------------------------------------------------------
    # mTLS Inter-Container Communication (T46.2)
    # -----------------------------------------------------------------------

    mtls_enabled: bool = Field(
        default=False,
        description=(
            "Enable mTLS for all inter-container connections. "
            "Defaults to False for backward compatibility.  When True, "
            "all data-plane connections use TLS with mutual certificate authentication."
        ),
    )
    mtls_ca_cert_path: str = Field(
        default="secrets/mtls/ca.crt",
        description=(
            "Path to the mTLS CA certificate.  Used when MTLS_ENABLED=true to verify "
            "the server certificate for PostgreSQL and Redis connections."
        ),
    )
    mtls_client_cert_path: str = Field(
        default="secrets/mtls/app.crt",
        description=(
            "Path to the mTLS client certificate.  Presented to PostgreSQL and Redis "
            "servers for mutual authentication when MTLS_ENABLED=true."
        ),
    )
    mtls_client_key_path: str = Field(
        default="secrets/mtls/app.key",
        description=(
            "Path to the mTLS client private key.  Must correspond to the certificate "
            "at MTLS_CLIENT_CERT_PATH.  Used when MTLS_ENABLED=true."
        ),
    )

    # -----------------------------------------------------------------------
    # Parquet Memory Bounds (T47.7)
    # -----------------------------------------------------------------------

    parquet_max_file_bytes: int = Field(
        default=2 * 1024**3,
        gt=0,
        description=(
            "Maximum Parquet file or payload size in bytes before loading into memory. "
            "Size check fires before row-count check to reject oversized data early. "
            "Defaults to 2 GiB.  Must be > 0."
        ),
    )
    parquet_max_rows: int = Field(
        default=10_000_000,
        gt=0,
        description=(
            "Maximum number of rows permitted in a loaded Parquet DataFrame. "
            "Row-count check fires after loading; raises DatasetTooLargeError when "
            "the limit is exceeded.  Defaults to 10,000,000.  Must be > 0."
        ),
    )

    # -----------------------------------------------------------------------
    # Audit Trail Anchoring (T48.4)
    # -----------------------------------------------------------------------

    anchor_backend: str = Field(
        default="local_file",
        description=(
            "Anchor backend type for audit trail anchoring. "
            "Either 'local_file' or 's3_object_lock'. "
            "Defaults to 'local_file'."
        ),
    )
    anchor_file_path: str = Field(
        default="logs/audit_anchors.jsonl",
        description=(
            "File path for the local-file anchor backend. Defaults to 'logs/audit_anchors.jsonl'."
        ),
    )
    anchor_every_n_events: int = Field(
        default=1000,
        gt=0,
        description=("Publish an anchor every N audit events. Must be > 0.  Defaults to 1000."),
    )
    anchor_every_seconds: int = Field(
        default=86400,
        gt=0,
        description=(
            "Publish an anchor at most once per this many seconds, "
            "regardless of event count.  Must be > 0.  Defaults to 86400 (24 h)."
        ),
    )

    # -----------------------------------------------------------------------
    # Health Check Strict Mode (T68.4)
    # -----------------------------------------------------------------------

    conclave_health_strict: bool | None = Field(
        default=None,
        description=(
            "When True, /ready returns 503 if any configured service (database, Redis) "
            "is unreachable or unconfigured-but-expected in strict mode. "
            "When False (permissive), unconfigured services are skipped (development behavior). "
            "When None (default), auto-detected: True in production, False in development. "
            "Set CONCLAVE_HEALTH_STRICT=true to force strict mode in any environment. "
            "T68.4 — Health Check Strict Mode for Production."
        ),
    )

    # -----------------------------------------------------------------------
    # Parquet Path Sandbox (T69.7)
    # -----------------------------------------------------------------------

    conclave_data_dir: str = Field(
        default="data/",
        description=(
            "Base directory that parquet_path values must resolve to (path sandbox). "
            "All JobCreateRequest.parquet_path values must be inside this directory after "
            "Path.resolve(). Defaults to 'data/' (resolved to absolute at construction). "
            "Forbid root '/' to prevent the sandbox from being a no-op. "
            "In production mode, the directory must exist at startup. "
            "T69.7 — Sandbox parquet_path to Allowed Directory (ADV-P68-02)."
        ),
    )

    # -----------------------------------------------------------------------
    # Database Connection Pool (T74.1)
    # -----------------------------------------------------------------------

    conclave_db_pool_size: int = Field(
        default=5,
        gt=0,
        le=50,
        description=(
            "SQLAlchemy QueuePool size for the FastAPI connection pool. "
            "In production, PgBouncer handles external multiplexing so this is "
            "intentionally modest. Must be > 0 and ≤ 50. Defaults to 5 (original "
            "_POOL_SIZE hardcoded value). T74.1 — Externalize DB pool parameters."
        ),
    )
    conclave_db_max_overflow: int = Field(
        default=10,
        gt=0,
        le=200,
        description=(
            "SQLAlchemy QueuePool max_overflow for the FastAPI connection pool. "
            "Temporary connections allowed above pool_size. Must be > 0 and ≤ 200. "
            "Defaults to 10 (original _MAX_OVERFLOW hardcoded value). T74.1."
        ),
    )
    conclave_db_worker_pool_size: int = Field(
        default=1,
        gt=0,
        le=10,
        description=(
            "SQLAlchemy QueuePool size for Huey worker connections. "
            "Each worker handles one task at a time; pool_size=1 provides a single "
            "persistent connection. Must be > 0 and ≤ 10. Defaults to 1. T74.1."
        ),
    )
    conclave_db_worker_max_overflow: int = Field(
        default=2,
        gt=0,
        le=50,
        description=(
            "SQLAlchemy QueuePool max_overflow for Huey worker connections. "
            "Allows temporary burst connections. Must be > 0 and ≤ 50. Defaults to 2. T74.1."
        ),
    )
    conclave_db_worker_pool_recycle: int = Field(
        default=1800,
        gt=0,
        le=7200,
        description=(
            "Recycle Huey worker connections after this many seconds. "
            "Matches PgBouncer server_idle_timeout. Must be > 0 and ≤ 7200. "
            "Defaults to 1800 (30 minutes). T74.1."
        ),
    )
    conclave_db_worker_pool_timeout: int = Field(
        default=30,
        gt=0,
        le=300,
        description=(
            "Seconds to wait for a Huey worker connection from the pool before "
            "raising TimeoutError. Must be > 0 and ≤ 300. Defaults to 30. T74.1."
        ),
    )

    # -----------------------------------------------------------------------
    # Rate Limit Window (T74.2)
    # -----------------------------------------------------------------------

    conclave_rate_limit_window_seconds: int = Field(
        default=60,
        gt=0,
        le=3600,
        description=(
            "Duration in seconds of the fixed-window rate limit bucket. "
            "Controls the Redis key TTL and the window embedded in Redis key names. "
            "IMPORTANT: changing this value requires an application restart — "
            "the window is baked into the Redis key format at startup. "
            "Existing rate limit counters with the old window prefix will expire "
            "naturally (TTL = old window value). "
            "Must be > 0 and ≤ 3600. Defaults to 60 (per-minute buckets). T74.2."
        ),
    )

    # -----------------------------------------------------------------------
    # Validators
    # -----------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_production_required_fields(self) -> ConclaveSettingsFields:
        """Reject empty required fields in production; emit ENV= deprecation warning.

        T57.6: Emits deprecation WARNING when legacy ``ENV=`` is set.
        T57.3/T63.1: In production mode, validates all required secret fields.
        Collects all errors before raising so the operator gets a complete list.

        Returns:
            The validated instance (self).

        Raises:
            ValueError: When production mode is active and any required field is empty.
        """
        if self.env:
            _logger.warning(
                "ENV= environment variable is deprecated; migrate to CONCLAVE_ENV= instead. "
                "Current value: ENV=%r",
                self.env,
            )
            if self.env.lower() != self.conclave_env.lower():
                _logger.warning(
                    "ENV=%r and CONCLAVE_ENV=%r conflict; CONCLAVE_ENV takes precedence (T57.6).",
                    self.env,
                    self.conclave_env,
                )
        field_errors = _collect_production_field_errors(self)
        if field_errors:
            raise ValueError(" | ".join(field_errors))
        return self

    @model_validator(mode="after")
    def _validate_multi_key_signing_consistency(self) -> ConclaveSettingsFields:
        """Validate multi-key signing consistency in all deployment modes (T42.1).

        When ``artifact_signing_keys`` is non-empty, ``artifact_signing_key_active``
        must be set and present as a key within the map.  This prevents silent
        misconfiguration during key rotation where the active key pointer is
        forgotten or misspelled.

        Checked in ALL deployment modes (not just production) because rotation
        misconfiguration can cause signed artifacts to be unverifiable regardless
        of environment.

        Returns:
            The validated instance (self).

        Raises:
            ValueError: When ``artifact_signing_keys`` is non-empty but
                ``artifact_signing_key_active`` is absent or not in the map.
        """
        if not self.artifact_signing_keys:
            return self

        if not self.artifact_signing_key_active:
            raise ValueError(
                "artifact_signing_key_active (ARTIFACT_SIGNING_KEY_ACTIVE) must be set "
                "when artifact_signing_keys (ARTIFACT_SIGNING_KEYS) is non-empty. "
                "Set it to the hex key ID of the currently active signing key."
            )
        if self.artifact_signing_key_active not in self.artifact_signing_keys:
            raise ValueError(
                f"artifact_signing_key_active '{self.artifact_signing_key_active}' "
                f"is not present in artifact_signing_keys. "
                f"Known key IDs: {sorted(self.artifact_signing_keys.keys())}"
            )
        return self

    @model_validator(mode="after")
    def _warn_rate_limit_window_mismatch(self) -> ConclaveSettingsFields:
        """Warn when rate limit window diverges from the hardcoded per-minute periods.

        All rate limit tiers in RateLimitGateMiddleware are parsed as
        "X/minute" (60-second period).  conclave_rate_limit_window_seconds
        controls the Redis key TTL and bucket name but NOT the parsed period.
        Setting it to a non-60 value creates a semantic mismatch: the stated
        tier is "per minute" but the actual bucket window is different,
        weakening or strengthening all rate limits proportionally.

        Emits a WARNING in all deployment modes to surface the misconfiguration.
        This is a WARNING (not ValueError) because the setting has a valid use
        case if the operator explicitly changes the middleware period strings.

        Returns:
            The validated instance (self).
        """
        if self.conclave_rate_limit_window_seconds != 60:
            _logger.warning(
                "CONCLAVE_RATE_LIMIT_WINDOW_SECONDS=%d deviates from the default 60. "
                "All rate limit tiers are hardcoded as X/minute in RateLimitGateMiddleware. "
                "A non-60 window creates a mismatch: the Redis TTL (%ds) differs from the "
                "parsed period (60s), weakening or strengthening all limits proportionally. "
                "Ensure rate_limit_middleware.py limit period strings are updated to match. "
                "See .env.example for details. (T74.2 red-team finding)",
                self.conclave_rate_limit_window_seconds,
                self.conclave_rate_limit_window_seconds,
            )
        return self

    @model_validator(mode="after")
    def _warn_unrecognized_conclave_env_vars(self) -> ConclaveSettingsFields:
        """Log a WARNING for any CONCLAVE_ env var that doesn't match a known field.

        Keeps ``extra="ignore"`` (fail-open) to avoid breaking deployments with
        OS-level or CI-platform env vars.  The WARNING gives operators visibility
        into potential typos without causing startup failures.

        Known CONCLAVE_ fields are derived from :attr:`model_fields` by
        collecting the subset of field names that start with ``conclave_``.

        Returns:
            The validated instance (self).
        """
        # Collect known CONCLAVE_ env var names from two sources:
        # 1. Field names starting with "conclave_" (e.g. CONCLAVE_ENV, CONCLAVE_SSL_REQUIRED).
        # 2. AliasChoices entries starting with "CONCLAVE_" from any field's
        #    validation_alias (e.g. CONCLAVE_DATABASE_URL, CONCLAVE_AUDIT_KEY).
        #    This ensures the warning is NOT emitted for known T63.2 aliases.
        known_from_field_names: set[str] = {
            field_name.upper()
            for field_name in self.__class__.model_fields
            if field_name.startswith("conclave_")
        }
        known_from_aliases: set[str] = {
            alias.upper()
            for field_info in self.__class__.model_fields.values()
            if isinstance(field_info.validation_alias, AliasChoices)
            for alias in field_info.validation_alias.choices
            if isinstance(alias, str) and alias.upper().startswith("CONCLAVE_")
        }
        known_conclave_env_vars: frozenset[str] = frozenset(
            known_from_field_names | known_from_aliases
        )
        for env_var, value in os.environ.items():
            if env_var.startswith("CONCLAVE_") and env_var not in known_conclave_env_vars:
                # Redact the value — a typo like CONCLAVE_AUDIT_KEY
                # would otherwise emit raw key material to logs.
                _redacted = "***" if value else "(empty)"
                _logger.warning(
                    "Unrecognized CONCLAVE_ environment variable: %s=%s — "
                    "this variable is not a known ConclaveSettings field and will be ignored. "
                    "Check for typos. Known CONCLAVE_ vars: %s",
                    env_var,
                    _redacted,
                    sorted(known_conclave_env_vars),
                )
        return self

    @model_validator(mode="after")
    def _apply_health_strict_default(self) -> ConclaveSettingsFields:
        """Set conclave_health_strict based on deployment mode when not explicitly configured.

        Per T68.4 spec amendment: the field is bool | None = None.
        When None (not explicitly set), the value is auto-derived:
        - True in production (fail-closed by default).
        - False in development (permissive by default, preserving current behavior).

        This gives environment-dependent defaults while allowing explicit override
        via CONCLAVE_HEALTH_STRICT=true/false in any environment.

        Returns:
            The validated instance (self).
        """
        if self.conclave_health_strict is None:
            self.conclave_health_strict = self.conclave_env.lower() == "production"
        return self

    @model_validator(mode="after")
    def _validate_conclave_data_dir(self) -> ConclaveSettingsFields:
        """Validate and resolve conclave_data_dir (T69.7, ADV-P68-02).

        Rules (enforced in order):
        1. Resolve to absolute path so relative dirs like 'data/' work.
        2. Reject '/' (filesystem root) — allows ALL paths, defeating sandbox.
        3. In production mode, require the directory to exist.

        The resolved absolute path is stored back on the instance so
        validate_parquet_path in JobCreateRequest can use it consistently.

        Returns:
            The validated instance (self).

        Raises:
            ValueError: When conclave_data_dir is '/' or, in production mode,
                when the directory does not exist.
        """
        resolved = Path(self.conclave_data_dir).resolve()

        # Rule 2: forbid root — would allow any path on the filesystem.
        if str(resolved) == "/":
            raise ValueError(
                "CONCLAVE_DATA_DIR must not be the filesystem root '/'. "
                "Set it to a specific data directory (e.g. 'data/' or '/app/data')."
            )

        # Rule 3: production requires the directory to exist when explicitly configured.
        # The default 'data/' is created at runtime by the synthesizer pipeline;
        # only reject if the operator has set a custom directory that does not exist.
        _default_resolved = Path("data/").resolve()
        is_default_dir = resolved == _default_resolved
        if (
            self.conclave_env.lower() == "production"
            and not is_default_dir
            and not resolved.exists()
        ):
            raise ValueError(
                f"CONCLAVE_DATA_DIR '{self.conclave_data_dir}' (resolved: {resolved}) "
                f"does not exist. Create the directory before starting the application "
                f"in production mode."
            )

        # Store resolved absolute path so consumers get a consistent string.
        self.conclave_data_dir = str(resolved)
        return self


__all__ = [
    "AnchorSettings",
    "ConclaveSettingsFields",
    "ParquetSettings",
    "RateLimitSettings",
    "RetentionSettings",
    "TLSSettings",
    "WebhookSettings",
]
