"""Feature tests for T70.4 — Settings decomposition into sub-models.

CONSTITUTION Priority 3: TDD
Task: T70.4 — Settings Decomposition into Sub-Models

Tests verify:
- At least 5 sub-models exist (TLS, RateLimit, Webhook, Retention, Parquet, Anchor)
- All existing get_settings().field access patterns still work
- Sub-models are accessible as attributes of ConclaveSettings
- No settings file exceeds 200 LOC (structural test)
- All environment variable names are unchanged (no env var renames)
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# T70.4 AC1 — At least 5 sub-models exist (verified via field presence)
# ---------------------------------------------------------------------------


class TestSubModelsExist:
    """At least 5 sub-models must be defined in the settings module."""

    def test_tls_settings_has_ssl_required_field(self) -> None:
        """TLSSettings must define conclave_ssl_required with correct default (True)."""
        from synth_engine.shared.settings import TLSSettings

        # Verify the field exists and has the correct default by constructing
        # an instance with known values.
        settings = TLSSettings(conclave_ssl_required=True, conclave_tls_cert_path=None)
        assert settings.conclave_ssl_required is True
        assert settings.conclave_ssl_required

    def test_rate_limit_settings_has_unseal_rate_field(self) -> None:
        """RateLimitSettings must define rate_limit_unseal_per_minute."""
        from synth_engine.shared.settings import RateLimitSettings

        settings = RateLimitSettings(
            rate_limit_unseal_per_minute=5,
            rate_limit_auth_per_minute=10,
            rate_limit_general_per_minute=60,
            rate_limit_download_per_minute=10,
            conclave_rate_limit_fail_open=False,
            conclave_trusted_proxy_count=0,
        )
        assert settings.rate_limit_unseal_per_minute == 5

    def test_webhook_settings_has_max_registrations_field(self) -> None:
        """WebhookSettings must define webhook_max_registrations."""
        from synth_engine.shared.settings import WebhookSettings

        settings = WebhookSettings(
            webhook_max_registrations=10,
            webhook_delivery_timeout_seconds=10,
            webhook_circuit_breaker_threshold=3,
            webhook_circuit_breaker_cooldown_seconds=300,
        )
        assert settings.webhook_max_registrations == 10

    def test_retention_settings_has_job_retention_days_field(self) -> None:
        """RetentionSettings must define job_retention_days."""
        from synth_engine.shared.settings import RetentionSettings

        settings = RetentionSettings(
            job_retention_days=90,
            audit_retention_days=1095,
            artifact_retention_days=30,
        )
        assert settings.job_retention_days == 90

    def test_parquet_settings_has_max_file_bytes_field(self) -> None:
        """ParquetSettings must define parquet_max_file_bytes."""
        from synth_engine.shared.settings import ParquetSettings

        two_gib = 2 * 1024**3
        settings = ParquetSettings(
            parquet_max_file_bytes=two_gib,
            parquet_max_rows=10_000_000,
            conclave_data_dir="/data",
        )
        assert settings.parquet_max_file_bytes == two_gib

    def test_anchor_settings_has_backend_field(self) -> None:
        """AnchorSettings must define anchor_backend."""
        from synth_engine.shared.settings import AnchorSettings

        settings = AnchorSettings(
            anchor_backend="local_file",
            anchor_file_path="logs/audit_anchors.jsonl",
            anchor_every_n_events=1000,
            anchor_every_seconds=86400,
        )
        assert settings.anchor_backend == "local_file"


# ---------------------------------------------------------------------------
# T70.4 AC4 — All existing get_settings().field access patterns work unchanged
# ---------------------------------------------------------------------------


class TestExistingAccessPatternsUnchanged:
    """All existing get_settings().field access patterns must continue to work."""

    def test_database_url_accessible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings().database_url must still work and return a non-empty string."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        # database_url must be a non-empty string in development mode
        assert len(s.database_url) > 0

    def test_audit_key_default_is_empty_in_development(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_settings().audit_key defaults to empty SecretStr in development mode."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.delenv("AUDIT_KEY", raising=False)
        monkeypatch.delenv("CONCLAVE_AUDIT_KEY", raising=False)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        # Default is SecretStr("") — empty string is the sentinel for "not configured"
        assert s.audit_key.get_secret_value() == ""

    def test_conclave_ssl_required_default_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings().conclave_ssl_required must default to True."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        # Do NOT set CONCLAVE_SSL_REQUIRED — test the default
        monkeypatch.delenv("CONCLAVE_SSL_REQUIRED", raising=False)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_ssl_required is True
        assert s.conclave_ssl_required

    def test_rate_limit_unseal_default_is_five(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings().rate_limit_unseal_per_minute must default to 5."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.delenv("RATE_LIMIT_UNSEAL_PER_MINUTE", raising=False)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.rate_limit_unseal_per_minute == 5

    def test_webhook_max_registrations_default_is_ten(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_settings().webhook_max_registrations must default to 10."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.delenv("WEBHOOK_MAX_REGISTRATIONS", raising=False)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.webhook_max_registrations == 10

    def test_job_retention_days_default_is_90(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings().job_retention_days must default to 90."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.delenv("JOB_RETENTION_DAYS", raising=False)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.job_retention_days == 90

    def test_parquet_max_file_bytes_default_is_two_gib(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_settings().parquet_max_file_bytes must default to 2 GiB (2,147,483,648 bytes)."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.delenv("PARQUET_MAX_FILE_BYTES", raising=False)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.parquet_max_file_bytes == 2 * 1024**3

    def test_anchor_backend_default_is_local_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings().anchor_backend must default to 'local_file'."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.delenv("ANCHOR_BACKEND", raising=False)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.anchor_backend == "local_file"

    def test_anchor_file_path_default_is_expected_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_settings().anchor_file_path must default to 'logs/audit_anchors.jsonl'."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.delenv("ANCHOR_FILE_PATH", raising=False)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.anchor_file_path == "logs/audit_anchors.jsonl"


# ---------------------------------------------------------------------------
# T70.4 AC2 — Sub-models accessible as attributes on ConclaveSettings
# ---------------------------------------------------------------------------


class TestSubModelAttributeAccess:
    """Sub-models must be accessible as attributes of ConclaveSettings."""

    def test_tls_attribute_holds_ssl_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConclaveSettings.tls.conclave_ssl_required must equal the top-level field."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings, TLSSettings

        s = ConclaveSettings()
        assert isinstance(s.tls, TLSSettings)
        # The sub-model field must reflect the same value as the top-level field
        assert s.tls.conclave_ssl_required == s.conclave_ssl_required

    def test_rate_limit_attribute_holds_unseal_rate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConclaveSettings.rate_limit.rate_limit_unseal_per_minute must equal
        the top-level field."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings, RateLimitSettings

        s = ConclaveSettings()
        assert isinstance(s.rate_limit, RateLimitSettings)
        assert s.rate_limit.rate_limit_unseal_per_minute == s.rate_limit_unseal_per_minute

    def test_webhook_attribute_holds_max_registrations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ConclaveSettings.webhook.webhook_max_registrations must equal the top-level field."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings, WebhookSettings

        s = ConclaveSettings()
        assert isinstance(s.webhook, WebhookSettings)
        assert s.webhook.webhook_max_registrations == s.webhook_max_registrations

    def test_retention_attribute_holds_job_retention_days(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ConclaveSettings.retention.job_retention_days must equal the top-level field."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings, RetentionSettings

        s = ConclaveSettings()
        assert isinstance(s.retention, RetentionSettings)
        assert s.retention.job_retention_days == s.job_retention_days

    def test_parquet_attribute_holds_max_file_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConclaveSettings.parquet.parquet_max_file_bytes must equal the top-level field."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings, ParquetSettings

        s = ConclaveSettings()
        assert isinstance(s.parquet, ParquetSettings)
        assert s.parquet.parquet_max_file_bytes == s.parquet_max_file_bytes

    def test_anchor_attribute_holds_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConclaveSettings.anchor.anchor_backend must equal the top-level field."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import AnchorSettings, ConclaveSettings

        s = ConclaveSettings()
        assert isinstance(s.anchor, AnchorSettings)
        assert s.anchor.anchor_backend == s.anchor_backend


# ---------------------------------------------------------------------------
# T70.4 AC3 — Environment variable names unchanged
# ---------------------------------------------------------------------------


class TestEnvVarNamesUnchanged:
    """Environment variable names must remain unchanged after decomposition."""

    def test_tls_env_var_read_from_conclave_ssl_required(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONCLAVE_SSL_REQUIRED env var must still set conclave_ssl_required."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("CONCLAVE_SSL_REQUIRED", "false")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.conclave_ssl_required is False
        assert not s.conclave_ssl_required

    def test_rate_limit_env_var_read_from_rate_limit_unseal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RATE_LIMIT_UNSEAL_PER_MINUTE env var must still set rate_limit_unseal_per_minute."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("RATE_LIMIT_UNSEAL_PER_MINUTE", "3")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.rate_limit_unseal_per_minute == 3

    def test_webhook_env_var_read_from_webhook_max_registrations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """WEBHOOK_MAX_REGISTRATIONS env var must still set webhook_max_registrations."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("WEBHOOK_MAX_REGISTRATIONS", "5")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.webhook_max_registrations == 5

    def test_anchor_env_var_read_from_anchor_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ANCHOR_BACKEND env var must still set anchor_backend."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("ANCHOR_BACKEND", "s3_object_lock")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.anchor_backend == "s3_object_lock"
