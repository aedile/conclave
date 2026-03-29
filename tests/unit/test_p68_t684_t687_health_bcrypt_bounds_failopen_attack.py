"""Negative/attack tests for health strict mode, bcrypt narrowing,
input bounds, and fail-open blocking (T68.4-T68.7).

Covers:
- T68.4: Health check strict mode — configured-but-unreachable service returns 503
- T68.5: Bcrypt narrowing — RuntimeError/MemoryError propagate, ValueError/TypeError caught
- T68.6: Input bounds — oversized fields return 422
- T68.7: rate_limit_fail_open=True in production raises at startup

ATTACK-FIRST TDD — these tests are written before the GREEN phase.
CONSTITUTION Priority 0: Security — misconfig must block, not pass silently
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T68.4 — Health Check Strict Mode for Production
Task: T68.5 — Narrow Bcrypt Exception Handling
Task: T68.6 — Close Unbounded Input Field Advisory
Task: T68.7 — Enforce rate_limit_fail_open Block in Production
"""

from __future__ import annotations

import base64
import os
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_vault_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Unseal vault for tests and reset after.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.security.vault import VaultState

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    VaultState.reset()
    yield
    VaultState.reset()


# ---------------------------------------------------------------------------
# T68.4 — Health strict mode attack tests
# ---------------------------------------------------------------------------


