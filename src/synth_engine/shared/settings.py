"""Centralized Pydantic settings model for the Conclave Engine.

All environment variables consumed by the engine are declared in
:class:`~synth_engine.shared.settings_models.ConclaveSettingsFields` (T74.3).
This file is the composition root: it combines that field mixin with
``pydantic_settings.BaseSettings`` (which provides env-var loading) and
exposes the :func:`get_settings` singleton.

Vault-deferred values
---------------------
``VAULT_SEAL_SALT`` is intentionally excluded from this model.  It is read
only at vault unseal time (inside :meth:`VaultState.unseal()`) — never at
application boot.  Including it here would force operators to provide the
salt before the application starts, which violates the vault's deferred
security model.

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
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from synth_engine.shared.settings_models import (
    AnchorSettings,
    ConclaveSettingsFields,
    ParquetSettings,
    RateLimitSettings,
    RetentionSettings,
    TLSSettings,
    WebhookSettings,
)

# Re-export sub-model classes for backward compatibility.
# Existing callers of "from synth_engine.shared.settings import TLSSettings" etc.
# continue to work unchanged (T71.4).
__all__ = [
    "AnchorSettings",
    "ConclaveSettings",
    "ParquetSettings",
    "RateLimitSettings",
    "RetentionSettings",
    "TLSSettings",
    "WebhookSettings",
    "get_settings",
]


class ConclaveSettings(ConclaveSettingsFields, BaseSettings):
    """Pydantic BaseSettings model for the Conclave Engine.

    Inherits all field declarations and validators from
    :class:`~synth_engine.shared.settings_models.ConclaveSettingsFields`.
    ``BaseSettings`` provides environment-variable loading (env file, OS env).

    Fields map directly to environment variable names (case-insensitive).
    Required fields with no default will raise a ``ValidationError`` at
    construction time if the corresponding env var is absent or empty.

    Vault-deferred values (``VAULT_SEAL_SALT``) are intentionally excluded.
    See module docstring for rationale.

    Secret fields (``audit_key``, ``jwt_secret_key``, ``artifact_signing_key``,
    ``masking_salt``) are typed as ``pydantic.SecretStr`` so that raw key
    material is never exposed via ``repr()``, ``model_dump()``, or Pydantic
    validation errors.  Access the underlying value with ``.get_secret_value()``.
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
