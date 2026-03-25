"""Integration tests for GET /jobs/{id}/download endpoint (P23-T23.2).

These are INTEGRATION tests per the two-gate test policy in CLAUDE.md.
They use real in-process FastAPI + SQLite (in-memory) to exercise the
full download pipeline: job lookup → file read → streaming response.

No PostgreSQL is required for these tests — the download endpoint does
not exercise PostgreSQL-specific features (the file is read from disk,
not from the DB).

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
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from synth_engine.shared.settings import get_settings

pytestmark = pytest.mark.integration


def _make_integration_app(engine: Any) -> Any:
    """Build a fully-wired FastAPI app for integration tests.

    Args:
        engine: SQLAlchemy engine with test database tables created.

    Returns:
        A FastAPI app with all routers and error handlers wired.
    """
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    app = create_app()
    register_error_handlers(app)
    app.include_router(jobs_router)

    def _override() -> Any:
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db_session] = _override
    return app


@pytest.fixture
def sqlite_engine() -> Any:
    """Provide an in-memory SQLite engine with synthesis tables.

    Returns:
        A SQLAlchemy engine backed by an in-memory SQLite database.
    """
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


class TestDownloadAfterSynthesis:
    """Integration tests for the complete download-after-synthesis flow."""

    @pytest.mark.asyncio
    async def test_download_after_complete_job_returns_file_bytes(
        self, tmp_path: Path, sqlite_engine: Any
    ) -> None:
        """Integration: download after synthesis returns the correct Parquet file bytes."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        expected_bytes = b"PAR1\x00\xff\xfe integration test parquet payload"
        parquet_file = tmp_path / "orders-synthetic.parquet"
        parquet_file.write_bytes(expected_bytes)

        with Session(sqlite_engine) as session:
            job = SynthesisJob(
                table_name="orders",
                parquet_path="/tmp/orders.parquet",
                total_epochs=5,
                num_rows=50,
                status="COMPLETE",
                output_path=str(parquet_file),
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = _make_integration_app(sqlite_engine)

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
            os.environ.pop("ARTIFACT_SIGNING_KEY", None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 200
        assert response.content == expected_bytes

    @pytest.mark.asyncio
    async def test_download_content_disposition_uses_table_name(
        self, tmp_path: Path, sqlite_engine: Any
    ) -> None:
        """Integration: Content-Disposition header uses '<table_name>-synthetic.parquet'."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_file = tmp_path / "analytics-synthetic.parquet"
        parquet_file.write_bytes(b"parquet payload")

        with Session(sqlite_engine) as session:
            job = SynthesisJob(
                table_name="analytics",
                parquet_path="/tmp/analytics.parquet",
                total_epochs=5,
                num_rows=50,
                status="COMPLETE",
                output_path=str(parquet_file),
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = _make_integration_app(sqlite_engine)

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
            os.environ.pop("ARTIFACT_SIGNING_KEY", None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "analytics-synthetic.parquet" in cd

    @pytest.mark.asyncio
    async def test_download_with_valid_hmac_signature_succeeds(
        self, tmp_path: Path, sqlite_engine: Any
    ) -> None:
        """Integration: download with a matching HMAC signature returns 200."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_bytes = b"PAR1\x00 signed integration test payload"
        parquet_file = tmp_path / "signed-synthetic.parquet"
        parquet_file.write_bytes(parquet_bytes)

        signing_key = b"\x12\x34\x56\x78" * 8  # 32 bytes
        digest = hmac.new(signing_key, parquet_bytes, hashlib.sha256).digest()
        sig_file = tmp_path / "signed-synthetic.parquet.sig"
        sig_file.write_bytes(digest)

        with Session(sqlite_engine) as session:
            job = SynthesisJob(
                table_name="signed",
                parquet_path="/tmp/signed.parquet",
                total_epochs=5,
                num_rows=50,
                status="COMPLETE",
                output_path=str(parquet_file),
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = _make_integration_app(sqlite_engine)

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
            patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 200
        assert response.content == parquet_bytes

    @pytest.mark.asyncio
    async def test_download_with_tampered_file_returns_409(
        self, tmp_path: Path, sqlite_engine: Any
    ) -> None:
        """Integration: download with a mismatched HMAC signature returns 409."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_bytes = b"PAR1\x00 tampered integration test payload"
        parquet_file = tmp_path / "tampered-synthetic.parquet"
        parquet_file.write_bytes(parquet_bytes)

        # Write a bad signature (all zeros — will not match)
        sig_file = tmp_path / "tampered-synthetic.parquet.sig"
        sig_file.write_bytes(b"\x00" * 32)

        with Session(sqlite_engine) as session:
            job = SynthesisJob(
                table_name="tampered",
                parquet_path="/tmp/tampered.parquet",
                total_epochs=5,
                num_rows=50,
                status="COMPLETE",
                output_path=str(parquet_file),
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = _make_integration_app(sqlite_engine)

        signing_key = b"\xde\xad\xbe\xef" * 8  # 32 bytes

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
            patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}),
        ):
            get_settings.cache_clear()  # force re-read of ARTIFACT_SIGNING_KEY from patched env
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(f"/jobs/{job_id}/download")
            finally:
                get_settings.cache_clear()  # prevent cache poisoning for subsequent tests

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_download_failed_job_returns_404(
        self, tmp_path: Path, sqlite_engine: Any
    ) -> None:
        """Integration: download for a FAILED job returns 404."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        with Session(sqlite_engine) as session:
            job = SynthesisJob(
                table_name="failed",
                parquet_path="/tmp/failed.parquet",
                total_epochs=5,
                num_rows=50,
                status="FAILED",
                error_msg="Training failed",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = _make_integration_app(sqlite_engine)

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
            os.environ.pop("ARTIFACT_SIGNING_KEY", None)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/jobs/{job_id}/download")

        assert response.status_code == 404
