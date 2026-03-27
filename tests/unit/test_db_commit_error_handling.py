"""Negative/attack tests for database commit error handling (T62.1).

Attack tests verifying that:
1. SQLAlchemy IntegrityError during session.commit() returns RFC 7807 409 — not 500,
   not a raw SQLAlchemy error.
2. SQLAlchemyError (non-integrity) during session.commit() returns RFC 7807 500.
3. session.rollback() is always called in the except block — poisoned connection
   guard.
4. Error response detail NEVER contains SQL text, table names, or constraint names.
5. Shred endpoint does NOT return 200 SHREDDED until after a confirmed commit.
6. All five routers (connections, jobs, settings, webhooks, admin) are covered.

CONSTITUTION Priority 0: Security — no SQL leakage, no poisoned connection pool
CONSTITUTION Priority 3: TDD — Attack tests committed before implementation (Rule 22)
Task: T62.1 — Wrap Database Commits in Exception Handlers
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connections_app() -> tuple[Any, Any]:
    """Build test app with in-memory DB wired to connections router.

    Returns:
        (app, engine) tuple.
    """
    from sqlalchemy.pool import StaticPool

    from sqlmodel import Session, SQLModel, create_engine

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

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    return app, engine


def _make_jobs_app() -> tuple[Any, Any]:
    """Build test app with in-memory DB wired to jobs router.

    Returns:
        (app, engine) tuple.
    """
    from sqlalchemy.pool import StaticPool

    from sqlmodel import Session, SQLModel, create_engine

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    # Seed a COMPLETE job for shred tests
    with Session(engine) as session:
        job = SynthesisJob(
            table_name="customers",
            parquet_path="/tmp/c.parquet",
            total_epochs=5,
            num_rows=50,
            status="COMPLETE",
            owner_id="operator-1",
        )
        session.add(job)
        session.commit()

    app = create_app()
    register_error_handlers(app)
    app.include_router(jobs_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    return app, engine


def _make_settings_app() -> tuple[Any, Any]:
    """Build test app with in-memory DB wired to settings router.

    Returns:
        (app, engine) tuple.
    """
    from sqlalchemy.pool import StaticPool

    from sqlmodel import Session, SQLModel, create_engine

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

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    return app, engine


def _make_webhooks_app() -> tuple[Any, Any]:
    """Build test app with in-memory DB wired to webhooks router.

    Returns:
        (app, engine) tuple.
    """
    from sqlalchemy.pool import StaticPool

    from sqlmodel import Session, SQLModel, create_engine

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.webhooks import router as webhooks_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()
    register_error_handlers(app)
    app.include_router(webhooks_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    return app, engine


def _make_admin_app() -> tuple[Any, Any]:
    """Build test app with in-memory DB wired to admin router.

    Returns:
        (app, engine) tuple.
    """
    from sqlalchemy.pool import StaticPool

    from sqlmodel import Session, SQLModel, create_engine

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.admin import router as admin_router
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        job = SynthesisJob(
            table_name="test_table",
            parquet_path="/tmp/t.parquet",
            total_epochs=5,
            num_rows=50,
            owner_id="operator-1",
        )
        session.add(job)
        session.commit()

    app = create_app()
    register_error_handlers(app)
    app.include_router(admin_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    return app, engine


def _vault_license_patches() -> tuple[Any, Any]:
    """Return the two standard test patches for vault and licensing.

    Returns:
        Tuple of (vault_patch, license_patch) context managers.
    """
    return (
        patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ),
        patch(
            "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
            return_value=True,
        ),
    )


# ---------------------------------------------------------------------------
# T62.1 — connections.py: create_connection commit error handling
# ---------------------------------------------------------------------------


class TestConnectionsCommitErrors:
    """Verify RFC 7807 error responses when session.commit() fails in connections router."""

    @pytest.mark.asyncio
    async def test_create_connection_integrity_error_returns_409_rfc7807(self) -> None:
        """IntegrityError during create_connection must return 409 with RFC 7807 body."""
        from sqlalchemy.exc import IntegrityError

        import httpx
        from httpx import ASGITransport, AsyncClient

        app, _ = _make_connections_app()

        # Simulate an IntegrityError on commit
        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(
            side_effect=IntegrityError(
                "UNIQUE constraint failed: connection.name",
                params={},
                orig=Exception("constraint"),
            )
        )
        mock_session.rollback = MagicMock()
        mock_session.refresh = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with vault_p, license_p:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/connections",
                    json={
                        "name": "test-conn",
                        "host": "localhost",
                        "port": 5432,
                        "database": "mydb",
                        "schema_name": "public",
                    },
                )

        assert response.status_code == 409
        body = response.json()
        assert body["status"] == 409
        assert body["title"] == "Conflict"
        assert "type" in body

    @pytest.mark.asyncio
    async def test_create_connection_sqlalchemy_error_returns_500_rfc7807(self) -> None:
        """Generic SQLAlchemyError during create_connection must return 500 RFC 7807."""
        from sqlalchemy.exc import SQLAlchemyError

        from httpx import ASGITransport, AsyncClient

        app, _ = _make_connections_app()

        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(
            side_effect=SQLAlchemyError("connection lost")
        )
        mock_session.rollback = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with vault_p, license_p:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/connections",
                    json={
                        "name": "test-conn",
                        "host": "localhost",
                        "port": 5432,
                        "database": "mydb",
                        "schema_name": "public",
                    },
                )

        assert response.status_code == 500
        body = response.json()
        assert body["status"] == 500
        assert body["title"] == "Internal Server Error"

    @pytest.mark.asyncio
    async def test_create_connection_commit_error_triggers_rollback(self) -> None:
        """session.rollback() MUST be called when commit raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        from httpx import ASGITransport, AsyncClient

        app, _ = _make_connections_app()

        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(
            side_effect=IntegrityError(
                "UNIQUE constraint failed",
                params={},
                orig=Exception("x"),
            )
        )
        mock_session.rollback = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with vault_p, license_p:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(
                    "/api/v1/connections",
                    json={
                        "name": "test-conn",
                        "host": "localhost",
                        "port": 5432,
                        "database": "mydb",
                        "schema_name": "public",
                    },
                )

        # Rollback MUST have been called to avoid poisoning the connection pool
        mock_session.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_integrity_error_detail_does_not_leak_sql(self) -> None:
        """409 response detail must NOT contain SQL text, table names, or constraint names."""
        from sqlalchemy.exc import IntegrityError

        from httpx import ASGITransport, AsyncClient

        app, _ = _make_connections_app()

        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(
            side_effect=IntegrityError(
                "UNIQUE constraint failed: connection.name — very sensitive SQL detail",
                params={},
                orig=Exception("constraint"),
            )
        )
        mock_session.rollback = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with vault_p, license_p:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/connections",
                    json={
                        "name": "test-conn",
                        "host": "localhost",
                        "port": 5432,
                        "database": "mydb",
                        "schema_name": "public",
                    },
                )

        body = response.json()
        detail = body.get("detail", "")
        # Must not contain SQL internals
        assert "UNIQUE constraint failed" not in detail
        assert "connection.name" not in detail
        assert "sensitive SQL detail" not in detail

    @pytest.mark.asyncio
    async def test_delete_connection_sqlalchemy_error_returns_500_and_rollback(
        self,
    ) -> None:
        """SQLAlchemyError during delete_connection must return 500 and rollback."""
        from sqlalchemy.exc import SQLAlchemyError

        from sqlmodel import Session, SQLModel, create_engine
        from sqlalchemy.pool import StaticPool

        from httpx import ASGITransport, AsyncClient

        from synth_engine.bootstrapper.schemas.connections import Connection

        # Create an engine with a real connection so we can seed a record
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        conn_id = "test-conn-id"
        with Session(engine) as session:
            conn = Connection(
                id=conn_id,
                name="my-conn",
                host="localhost",
                port=5432,
                database="mydb",
                schema_name="public",
                owner_id="operator-1",
            )
            session.add(conn)
            session.commit()

        app, _ = _make_connections_app()

        # Override with a mock session that fails on commit but returns the real conn on get
        mock_session = MagicMock()
        real_conn = Connection(
            id=conn_id,
            name="my-conn",
            host="localhost",
            port=5432,
            database="mydb",
            schema_name="public",
            owner_id="operator-1",
        )
        mock_session.get = MagicMock(return_value=real_conn)
        mock_session.delete = MagicMock()
        mock_session.commit = MagicMock(side_effect=SQLAlchemyError("disk full"))
        mock_session.rollback = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with vault_p, license_p:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.delete(f"/api/v1/connections/{conn_id}")

        assert response.status_code == 500
        mock_session.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# T62.1 — jobs.py: create_job commit error handling
