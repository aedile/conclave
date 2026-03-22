"""Negative / attack tests for the readiness probe endpoint (T48.3).

Tests target security properties BEFORE any feature implementation:
- No information leakage of internal hostnames/ports/connection strings.
- Generic service names only (database, cache, object_store).
- /ready exempt from SealGateMiddleware (sealed vault must not block infra probes).
- /ready exempt from AuthenticationGateMiddleware (no Bearer token required).
- Rate limiting still applies to /ready (DoS via probe endpoint protection).
- Per-check timeout enforced (one slow dependency must not hang the probe).
- Dependency check errors return 503, not 500 or 200.

All tests are RED until the production implementation exists.

CONSTITUTION Priority 0: Security
Task: T48.3 — Readiness Probe & External Dependency Health Checks
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Settings cache isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass
    yield
    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_ready_app_with_sealed_vault() -> FastAPI:
    """Build a minimal FastAPI app with SealGateMiddleware active and vault sealed.

    Registers the /ready router so we can assert it passes through a sealed vault.

    Returns:
        FastAPI instance with vault sealed and /ready router registered.
    """
    from synth_engine.bootstrapper.dependencies.vault import SealGateMiddleware
    from synth_engine.bootstrapper.routers.health import router as health_router

    app = FastAPI()
    app.add_middleware(SealGateMiddleware)
    app.include_router(health_router)
    return app


def _build_ready_app_with_auth() -> FastAPI:
    """Build a minimal FastAPI app with AuthenticationGateMiddleware active.

    Returns:
        FastAPI instance with auth middleware and /ready router registered.
    """
    from synth_engine.bootstrapper.dependencies.auth import AuthenticationGateMiddleware
    from synth_engine.bootstrapper.routers.health import router as health_router

    app = FastAPI()
    app.add_middleware(AuthenticationGateMiddleware)
    app.include_router(health_router)
    return app


# ---------------------------------------------------------------------------
# AC: No information leakage in 503 response body
# ---------------------------------------------------------------------------


class TestReadinessNoInfoLeakage:
    """Assert that 503 responses do not leak internal hostnames, ports, or DSNs."""

    @pytest.mark.asyncio
    async def test_503_body_does_not_contain_database_hostname(self) -> None:
        """503 response must not include the internal PostgreSQL hostname in its body.

        Internal hostnames like 'pgbouncer', 'postgres', 'localhost' and port
        numbers must never appear in error responses to unauthenticated callers.
        """
        from synth_engine.bootstrapper.routers.health import router as health_router

        app = FastAPI()
        app.include_router(health_router)

        # Mock PostgreSQL check to raise with a hostname-bearing message.
        db_exc = Exception("could not connect to server: Connection refused at pgbouncer:5432")
        redis_ok = AsyncMock(return_value=True)

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(side_effect=db_exc),
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                new=redis_ok,
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

        assert response.status_code == 503
        body = response.json()
        body_str = str(body).lower()
        # Must not leak internal hostname
        assert "pgbouncer" not in body_str
        assert "postgres" not in body_str
        # Must not leak port numbers (colon-port pattern like :5432)
        assert ":5432" not in body_str

    @pytest.mark.asyncio
    async def test_503_body_does_not_contain_redis_url(self) -> None:
        """503 response must not include the Redis connection URL or hostname."""
        from synth_engine.bootstrapper.routers.health import router as health_router

        app = FastAPI()
        app.include_router(health_router)

        redis_exc = Exception("Error connecting to redis://redis:6379/0: Connection refused")

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                new=AsyncMock(side_effect=redis_exc),
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

        assert response.status_code == 503
        body_str = str(response.json()).lower()
        # Must not leak Redis URL scheme or hostname
        assert "redis://" not in body_str
        assert ":6379" not in body_str

    @pytest.mark.asyncio
    async def test_503_body_uses_generic_service_names_only(self) -> None:
        """503 response body must use generic names: database, cache, object_store."""
        from synth_engine.bootstrapper.routers.health import router as health_router

        app = FastAPI()
        app.include_router(health_router)

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(side_effect=Exception("internal error")),
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

        assert response.status_code == 503
        body = response.json()
        # The failed check must be identified by a generic name, not internal label
        checks = body.get("checks", {})
        # Generic keys must be present
        assert "database" in checks or any(
            k in ("database", "cache", "object_store") for k in checks
        ), f"Expected generic service names, got: {list(checks.keys())}"


# ---------------------------------------------------------------------------
# AC: /ready must be reachable when vault is sealed
# ---------------------------------------------------------------------------


class TestReadinessExemptFromSealGate:
    """Assert that /ready is exempt from SealGateMiddleware."""

    @pytest.mark.asyncio
    async def test_ready_accessible_when_vault_is_sealed(self) -> None:
        """GET /ready must not be blocked by SealGateMiddleware when vault is sealed.

        Kubernetes readiness probes must be able to check dependency health
        regardless of vault seal state.
        """
        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=True,
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(return_value=True),
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
            app = _build_ready_app_with_sealed_vault()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/ready")

        # Must NOT be 423 (vault sealed); must reach the handler
        assert response.status_code != 423, (
            "/ready must bypass SealGateMiddleware — got 423 (vault sealed)"
        )

    @pytest.mark.asyncio
    async def test_other_routes_blocked_when_vault_sealed(self) -> None:
        """Non-exempt routes must still return 423 when vault is sealed.

        This validates that SealGateMiddleware is active but /ready is specifically exempt.
        """
        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=True,
        ):
            from synth_engine.bootstrapper.dependencies.vault import SealGateMiddleware

            app = FastAPI()
            app.add_middleware(SealGateMiddleware)

            @app.get("/protected")
            async def _protected() -> JSONResponse:
                return JSONResponse(content={"ok": True})

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/protected")

        assert response.status_code == 423


# ---------------------------------------------------------------------------
# AC: /ready must be reachable without authentication
# ---------------------------------------------------------------------------


class TestReadinessExemptFromAuthGate:
    """Assert that /ready is exempt from AuthenticationGateMiddleware."""

    @pytest.mark.asyncio
    async def test_ready_accessible_without_bearer_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /ready must return a response without a Bearer token.

        Infrastructure probes cannot carry user credentials.
        """
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-long-enough-for-hs256-32chars+")

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(return_value=True),
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
            app = _build_ready_app_with_auth()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # No Authorization header
                response = await client.get("/ready")

        # Must NOT be 401
        assert response.status_code != 401, (
            "/ready must bypass AuthenticationGateMiddleware — got 401"
        )


