"""Centralized Pydantic settings model for the Conclave Engine.

All environment variables consumed by the engine are declared here as typed
fields with defaults and validators.  This provides a single discoverable
source of truth for configuration, replaces scattered ``os.environ.get()``
calls throughout the codebase, and ensures fail-fast validation at startup.

Vault-deferred values
---------------------
``VAULT_SEAL_SALT`` is intentionally excluded from this model.  It is read
only at vault unseal time (inside :meth:`VaultState.unseal()`) — never at
application boot.  Including it here would force operators to provide the salt
before the application starts, which violates the vault's deferred security
model.

Secret fields
-------------
``audit_key``, ``jwt_secret_key``, ``artifact_signing_key``, and
``masking_salt`` are declared as ``pydantic.SecretStr`` (or
``pydantic.SecretStr | None`` where the field is optional).  This prevents
raw key material from appearing in ``repr(settings)``, ``model_dump()``,
and Pydantic validation error messages.  To read the underlying value, call
``.get_secret_value()`` on the field.

Usage
-----
Consume settings via the :func:`get_settings` singleton::

    from synth_engine.shared.settings import get_settings

    s = get_settings()
    db_url = s.database_url

In FastAPI routes, inject via ``Depends``::

    from fastapi import Depends
    from synth_engine.shared.settings import ConclaveSettings, get_settings

    def my_route(settings: ConclaveSettings = Depends(get_settings)) -> ...:
        ...

Boundary constraints
--------------------
``shared/`` must not import from ``modules/`` or ``bootstrapper/``.
This module imports only ``pydantic-settings`` and stdlib — no violation.

CONSTITUTION Priority 0: Security — centralized fail-fast configuration
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T36.1 — Centralize Configuration Into Pydantic Settings Model
Task: T39.1 — Add Authentication Middleware (JWT Bearer Token)
Task: T39.3 — Add Rate Limiting Middleware
Task: T41.1 — Implement Data Retention Policy
Task: T42.2 — Add HTTPS Enforcement & Deployment Safety Checks
Task: T42.1 — Artifact Signing Key Versioning (multi-key support)
Task: T45.2 — Reintroduce Orphan Task Reaper (TBD-08)
Task: T45.3 — Implement Webhook Callbacks for Task Completion
Task: T46.2 — Wire mTLS on All Container-to-Container Connections
Task: T47.7 — Add Parquet Memory Bounds (parquet_max_file_bytes, parquet_max_rows)
Task: T48.4 — Audit Trail Anchoring (anchor_backend, anchor_file_path,
              anchor_every_n_events, anchor_every_seconds)
Task: T50.3 — Default to Production Mode (secure-by-default)
Task: fix/review-critical-issues — Use SecretStr for secret fields
Task: T57.3 — Production-Mode Validation for Required Settings
Task: T57.6 — Unify Environment Configuration
Task: T63.3 — Rate Limiter Fail-Closed on Redis Failure (rate_limit_fail_open)
Task: T63.1 — Consolidate Settings Validation
Task: T63.2 — Unify Environment Variable Naming
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_logger = logging.getLogger(__name__)

#: Minimum structural length for a bcrypt hash ($2b$NN$<22-char salt><31-char hash>).
#: A full bcrypt output is 60 characters; 59 is the minimum we accept to guard against
#: truncation without calling bcrypt.checkpw() (which is intentionally slow).
_BCRYPT_HASH_PREFIX: str = "$2b$"
_BCRYPT_HASH_MIN_LENGTH: int = 59


# ---------------------------------------------------------------------------
# Settings sub-models (T70.4) — grouped views for organized access.
# These are plain BaseModel classes; ConclaveSettings aggregates them via
# @property accessors to preserve all existing flat-field access patterns.
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


class ConclaveSettings(BaseSettings):
    """Pydantic BaseSettings model for the Conclave Engine.

    All environment variables consumed by the engine are declared here.
    Fields map directly to environment variable names (case-insensitive).
    Required fields with no default will raise a ``ValidationError`` at
    construction time if the corresponding env var is absent or empty.

    Vault-deferred values (``VAULT_SEAL_SALT``) are intentionally excluded.
    See module docstring for rationale.

    Secret fields (``audit_key``, ``jwt_secret_key``, ``artifact_signing_key``,
    ``masking_salt``) are typed as ``pydantic.SecretStr`` so that raw key
    material is never exposed via ``repr()``, ``model_dump()``, or Pydantic
    validation errors.  Access the underlying value with ``.get_secret_value()``.

    Each field carries a ``Field(description=...)`` annotation that is the
    authoritative source of truth for that field's semantics.  See the field
    declarations below for per-field documentation.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # env_prefix="" — no prefix; env var names match field names exactly
        # (e.g. DATABASE_URL -> database_url after Pydantic's lowercasing).
        extra="ignore",
        case_sensitive=False,
    )

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
    # Validators
    # -----------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_production_required_fields(self) -> ConclaveSettings:
        """Reject empty required fields in production and handle ENV= alias (T57.3, T57.6, T63.1).

        Three concerns are handled here:

        **T57.6 — ENV= alias resolution**: If the legacy ``ENV=`` variable is set,
        a deprecation WARNING is emitted.  When both ``ENV`` and ``CONCLAVE_ENV``
        are set and conflict, ``conclave_env`` takes precedence.

        **T57.3 — Always-required field validation**: ``database_url`` and
        ``audit_key`` are validated in production mode at construction time.
        In non-production environments these fields may be empty — validation
        is deferred to ``config_validation.validate_config()`` at startup.

        **T63.1 — Production-required field validation**: In production mode,
        additional fields are required: ``artifact_signing_key`` or
        ``artifact_signing_keys`` (at least one), ``masking_salt``,
        ``jwt_secret_key``, and ``operator_credentials_hash``.

        All errors are collected before raising so the operator receives a complete
        list in a single error message.

        The ``database_url`` value is **never** included in error messages —
        it may contain credentials (user:password@host).

        Returns:
            The validated ``ConclaveSettings`` instance (self).

        Raises:
            ValueError: When production mode is active and any required field is
                empty or whitespace-only.
        """
        # T57.6: Emit WARNING whenever legacy ENV= is set.
        if self.env:
            _logger.warning(
                "ENV= environment variable is deprecated; migrate to CONCLAVE_ENV= instead. "
                "Current value: ENV=%r",
                self.env,
            )
            if self.env.lower() != self.conclave_env.lower():
                # Both are explicitly set and conflict — conclave_env wins.
                _logger.warning(
                    "ENV=%r and CONCLAVE_ENV=%r conflict; CONCLAVE_ENV takes precedence (T57.6).",
                    self.env,
                    self.conclave_env,
                )

        # T57.3 / T63.1: Production-required field validation (construction-time).
        # Collect ALL field errors before raising so the operator receives a complete
        # list in a single error message.
        #
        # Initialise field_errors unconditionally so the reference at the end of this
        # method is always valid, regardless of which branch is taken.
        field_errors: list[str] = []

        if self.conclave_env.lower() == "production":
            # DATABASE_URL — required in production.
            if not self.database_url or not self.database_url.strip():
                field_errors.append(
                    "database_url must not be empty in production mode (CONCLAVE_ENV=production). "
                    "Set DATABASE_URL to a valid PostgreSQL DSN."
                )

            # AUDIT_KEY — SecretStr; MUST call .get_secret_value() before checking.
            # A bare `if not self.audit_key` is ALWAYS False because the SecretStr
            # object is truthy regardless of the wrapped value.
            audit_key_value = self.audit_key.get_secret_value()
            if not audit_key_value or not audit_key_value.strip():
                field_errors.append(
                    "audit_key must not be empty in production mode (CONCLAVE_ENV=production). "
                    "Set AUDIT_KEY to a valid hex-encoded 32-byte HMAC key."
                )

            # ARTIFACT_SIGNING_KEY — required when ARTIFACT_SIGNING_KEYS is absent.
            signing_key_value = (
                self.artifact_signing_key.get_secret_value()
                if self.artifact_signing_key is not None
                else ""
            )
            has_signing_key = bool(signing_key_value and signing_key_value.strip())
            has_signing_keys_map = bool(self.artifact_signing_keys)
            if not has_signing_key and not has_signing_keys_map:
                field_errors.append(
                    "artifact_signing_key (or artifact_signing_keys) must not be empty "
                    "in production mode (CONCLAVE_ENV=production). "
                    "Set ARTIFACT_SIGNING_KEY to a hex-encoded HMAC key."
                )

            # MASKING_SALT — required in production.
            masking_salt_value = (
                self.masking_salt.get_secret_value() if self.masking_salt is not None else ""
            )
            if not masking_salt_value or not masking_salt_value.strip():
                field_errors.append(
                    "masking_salt must not be empty in production mode (CONCLAVE_ENV=production). "
                    "Set MASKING_SALT to a secret salt value."
                )

            # JWT_SECRET_KEY — required in production.
            jwt_key_value = self.jwt_secret_key.get_secret_value()
            if not jwt_key_value or not jwt_key_value.strip():
                field_errors.append(
                    "jwt_secret_key must not be empty in production mode "
                    "(CONCLAVE_ENV=production). "
                    "Set JWT_SECRET_KEY to a cryptographically random string."
                )

            # OPERATOR_CREDENTIALS_HASH — required in production.
            # Also validate bcrypt format: must start with $2b$ and be >= 59 chars.
            if not self.operator_credentials_hash:
                field_errors.append(
                    "operator_credentials_hash must not be empty "
                    "in production mode (CONCLAVE_ENV=production). "
                    "Set OPERATOR_CREDENTIALS_HASH to a bcrypt hash of the operator passphrase."
                )
            elif not (
                self.operator_credentials_hash.startswith(_BCRYPT_HASH_PREFIX)
                and len(self.operator_credentials_hash) >= _BCRYPT_HASH_MIN_LENGTH
            ):
                field_errors.append(
                    "OPERATOR_CREDENTIALS_HASH has an invalid format — "
                    f"expected a bcrypt hash starting with '{_BCRYPT_HASH_PREFIX}' "
                    f"and at least {_BCRYPT_HASH_MIN_LENGTH} characters long."
                )

        if field_errors:
            raise ValueError(" | ".join(field_errors))

        return self

    @model_validator(mode="after")
    def _validate_multi_key_signing_consistency(self) -> ConclaveSettings:
        """Validate multi-key signing consistency in all deployment modes (T42.1).

        When ``artifact_signing_keys`` is non-empty, ``artifact_signing_key_active``
        must be set and present as a key within the map.  This prevents silent
        misconfiguration during key rotation where the active key pointer is
        forgotten or misspelled.

        Checked in ALL deployment modes (not just production) because rotation
        misconfiguration can cause signed artifacts to be unverifiable regardless
        of environment.

        Returns:
            The validated ``ConclaveSettings`` instance (self).

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
    def _warn_unrecognized_conclave_env_vars(self) -> ConclaveSettings:
        """Log a WARNING for any CONCLAVE_ env var that doesn't match a known field.

        Keeps ``extra="ignore"`` (fail-open) to avoid breaking deployments with
        OS-level or CI-platform env vars.  The WARNING gives operators visibility
        into potential typos without causing startup failures.

        Known CONCLAVE_ fields are derived from :attr:`model_fields` by
        collecting the subset of field names that start with ``conclave_``.

        Returns:
            The validated ``ConclaveSettings`` instance (self).
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
    def _apply_health_strict_default(self) -> ConclaveSettings:
        """Set conclave_health_strict based on deployment mode when not explicitly configured.

        Per T68.4 spec amendment: the field is bool | None = None.
        When None (not explicitly set), the value is auto-derived:
        - True in production (fail-closed by default).
        - False in development (permissive by default, preserving current behavior).

        This gives environment-dependent defaults while allowing explicit override
        via CONCLAVE_HEALTH_STRICT=true/false in any environment.

        Returns:
            The validated ConclaveSettings instance (self).
        """
        if self.conclave_health_strict is None:
            self.conclave_health_strict = self.conclave_env.lower() == "production"
        return self

    @model_validator(mode="after")
    def _validate_conclave_data_dir(self) -> ConclaveSettings:
        """Validate and resolve conclave_data_dir (T69.7, ADV-P68-02).

        Rules (enforced in order):
        1. Resolve to absolute path so relative dirs like 'data/' work.
        2. Reject '/' (filesystem root) — allows ALL paths, defeating sandbox.
        3. In production mode, require the directory to exist.

        The resolved absolute path is stored back on the instance so
        validate_parquet_path in JobCreateRequest can use it consistently.

        Returns:
            The validated ConclaveSettings instance (self).

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

    # -----------------------------------------------------------------------
    # Methods
    # -----------------------------------------------------------------------

    def is_production(self) -> bool:
        """Return ``True`` if the current deployment mode is production.

        Production mode is determined by ``conclave_env`` (single source of
        truth after T57.6 unification).  The legacy ``env`` alias is handled
        in ``_validate_production_required_fields``; ``is_production()`` reads
        only ``conclave_env``.

        Returns:
            ``True`` when ``conclave_env == "production"`` (case-insensitive),
            ``False`` otherwise.
        """
        return self.conclave_env.lower() == "production"

    # --- T70.4 Sub-model property accessors ---

    @property
    def tls(self) -> TLSSettings:
        """Return a TLSSettings view of the TLS-related fields (T70.4)."""
        return TLSSettings(
            conclave_ssl_required=self.conclave_ssl_required,
            conclave_tls_cert_path=self.conclave_tls_cert_path,
        )

    @property
    def rate_limit(self) -> RateLimitSettings:
        """Return a RateLimitSettings view of the rate-limiting fields (T70.4)."""
        return RateLimitSettings(
            rate_limit_unseal_per_minute=self.rate_limit_unseal_per_minute,
            rate_limit_auth_per_minute=self.rate_limit_auth_per_minute,
            rate_limit_general_per_minute=self.rate_limit_general_per_minute,
            rate_limit_download_per_minute=self.rate_limit_download_per_minute,
            conclave_rate_limit_fail_open=self.conclave_rate_limit_fail_open,
            conclave_trusted_proxy_count=self.conclave_trusted_proxy_count,
        )

    @property
    def webhook(self) -> WebhookSettings:
        """Return a WebhookSettings view of the webhook delivery fields (T70.4)."""
        return WebhookSettings(
            webhook_max_registrations=self.webhook_max_registrations,
            webhook_delivery_timeout_seconds=self.webhook_delivery_timeout_seconds,
            webhook_circuit_breaker_threshold=self.webhook_circuit_breaker_threshold,
            webhook_circuit_breaker_cooldown_seconds=self.webhook_circuit_breaker_cooldown_seconds,
        )

    @property
    def retention(self) -> RetentionSettings:
        """Return a RetentionSettings view of the data-retention fields (T70.4)."""
        return RetentionSettings(
            job_retention_days=self.job_retention_days,
            audit_retention_days=self.audit_retention_days,
            artifact_retention_days=self.artifact_retention_days,
        )

    @property
    def parquet(self) -> ParquetSettings:
        """Return a ParquetSettings view of the Parquet-bound fields (T70.4)."""
        return ParquetSettings(
            parquet_max_file_bytes=self.parquet_max_file_bytes,
            parquet_max_rows=self.parquet_max_rows,
            conclave_data_dir=self.conclave_data_dir,
        )

    @property
    def anchor(self) -> AnchorSettings:
        """Return an AnchorSettings view of the audit-anchor fields (T70.4)."""
        return AnchorSettings(
            anchor_backend=self.anchor_backend,
            anchor_file_path=self.anchor_file_path,
            anchor_every_n_events=self.anchor_every_n_events,
            anchor_every_seconds=self.anchor_every_seconds,
        )


@lru_cache(maxsize=1)
def get_settings() -> ConclaveSettings:
    """Return the singleton :class:`ConclaveSettings` instance.

    Uses ``@lru_cache(maxsize=1)`` to ensure exactly one
    ``ConclaveSettings`` instance is created per process lifecycle.

    Call ``get_settings.cache_clear()`` in tests to reset the cache
    between test cases that manipulate environment variables.

    Returns:
        The cached :class:`ConclaveSettings` instance.
    """
    return ConclaveSettings()
