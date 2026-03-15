"""Unit tests for the Jobs router (bootstrapper/routers/jobs.py).

Tests follow TDD RED phase — all tests must fail before implementation.

Task: P5-T5.1 — Task Orchestration API Core
CONSTITUTION Priority 3: TDD — RED phase
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit


def _make_test_app() -> Any:
    """Build a test FastAPI app with an in-memory SQLite database."""
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router
    from synth_engine.modules.synthesizer.job_models import SynthesisJob

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    # Seed a test job
    with Session(engine) as session:
        job = SynthesisJob(
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            total_epochs=10,
        )
        session.add(job)
        session.commit()

    app = create_app()
    register_error_handlers(app)
    app.include_router(jobs_router)

    # Override the DB dependency to use the in-memory engine
    from synth_engine.bootstrapper.dependencies.db import get_db_session

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    return app, engine


class TestJobsListEndpoint:
    """Tests for GET /jobs cursor-based pagination."""

    @pytest.mark.asyncio
    async def test_list_jobs_returns_200(self) -> None:
        """GET /jobs must return HTTP 200."""
        app, engine = _make_test_app()

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/jobs")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_jobs_returns_items_list(self) -> None:
        """GET /jobs must return JSON with an 'items' list."""
        app, engine = _make_test_app()

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/jobs")

        body = response.json()
        assert "items" in body

    @pytest.mark.asyncio
    async def test_list_jobs_cursor_pagination_after(self) -> None:
        """GET /jobs?after=<cursor> must return jobs with id > cursor."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            for i in range(5):
                job = SynthesisJob(
                    table_name=f"table_{i}",
                    parquet_path=f"/tmp/t{i}.parquet",
                    total_epochs=10,
                )
                session.add(job)
            session.commit()

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/jobs?after=2&limit=10")

        body = response.json()
        items = body["items"]
        for item in items:
            assert item["id"] > 2

    @pytest.mark.asyncio
    async def test_list_jobs_respects_limit(self) -> None:
        """GET /jobs?limit=2 must return at most 2 items."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            for i in range(5):
                job = SynthesisJob(
                    table_name=f"table_{i}",
                    parquet_path=f"/tmp/t{i}.parquet",
                    total_epochs=10,
                )
                session.add(job)
            session.commit()

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/jobs?limit=2")

        body = response.json()
        assert len(body["items"]) <= 2


class TestJobGetEndpoint:
    """Tests for GET /jobs/{id}."""

    @pytest.mark.asyncio
    async def test_get_job_returns_200_for_existing_job(self) -> None:
        """GET /jobs/{id} must return HTTP 200 for an existing job."""
        app, engine = _make_test_app()

        # Get the seeded job's id
        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_job_returns_404_for_missing_job(self) -> None:
        """GET /jobs/{id} must return HTTP 404 with RFC 7807 for missing job."""
        app, engine = _make_test_app()

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/jobs/99999")

        assert response.status_code == 404
        body = response.json()
        assert body.get("status") == 404
        assert "type" in body

    @pytest.mark.asyncio
    async def test_get_job_returns_correct_fields(self) -> None:
        """GET /jobs/{id} must return job fields including status, table_name."""
        app, engine = _make_test_app()

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}")

        body = response.json()
        assert "status" in body
        assert "table_name" in body
        assert body["table_name"] == "customers"


class TestJobCreateEndpoint:
    """Tests for POST /jobs."""

    @pytest.mark.asyncio
    async def test_create_job_returns_201(self) -> None:
        """POST /jobs must return HTTP 201 Created."""
        app, engine = _make_test_app()

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/jobs",
                    json={
                        "table_name": "orders",
                        "parquet_path": "/tmp/orders.parquet",
                        "total_epochs": 5,
                    },
                )

        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_create_job_returns_created_job_body(self) -> None:
        """POST /jobs must return the newly created job body."""
        app, engine = _make_test_app()

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/jobs",
                    json={
                        "table_name": "orders",
                        "parquet_path": "/tmp/orders.parquet",
                        "total_epochs": 5,
                    },
                )

        body = response.json()
        assert body["table_name"] == "orders"
        assert body["status"] == "QUEUED"
        assert "id" in body


class TestJobStartEndpoint:
    """Tests for POST /jobs/{id}/start."""

    @pytest.mark.asyncio
    async def test_start_job_returns_202(self) -> None:
        """POST /jobs/{id}/start must return HTTP 202 Accepted."""
        app, engine = _make_test_app()

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch("synth_engine.bootstrapper.routers.jobs.run_synthesis_job"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/jobs/{job_id}/start")

        assert response.status_code == 202

    @pytest.mark.asyncio
    async def test_start_job_enqueues_huey_task(self) -> None:
        """POST /jobs/{id}/start must call run_synthesis_job with the job id."""
        app, engine = _make_test_app()

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        mock_task = MagicMock()
        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.routers.jobs.run_synthesis_job",
                mock_task,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(f"/jobs/{job_id}/start")

        mock_task.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    async def test_start_nonexistent_job_returns_404(self) -> None:
        """POST /jobs/{id}/start must return 404 for a nonexistent job."""
        app, engine = _make_test_app()

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/jobs/99999/start")

        assert response.status_code == 404


class TestJobSSEEndpoint:
    """Tests for GET /jobs/{id}/stream SSE endpoint."""

    @pytest.mark.asyncio
    async def test_stream_nonexistent_job_returns_404(self) -> None:
        """GET /jobs/{id}/stream must return 404 for a nonexistent job."""
        app, engine = _make_test_app()

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/jobs/99999/stream")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_stream_completed_job_yields_complete_event(self) -> None:
        """GET /jobs/{id}/stream for a COMPLETE job must yield a complete event."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/t.parquet",
                total_epochs=10,
                status="COMPLETE",
                current_epoch=10,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/stream")

        assert response.status_code == 200
        content = response.text
        assert "complete" in content

    @pytest.mark.asyncio
    async def test_stream_failed_job_yields_error_event(self) -> None:
        """GET /jobs/{id}/stream for a FAILED job must yield an error event."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/t.parquet",
                total_epochs=10,
                status="FAILED",
                error_msg="OOM error at /internal/path/to/code.py",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/stream")

        assert response.status_code == 200
        content = response.text
        assert "error" in content
        # error_msg must be sanitized — path must not leak
        assert "/internal/path" not in content