# ---------------------------------------------------------------------------


class TestJobsCommitErrors:
    """Verify RFC 7807 error responses when session.commit() fails in jobs router."""

    @pytest.mark.asyncio
    async def test_create_job_integrity_error_returns_409_rfc7807(self) -> None:
        """IntegrityError during create_job must return 409 with RFC 7807 body."""
        from sqlalchemy.exc import IntegrityError

        from httpx import ASGITransport, AsyncClient

        app, _ = _make_jobs_app()

        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(
            side_effect=IntegrityError(
                "UNIQUE constraint failed: synthesisjob.table_name",
                params={},
                orig=Exception("constraint"),
            )
        )
        mock_session.rollback = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with vault_p, license_p:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/jobs",
                    json={
                        "table_name": "customers",
                        "parquet_path": "/tmp/c.parquet",
                        "total_epochs": 5,
                        "num_rows": 100,
                    },
                )

        assert response.status_code == 409
        body = response.json()
        assert body["status"] == 409
        assert body["title"] == "Conflict"

    @pytest.mark.asyncio
    async def test_create_job_sqlalchemy_error_returns_500_and_rollback(self) -> None:
        """SQLAlchemyError during create_job must return 500 and call rollback."""
        from sqlalchemy.exc import SQLAlchemyError

        from httpx import ASGITransport, AsyncClient

        app, _ = _make_jobs_app()

        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(side_effect=SQLAlchemyError("db error"))
        mock_session.rollback = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with vault_p, license_p:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/jobs",
                    json={
                        "table_name": "customers",
                        "parquet_path": "/tmp/c.parquet",
                        "total_epochs": 5,
                        "num_rows": 100,
                    },
                )

        assert response.status_code == 500
        mock_session.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# T62.1 — jobs.py: shred commit failure — CRITICAL bug fix
