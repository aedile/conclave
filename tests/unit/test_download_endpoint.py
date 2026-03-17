"""Unit tests for GET /jobs/{id}/download endpoint (P23-T23.2).

Tests follow TDD RED phase — all tests must fail before implementation.

Covers:
  - 404 when job does not exist
  - 404 when job is not COMPLETE
  - 404 when output_path is None
  - 404 when artifact file is missing from disk
  - 200 streaming response with correct headers (no signing)
  - 409 when HMAC signature verification fails
  - 200 when signing enabled and signature matches
  - Content-Disposition and Content-Type headers

Task: P23-T23.2 — /jobs/{id}/download Endpoint
CONSTITUTION Priority 3: TDD — RED phase
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

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

    from synth_engine.bootstrapper.dependencies.db import get_db_session

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
                response = await client.get("/jobs/99999/download")

        assert response.status_code == 404
        body = response.json()
        assert body.get("status") == 404

    @pytest.mark.asyncio
    async def test_download_queued_job_returns_404(self) -> None:
        """GET /jobs/{id}/download returns 404 when job status is not COMPLETE."""
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
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_training_job_returns_404(self) -> None:
        """GET /jobs/{id}/download returns 404 when job status is TRAINING."""
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
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_complete_job_no_output_path_returns_404(self) -> None:
        """GET /jobs/{id}/download returns 404 when job is COMPLETE but output_path is None."""
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
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_complete_job_missing_file_returns_404(self) -> None:
        """GET /jobs/{id}/download returns 404 when output_path points to a nonexistent file."""
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
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 404


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
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

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
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

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
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

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
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

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


class TestDownloadEndpointHMACSigning:
    """Tests for HMAC signature verification on GET /jobs/{id}/download."""

    @pytest.mark.asyncio
    async def test_download_valid_signature_returns_200(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download returns 200 when HMAC signature matches."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        parquet_bytes = b"PAR1\x00real parquet bytes for signing test"
        parquet_path = tmp_path / "signed-synthetic.parquet"
        parquet_path.write_bytes(parquet_bytes)

        # Create a 32-byte key and compute a valid signature
        signing_key = b"\xab" * 32
        digest = hmac.new(signing_key, parquet_bytes, hashlib.sha256).digest()
        sig_path = tmp_path / "signed-synthetic.parquet.sig"
        sig_path.write_bytes(digest)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="signed",
                parquet_path="/tmp/signed.parquet",
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

        with p1, p2, patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_download_invalid_signature_returns_409(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download returns 409 when HMAC signature does not match."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        parquet_bytes = b"PAR1\x00real parquet bytes for tamper test"
        parquet_path = tmp_path / "tampered-synthetic.parquet"
        parquet_path.write_bytes(parquet_bytes)

        # Write a WRONG signature (all zeros)
        sig_path = tmp_path / "tampered-synthetic.parquet.sig"
        sig_path.write_bytes(b"\x00" * 32)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="tampered",
                parquet_path="/tmp/tampered.parquet",
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

        # Use a valid key — but the stored signature is wrong
        signing_key = b"\xab" * 32
        with p1, p2, patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_download_missing_sig_file_returns_409(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download returns 409 when signing key set but .sig file is missing."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        parquet_bytes = b"PAR1\x00parquet bytes no sig"
        parquet_path = tmp_path / "nosig-synthetic.parquet"
        parquet_path.write_bytes(parquet_bytes)
        # Deliberately do NOT write the .sig file

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="nosig",
                parquet_path="/tmp/nosig.parquet",
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

        signing_key = b"\xcd" * 32
        with p1, p2, patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_download_no_signing_key_skips_verification(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download returns 200 when ARTIFACT_SIGNING_KEY is absent.

        Signature verification is skipped when no signing key is configured.
        """
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        parquet_bytes = b"unsigned parquet content"
        parquet_path = tmp_path / "unsigned-synthetic.parquet"
        parquet_path.write_bytes(parquet_bytes)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="unsigned",
                parquet_path="/tmp/unsigned.parquet",
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

    @pytest.mark.asyncio
    async def test_download_409_response_uses_problem_detail_format(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download 409 response must follow RFC 7807 Problem Details format."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        parquet_bytes = b"PAR1 tamper test bytes"
        parquet_path = tmp_path / "conflict-synthetic.parquet"
        parquet_path.write_bytes(parquet_bytes)

        sig_path = tmp_path / "conflict-synthetic.parquet.sig"
        sig_path.write_bytes(b"\x00" * 32)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="conflict",
                parquet_path="/tmp/conflict.parquet",
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

        signing_key = b"\xef" * 32
        with p1, p2, patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 409
        body = response.json()
        assert body.get("status") == 409
        assert "type" in body
        assert "title" in body
        assert "detail" in body
