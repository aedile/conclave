"""Unit tests for the Settings router (bootstrapper/routers/settings.py).

Tests follow TDD RED phase — all tests must fail before implementation.

Task: P5-T5.1 — Task Orchestration API Core
CONSTITUTION Priority 3: TDD — RED phase
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit


def _make_settings_app() -> Any:
    """Build a test FastAPI app wired with the settings router."""
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.settings import router as settings_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()
    register_error_handlers(app)
    app.include_router(settings_router)

    def _override() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    return app


class TestSettingsCRUD:
    """CRUD tests for the /settings endpoints."""

    @pytest.mark.asyncio
    async def test_list_settings_returns_200(self) -> None:
        """GET /settings must return HTTP 200 with items list."""
        app = _make_settings_app()

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/settings")

        assert response.status_code == 200
        assert "items" in response.json()

    @pytest.mark.asyncio
    async def test_upsert_setting_returns_200(self) -> None:
        """PUT /settings/{key} must return HTTP 200 with the upserted value."""
        app = _make_settings_app()

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.put(
                    "/settings/max_epochs",
                    json={"value": "300"},
                )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_setting_returns_value(self) -> None:
        """GET /settings/{key} must return the stored value."""
        app = _make_settings_app()

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.put("/settings/theme", json={"value": "dark"})
                response = await client.get("/settings/theme")

        assert response.status_code == 200
        body = response.json()
        assert body["key"] == "theme"
        assert body["value"] == "dark"

    @pytest.mark.asyncio
    async def test_get_missing_setting_returns_404(self) -> None:
        """GET /settings/{key} must return 404 for nonexistent key."""
        app = _make_settings_app()

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/settings/nonexistent_key_abc")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_setting_returns_204(self) -> None:
        """DELETE /settings/{key} must return 204 No Content."""
        app = _make_settings_app()

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.put("/settings/to_delete", json={"value": "x"})
                response = await client.delete("/settings/to_delete")

        assert response.status_code == 204
