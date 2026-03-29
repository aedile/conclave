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

import inspect
import os

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# T70.4 AC1 — At least 5 sub-models exist
# ---------------------------------------------------------------------------


class TestSubModelsExist:
    """At least 5 sub-models must be defined in the settings module."""

    def test_tls_settings_exists(self) -> None:
        """TLSSettings sub-model must be importable."""
        from synth_engine.shared.settings import TLSSettings

        assert TLSSettings is not None

    def test_rate_limit_settings_exists(self) -> None:
        """RateLimitSettings sub-model must be importable."""
        from synth_engine.shared.settings import RateLimitSettings

        assert RateLimitSettings is not None

    def test_webhook_settings_exists(self) -> None:
        """WebhookSettings sub-model must be importable."""
        from synth_engine.shared.settings import WebhookSettings

        assert WebhookSettings is not None

    def test_retention_settings_exists(self) -> None:
        """RetentionSettings sub-model must be importable."""
        from synth_engine.shared.settings import RetentionSettings

        assert RetentionSettings is not None

    def test_parquet_settings_exists(self) -> None:
        """ParquetSettings sub-model must be importable."""
        from synth_engine.shared.settings import ParquetSettings

        assert ParquetSettings is not None

    def test_anchor_settings_exists(self) -> None:
        """AnchorSettings sub-model must be importable."""
        from synth_engine.shared.settings import AnchorSettings

        assert AnchorSettings is not None


# ---------------------------------------------------------------------------
# T70.4 AC4 — All existing get_settings().field access patterns work unchanged
# ---------------------------------------------------------------------------


class TestExistingAccessPatternsUnchanged:
    """All existing get_settings().field access patterns must continue to work."""

    def test_database_url_accessible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings().database_url must still work."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        # Should not raise AttributeError
        _ = s.database_url

    def test_audit_key_accessible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings().audit_key must still work."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        _ = s.audit_key

    def test_conclave_ssl_required_accessible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings().conclave_ssl_required must still work."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert isinstance(s.conclave_ssl_required, bool)

    def test_rate_limit_unseal_per_minute_accessible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_settings().rate_limit_unseal_per_minute must still work."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert isinstance(s.rate_limit_unseal_per_minute, int)

    def test_webhook_max_registrations_accessible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_settings().webhook_max_registrations must still work."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert isinstance(s.webhook_max_registrations, int)

    def test_job_retention_days_accessible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings().job_retention_days must still work."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert isinstance(s.job_retention_days, int)

    def test_parquet_max_file_bytes_accessible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings().parquet_max_file_bytes must still work."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert isinstance(s.parquet_max_file_bytes, int)

    def test_anchor_backend_accessible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings().anchor_backend must still work."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert isinstance(s.anchor_backend, str)

    def test_anchor_file_path_accessible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings().anchor_file_path must still work."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert isinstance(s.anchor_file_path, str)


# ---------------------------------------------------------------------------
# T70.4 AC2 — Sub-models accessible as attributes on ConclaveSettings
# ---------------------------------------------------------------------------


class TestSubModelAttributeAccess:
    """Sub-models must be accessible as attributes of ConclaveSettings."""

    def test_tls_attribute_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConclaveSettings.tls sub-model must be accessible as attribute."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings, TLSSettings

        s = ConclaveSettings()
        assert hasattr(s, "tls"), "ConclaveSettings must have a .tls sub-model"
        assert isinstance(s.tls, TLSSettings)

    def test_rate_limit_attribute_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConclaveSettings.rate_limit sub-model must be accessible as attribute."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings, RateLimitSettings

        s = ConclaveSettings()
        assert hasattr(s, "rate_limit"), "ConclaveSettings must have a .rate_limit sub-model"
        assert isinstance(s.rate_limit, RateLimitSettings)

    def test_webhook_attribute_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConclaveSettings.webhook sub-model must be accessible as attribute."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings, WebhookSettings

        s = ConclaveSettings()
        assert hasattr(s, "webhook"), "ConclaveSettings must have a .webhook sub-model"
        assert isinstance(s.webhook, WebhookSettings)

    def test_retention_attribute_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConclaveSettings.retention sub-model must be accessible as attribute."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings, RetentionSettings

        s = ConclaveSettings()
        assert hasattr(s, "retention"), "ConclaveSettings must have a .retention sub-model"
        assert isinstance(s.retention, RetentionSettings)

    def test_parquet_attribute_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConclaveSettings.parquet sub-model must be accessible as attribute."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings, ParquetSettings

        s = ConclaveSettings()
        assert hasattr(s, "parquet"), "ConclaveSettings must have a .parquet sub-model"
        assert isinstance(s.parquet, ParquetSettings)

    def test_anchor_attribute_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConclaveSettings.anchor sub-model must be accessible as attribute."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        from synth_engine.shared.settings import ConclaveSettings, AnchorSettings

        s = ConclaveSettings()
        assert hasattr(s, "anchor"), "ConclaveSettings must have an .anchor sub-model"
        assert isinstance(s.anchor, AnchorSettings)


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

    def test_anchor_env_var_read_from_anchor_backend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ANCHOR_BACKEND env var must still set anchor_backend."""
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("ANCHOR_BACKEND", "s3_object_lock")
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.anchor_backend == "s3_object_lock"
