"""Unit tests for HMAC verification bypass conditions on GET /jobs/{id}/download.

Tests verify that signature verification is skipped (not blocked) when:
  - ARTIFACT_SIGNING_KEY is absent from environment
  - ARTIFACT_SIGNING_KEY is invalid hex (non-decodable)
  - ARTIFACT_SIGNING_KEY is whitespace-only (empty after strip)
  - OSError is raised inside _verify_artifact_signature (returns None, not False)

In all bypass cases the endpoint should return 200 and stream the file normally.

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


class TestDownloadEndpointHMACBypass:
    """Tests for conditions under which HMAC verification is skipped gracefully."""

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
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

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
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_download_invalid_hex_signing_key_skips_verification(
        self, tmp_path: Path
    ) -> None:
        """GET /jobs/{id}/download returns 200 when ARTIFACT_SIGNING_KEY is invalid hex.

        A non-hex signing key causes verification to be skipped (logged as WARNING),
        not an error that blocks the download.
        """
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_bytes = b"parquet bytes for invalid hex key test"
        parquet_path = tmp_path / "invalid_hex-synthetic.parquet"
        parquet_path.write_bytes(parquet_bytes)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="invalid_hex",
                parquet_path="/tmp/invalid_hex.parquet",
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

        # "not-valid-hex" is not a valid hex string
        with p1, p2, patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": "not-valid-hex"}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_download_whitespace_signing_key_skips_verification(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download returns 200 when ARTIFACT_SIGNING_KEY is whitespace-only.

        A whitespace-only key (empty after strip) skips verification rather than
        treating the empty key as valid.
        """
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_bytes = b"parquet bytes for whitespace key test"
        parquet_path = tmp_path / "ws_key-synthetic.parquet"
        parquet_path.write_bytes(parquet_bytes)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="ws_key",
                parquet_path="/tmp/ws_key.parquet",
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

        # Whitespace-only key: strip() produces empty string → skip verification
        with p1, p2, patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": "   "}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_download_oserror_during_verification_skips_to_streaming(
        self, tmp_path: Path
    ) -> None:
        """OSError during _verify_artifact_signature skips verification (returns None).

        When reading the artifact or sidecar raises OSError, the function returns
        None (not False).  The endpoint then falls through to the streaming response.
        The file-read in _iter_file_chunks will raise and result in a 500 —
        but the endpoint does NOT return a 409 (Conflict / tampered semantics).

        Here we patch _verify_artifact_signature directly to return None (OSError path)
        so that we isolate the endpoint's handling from the internal file read.
        """
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_bytes = b"parquet bytes for oserror test"
        parquet_path = tmp_path / "oserror-synthetic.parquet"
        parquet_path.write_bytes(parquet_bytes)

        sig_path = tmp_path / "oserror-synthetic.parquet.sig"
        sig_path.write_bytes(b"\x00" * 32)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="oserror_tbl",
                parquet_path="/tmp/oserror.parquet",
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

        signing_key = b"\xab" * 32

        # Patch _verify_artifact_signature directly to return None (OSError path)
        # so that we isolate the endpoint's handling from the internal file read.
        with (
            p1,
            p2,
            patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}),
            patch(
                "synth_engine.bootstrapper.routers.jobs_streaming._verify_artifact_signature",
                return_value=None,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        # None return → verification skipped → streaming proceeds → 200
        assert response.status_code == 200
