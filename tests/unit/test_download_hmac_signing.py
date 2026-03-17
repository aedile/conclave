"""Unit tests for HMAC signature enforcement on GET /jobs/{id}/download.

Tests verify that the download endpoint enforces artifact integrity when
ARTIFACT_SIGNING_KEY is set to a valid hex key:
  - 200 returned when signature matches
  - 409 returned when signature does not match (tampered file)
  - 409 returned when .sig sidecar file is missing
  - 409 response follows RFC 7807 Problem Details format

CONSTITUTION Priority 3: TDD RED Phase.
Task: P23-T23.2 — /jobs/{id}/download Endpoint
Task: P26-T26.6 — Split from test_download_endpoint.py for maintainability
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


class TestDownloadEndpointHMACSigningActive:
    """Tests for HMAC signature enforcement when ARTIFACT_SIGNING_KEY is set."""

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
        body = response.json()
        assert "tampered" in body["detail"].lower() or "signature" in body["detail"].lower()

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
        assert body["status"] == 409
        assert "type" in body
        assert "title" in body
        assert "detail" in body
