"""Feature tests for mTLS wiring — T46.2.

Tests verify:
- ConclaveSettings fields for mTLS (mtls_enabled, cert paths)
- Redis URL promotion to rediss:// scheme
- DB engine connect_args with TLS params when mTLS enabled
- Startup validation passes when cert files exist
- Task queue uses rediss:// when mTLS enabled
- Redis client passes SSL kwargs when mTLS enabled
- factories.py sync engine uses TLS connect_args when mTLS enabled

CONSTITUTION Priority 0: Security — mTLS wiring correctness
CONSTITUTION Priority 3: TDD — RED before GREEN
Task: T46.2 — Wire mTLS on All Container-to-Container Connections
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None, None, None]:
    """Clear get_settings() LRU cache before/after every test (AC19)."""
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def mtls_cert_files(tmp_path: Path) -> dict[str, Path]:
    """Create dummy cert/key files for mTLS settings tests."""
    ca = tmp_path / "ca.crt"
    cert = tmp_path / "app.crt"
    key = tmp_path / "app.key"
    ca.write_text("dummy-ca")
    cert.write_text("dummy-cert")
    key.write_text("dummy-key")
    return {"ca": ca, "cert": cert, "key": key}


# ---------------------------------------------------------------------------
# Tests: ConclaveSettings mTLS fields
# ---------------------------------------------------------------------------


def test_conclave_settings_has_mtls_enabled_field() -> None:
    """ConclaveSettings exposes mtls_enabled field with default=False."""
    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings(database_url="sqlite:///test.db", audit_key="aa" * 32)
    assert s.mtls_enabled is False


def test_conclave_settings_mtls_enabled_true_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MTLS_ENABLED=true is parsed correctly by ConclaveSettings."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.mtls_enabled is True


def test_conclave_settings_mtls_cert_paths_have_defaults() -> None:
    """mTLS cert path fields have sensible default values."""
    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings(database_url="sqlite:///test.db", audit_key="aa" * 32)
    assert s.mtls_ca_cert_path == "secrets/mtls/ca.crt"
    assert s.mtls_client_cert_path == "secrets/mtls/app.crt"
    assert s.mtls_client_key_path == "secrets/mtls/app.key"


def test_conclave_settings_mtls_cert_paths_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mTLS cert path fields are populated from environment variables."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_CA_CERT_PATH", "/custom/ca.crt")
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", "/custom/app.crt")
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", "/custom/app.key")

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.mtls_ca_cert_path == "/custom/ca.crt"
    assert s.mtls_client_cert_path == "/custom/app.crt"
    assert s.mtls_client_key_path == "/custom/app.key"


# ---------------------------------------------------------------------------
# Tests: Redis URL promotion
# ---------------------------------------------------------------------------


def test_promote_redis_url_upgrades_redis_to_rediss() -> None:
    """_promote_redis_url_to_tls converts redis:// to rediss://."""
    from synth_engine.shared.task_queue import _promote_redis_url_to_tls

    assert _promote_redis_url_to_tls("redis://redis:6379/0") == "rediss://redis:6379/0"


def test_promote_redis_url_preserves_host_and_path() -> None:
    """_promote_redis_url_to_tls preserves host, port, and database index."""
    from synth_engine.shared.task_queue import _promote_redis_url_to_tls

    result = _promote_redis_url_to_tls("redis://myhost:6380/3")
    assert result == "rediss://myhost:6380/3"


def test_promote_redis_url_preserves_auth() -> None:
    """_promote_redis_url_to_tls preserves auth credentials in the URL."""
    from synth_engine.shared.task_queue import _promote_redis_url_to_tls

    result = _promote_redis_url_to_tls("redis://:secret@redis:6379/0")  # pragma: allowlist secret
    assert result == "rediss://:secret@redis:6379/0"  # pragma: allowlist secret


def test_promote_redis_url_no_double_promotion() -> None:
    """_promote_redis_url_to_tls is idempotent — rediss:// is unchanged."""
    from synth_engine.shared.task_queue import _promote_redis_url_to_tls

    url = "rediss://redis:6379/0"
    assert _promote_redis_url_to_tls(url) == url


# ---------------------------------------------------------------------------
# Tests: startup validation passes when cert files exist
# ---------------------------------------------------------------------------


def test_validate_config_passes_when_mtls_cert_files_exist(
    mtls_cert_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() does NOT raise when MTLS_ENABLED=true and all cert files exist."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", str(mtls_cert_files["ca"]))
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", str(mtls_cert_files["cert"]))
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", str(mtls_cert_files["key"]))

    from synth_engine.bootstrapper.config_validation import validate_config

    # Should NOT raise
    validate_config()


def test_validate_config_does_not_check_cert_files_when_mtls_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_config() ignores missing cert files when MTLS_ENABLED=false."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "false")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", "/nonexistent/ca.crt")
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", "/nonexistent/cert.crt")
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", "/nonexistent/key.key")

    from synth_engine.bootstrapper.config_validation import validate_config

    # Should NOT raise — mTLS is disabled so cert files are irrelevant
    validate_config()


