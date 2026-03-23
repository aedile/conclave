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
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConclaveSettings(BaseSettings):
    """Pydantic BaseSettings model for the Conclave Engine.

    All environment variables consumed by the engine are declared here.
    Fields map directly to environment variable names (case-insensitive).
    Required fields with no default will raise a ``ValidationError`` at
    construction time if the corresponding env var is absent or empty.

    Vault-deferred values (``VAULT_SEAL_SALT``) are intentionally excluded.
    See module docstring for rationale.

    Attributes:
        database_url: Async-compatible PostgreSQL DSN or SQLite URL.
            Required in all deployment modes.
        audit_key: Hex-encoded 32-byte HMAC key for audit event signing.
            Required in all deployment modes.
        ale_key: Fernet key for Application-Level Encryption.
            Optional — vault KEK path is preferred in production.
        artifact_signing_key: Hex-encoded HMAC key for ModelArtifact pickle signing.
            Required in production mode only.
        artifact_signing_key: Hex-encoded HMAC key for Parquet artifact
            signing (legacy single-key mode).  Deprecated in favour of
            ``artifact_signing_keys`` but retained for backward
            compatibility.  Required in production mode only when
            ``artifact_signing_keys`` is absent.
        artifact_signing_keys: JSON-encoded dict mapping hex key ID strings
            to hex key strings.  Enables multi-key rotation.  When set,
            takes precedence over ``artifact_signing_key``.
            Example: ``'{"00000001": "abcd...ef", "00000002": "1234...56"}'``.
        artifact_signing_key_active: Hex key ID string identifying the
            currently active signing key in ``artifact_signing_keys``.
            New artifacts are signed with this key; old artifacts signed
            with any key in the map remain verifiable.
        masking_salt: Secret salt for deterministic HMAC masking.
            Required in production mode only.
        conclave_env: Deployment environment name (e.g. ``"production"``).
            Checked by :meth:`is_production`.
        env: Legacy deployment environment name — also checked by
            :meth:`is_production` for backward compatibility.
        conclave_ssl_required: Whether to enforce SSL for PostgreSQL connections.
            Defaults to ``True``.
        conclave_tls_cert_path: Path to a TLS certificate file used by the
            reverse proxy.  When set, the T42.2 startup health check treats TLS
            as configured and suppresses the misconfiguration warning.  Maps to
            the ``CONCLAVE_TLS_CERT_PATH`` environment variable.
        force_cpu: Force CPU device selection regardless of CUDA availability.
            Defaults to ``False``.
        otel_exporter_otlp_endpoint: OTLP gRPC endpoint URL for OpenTelemetry.
            When absent, an InMemorySpanExporter is used (air-gap safe).
        huey_backend: Huey task queue backend (``"redis"`` or ``"memory"``).
            Defaults to ``"redis"``.
        huey_immediate: Execute Huey tasks synchronously in the calling process.
            Recommended for integration tests.  Defaults to ``False``.
        redis_url: Redis connection URL for the Huey Redis backend.
            Defaults to ``redis://redis:6379/0``.
        license_public_key: PEM-encoded RSA public key for license JWT
            verification.  Falls back to the embedded placeholder when absent.
        jwt_algorithm: JWT signing algorithm, pinned to prevent confusion attacks.
            Defaults to ``"HS256"``.
        jwt_expiry_seconds: Lifetime of issued JWT tokens in seconds.
            Defaults to ``3600`` (1 hour).
        operator_credentials_hash: bcrypt hash of the operator passphrase used
            for ``POST /auth/token``.  Empty string means no operator is
            configured and token issuance will always fail.
        jwt_secret_key: HMAC secret key for JWT signing and verification.
            Required when ``jwt_algorithm`` is ``"HS256"`` or ``"HS384"`` or
            ``"HS512"``.  Empty string in development/test only.
        rate_limit_unseal_per_minute: Maximum requests to ``/unseal`` per IP
            per minute.  Brute-force protection for the vault unseal endpoint.
            Defaults to ``5``.
        rate_limit_auth_per_minute: Maximum requests to ``/auth/token`` per IP
            per minute.  Credential stuffing protection.  Defaults to ``10``.
        rate_limit_general_per_minute: Maximum requests per authenticated
            operator per minute on all other endpoints.  Defaults to ``60``.
        rate_limit_download_per_minute: Maximum download requests per
            authenticated operator per minute.  Bandwidth protection.
            Defaults to ``10``.
        reaper_stale_threshold_minutes: Number of minutes after which an
            IN_PROGRESS synthesis job is considered orphaned.  Must be >= 5
            to prevent accidental mass-reaping.  Defaults to 60 minutes.
        webhook_max_registrations: Maximum number of active webhook
            registrations per operator.  Defaults to 10.
        webhook_delivery_timeout_seconds: HTTP timeout in seconds for each
            webhook delivery attempt.  Defaults to 10.
        parquet_max_file_bytes: Maximum Parquet file or payload size in bytes.
            Size check fires before row-count check.  Defaults to 2 GiB.
        parquet_max_rows: Maximum number of rows permitted in a loaded Parquet
            DataFrame.  Defaults to 10,000,000.
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
        description=(
            "Async-compatible PostgreSQL DSN or SQLite URL. "
            "Required at runtime — startup validation enforced by "
            "config_validation.validate_config()."
        ),
    )
    audit_key: str = Field(
        default="",
        description=(
            "Hex-encoded 32-byte HMAC key for audit event signing. "
            "Required at runtime — startup validation enforced by "
            "config_validation.validate_config()."
        ),
    )

    # -----------------------------------------------------------------------
    # Optional secrets (required only in production mode)
    # -----------------------------------------------------------------------

    artifact_signing_key: str | None = Field(
        default=None,
        description=(
            "Hex-encoded HMAC key for Parquet artifact signing. "
            "Legacy single-key mode — deprecated in favour of "
            "ARTIFACT_SIGNING_KEYS.  Required in production mode only "
            "when ARTIFACT_SIGNING_KEYS is absent."
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
    masking_salt: str | None = Field(
        default=None,
        description=("Secret salt for deterministic HMAC masking. Required in production mode."),
    )
    ale_key: str | None = Field(
        default=None,
        description=(
            "Fernet key for Application-Level Encryption. "
            "Optional — vault KEK path preferred in production."
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
        default="",
        description=(
            "Deployment environment name (e.g. 'production'). "
            "Checked alongside ENV by is_production()."
        ),
    )
    env: str = Field(
        default="",
        description=(
            "Legacy deployment environment variable. "
            "Checked alongside CONCLAVE_ENV by is_production()."
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
    jwt_secret_key: str = Field(
        default="",
        description=(
            "HMAC secret key for JWT signing and verification. "
            "Required when jwt_algorithm is HS256/HS384/HS512. "
            "Must be a cryptographically random string of at least 32 characters. "
            "Empty string only acceptable in development/test environments."
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
    # Methods
    # -----------------------------------------------------------------------

    def is_production(self) -> bool:
        """Return ``True`` if the current deployment mode is production.

        Production mode is indicated by either of:
          - ``ENV=production``
          - ``CONCLAVE_ENV=production``

        Both env var names are checked for maximum compatibility with
        deployment tooling that may use either convention.

        Returns:
            ``True`` when the deployment mode is production, ``False`` otherwise.
        """
        return self.env.lower() == "production" or self.conclave_env.lower() == "production"


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
