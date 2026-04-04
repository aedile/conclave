"""Unit tests for the Settings router (bootstrapper/routers/settings.py).

Tests follow TDD RED phase — all tests must fail before implementation.

Task: P5-T5.1 — Task Orchestration API Core
Task: T49.2 — Replace isinstance/existence assertions with specific field value checks
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
    """Build a test FastAPI app wired with the settings router.

    Overrides ``get_current_operator`` to bypass JWT auth in functional tests
    that are not testing authentication itself (ADV-021).

    Rate limit is set to 10,000/min to prevent flaky 429s in CI — these tests
    exercise CRUD logic, not rate limiting (P78 CI fix).

    Returns:
        FastAPI app instance with settings router and mocked auth.
    """
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
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

    # Raise rate limit ceiling to prevent flaky 429s — these tests exercise
    # settings CRUD, not rate limiting.  The in-memory rate limiter accumulates
    # state across test functions sharing the same process, causing spurious
    # 429s in long CI runs (P78 fix).
    import os
    _prev = os.environ.get("RATE_LIMIT_GENERAL_PER_MINUTE")
    os.environ["RATE_LIMIT_GENERAL_PER_MINUTE"] = "10000"
    try:
        # Clear cached settings so the new env var takes effect
        from synth_engine.shared.settings import get_settings
        get_settings.cache_clear()
        app = create_app()
    finally:
        if _prev is None:
            os.environ.pop("RATE_LIMIT_GENERAL_PER_MINUTE", None)
        else:
            os.environ["RATE_LIMIT_GENERAL_PER_MINUTE"] = _prev
        get_settings.cache_clear()
    register_error_handlers(app)
    app.include_router(settings_router)

    def _override() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    # Override auth for non-auth-focused tests — they test settings CRUD, not authn
    app.dependency_overrides[get_current_operator] = lambda: "test-operator"
    return app


class TestSettingsCRUD:
    """CRUD tests for the /settings endpoints."""

    @pytest.mark.asyncio
    async def test_list_settings_returns_empty_items_on_fresh_db(self) -> None:
        """GET /settings must return HTTP 200 with an empty items list on a fresh database.

        Hardened from T49.2: previously asserted only 'items' in response.json().
        Now asserts the exact shape and value: items must be a list and must be
        empty when no settings have been stored yet.
        """
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
                response = await client.get("/api/v1/settings")

        assert response.status_code == 200
        body = response.json()
        assert "items" in body
        assert body["items"] == [], f"Expected empty items list on fresh DB, got: {body['items']!r}"

    @pytest.mark.asyncio
    async def test_list_settings_returns_previously_stored_settings(self) -> None:
        """GET /settings returns all stored settings with correct key/value pairs.

        Verifies that list returns the exact settings previously upserted,
        not just any non-empty list.
        """
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
                await client.put("/api/v1/settings/color_scheme", json={"value": "dark"})
                response = await client.get("/api/v1/settings")

        assert response.status_code == 200
        body = response.json()
        items = body["items"]
        assert len(items) == 1
        assert items[0]["key"] == "color_scheme"
        assert items[0]["value"] == "dark"

    @pytest.mark.asyncio
    async def test_upsert_setting_returns_key_and_value(self) -> None:
        """PUT /settings/{key} must return HTTP 200 with the exact key and value upserted.

        Hardened from T49.2: previously only checked response.status_code == 200.
        Now asserts the response body contains the correct key and value fields.
        """
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
                    "/api/v1/settings/max_epochs",
                    json={"value": "300"},
                )

        assert response.status_code == 200
        body = response.json()
        assert body["key"] == "max_epochs", f"Expected key='max_epochs', got {body.get('key')!r}"
        assert body["value"] == "300", f"Expected value='300', got {body.get('value')!r}"

    @pytest.mark.asyncio
    async def test_upsert_setting_updates_existing_value(self) -> None:
        """PUT /settings/{key} on an existing key updates the value (upsert semantics).

        Verifies that a second PUT on the same key replaces the stored value,
        not duplicates it.
        """
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
                await client.put("/api/v1/settings/batch_size", json={"value": "32"})
                response = await client.put("/api/v1/settings/batch_size", json={"value": "64"})

        assert response.status_code == 200
        body = response.json()
        assert body["key"] == "batch_size"
        assert body["value"] == "64", (
            f"Upsert must overwrite previous value; expected '64', got {body.get('value')!r}"
        )

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
                await client.put("/api/v1/settings/theme", json={"value": "dark"})
                response = await client.get("/api/v1/settings/theme")

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
                response = await client.get("/api/v1/settings/nonexistent_key_abc")

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
                await client.put("/api/v1/settings/to_delete", json={"value": "x"})
                response = await client.delete("/api/v1/settings/to_delete")

        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_then_get_setting_returns_404(self) -> None:
        """Deleting a setting then GETting it returns 404 (T49.2).

        Verifies that delete has a real effect: the setting is gone from
        the database, not just marked deleted.
        """
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
                await client.put("/api/v1/settings/ephemeral_key", json={"value": "to_be_deleted"})
                await client.delete("/api/v1/settings/ephemeral_key")
                response = await client.get("/api/v1/settings/ephemeral_key")

        assert response.status_code == 404, (
            "Setting must not be retrievable after deletion; "
            f"expected 404, got {response.status_code}"
        )