class TestHealthStrictMode:
    """Health endpoint strict mode must return 503 when services are unreachable."""

    @pytest.fixture
    def unsealed_vault(self, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
        """Unseal vault so 200-path tests can pass vault check.

        Args:
            monkeypatch: pytest monkeypatch fixture.

        Yields:
            None.
        """
        from synth_engine.shared.security.vault import VaultState

        VaultState.unseal("test-passphrase-health")
        yield
        VaultState.reset()

    @pytest.mark.asyncio
    async def test_ready_strict_mode_unreachable_db_returns_503(
        self, monkeypatch: pytest.MonkeyPatch, unsealed_vault: None
    ) -> None:
        """In strict mode, unreachable database must return 503.

        Arrange: set DATABASE_URL, strict mode=True, mock DB to raise.
        Act: GET /ready.
        Assert: 503.
        """
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db:5432/test")
        monkeypatch.setenv("CONCLAVE_HEALTH_STRICT", "true")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.routers.health import router as health_router

        app = FastAPI()
        app.include_router(health_router)

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(side_effect=Exception("connection refused")),
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_minio",
                new=AsyncMock(return_value=None),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/ready")

        assert response.status_code == 503, (
            f"Strict mode + unreachable DB must return 503; got {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_ready_strict_mode_missing_database_url_returns_503(
        self, monkeypatch: pytest.MonkeyPatch, unsealed_vault: None
    ) -> None:
        """Strict mode + no DATABASE_URL configured must return 503.

        When strict=True, an unconfigured DATABASE_URL means the service is
        expected but absent — the instance is not ready.
        """
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("CONCLAVE_DATABASE_URL", raising=False)
        monkeypatch.setenv("CONCLAVE_HEALTH_STRICT", "true")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.routers.health import router as health_router

        app = FastAPI()
        app.include_router(health_router)

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_minio",
                new=AsyncMock(return_value=None),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/ready")

        assert response.status_code == 503, (
            f"Strict mode + no DATABASE_URL must return 503; got {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_ready_strict_mode_redis_unreachable_returns_503(
        self, monkeypatch: pytest.MonkeyPatch, unsealed_vault: None
    ) -> None:
        """Strict mode + unreachable Redis must return 503.

        Arrange: set DATABASE_URL, strict mode=True, mock Redis to raise.
        Act: GET /ready.
        Assert: 503.
        """
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db:5432/test")
        monkeypatch.setenv("CONCLAVE_HEALTH_STRICT", "true")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.routers.health import router as health_router

        app = FastAPI()
        app.include_router(health_router)

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                new=AsyncMock(side_effect=Exception("redis unavailable")),
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_minio",
                new=AsyncMock(return_value=None),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/ready")

        assert response.status_code == 503, (
            f"Strict mode + unreachable Redis must return 503; got {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_ready_permissive_mode_unreachable_db_returns_200(
        self, monkeypatch: pytest.MonkeyPatch, unsealed_vault: None
    ) -> None:
        """Permissive (development) mode + unreachable DB must NOT return 503.

        Existing skip behavior is preserved in permissive mode.
        The /ready endpoint returns 200 (or skips) when DB is unconfigured and strict=False.
        """
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("CONCLAVE_DATABASE_URL", raising=False)
        monkeypatch.setenv("CONCLAVE_HEALTH_STRICT", "false")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.routers.health import router as health_router

        app = FastAPI()
        app.include_router(health_router)

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_minio",
                new=AsyncMock(return_value=None),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/ready")

        assert response.status_code == 200, (
            f"Permissive mode + no DB URL must return 200; got {response.status_code}"
        )
        body = response.json()
        assert body["checks"]["database"] == "skipped", (
            f"Permissive mode + no DB URL must report database=skipped; got {body['checks']['database']}"
        )

    def test_ready_strict_defaults_true_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """conclave_health_strict must default to True when CONCLAVE_ENV=production.

        Per T68.4 spec amendment: the field uses a model_validator that sets
        strict=True when the env is production and the field was not explicitly set.
        Sets all production-required fields to avoid unrelated startup failures.
        """
        monkeypatch.setenv("CONCLAVE_ENV", "production")
        monkeypatch.delenv("CONCLAVE_HEALTH_STRICT", raising=False)
        # Set all production-required fields to avoid unrelated ValidationError
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db:5432/test")
        monkeypatch.setenv("AUDIT_KEY", "a" * 64)
        monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key-32-chars-minimum!")
        monkeypatch.setenv(
            "OPERATOR_CREDENTIALS_HASH",
            "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/L/Ldv5t.iifcXiJea",  # pragma: allowlist secret
        )
        monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "b" * 64)
        monkeypatch.setenv("MASKING_SALT", "c" * 64)

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        settings = get_settings()
        assert settings.conclave_health_strict is True, (
            f"conclave_health_strict must default to True in production; "
            f"got {settings.conclave_health_strict}"
        )

    def test_ready_strict_defaults_false_in_development(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """conclave_health_strict must default to False when CONCLAVE_ENV=development.

        Development mode preserves permissive behavior by default.
        """
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.delenv("CONCLAVE_HEALTH_STRICT", raising=False)

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        settings = get_settings()
        assert settings.conclave_health_strict is False, (
            f"conclave_health_strict must default to False in development; "
            f"got {settings.conclave_health_strict}"
        )


# ---------------------------------------------------------------------------
# T68.5 — Bcrypt narrowing attack tests
# ---------------------------------------------------------------------------


class TestBcryptExceptionNarrowing:
    """verify_operator_credentials must narrow exception handling to ValueError/TypeError."""

    def _setup_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Configure a valid bcrypt hash in settings.

        Args:
            monkeypatch: pytest monkeypatch fixture.
        """
        import bcrypt as _bcrypt

        real_hash = _bcrypt.hashpw(b"test-passphrase", _bcrypt.gensalt()).decode()
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", real_hash)

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

    def test_bcrypt_runtime_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """RuntimeError from bcrypt.checkpw must propagate, not be caught as auth failure.

        RuntimeError is a system error that should crash loudly, not silently
        return False (which would mask the real problem as "wrong password").
        """
        self._setup_credentials(monkeypatch)

        from synth_engine.bootstrapper.dependencies.auth import verify_operator_credentials

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.auth._bcrypt.checkpw",
                side_effect=RuntimeError("bcrypt native library error"),
            ),
            pytest.raises(RuntimeError, match="bcrypt native library error"),
        ):
            verify_operator_credentials("any-passphrase")

    def test_bcrypt_memory_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MemoryError from bcrypt.checkpw must propagate, not be caught as auth failure.

        MemoryError is a serious system condition — masking it as "wrong password"
        would leave operators unable to diagnose critical resource exhaustion.
        """
        self._setup_credentials(monkeypatch)

        from synth_engine.bootstrapper.dependencies.auth import verify_operator_credentials

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.auth._bcrypt.checkpw",
                side_effect=MemoryError("out of memory"),
            ),
            pytest.raises(MemoryError, match="out of memory"),
        ):
            verify_operator_credentials("any-passphrase")

    def test_bcrypt_value_error_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ValueError from bcrypt.checkpw (malformed hash) must still return False.

        ValueError is a documented bcrypt failure mode for invalid/malformed hashes —
        treat it as "auth denied", not a system error.
        """
        self._setup_credentials(monkeypatch)

        from synth_engine.bootstrapper.dependencies.auth import verify_operator_credentials

        with patch(
            "synth_engine.bootstrapper.dependencies.auth._bcrypt.checkpw",
            side_effect=ValueError("Invalid salt: bcrypt hash format error"),
        ):
            result = verify_operator_credentials("any-passphrase")

        assert result is False, (
            f"ValueError from bcrypt must return False (auth denied); got {result!r}"
        )

    def test_bcrypt_type_error_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TypeError from bcrypt.checkpw (bad argument type) must still return False.

        TypeError is a documented bcrypt failure mode for malformed input types.
        """
        self._setup_credentials(monkeypatch)

        from synth_engine.bootstrapper.dependencies.auth import verify_operator_credentials

        with patch(
            "synth_engine.bootstrapper.dependencies.auth._bcrypt.checkpw",
            side_effect=TypeError("expected bytes, not str"),
        ):
            result = verify_operator_credentials("any-passphrase")

        assert result is False, (
            f"TypeError from bcrypt must return False (auth denied); got {result!r}"
        )


