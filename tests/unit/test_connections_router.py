"""Unit tests for the Connections router (bootstrapper/routers/connections.py).

Tests follow TDD RED phase — all tests must fail before implementation.

Task: P5-T5.1 — Task Orchestration API Core
Task: T39.4 — Encrypt Connection Metadata with ALE (router tests updated to
    unseal the vault so EncryptedString columns can encrypt/decrypt via the
    vault KEK path, consistent with how these tests already mock is_sealed)
CONSTITUTION Priority 3: TDD — RED phase
"""

from __future__ import annotations

import base64
import os
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit


def _make_connections_app() -> Any:
    """Build a test FastAPI app wired with the connections router."""
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.connections import router as connections_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()
    register_error_handlers(app)
    app.include_router(connections_router)

    def _override() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    return app


class TestConnectionsCRUD:
    """CRUD tests for the /connections endpoints."""

    @pytest.fixture(autouse=True)
    def _unseal_vault_for_ale(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        """Unseal the vault so EncryptedString columns can encrypt/decrypt.

        The router tests mock ``VaultState.is_sealed`` to return ``False`` to
        bypass the 423 vault gate.  Since that mock also affects
        ``get_fernet()`` in ``ale.py``, the vault must actually be unsealed
        so that ``VaultState.get_kek()`` succeeds and the ALE layer can
        derive the encryption key via HKDF.

        Resets (re-seals) the vault after each test for isolation.
        """
        from synth_engine.shared.security.vault import VaultState

        salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
        monkeypatch.setenv("VAULT_SEAL_SALT", salt)
        VaultState.unseal("test-router-passphrase")
        yield
        VaultState.reset()

    @pytest.mark.asyncio
    async def test_list_connections_returns_200(self) -> None:
        """GET /connections must return HTTP 200 with items list."""
        app = _make_connections_app()

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
                response = await client.get("/connections")

        assert response.status_code == 200
        assert "items" in response.json()

    @pytest.mark.asyncio
    async def test_create_connection_returns_201(self) -> None:
        """POST /connections must return HTTP 201 Created."""
        app = _make_connections_app()

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
                response = await client.post(
                    "/connections",
                    json={
                        "name": "prod-db",
                        "host": "postgres",
                        "port": 5432,
                        "database": "mydb",
                        "schema_name": "public",
                    },
                )

        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_create_connection_body_roundtrip(self) -> None:
        """POST /connections must persist and return the created connection."""
        app = _make_connections_app()

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
                response = await client.post(
                    "/connections",
                    json={
                        "name": "test-conn",
                        "host": "db-host",
                        "port": 5432,
                        "database": "testdb",
                        "schema_name": "public",
                    },
                )

        body = response.json()
        assert body["name"] == "test-conn"
        assert body["host"] == "db-host"
        assert "id" in body

    @pytest.mark.asyncio
    async def test_get_connection_returns_404_for_missing(self) -> None:
        """GET /connections/{id} must return 404 with RFC 7807 for missing."""
        app = _make_connections_app()

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
                response = await client.get(f"/connections/{uuid.uuid4()}")

        assert response.status_code == 404
        body = response.json()
        assert body.get("status") == 404

    @pytest.mark.asyncio
    async def test_delete_connection_returns_204(self) -> None:
        """DELETE /connections/{id} must return 204 No Content."""
        app = _make_connections_app()

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
                create_resp = await client.post(
                    "/connections",
                    json={
                        "name": "to-delete",
                        "host": "host",
                        "port": 5432,
                        "database": "db",
                        "schema_name": "public",
                    },
                )
                conn_id = create_resp.json()["id"]
                response = await client.delete(f"/connections/{conn_id}")

        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_nonexistent_connection_returns_404(self) -> None:
        """DELETE /connections/{id} must return 404 with RFC 7807 for missing.

        Attempting to delete a connection that does not exist must return
        HTTP 404 with a valid RFC 7807 Problem Details body.
        """
        app = _make_connections_app()
        nonexistent_id = str(uuid.uuid4())

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
                response = await client.delete(f"/connections/{nonexistent_id}")

        assert response.status_code == 404
        body = response.json()
        assert body.get("status") == 404
        assert "title" in body
        assert "detail" in body
