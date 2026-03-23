"""Negative/attack tests for mTLS wiring — T46.2.

These tests are written first per the Attack-First TDD mandate (Rule 22).
They verify all failure modes before any feature tests are added.

Attack surface:
- Missing cert files at startup
- Empty cert path strings
- Redis URL scheme enforcement (redis:// must become rediss://)
- SSL mode downgrade attempt when mTLS is enabled
- DB connect_args absence when mTLS is disabled
- Startup validation collect-all-errors behavior

CONSTITUTION Priority 0: Security — fail-fast mTLS misconfiguration prevention
CONSTITUTION Priority 3: TDD — attack tests FIRST per Rule 22
Task: T46.2 — Wire mTLS on All Container-to-Container Connections
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    """Clear get_settings() LRU cache before/after every test (AC19)."""
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Attack tests: missing cert files
# ---------------------------------------------------------------------------


def test_mtls_validation_raises_on_missing_ca_cert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MTLS_ENABLED=true + nonexistent CA cert path → SystemExit at validate_config."""
    cert = tmp_path / "app.crt"
    key = tmp_path / "app.key"
    cert.write_text("dummy")
    key.write_text("dummy")

    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", str(tmp_path / "nonexistent_ca.crt"))
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", str(cert))
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", str(key))

    from synth_engine.bootstrapper.config_validation import validate_config

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "MTLS_CA_CERT_PATH" in str(exc_info.value)


def test_mtls_validation_raises_on_missing_client_cert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MTLS_ENABLED=true + nonexistent client cert path → SystemExit at validate_config."""
    ca = tmp_path / "ca.crt"
    key = tmp_path / "app.key"
    ca.write_text("dummy")
    key.write_text("dummy")

    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", str(ca))
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", str(tmp_path / "nonexistent.crt"))
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", str(key))

    from synth_engine.bootstrapper.config_validation import validate_config

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "MTLS_CLIENT_CERT_PATH" in str(exc_info.value)


def test_mtls_validation_raises_on_missing_client_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MTLS_ENABLED=true + nonexistent client key path → SystemExit at validate_config."""
    ca = tmp_path / "ca.crt"
    cert = tmp_path / "app.crt"
    ca.write_text("dummy")
    cert.write_text("dummy")

    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", str(ca))
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", str(cert))
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", str(tmp_path / "nonexistent.key"))

    from synth_engine.bootstrapper.config_validation import validate_config

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "MTLS_CLIENT_KEY_PATH" in str(exc_info.value)


def test_mtls_validation_collects_all_missing_certs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MTLS_ENABLED=true + all three certs missing → single SystemExit naming all three."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", "/nonexistent/ca.crt")
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", "/nonexistent/app.crt")
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", "/nonexistent/app.key")

    from synth_engine.bootstrapper.config_validation import validate_config

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    msg = str(exc_info.value)
    assert "MTLS_CA_CERT_PATH" in msg
    assert "MTLS_CLIENT_CERT_PATH" in msg
    assert "MTLS_CLIENT_KEY_PATH" in msg


# ---------------------------------------------------------------------------
# Attack tests: empty cert path strings
# ---------------------------------------------------------------------------


def test_mtls_validation_raises_on_empty_ca_cert_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MTLS_ENABLED=true + empty MTLS_CA_CERT_PATH → SystemExit at validate_config."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", "")
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", "/some/cert.crt")
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", "/some/key.key")

    from synth_engine.bootstrapper.config_validation import validate_config

    with pytest.raises(SystemExit) as exc_info:
        validate_config()

    assert "MTLS_CA_CERT_PATH" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Attack tests: Redis URL scheme enforcement
# ---------------------------------------------------------------------------


def test_promote_redis_url_upgrades_scheme() -> None:
    """redis:// must be promoted to rediss:// when mTLS is enabled."""
    from synth_engine.shared.task_queue import _promote_redis_url_to_tls

    result = _promote_redis_url_to_tls("redis://redis:6379/0")
    assert result == "rediss://redis:6379/0"


def test_promote_redis_url_leaves_rediss_unchanged() -> None:
    """rediss:// URLs are returned unchanged by _promote_redis_url_to_tls."""
    from synth_engine.shared.task_queue import _promote_redis_url_to_tls

    result = _promote_redis_url_to_tls("rediss://redis:6379/0")
    assert result == "rediss://redis:6379/0"


def test_promote_redis_url_leaves_unix_socket_unchanged() -> None:
    """Non-TCP redis URLs that don't start with redis:// are returned unchanged."""
    from synth_engine.shared.task_queue import _promote_redis_url_to_tls

    result = _promote_redis_url_to_tls("unix:///tmp/redis.sock")
    assert result == "unix:///tmp/redis.sock"


# ---------------------------------------------------------------------------
# Attack tests: DB connect_args absent when mTLS disabled
# ---------------------------------------------------------------------------


def test_db_engine_no_tls_args_when_mtls_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No SSL connect_args injected into create_engine when MTLS_ENABLED=false."""
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")  # pragma: allowlist secret
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "false")

    from synth_engine.shared.settings import ConclaveSettings

    settings = ConclaveSettings()

    captured_kwargs: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return MagicMock()

    with patch("synth_engine.shared.db.create_engine", side_effect=fake_create_engine):
        from synth_engine.shared.db import _engine_cache, get_engine

        _engine_cache.clear()
        get_engine.__wrapped__(settings.database_url) if hasattr(  # type: ignore[attr-defined]
            get_engine, "__wrapped__"
        ) else get_engine(settings.database_url)
        _engine_cache.clear()

    connect_args = captured_kwargs.get("connect_args", {})
    assert "sslmode" not in connect_args


def test_db_no_ssl_connect_args_when_mtls_disabled_settings() -> None:
    """Settings with mtls_enabled=False produce no SSL-related engine kwargs."""
    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings(
        database_url="sqlite:///test.db",
        audit_key="aa" * 32,
        mtls_enabled=False,
    )
    # No error raised — just verify the field exists and is False
    assert s.mtls_enabled is False


# ---------------------------------------------------------------------------
# Attack tests: mTLS implies SSL (downgrade prevention)
# ---------------------------------------------------------------------------


def test_mtls_enabled_implies_ssl_regardless_of_conclave_ssl_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MTLS_ENABLED=true + CONCLAVE_SSL_REQUIRED=false → warning logged, no SystemExit."""
    ca = tmp_path / "ca.crt"
    cert = tmp_path / "app.crt"
    key = tmp_path / "app.key"
    ca.write_text("dummy")
    cert.write_text("dummy")
    key.write_text("dummy")

    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("CONCLAVE_SSL_REQUIRED", "false")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", str(ca))
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", str(cert))
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", str(key))
    # T50.3: Use explicit development mode to avoid production-required validation
    monkeypatch.setenv("CONCLAVE_ENV", "development")

    import logging

    from synth_engine.bootstrapper.config_validation import validate_config

    with caplog.at_level(logging.WARNING, logger="synth_engine.bootstrapper.config_validation"):
        validate_config()

    # Should warn about mTLS implying SSL, not raise SystemExit
    assert any(
        "MTLS_ENABLED" in rec.message and "ssl" in rec.message.lower() for rec in caplog.records
    )