# ---------------------------------------------------------------------------
# T68.6 — Input bounds attack tests
# ---------------------------------------------------------------------------


@pytest.fixture
def jobs_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build a minimal FastAPI app with the jobs router (pass-through auth).

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        TestClient for the jobs router.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")

    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Import all table models to register them
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob  # noqa: F401

    SQLModel.metadata.create_all(engine)

    app = FastAPI()
    app.include_router(jobs_router)

    def _get_session() -> Generator:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _get_session
    app.dependency_overrides[get_current_operator] = lambda: "test-operator"

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def webhooks_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build a minimal FastAPI app with the webhooks router (pass-through auth).

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        TestClient for the webhooks router.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")

    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.webhooks import router as webhooks_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from synth_engine.bootstrapper.schemas.webhooks import (  # noqa: F401
        WebhookDelivery,
        WebhookRegistration,
    )

    SQLModel.metadata.create_all(engine)

    app = FastAPI()
    app.include_router(webhooks_router)

    def _get_session() -> Generator:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _get_session
    app.dependency_overrides[get_current_operator] = lambda: "test-operator"

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def privacy_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build a minimal FastAPI app with the privacy router (pass-through auth).

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        TestClient for the privacy router.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")

    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.privacy import router as privacy_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from synth_engine.modules.privacy.ledger import PrivacyLedger  # noqa: F401

    SQLModel.metadata.create_all(engine)

    app = FastAPI()
    app.include_router(privacy_router)

    def _get_session() -> Generator:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _get_session
    app.dependency_overrides[get_current_operator] = lambda: "test-operator"

    return TestClient(app, raise_server_exceptions=False)


class TestInputBoundsValidation:
    """Oversized inputs must return 422 Unprocessable Entity."""

    def test_parquet_path_oversized_returns_422(self, jobs_client: TestClient) -> None:
        """parquet_path > 1024 chars must return 422.

        A path longer than 1024 characters is a DoS/log-injection vector.
        """
        oversized_path = "/data/" + "a" * 1020 + ".parquet"
        assert len(oversized_path) > 1024

        response = jobs_client.post(
            "/jobs",
            json={
                "table_name": "users",
                "parquet_path": oversized_path,
                "total_epochs": 10,
                "num_rows": 100,
            },
        )
        assert response.status_code == 422, (
            f"parquet_path > 1024 chars must return 422; got {response.status_code}. "
            f"Body: {response.json()}"
        )

    def test_table_name_oversized_returns_422(self, jobs_client: TestClient) -> None:
        """table_name > 255 chars must return 422.

        PostgreSQL identifier limit is 63 bytes; 255 is a conservative upper bound.
        """
        oversized_name = "a" * 256

        response = jobs_client.post(
            "/jobs",
            json={
                "table_name": oversized_name,
                "parquet_path": "/data/test.parquet",
                "total_epochs": 10,
                "num_rows": 100,
            },
        )
        assert response.status_code == 422, (
            f"table_name > 255 chars must return 422; got {response.status_code}. "
            f"Body: {response.json()}"
        )

    def test_callback_url_oversized_returns_422(self, webhooks_client: TestClient) -> None:
        """callback_url > 2048 chars must return 422.

        A URL exceeding the HTTP practical limit is a DoS vector.
        """
        oversized_url = "https://example.com/" + "a" * 2050

        response = webhooks_client.post(
            "/webhooks",
            json={
                "callback_url": oversized_url,
                "signing_key": "a" * 32,
                "events": ["job.completed"],
            },
        )
        assert response.status_code == 422, (
            f"callback_url > 2048 chars must return 422; got {response.status_code}. "
            f"Body: {response.json()}"
        )

    def test_signing_key_oversized_returns_422(self, webhooks_client: TestClient) -> None:
        """signing_key > 512 chars must return 422.

        An HMAC key exceeding 512 bytes is unnecessary and a DoS vector.
        """
        oversized_key = "a" * 513

        response = webhooks_client.post(
            "/webhooks",
            json={
                "callback_url": "https://example.com/webhook",
                "signing_key": oversized_key,
                "events": ["job.completed"],
            },
        )
        assert response.status_code == 422, (
            f"signing_key > 512 chars must return 422; got {response.status_code}. "
            f"Body: {response.json()}"
        )

    def test_justification_oversized_returns_422(self, privacy_client: TestClient) -> None:
        """justification > 2000 chars must return 422.

        Oversized justification strings are a log-injection/DoS vector.
        """
        oversized_justification = "a" * 2001

        response = privacy_client.post(
            "/privacy/budget/refresh",
            json={"justification": oversized_justification},
        )
        assert response.status_code == 422, (
            f"justification > 2000 chars must return 422; got {response.status_code}. "
            f"Body: {response.json()}"
        )


