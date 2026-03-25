"""Unit tests for successful 200 streaming responses on GET /jobs/{id}/download.

Tests verify:
  - 200 returned for a COMPLETE job with a valid output file
  - Content-Type: application/octet-stream
  - Content-Disposition contains the job table_name
  - Raw Parquet file bytes are streamed correctly
  - Multi-chunk file (>64 KiB) streams correctly

CONSTITUTION Priority 3: TDD RED Phase.
Task: P23-T23.2 — /jobs/{id}/download Endpoint
Task: P26-T26.6 — Split from test_download_endpoint.py for maintainability
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit


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


class TestDownloadEndpointSuccess:
    """Tests for successful 200 streaming responses."""

    @pytest.mark.asyncio
    async def test_download_complete_job_returns_200(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download returns 200 for a COMPLETE job with a valid output file."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_path = tmp_path / "customers-synthetic.parquet"
        parquet_path.write_bytes(b"fake parquet bytes")

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
                output_path=str(parquet_path),
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

        with p1, p2, patch.dict(os.environ, {}, clear=False):
            # Ensure no signing key is set
            os.environ.pop("ARTIFACT_SIGNING_KEY", None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_download_returns_octet_stream_content_type(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download returns Content-Type: application/octet-stream."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_path = tmp_path / "customers-synthetic.parquet"
        parquet_path.write_bytes(b"fake parquet bytes")

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
                output_path=str(parquet_path),
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
            os.environ.pop("ARTIFACT_SIGNING_KEY", None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.headers["content-type"] == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_download_returns_correct_content_disposition(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download returns Content-Disposition with job table_name."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_path = tmp_path / "customers-synthetic.parquet"
        parquet_path.write_bytes(b"fake parquet bytes")

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="my_table",
                parquet_path="/tmp/my_table.parquet",
                total_epochs=10,
                num_rows=100,
                status="COMPLETE",
                output_path=str(parquet_path),
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
            os.environ.pop("ARTIFACT_SIGNING_KEY", None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 200
        content_disposition = response.headers.get("content-disposition", "")
        assert "attachment" in content_disposition
        assert "my_table-synthetic.parquet" in content_disposition

    @pytest.mark.asyncio
    async def test_download_streams_file_bytes(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download returns the raw Parquet file bytes."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        expected_bytes = b"PAR1\x00\x01\x02\x03fake parquet content here"
        parquet_path = tmp_path / "t-synthetic.parquet"
        parquet_path.write_bytes(expected_bytes)

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
                status="COMPLETE",
                output_path=str(parquet_path),
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
            os.environ.pop("ARTIFACT_SIGNING_KEY", None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 200
        assert response.content == expected_bytes

    @pytest.mark.asyncio
    async def test_download_multichunk_file_streams_correctly(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download streams a file larger than 64 KiB in multiple chunks.

        Verifies that the streamed content equals the original bytes when the
        file exceeds _DOWNLOAD_CHUNK_SIZE (65 536 bytes).
        """
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        # Write a file that is 3 x 64 KiB + 1 byte -- forces multiple read iterations
        chunk_size = 65536
        expected_bytes = b"X" * (chunk_size * 3 + 1)
        parquet_path = tmp_path / "large-synthetic.parquet"
        parquet_path.write_bytes(expected_bytes)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="large_tbl",
                parquet_path="/tmp/large.parquet",
                total_epochs=5,
                num_rows=50,
                status="COMPLETE",
                output_path=str(parquet_path),
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
            os.environ.pop("ARTIFACT_SIGNING_KEY", None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 200
        assert len(response.content) == len(expected_bytes)
        assert response.content == expected_bytes
