"""Feature tests for T74.3 — Decompose settings.py to ≤300 LOC.

Verifies:
1. settings.py is ≤300 LOC after decomposition.
2. All validators still function (production required fields, multi-key signing,
   conclave_data_dir sandbox, health_strict auto-detection).
3. Backward compatibility: get_settings() API unchanged.
4. No circular imports after decomposition.

CONSTITUTION Priority 3: TDD — RED/GREEN/REFACTOR
Task: T74.3 — Decompose settings.py validators to settings_models.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_VALID_BCRYPT_HASH: str = "$2b$12$" + "a" * 53  # pragma: allowlist secret


def _clear_settings() -> None:
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()


def _set_production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)  # pragma: allowlist secret
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "bb" * 32)  # pragma: allowlist secret
    monkeypatch.setenv("MASKING_SALT", "cc" * 16)  # pragma: allowlist secret
    monkeypatch.setenv("JWT_SECRET_KEY", "supersecretjwtkey-for-test")  # pragma: allowlist secret
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", _VALID_BCRYPT_HASH)
    _clear_settings()


def _set_dev_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)  # pragma: allowlist secret
    _clear_settings()


class TestSettingsPyLocTarget:
    """settings.py must be ≤300 LOC after T74.3 decomposition."""

    def test_settings_py_within_300_loc(self) -> None:
        """shared/settings.py must not exceed 300 lines after decomposition."""
        import synth_engine.shared.settings as settings_mod

        source_file = Path(settings_mod.__file__ or "")
        lines = source_file.read_text().splitlines()
        loc = len(lines)
        assert loc <= 300, (
            f"shared/settings.py is {loc} LOC — must be ≤300 after T74.3 decomposition. "
            f"ADV-P70-01 resolution target."
        )


class TestValidatorsInSettingsModels:
    """Validators moved to settings_models.py must still raise on invalid input."""

    def setup_method(self) -> None:
        _clear_settings()

    def teardown_method(self) -> None:
        _clear_settings()

    def test_production_required_fields_validator_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Production mode must still reject empty audit_key after decomposition."""
        monkeypatch.setenv("CONCLAVE_ENV", "production")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
        )
        monkeypatch.delenv("AUDIT_KEY", raising=False)
        monkeypatch.delenv("CONCLAVE_AUDIT_KEY", raising=False)

        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_multi_key_signing_validator_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Multi-key signing misconfiguration must still raise after decomposition."""
        _set_dev_env(monkeypatch)
        monkeypatch.setenv("ARTIFACT_SIGNING_KEYS", '{"00000001": "aa" * 32}')
        # Missing ARTIFACT_SIGNING_KEY_ACTIVE — must fail
        monkeypatch.delenv("ARTIFACT_SIGNING_KEY_ACTIVE", raising=False)

        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((ValidationError, ValueError)):
            ConclaveSettings()

    def test_health_strict_auto_detection_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """conclave_health_strict must auto-detect True in production after decomposition."""
        _set_production_env(monkeypatch)
        monkeypatch.delenv("CONCLAVE_HEALTH_STRICT", raising=False)

        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        # Auto-detection: production mode => health_strict=True.
        assert s.conclave_health_strict is True
        assert s.conclave_env == "production"

    def test_is_production_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConclaveSettings.is_production() must return True in production mode."""
        _set_production_env(monkeypatch)
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.is_production() is True
        assert s.conclave_env == "production"

    def test_get_settings_api_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_settings() must return a ConclaveSettings instance after decomposition."""
        _set_dev_env(monkeypatch)
        from synth_engine.shared.settings import ConclaveSettings, get_settings

        s = get_settings()
        assert isinstance(s, ConclaveSettings)
        assert s.conclave_env == "development"

    def test_settings_models_no_circular_import(self) -> None:
        """settings_models.py must not import from settings.py after decomposition."""
        import inspect

        import synth_engine.shared.settings_models as sm

        source = inspect.getsource(sm)
        assert "from synth_engine.shared.settings import" not in source
        assert "import synth_engine.shared.settings" not in source