# ---------------------------------------------------------------------------
# Tests: DB engine TLS connect_args
# ---------------------------------------------------------------------------


def test_get_engine_passes_ssl_connect_args_when_mtls_enabled(
    mtls_cert_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_engine() passes sslmode=verify-full connect_args when MTLS_ENABLED=true."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")  # pragma: allowlist secret
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", str(mtls_cert_files["ca"]))
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", str(mtls_cert_files["cert"]))
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", str(mtls_cert_files["key"]))

    captured_kwargs: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return MagicMock()

    from synth_engine.shared.db import _engine_cache
    from synth_engine.shared.settings import ConclaveSettings

    settings = ConclaveSettings()
    db_url = settings.database_url

    _engine_cache.pop(db_url, None)

    with patch("synth_engine.shared.db.create_engine", side_effect=fake_create_engine):
        from synth_engine.shared.db import get_engine

        get_engine(db_url)
        _engine_cache.pop(db_url, None)

    connect_args = captured_kwargs.get("connect_args", {})
    assert isinstance(connect_args, dict)
    assert connect_args.get("sslmode") == "verify-full"
    assert str(connect_args.get("sslrootcert")) == str(mtls_cert_files["ca"])
    assert str(connect_args.get("sslcert")) == str(mtls_cert_files["cert"])
    assert str(connect_args.get("sslkey")) == str(mtls_cert_files["key"])


def test_get_engine_no_ssl_args_when_mtls_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_engine() passes no sslmode connect_args when MTLS_ENABLED=false."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")  # pragma: allowlist secret
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "false")

    captured_kwargs: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return MagicMock()

    from synth_engine.shared.db import _engine_cache
    from synth_engine.shared.settings import ConclaveSettings

    settings = ConclaveSettings()
    db_url = settings.database_url
    _engine_cache.pop(db_url, None)

    with patch("synth_engine.shared.db.create_engine", side_effect=fake_create_engine):
        from synth_engine.shared.db import get_engine

        get_engine(db_url)
        _engine_cache.pop(db_url, None)

    connect_args = captured_kwargs.get("connect_args", {})
    assert "sslmode" not in connect_args


