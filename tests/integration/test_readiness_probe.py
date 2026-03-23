"""Integration tests for the readiness probe endpoint GET /ready (T48.3).

These tests exercise the full HTTP stack with real dependencies where
available (database, Redis) or appropriate mocks.

Tests verify:
- GET /ready returns 200 or 503 (never other status codes)
- /ready passes through the full middleware stack without 423/401
- /ready is reachable without authentication token
- Response body has correct schema in both 200 and 503 cases

CONSTITUTION Priority 0: Security
CONSTITUTION Priority 3: TDD
Task: T48.3 -- Readiness Probe & External Dependency Health Checks
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Settings cache isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None -- setup and teardown only.
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
# Full-stack integration tests
# ---------------------------------------------------------------------------


class TestReadinessFullStack:
    """Integration tests for /ready through the full middleware stack."""

    @pytest.mark.asyncio
    async def test_ready_returns_valid_http_status(self) -> None:
        """GET /ready must return 200 or 503 through the full middleware stack.

        This test drives the full create_app() stack to ensure /ready is
        reachable and the middleware chain does not block it.
        """
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

        assert response.status_code in (200, 503), (
            f"GET /ready must return 200 or 503, got {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_ready_not_blocked_by_sealed_vault_in_full_stack(self) -> None:
        """GET /ready must not be blocked by SealGateMiddleware in the full stack.

        Kubernetes readiness probes must work regardless of vault seal state.
        """
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
                return_value=True,  # vault is SEALED
            ),
        ):
            app = create_app()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/ready")

        # 423 would mean SealGateMiddleware blocked the request -- must not happen
        assert response.status_code != 423, (
            "/ready must bypass SealGateMiddleware even when vault is sealed"
        )
        assert response.status_code in (200, 503)

    @pytest.mark.asyncio
    async def test_ready_not_blocked_by_auth_in_full_stack(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /ready must not require authentication in the full stack.

        Infrastructure probes cannot carry Bearer tokens.
        """
        monkeypatch.setenv("JWT_SECRET_KEY", "integration-test-key-long-enough-32chars+")

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
                # No Authorization header
                response = await client.get("/ready")

        # 401 would mean auth middleware blocked the request -- must not happen
        assert response.status_code != 401, (
            "/ready must bypass AuthenticationGateMiddleware without a token"
        )
        assert response.status_code in (200, 503)

    @pytest.mark.asyncio
    async def test_ready_response_body_schema_200(self) -> None:
        """GET /ready 200 response must include status='ok' and checks dict."""
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

        assert response.status_code == 200
        body = response.json()
        assert body.get("status") == "ok"
        checks = body.get("checks", {})
        assert "database" in checks
        assert "cache" in checks
        assert checks["database"] == "ok"
        assert checks["cache"] == "ok"

    @pytest.mark.asyncio
    async def test_ready_503_response_body_schema(self) -> None:
        """GET /ready 503 response must include status='degraded' and failed checks."""
        from synth_engine.bootstrapper.main import create_app

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(side_effect=Exception("db unreachable")),
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

        assert response.status_code == 503
        body = response.json()
        assert body.get("status") == "degraded"
        checks = body.get("checks", {})
        assert checks.get("database") == "error"
        assert checks.get("cache") == "ok"

    @pytest.mark.asyncio
    async def test_ready_no_info_leakage_in_503(self) -> None:
        """GET /ready 503 body must not expose internal hostnames or error details."""
        from synth_engine.bootstrapper.main import create_app

        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                new=AsyncMock(side_effect=Exception("connection refused to pgbouncer:5432")),
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

        assert response.status_code == 503
        body_str = str(response.json()).lower()
        # Must not leak internal hostname or port
        assert "pgbouncer" not in body_str
        assert ":5432" not in body_str
        assert "connection refused" not in body_str
