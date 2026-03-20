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
        artifact_signing_key: Hex-encoded HMAC key for Parquet artifact signing.
            Required in production mode only.
        masking_salt: Secret salt for deterministic HMAC masking.
            Required in production mode only.
        conclave_env: Deployment environment name (e.g. ``"production"``).
            Checked by :meth:`is_production`.
        env: Legacy deployment environment name — also checked by
            :meth:`is_production` for backward compatibility.
        conclave_ssl_required: Whether to enforce SSL for PostgreSQL connections.
            Defaults to ``True``.
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
            "Hex-encoded HMAC key for Parquet artifact signing. Required in production mode."
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