# ---------------------------------------------------------------------------


class TestShredCommitFailure:
    """Verify shred endpoint does NOT return 200 SHREDDED if commit fails."""

    @pytest.mark.asyncio
    async def test_shred_commit_failure_does_not_return_200(self) -> None:
        """If session.commit() fails after shred, endpoint must NOT return 200 SHREDDED.

        CRITICAL: The old code returned 200 SHREDDED *before* the commit.
        If the commit fails, the operator gets a confirmed shred that was never
        recorded. This test verifies the fix: 200 is only returned after commit.
        """
        from sqlalchemy.exc import SQLAlchemyError

        from httpx import ASGITransport, AsyncClient

        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        app, _ = _make_jobs_app()

        job = SynthesisJob(
            id=999,
            table_name="customers",
            parquet_path="/tmp/c.parquet",
            total_epochs=5,
            num_rows=50,
            status="COMPLETE",
            owner_id="operator-1",
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=job)
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(side_effect=SQLAlchemyError("commit failed"))
        mock_session.rollback = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with (
            vault_p,
            license_p,
            patch(
                "synth_engine.bootstrapper.routers.jobs.shred_artifacts",
            ),
            patch(
                "synth_engine.bootstrapper.routers.jobs.get_audit_logger",
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/v1/jobs/999/shred")

        # If commit fails, must NOT return 200 SHREDDED
        assert response.status_code != 200
        body = response.json()
        # Must not claim SHREDDED when commit failed
        assert body.get("status") != "SHREDDED"

    @pytest.mark.asyncio
    async def test_shred_commit_failure_calls_rollback(self) -> None:
        """session.rollback() must be called when shred commit fails."""
        from sqlalchemy.exc import SQLAlchemyError

        from httpx import ASGITransport, AsyncClient

        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        app, _ = _make_jobs_app()

        job = SynthesisJob(
            id=888,
            table_name="customers",
            parquet_path="/tmp/c.parquet",
            total_epochs=5,
            num_rows=50,
            status="COMPLETE",
            owner_id="operator-1",
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=job)
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(side_effect=SQLAlchemyError("commit failed"))
        mock_session.rollback = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with (
            vault_p,
            license_p,
            patch("synth_engine.bootstrapper.routers.jobs.shred_artifacts"),
            patch("synth_engine.bootstrapper.routers.jobs.get_audit_logger"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post("/api/v1/jobs/888/shred")

        mock_session.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# T62.1 — settings.py: upsert_setting commit error handling
# ---------------------------------------------------------------------------


class TestSettingsCommitErrors:
    """Verify RFC 7807 error responses when session.commit() fails in settings router."""

    @pytest.mark.asyncio
    async def test_upsert_setting_sqlalchemy_error_returns_500_and_rollback(
        self,
    ) -> None:
        """SQLAlchemyError during upsert_setting must return 500 and call rollback."""
        from sqlalchemy.exc import SQLAlchemyError

        from httpx import ASGITransport, AsyncClient

        app, _ = _make_settings_app()

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=None)  # New key — create branch
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(side_effect=SQLAlchemyError("db error"))
        mock_session.rollback = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.dependencies.auth import require_scope

        def _bad_session() -> Any:
            yield mock_session

        def _noop_scope() -> str:
            return "test-operator"

        app.dependency_overrides[get_db_session] = _bad_session
        app.dependency_overrides[require_scope("settings:write")] = _noop_scope

        vault_p, license_p = _vault_license_patches()
        with vault_p, license_p:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.put(
                    "/api/v1/settings/my-key",
                    json={"value": "my-value"},
                )

        assert response.status_code == 500
        mock_session.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_setting_sqlalchemy_error_returns_500_and_rollback(
        self,
    ) -> None:
        """SQLAlchemyError during delete_setting must return 500 and call rollback."""
        from sqlalchemy.exc import SQLAlchemyError

        from httpx import ASGITransport, AsyncClient

        from synth_engine.bootstrapper.schemas.settings import Setting

        app, _ = _make_settings_app()

        existing_setting = Setting(key="my-key", value="val")
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=existing_setting)
        mock_session.delete = MagicMock()
        mock_session.commit = MagicMock(side_effect=SQLAlchemyError("disk full"))
        mock_session.rollback = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.dependencies.auth import require_scope

        def _bad_session() -> Any:
            yield mock_session

        def _noop_scope() -> str:
            return "test-operator"

        app.dependency_overrides[get_db_session] = _bad_session
        app.dependency_overrides[require_scope("settings:write")] = _noop_scope

        vault_p, license_p = _vault_license_patches()
        with vault_p, license_p:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.delete("/api/v1/settings/my-key")

        assert response.status_code == 500
        mock_session.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# T62.1 — webhooks.py: register_webhook commit error handling
# ---------------------------------------------------------------------------


class TestWebhooksCommitErrors:
    """Verify RFC 7807 error responses when session.commit() fails in webhooks router."""

    @pytest.mark.asyncio
    async def test_register_webhook_sqlalchemy_error_returns_500_and_rollback(
        self,
    ) -> None:
        """SQLAlchemyError during register_webhook commit must return 500 and rollback."""
        from sqlalchemy.exc import SQLAlchemyError

        from httpx import ASGITransport, AsyncClient

        app, _ = _make_webhooks_app()

        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.exec = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        mock_session.commit = MagicMock(side_effect=SQLAlchemyError("db write failed"))
        mock_session.rollback = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with (
            vault_p,
            license_p,
            patch(
                "synth_engine.bootstrapper.routers.webhooks.validate_callback_url",
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/webhooks/",
                    json={
                        "callback_url": "https://example.com/hook",
                        "signing_key": "a" * 32,
                        "events": ["job.completed"],
                    },
                )

        assert response.status_code == 500
        mock_session.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_deactivate_webhook_sqlalchemy_error_returns_500_and_rollback(
        self,
    ) -> None:
        """SQLAlchemyError during deactivate_webhook commit must return 500 and rollback."""
        from sqlalchemy.exc import SQLAlchemyError

        from httpx import ASGITransport, AsyncClient

        from synth_engine.bootstrapper.schemas.webhooks import WebhookRegistration

        app, _ = _make_webhooks_app()

        webhook_id = "test-webhook-uuid"
        reg = WebhookRegistration(
            id=webhook_id,
            owner_id="operator-1",
            callback_url="https://example.com/hook",
            signing_key="a" * 32,
            events='["job.completed"]',
            active=True,
        )

        mock_session = MagicMock()
        mock_session.exec = MagicMock(return_value=MagicMock(first=MagicMock(return_value=reg)))
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(side_effect=SQLAlchemyError("disk full"))
        mock_session.rollback = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with vault_p, license_p:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.delete(f"/api/v1/webhooks/{webhook_id}")

        assert response.status_code == 500
        mock_session.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# T62.1 — admin.py: set_legal_hold commit error handling
# ---------------------------------------------------------------------------


class TestAdminCommitErrors:
    """Verify RFC 7807 error responses when session.commit() fails in admin router."""

    @pytest.mark.asyncio
    async def test_legal_hold_sqlalchemy_error_returns_500_and_rollback(self) -> None:
        """SQLAlchemyError during legal_hold commit must return 500 and call rollback."""
        from sqlalchemy.exc import SQLAlchemyError

        from httpx import ASGITransport, AsyncClient

        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        app, _ = _make_admin_app()

        job = SynthesisJob(
            id=1,
            table_name="test_table",
            parquet_path="/tmp/t.parquet",
            total_epochs=5,
            num_rows=50,
            owner_id="operator-1",
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=job)
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(side_effect=SQLAlchemyError("db error"))
        mock_session.rollback = MagicMock()
        mock_session.refresh = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with (
            vault_p,
            license_p,
            patch(
                "synth_engine.bootstrapper.routers.admin.get_audit_logger",
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    "/api/v1/admin/jobs/1/legal-hold",
                    json={"enable": True},
                )

        assert response.status_code == 500
        mock_session.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_legal_hold_error_response_does_not_leak_sql(self) -> None:
        """500 response from legal_hold commit failure must not expose SQL details."""
        from sqlalchemy.exc import SQLAlchemyError

        from httpx import ASGITransport, AsyncClient

        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        app, _ = _make_admin_app()

        job = SynthesisJob(
            id=2,
            table_name="confidential_table_name",
            parquet_path="/tmp/t.parquet",
            total_epochs=5,
            num_rows=50,
            owner_id="operator-1",
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=job)
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(
            side_effect=SQLAlchemyError(
                "ERROR: table confidential_table_name constraint pkey"
            )
        )
        mock_session.rollback = MagicMock()
        mock_session.refresh = MagicMock()

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        def _bad_session() -> Any:
            yield mock_session

        app.dependency_overrides[get_db_session] = _bad_session

        vault_p, license_p = _vault_license_patches()
        with (
            vault_p,
            license_p,
            patch("synth_engine.bootstrapper.routers.admin.get_audit_logger"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    "/api/v1/admin/jobs/2/legal-hold",
                    json={"enable": True},
                )

        body = response.json()
        detail = body.get("detail", "")
        assert "confidential_table_name" not in detail
        assert "pkey" not in detail
        assert "ERROR:" not in detail