# ---------------------------------------------------------------------------
# T68.7 — rate_limit_fail_open block in production
# ---------------------------------------------------------------------------


class TestRateLimitFailOpenProduction:
    """validate_config must raise SystemExit when fail_open=True in production."""

    def test_rate_limit_fail_open_production_raises_at_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Production mode + fail_open=True must raise SystemExit at startup.

        Arrange: set CONCLAVE_ENV=production, CONCLAVE_RATE_LIMIT_FAIL_OPEN=true.
        Act: call validate_config().
        Assert: SystemExit raised.
        """
        monkeypatch.setenv("CONCLAVE_ENV", "production")
        monkeypatch.setenv("CONCLAVE_RATE_LIMIT_FAIL_OPEN", "true")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db:5432/test")
        monkeypatch.setenv("AUDIT_KEY", "a" * 64)
        # Set all production-required fields to avoid unrelated failures
        monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key-32-chars-minimum!")
        monkeypatch.setenv(
            "OPERATOR_CREDENTIALS_HASH",
            "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/L/Ldv5t.iifcXiJea",  # pragma: allowlist secret
        )
        monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "b" * 64)
        monkeypatch.setenv("MASKING_SALT", "c" * 64)

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.config_validation import validate_config

        with pytest.raises(SystemExit):
            validate_config()

    def test_rate_limit_fail_open_development_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Development mode + fail_open=True must NOT raise at startup.

        Dev mode operators may intentionally enable fail-open for testing.
        """
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("CONCLAVE_RATE_LIMIT_FAIL_OPEN", "true")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db:5432/test")
        monkeypatch.setenv("AUDIT_KEY", "a" * 64)

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.config_validation import validate_config

        # Should NOT raise — development mode allows fail-open
        # (may raise for other reasons like vault warnings, which is fine)
        try:
            validate_config()
        except SystemExit as exc:
            # If SystemExit fires, it must NOT be about rate_limit_fail_open
            error_msg = str(exc)
            assert "rate_limit_fail_open" not in error_msg.lower(), (
                f"Development mode must allow fail-open=True; "
                f"but SystemExit raised with: {error_msg}"
            )
            assert "fail_open" not in error_msg.lower(), (
                f"Development mode must allow fail-open=True; "
                f"but SystemExit raised with: {error_msg}"
            )

    def test_rate_limit_fail_open_error_message_contains_remediation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit message must include remediation steps (specific env var to set).

        Operators need to know how to fix the misconfiguration, not just that it
        exists. The error message must name the specific setting to change.
        """
        monkeypatch.setenv("CONCLAVE_ENV", "production")
        monkeypatch.setenv("CONCLAVE_RATE_LIMIT_FAIL_OPEN", "true")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db:5432/test")
        monkeypatch.setenv("AUDIT_KEY", "a" * 64)
        monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key-32-chars-minimum!")
        monkeypatch.setenv(
            "OPERATOR_CREDENTIALS_HASH",
            "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/L/Ldv5t.iifcXiJea",  # pragma: allowlist secret
        )
        monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "b" * 64)
        monkeypatch.setenv("MASKING_SALT", "c" * 64)

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.config_validation import validate_config

        with pytest.raises(SystemExit) as exc_info:
            validate_config()

        error_msg = str(exc_info.value)
        # Must mention the setting name so operators know what to change
        assert "fail_open" in error_msg.lower() or "CONCLAVE_RATE_LIMIT_FAIL_OPEN" in error_msg, (
            f"Error message must name the misconfigured setting; got: {error_msg!r}"
        )
        # Must suggest a remediation (e.g., "Set ... to false")
        assert "false" in error_msg.lower() or "set" in error_msg.lower(), (
            f"Error message must include remediation steps; got: {error_msg!r}"
        )
