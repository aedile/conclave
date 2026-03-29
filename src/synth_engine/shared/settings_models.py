"""Settings sub-models for the Conclave Engine — extracted from settings.py (T71.4).

These plain Pydantic BaseModel classes group related :class:`ConclaveSettings`
fields into structured views.  They are intentionally separate from
``settings.py`` to keep that file under 300 LOC (ADV-P70-01).

**Zero-import constraint**: this module MUST NOT import from
``synth_engine.shared.settings`` — doing so would create a circular dependency
(settings.py imports from this file; this file must not import back).

All six sub-models were previously defined inline in ``settings.py`` at the
``T70.4`` task.  They have been extracted here verbatim — no behaviour changes.

Backward compatibility: ``settings.py`` re-exports all names from this module
so existing callers that import sub-model names from ``shared.settings``
continue to work unchanged.

CONSTITUTION Priority 5: Code Quality — file length, maintainability
Task: T70.4 — Settings sub-models (defined inline)
Task: T71.4 — Extract settings sub-models to settings_models.py (ADV-P70-01)
"""

from __future__ import annotations

from pydantic import BaseModel


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


__all__ = [
    "AnchorSettings",
    "ParquetSettings",
    "RateLimitSettings",
    "RetentionSettings",
    "TLSSettings",
    "WebhookSettings",
]