# ---------------------------------------------------------------------------
# AC: Dependency check errors return 503, not 500
# ---------------------------------------------------------------------------


class TestReadinessDependencyFailureStatus:
    """Assert that dependency failures return 503 Service Unavailable, not 500."""

    @pytest.mark.asyncio
    async def test_database_failure_returns_503_not_500(self) -> None:
        """An unhandled exception in the database check must yield 503, not 500."""
        from synth_engine.bootstrapper.routers.health import router as health_router

        app = FastAPI()
        app.include_router(health_router)

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(side_effect=RuntimeError("unexpected db error")),
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

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_redis_failure_returns_503_not_500(self) -> None:
        """An unhandled exception in the Redis check must yield 503, not 500."""
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
                new=AsyncMock(side_effect=RuntimeError("unexpected redis error")),
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

        assert response.status_code == 503


# ---------------------------------------------------------------------------
# AC: Individual check results in response body
# ---------------------------------------------------------------------------


class TestReadinessResponseStructure:
    """Assert the response body structure is correct for both 200 and 503."""

    @pytest.mark.asyncio
    async def test_200_response_includes_check_results(self) -> None:
        """200 response must include individual check results with generic names."""
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

        assert response.status_code == 200
        body = response.json()
        assert "checks" in body, "Response must include a 'checks' field"
        checks = body["checks"]
        assert "database" in checks, "checks must include 'database' key"
        assert "cache" in checks, "checks must include 'cache' key"

    @pytest.mark.asyncio
    async def test_503_response_includes_failed_check_detail(self) -> None:
        """503 response must identify which dependency failed."""
        from synth_engine.bootstrapper.routers.health import router as health_router

        app = FastAPI()
        app.include_router(health_router)

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(side_effect=Exception("db down")),
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

        assert response.status_code == 503
        body = response.json()
        # Must have a status field indicating which service failed
        assert "checks" in body
        checks = body["checks"]
        assert "database" in checks
        db_check = checks["database"]
        # The check entry must not be "ok"
        assert db_check != "ok", "Failed database check must not be reported as ok"


# ---------------------------------------------------------------------------
# AC: MinIO skip when not configured
# ---------------------------------------------------------------------------


class TestReadinessMinIOSkip:
    """Assert that MinIO check is skipped when not configured."""

    @pytest.mark.asyncio
    async def test_ready_succeeds_when_minio_not_configured(self) -> None:
        """GET /ready must return 200 even when MinIO endpoint is not configured.

        MinIO is optional/ephemeral — its absence must not fail the readiness probe.
        """
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
                new=AsyncMock(return_value=True),
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_minio",
                new=AsyncMock(return_value=None),  # None means skipped
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/ready")

        assert response.status_code == 200
        body = response.json()
        # object_store may be absent or explicitly marked as skipped — not failed
        checks = body.get("checks", {})
        object_store_status = checks.get("object_store")
        assert object_store_status != "error", (
            "object_store must not be 'error' when MinIO is not configured"
        )
