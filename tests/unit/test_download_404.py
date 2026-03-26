"""Unit tests for 404 error conditions on GET /jobs/{id}/download.

Tests verify that the download endpoint returns 404 for:
  - Non-existent job IDs
  - Jobs that are not COMPLETE (QUEUED, TRAINING, SHREDDED)
  - COMPLETE jobs with no output_path
  - COMPLETE jobs whose output file is missing from disk

CONSTITUTION Priority 3: TDD RED Phase.
Task: P23-T23.2 — /jobs/{id}/download Endpoint
Task: P26-T26.6 — Split from test_download_endpoint.py for maintainability
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit


def _make_test_app() -> Any:
    """Build a test FastAPI app with an in-memory SQLite database."""
    from sqlalchemy.pool import StaticPool

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

    # Seed a QUEUED test job (no output_path)
    with Session(engine) as session:
        job = SynthesisJob(
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            total_epochs=10,
            num_rows=100,
            status="QUEUED",
        )
        session.add(job)
        session.commit()

    app = create_app()
    register_error_handlers(app)
    app.include_router(jobs_router)

    def _override_session() -> Any:
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db_session] = _override_session
    return app, engine


def _vault_license_patches() -> tuple[Any, Any]:
    """Return patches for vault sealed and license state."""
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


class TestDownloadEndpoint404Cases:
    """Tests for 404 error conditions on GET /jobs/{id}/download."""

    @pytest.mark.asyncio
    async def test_download_nonexistent_job_returns_404(self) -> None:
        """GET /jobs/{id}/download returns 404 when the job does not exist."""
        app, _ = _make_test_app()
        p1, p2 = _vault_license_patches()

        with p1, p2:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/jobs/99999/download")

        assert response.status_code == 404
        body = response.json()
        assert body["status"] == 404

    @pytest.mark.asyncio
    async def test_download_queued_job_returns_404(self) -> None:
        """GET /jobs/{id}/download returns 404 when job status is not COMPLETE."""
        from sqlalchemy.pool import StaticPool

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

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="customers",
                parquet_path="/tmp/customers.parquet",
                total_epochs=10,
                num_rows=100,
                status="QUEUED",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_db_session] = _override
        p1, p2 = _vault_license_patches()

        with p1, p2:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_training_job_returns_404(self) -> None:
        """GET /jobs/{id}/download returns 404 when job status is TRAINING."""
        from sqlalchemy.pool import StaticPool

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

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/t.parquet",
                total_epochs=10,
                num_rows=100,
                status="TRAINING",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_db_session] = _override
        p1, p2 = _vault_license_patches()

        with p1, p2:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_complete_job_no_output_path_returns_404(self) -> None:
        """GET /jobs/{id}/download returns 404 when job is COMPLETE but output_path is None."""
        from sqlalchemy.pool import StaticPool

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

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="customers",
                parquet_path="/tmp/customers.parquet",
                total_epochs=10,
                num_rows=100,
                status="COMPLETE",
                output_path=None,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_db_session] = _override
        p1, p2 = _vault_license_patches()

        with p1, p2:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_complete_job_missing_file_returns_404(self) -> None:
        """GET /jobs/{id}/download returns 404 when output_path points to a nonexistent file."""
        from sqlalchemy.pool import StaticPool

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

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="customers",
                parquet_path="/tmp/customers.parquet",
                total_epochs=10,
                num_rows=100,
                status="COMPLETE",
                output_path="/nonexistent/path/file.parquet",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_db_session] = _override
        p1, p2 = _vault_license_patches()

        with p1, p2:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_shredded_job_returns_404(self) -> None:
        """GET /jobs/{id}/download returns 404 when job status is SHREDDED."""
        from sqlalchemy.pool import StaticPool

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

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="shredded_tbl",
                parquet_path="/tmp/shredded.parquet",
                total_epochs=5,
                num_rows=50,
                status="SHREDDED",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_db_session] = _override
        p1, p2 = _vault_license_patches()

        with p1, p2:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        assert response.status_code == 404
