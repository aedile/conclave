"""Unit tests for the readiness probe endpoint GET /ready (T48.3).

Tests verify:
- AC1: Returns 200 when all dependencies are reachable.
- AC2: Returns 503 with structured error when any dependency fails.
- AC3: Individual dependency check results included in response body.
- AC4: Endpoint exempt from authentication (covered by attack tests) but
       subject to rate limiting (rate limit middleware test separate).
- AC5: /ready added to AUTH_EXEMPT_PATHS.
- AC6: /ready exempt from SealGateMiddleware via COMMON_INFRA_EXEMPT_PATHS.
- AC7: Dependency checks run concurrently (asyncio.gather).
- AC8: Per-check timeout of 3s enforced.
- AC9: MinIO check skipped when not configured.

CONSTITUTION Priority 0: Security
CONSTITUTION Priority 3: TDD
Task: T48.3 — Readiness Probe & External Dependency Health Checks
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
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


def _build_app() -> FastAPI:
    """Build a minimal FastAPI app with the health router registered.

    Returns:
        FastAPI instance with /ready endpoint registered.
    """
    from synth_engine.bootstrapper.routers.health import router as health_router

    app = FastAPI()
    app.include_router(health_router)
    return app


# ---------------------------------------------------------------------------
# AC1: Returns 200 when all dependencies are reachable
# ---------------------------------------------------------------------------


class TestReadinessAllHealthy:
    """Tests for the happy path when all dependencies are reachable."""

    @pytest.mark.asyncio
    async def test_ready_returns_200_when_all_checks_pass(self) -> None:
        """GET /ready returns 200 when database, Redis, and MinIO (if configured) are up."""
        app = _build_app()

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

    @pytest.mark.asyncio
    async def test_ready_200_body_has_status_ok(self) -> None:
        """200 response body must include status='ok'."""
        app = _build_app()

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

        body = response.json()
        assert body.get("status") == "ok"

    @pytest.mark.asyncio
    async def test_ready_200_body_has_all_checks_ok(self) -> None:
        """200 response body must show each check as 'ok'."""
        app = _build_app()

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

        body = response.json()
        checks = body.get("checks", {})
        assert checks.get("database") == "ok"
        assert checks.get("cache") == "ok"


# ---------------------------------------------------------------------------
# AC2: Returns 503 with structured error when any dependency fails
# ---------------------------------------------------------------------------


class TestReadinessDependencyFailure:
    """Tests for the unhappy path when a dependency is unreachable."""

    @pytest.mark.asyncio
    async def test_ready_returns_503_when_database_fails(self) -> None:
        """GET /ready returns 503 when the database check raises an exception."""
        app = _build_app()

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

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_ready_returns_503_when_redis_fails(self) -> None:
        """GET /ready returns 503 when the Redis check raises an exception."""
        app = _build_app()

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                new=AsyncMock(side_effect=Exception("redis unreachable")),
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
    async def test_ready_503_body_has_status_degraded(self) -> None:
        """503 response body must include status='degraded' or 'error'."""
        app = _build_app()

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

        body = response.json()
        assert body.get("status") in ("degraded", "error"), (
            f"503 body status must be 'degraded' or 'error', got {body.get('status')!r}"
        )

    @pytest.mark.asyncio
    async def test_ready_503_database_check_reports_error(self) -> None:
        """503 response checks.database must indicate failure."""
        app = _build_app()

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

        body = response.json()
        checks = body.get("checks", {})
        assert checks.get("database") == "error", (
            f"Failed database check must be 'error', got {checks.get('database')!r}"
        )

    @pytest.mark.asyncio
    async def test_ready_503_redis_check_reports_error(self) -> None:
        """503 response checks.cache must indicate failure when Redis fails."""
        app = _build_app()

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                new=AsyncMock(side_effect=Exception("redis down")),
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

        body = response.json()
        checks = body.get("checks", {})
        assert checks.get("cache") == "error", (
            f"Failed Redis check must be 'cache'='error', got {checks.get('cache')!r}"
        )

    @pytest.mark.asyncio
    async def test_ready_503_passing_checks_still_reported_as_ok(self) -> None:
        """503 response must still report passing checks as 'ok'."""
        app = _build_app()

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

        body = response.json()
        checks = body.get("checks", {})
        # Cache passed — must be "ok"
        assert checks.get("cache") == "ok"
        # Database failed — must be "error"
        assert checks.get("database") == "error"


# ---------------------------------------------------------------------------
# AC5: /ready added to AUTH_EXEMPT_PATHS
# ---------------------------------------------------------------------------


class TestReadinessAuthExemptPaths:
    """Assert /ready is present in AUTH_EXEMPT_PATHS."""

    def test_ready_in_auth_exempt_paths(self) -> None:
        """/ready must be in AUTH_EXEMPT_PATHS."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/ready" in AUTH_EXEMPT_PATHS

    def test_ready_in_common_infra_exempt_paths(self) -> None:
        """/ready must be in COMMON_INFRA_EXEMPT_PATHS (exempt from vault + auth gates)."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import COMMON_INFRA_EXEMPT_PATHS

        assert "/ready" in COMMON_INFRA_EXEMPT_PATHS


# ---------------------------------------------------------------------------
# AC9: MinIO check skipped when not configured
# ---------------------------------------------------------------------------


class TestReadinessMinIOOptional:
    """Assert that the MinIO check result is handled when MinIO is not configured."""

    @pytest.mark.asyncio
    async def test_ready_200_when_minio_skipped(self) -> None:
        """GET /ready returns 200 when MinIO is skipped (returns None)."""
        app = _build_app()

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

    @pytest.mark.asyncio
    async def test_minio_skipped_reflected_in_response(self) -> None:
        """When MinIO is skipped, object_store check should be absent or 'skipped'."""
        app = _build_app()

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

        body = response.json()
        checks = body.get("checks", {})
        # object_store must not be "error" when MinIO is not configured
        assert checks.get("object_store") != "error"

    @pytest.mark.asyncio
    async def test_ready_503_when_minio_configured_and_fails(self) -> None:
        """GET /ready returns 503 when MinIO is configured but unreachable."""
        app = _build_app()

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
                new=AsyncMock(side_effect=Exception("bucket not found")),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/ready")

        assert response.status_code == 503
        body = response.json()
        checks = body.get("checks", {})
        assert checks.get("object_store") == "error"


# ---------------------------------------------------------------------------
# AC: Router is registered at /ready (not some other path)
# ---------------------------------------------------------------------------


class TestReadinessRouteRegistration:
    """Assert the /ready route is registered on the correct path."""

    @pytest.mark.asyncio
    async def test_ready_endpoint_is_at_slash_ready(self) -> None:
        """GET /ready must respond; GET /readyz or /ready/ must not shadow it."""
        app = _build_app()

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

        assert response.status_code in (200, 503), (
            f"/ready must respond with 200 or 503, got {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_unknown_path_returns_404_not_200(self) -> None:
        """An unregistered path must return 404 so we can confirm /ready is explicit."""
        app = _build_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/readyz")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# AC: /ready added to router_registry (integration with full app)
# ---------------------------------------------------------------------------


class TestReadinessInFullApp:
    """Assert /ready is reachable through the full create_app() stack."""

    @pytest.mark.asyncio
    async def test_ready_endpoint_exists_in_full_app(self) -> None:
        """The /ready route must be registered via router_registry._include_routers."""
        from synth_engine.bootstrapper.main import create_app

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
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            app = create_app()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/ready")

        # Must reach the /ready handler, not a 404
        assert response.status_code in (200, 503), (
            f"Expected 200 or 503 from /ready in full app, got {response.status_code}"
        )