def test_get_async_engine_passes_ssl_context_when_mtls_enabled(
    mtls_cert_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_async_engine() passes ssl context connect_args when MTLS_ENABLED=true."""
    import ssl

    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://user:pass@host/db"  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", str(mtls_cert_files["ca"]))
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", str(mtls_cert_files["cert"]))
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", str(mtls_cert_files["key"]))

    captured_kwargs: dict[str, object] = {}

    def fake_create_async_engine(url: str, **kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return MagicMock()

    from synth_engine.shared.db import _async_engine_cache
    from synth_engine.shared.settings import ConclaveSettings

    settings = ConclaveSettings()
    db_url = settings.database_url
    _async_engine_cache.pop(db_url, None)

    with patch(
        "synth_engine.shared.db.create_async_engine",
        side_effect=fake_create_async_engine,
    ):
        from synth_engine.shared.db import get_async_engine

        get_async_engine(db_url)
        _async_engine_cache.pop(db_url, None)

    connect_args = captured_kwargs.get("connect_args", {})
    assert isinstance(connect_args, dict)
    # asyncpg uses ssl= keyword with an ssl.SSLContext
    ssl_ctx = connect_args.get("ssl")
    assert isinstance(ssl_ctx, ssl.SSLContext)


# ---------------------------------------------------------------------------
# Tests: Redis client TLS kwargs
# ---------------------------------------------------------------------------


def test_redis_client_gets_ssl_kwargs_when_mtls_enabled(
    mtls_cert_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_redis_client() passes ssl_certfile/ssl_keyfile/ssl_ca_certs when mTLS enabled."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", str(mtls_cert_files["ca"]))
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", str(mtls_cert_files["cert"]))
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", str(mtls_cert_files["key"]))

    captured_url: list[str] = []
    captured_kwargs: dict[str, object] = {}

    import redis as redis_lib

    original_from_url = redis_lib.Redis.from_url

    def fake_from_url(url: str, **kwargs: object) -> MagicMock:  # type: ignore[misc]
        captured_url.append(url)
        captured_kwargs.update(kwargs)
        return MagicMock()

    from synth_engine.bootstrapper.dependencies import redis as redis_dep

    redis_dep._client = None  # Reset singleton

    with patch.object(redis_lib.Redis, "from_url", staticmethod(fake_from_url)):
        redis_dep._client = None
        redis_dep.get_redis_client()
        redis_dep._client = None

    assert captured_url[0].startswith("rediss://")
    assert captured_kwargs.get("ssl_certfile") == str(mtls_cert_files["cert"])
    assert captured_kwargs.get("ssl_keyfile") == str(mtls_cert_files["key"])
    assert captured_kwargs.get("ssl_ca_certs") == str(mtls_cert_files["ca"])
    assert captured_kwargs.get("ssl_cert_reqs") == "required"


def test_redis_client_no_ssl_kwargs_when_mtls_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_redis_client() passes no ssl_ kwargs when MTLS_ENABLED=false."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "false")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")

    captured_url: list[str] = []
    captured_kwargs: dict[str, object] = {}

    import redis as redis_lib

    def fake_from_url(url: str, **kwargs: object) -> MagicMock:  # type: ignore[misc]
        captured_url.append(url)
        captured_kwargs.update(kwargs)
        return MagicMock()

    from synth_engine.bootstrapper.dependencies import redis as redis_dep

    redis_dep._client = None

    with patch.object(redis_lib.Redis, "from_url", staticmethod(fake_from_url)):
        redis_dep._client = None
        redis_dep.get_redis_client()
        redis_dep._client = None

    assert captured_url[0].startswith("redis://")
    assert "ssl_certfile" not in captured_kwargs
    assert "ssl_keyfile" not in captured_kwargs
    assert "ssl_ca_certs" not in captured_kwargs


# ---------------------------------------------------------------------------
# Tests: Huey task queue TLS
# ---------------------------------------------------------------------------


def test_build_huey_uses_rediss_url_when_mtls_enabled(
    mtls_cert_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_build_huey() uses rediss:// URL and passes TLS kwargs when MTLS_ENABLED=true."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("HUEY_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", str(mtls_cert_files["ca"]))
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", str(mtls_cert_files["cert"]))
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", str(mtls_cert_files["key"]))

    captured_url: list[str] = []
    captured_conn_kwargs: list[dict[str, object]] = []

    from huey import RedisHuey

    original_init = RedisHuey.__init__

    def fake_redis_huey_init(
        self: RedisHuey, name: str, url: str | None = None, **kwargs: object
    ) -> None:
        if url is not None:
            captured_url.append(url)
        conn_kw = kwargs.get("connection_kwargs")
        if isinstance(conn_kw, dict):
            captured_conn_kwargs.append(conn_kw)
        # Don't actually init to avoid network calls
        self.name = name  # type: ignore[attr-defined]
        self.immediate = kwargs.get("immediate", False)  # type: ignore[attr-defined]

    with patch.object(RedisHuey, "__init__", fake_redis_huey_init):
        from synth_engine.shared.task_queue import _build_huey

        _build_huey()

    assert len(captured_url) == 1
    assert captured_url[0].startswith("rediss://")

    assert len(captured_conn_kwargs) == 1
    ck = captured_conn_kwargs[0]
    assert ck.get("ssl_certfile") == str(mtls_cert_files["cert"])
    assert ck.get("ssl_keyfile") == str(mtls_cert_files["key"])
    assert ck.get("ssl_ca_certs") == str(mtls_cert_files["ca"])
    assert ck.get("ssl_cert_reqs") == "required"


# ---------------------------------------------------------------------------
# Tests: factories.py spend_budget sync engine TLS
# ---------------------------------------------------------------------------


def test_build_spend_budget_fn_uses_ssl_connect_args_when_mtls_enabled(
    mtls_cert_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_spend_budget_fn() sync engine has TLS connect_args when MTLS_ENABLED=true."""
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://user:pass@host/db"  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "true")
    monkeypatch.setenv("MTLS_CA_CERT_PATH", str(mtls_cert_files["ca"]))
    monkeypatch.setenv("MTLS_CLIENT_CERT_PATH", str(mtls_cert_files["cert"]))
    monkeypatch.setenv("MTLS_CLIENT_KEY_PATH", str(mtls_cert_files["key"]))

    captured_kwargs: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return MagicMock()

    with patch("synth_engine.bootstrapper.factories.create_engine", side_effect=fake_create_engine):
        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        build_spend_budget_fn()

    connect_args = captured_kwargs.get("connect_args", {})
    assert isinstance(connect_args, dict)
    assert connect_args.get("sslmode") == "verify-full"
    assert str(connect_args.get("sslrootcert")) == str(mtls_cert_files["ca"])
    assert str(connect_args.get("sslcert")) == str(mtls_cert_files["cert"])
    assert str(connect_args.get("sslkey")) == str(mtls_cert_files["key"])


def test_build_spend_budget_fn_no_ssl_args_when_mtls_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_spend_budget_fn() sync engine has NO TLS connect_args when MTLS_ENABLED=false."""
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://user:pass@host/db"  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("MTLS_ENABLED", "false")

    captured_kwargs: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return MagicMock()

    with patch("synth_engine.bootstrapper.factories.create_engine", side_effect=fake_create_engine):
        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        build_spend_budget_fn()

    connect_args = captured_kwargs.get("connect_args", {})
    assert "sslmode" not in connect_args
