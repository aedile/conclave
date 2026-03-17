"""Unit tests for POST /jobs/{id}/shred — Cryptographic Erasure Endpoint.

Tests follow TDD RED phase — all tests must fail before implementation.

Task: P23-T23.4 — Cryptographic Erasure Endpoint
CONSTITUTION Priority 3: TDD — RED phase
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test app factory helpers
# ---------------------------------------------------------------------------


def _make_test_app_with_job(
    status: str = "COMPLETE",
    output_path: str | None = None,
    artifact_path: str | None = None,
) -> tuple[Any, Any]:
    """Build a test FastAPI app with a seeded SynthesisJob.

    Args:
        status: Initial job status to seed.
        output_path: Value for job.output_path (optional).
        artifact_path: Value for job.artifact_path (optional).

    Returns:
        A (app, engine) tuple with one seeded job.
    """
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
            status=status,
            output_path=output_path,
            artifact_path=artifact_path,
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


def _vault_and_license_patches() -> tuple[Any, Any]:
    """Return the standard vault/license patches used in every test."""
    vault_patch = patch(
        "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
        return_value=False,
    )
    license_patch = patch(
        "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
        return_value=True,
    )
    return vault_patch, license_patch


# ---------------------------------------------------------------------------
# TestShredEndpointHappyPath
# ---------------------------------------------------------------------------


class TestShredEndpointHappyPath:
    """Tests for POST /jobs/{id}/shred — successful erasure."""

    @pytest.mark.asyncio
    async def test_shred_complete_job_returns_200(self) -> None:
        """POST /jobs/{id}/shred on a COMPLETE job must return HTTP 200."""
        app, engine = _make_test_app_with_job(status="COMPLETE")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        vault_patch, license_patch = _vault_and_license_patches()
        with (
            vault_patch,
            license_patch,
            patch("synth_engine.bootstrapper.routers.jobs.shred_artifacts"),
            patch("synth_engine.bootstrapper.routers.jobs.get_audit_logger"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/jobs/{job_id}/shred")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_shred_sets_status_to_shredded(self) -> None:
        """POST /jobs/{id}/shred must transition job status to SHREDDED."""
        app, engine = _make_test_app_with_job(status="COMPLETE")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        vault_patch, license_patch = _vault_and_license_patches()
        with (
            vault_patch,
            license_patch,
            patch("synth_engine.bootstrapper.routers.jobs.shred_artifacts"),
            patch("synth_engine.bootstrapper.routers.jobs.get_audit_logger"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(f"/jobs/{job_id}/shred")

        with Session(engine) as session:
            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            updated = session.get(SynthesisJob, job_id)
            assert updated is not None
            assert updated.status == "SHREDDED"

    @pytest.mark.asyncio
    async def test_shred_response_body_contains_confirmation(self) -> None:
        """POST /jobs/{id}/shred must return body with shredded status."""
        app, engine = _make_test_app_with_job(status="COMPLETE")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        vault_patch, license_patch = _vault_and_license_patches()
        with (
            vault_patch,
            license_patch,
            patch("synth_engine.bootstrapper.routers.jobs.shred_artifacts"),
            patch("synth_engine.bootstrapper.routers.jobs.get_audit_logger"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/jobs/{job_id}/shred")

        body = response.json()
        assert body.get("status") == "SHREDDED"
        assert body.get("job_id") == job_id

    @pytest.mark.asyncio
    async def test_shred_calls_shred_artifacts_domain_function(self) -> None:
        """POST /jobs/{id}/shred must delegate file deletion to shred_artifacts().

        Verifies the job passed to shred_artifacts has the correct primary key (job_id).
        """
        app, engine = _make_test_app_with_job(status="COMPLETE")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        captured_ids: list[int | None] = []

        def _capture_and_shred(job: Any) -> None:
            # Capture the job id eagerly while the SQLAlchemy session is live
            # (accessing .id after session close triggers DetachedInstanceError).
            captured_ids.append(job.__dict__.get("id"))

        vault_patch, license_patch = _vault_and_license_patches()
        with (
            vault_patch,
            license_patch,
            patch(
                "synth_engine.bootstrapper.routers.jobs.shred_artifacts",
                side_effect=_capture_and_shred,
            ),
            patch("synth_engine.bootstrapper.routers.jobs.get_audit_logger"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(f"/jobs/{job_id}/shred")

        # FINDING 5 fix: verify shred_artifacts was called with the correct job.
        assert len(captured_ids) == 1
        assert captured_ids[0] == job_id

    @pytest.mark.asyncio
    async def test_shred_emits_worm_audit_event(self) -> None:
        """POST /jobs/{id}/shred must emit a WORM audit event."""
        app, engine = _make_test_app_with_job(status="COMPLETE")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        mock_audit_logger = MagicMock()
        mock_get_audit_logger = MagicMock(return_value=mock_audit_logger)
        vault_patch, license_patch = _vault_and_license_patches()
        with (
            vault_patch,
            license_patch,
            patch("synth_engine.bootstrapper.routers.jobs.shred_artifacts"),
            patch(
                "synth_engine.bootstrapper.routers.jobs.get_audit_logger",
                mock_get_audit_logger,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(f"/jobs/{job_id}/shred")

        mock_audit_logger.log_event.assert_called_once()
        call_kwargs = mock_audit_logger.log_event.call_args.kwargs
        assert call_kwargs["event_type"] == "ARTIFACT_SHREDDED"

    @pytest.mark.asyncio
    async def test_shred_audit_event_includes_job_id(self) -> None:
        """Audit event details must include the job ID."""
        app, engine = _make_test_app_with_job(status="COMPLETE")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        mock_audit_logger = MagicMock()
        mock_get_audit_logger = MagicMock(return_value=mock_audit_logger)
        vault_patch, license_patch = _vault_and_license_patches()
        with (
            vault_patch,
            license_patch,
            patch("synth_engine.bootstrapper.routers.jobs.shred_artifacts"),
            patch(
                "synth_engine.bootstrapper.routers.jobs.get_audit_logger",
                mock_get_audit_logger,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(f"/jobs/{job_id}/shred")

        call_kwargs = mock_audit_logger.log_event.call_args.kwargs
        assert call_kwargs["details"]["job_id"] == str(job_id)

    @pytest.mark.asyncio
    async def test_shred_status_transitions_to_shredded_even_if_audit_fails(
        self,
    ) -> None:
        """FINDING 4: Job must reach SHREDDED even when the WORM audit logger raises.

        Audit log failure is swallowed post-deletion because aborting would
        leave ghost state (COMPLETE with no files).  See ADR-0034.
        """
        app, engine = _make_test_app_with_job(status="COMPLETE")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        def _raise_audit_logger() -> None:
            raise RuntimeError("audit down")

        vault_patch, license_patch = _vault_and_license_patches()
        with (
            vault_patch,
            license_patch,
            patch("synth_engine.bootstrapper.routers.jobs.shred_artifacts"),
            patch(
                "synth_engine.bootstrapper.routers.jobs.get_audit_logger",
                side_effect=_raise_audit_logger,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/jobs/{job_id}/shred")

        assert response.status_code == 200

        with Session(engine) as session:
            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            updated = session.get(SynthesisJob, job_id)
            assert updated is not None
            assert updated.status == "SHREDDED"


# ---------------------------------------------------------------------------
# TestShredEndpointErrorPaths
# ---------------------------------------------------------------------------


class TestShredEndpointErrorPaths:
    """Tests for POST /jobs/{id}/shred — error and guard conditions."""

    @pytest.mark.asyncio
    async def test_shred_nonexistent_job_returns_404(self) -> None:
        """POST /jobs/{id}/shred for a missing job must return 404."""
        app, engine = _make_test_app_with_job(status="COMPLETE")

        vault_patch, license_patch = _vault_and_license_patches()
        with vault_patch, license_patch:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/jobs/99999/shred")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_shred_queued_job_returns_404(self) -> None:
        """POST /jobs/{id}/shred on a QUEUED job must return 404 (not COMPLETE)."""
        app, engine = _make_test_app_with_job(status="QUEUED")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        vault_patch, license_patch = _vault_and_license_patches()
        with vault_patch, license_patch:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/jobs/{job_id}/shred")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_shred_training_job_returns_404(self) -> None:
        """POST /jobs/{id}/shred on a TRAINING job must return 404."""
        app, engine = _make_test_app_with_job(status="TRAINING")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        vault_patch, license_patch = _vault_and_license_patches()
        with vault_patch, license_patch:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/jobs/{job_id}/shred")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_shred_failed_job_returns_404(self) -> None:
        """POST /jobs/{id}/shred on a FAILED job must return 404."""
        app, engine = _make_test_app_with_job(status="FAILED")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        vault_patch, license_patch = _vault_and_license_patches()
        with vault_patch, license_patch:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/jobs/{job_id}/shred")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_shred_already_shredded_job_returns_404(self) -> None:
        """POST /jobs/{id}/shred on an already-SHREDDED job must return 404."""
        app, engine = _make_test_app_with_job(status="SHREDDED")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        vault_patch, license_patch = _vault_and_license_patches()
        with vault_patch, license_patch:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/jobs/{job_id}/shred")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_shred_generating_job_returns_404(self) -> None:
        """FINDING 6: POST /jobs/{id}/shred on a GENERATING job must return 404."""
        app, engine = _make_test_app_with_job(status="GENERATING")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        vault_patch, license_patch = _vault_and_license_patches()
        with vault_patch, license_patch:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/jobs/{job_id}/shred")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_shred_404_uses_rfc7807_format(self) -> None:
        """POST /jobs/{id}/shred 404 response must follow RFC 7807 Problem Details."""
        app, engine = _make_test_app_with_job(status="QUEUED")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        vault_patch, license_patch = _vault_and_license_patches()
        with vault_patch, license_patch:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/jobs/{job_id}/shred")

        body = response.json()
        assert body.get("status") == 404
        assert "type" in body
        assert "title" in body
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_shred_oserror_returns_500_rfc7807(self) -> None:
        """FINDING 1: OSError from shred_artifacts must yield HTTP 500 RFC 7807 response.

        When Path.unlink raises OSError, the router must catch it, log at ERROR
        with basename only, and return a 500 Problem Detail — never an unhandled
        exception that exposes internal paths.
        """
        app, engine = _make_test_app_with_job(status="COMPLETE")

        with Session(engine) as session:
            from sqlmodel import select

            from synth_engine.modules.synthesizer.job_models import SynthesisJob

            job = session.exec(select(SynthesisJob)).first()
            assert job is not None
            job_id = job.id

        vault_patch, license_patch = _vault_and_license_patches()
        with (
            vault_patch,
            license_patch,
            patch(
                "synth_engine.bootstrapper.routers.jobs.shred_artifacts",
                side_effect=OSError("Permission denied"),
            ),
            patch("synth_engine.bootstrapper.routers.jobs.get_audit_logger"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/jobs/{job_id}/shred")

        assert response.status_code == 500
        body = response.json()
        assert body.get("status") == 500
        assert "type" in body
        assert "title" in body
        assert "detail" in body
        assert "erasure" in body["detail"].lower() or "artifact" in body["detail"].lower()


# ---------------------------------------------------------------------------
# TestShredArtifactsDomainFunction
# ---------------------------------------------------------------------------


class TestShredArtifactsDomainFunction:
    """Unit tests for shred_artifacts() in modules/synthesizer/shred.py."""

    def test_shred_artifacts_deletes_output_path(self, tmp_path: Path) -> None:
        """shred_artifacts() must delete the output Parquet file."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.shred import shred_artifacts

        parquet = tmp_path / "out.parquet"
        parquet.write_bytes(b"fake parquet data")

        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/t.parquet",
            total_epochs=1,
            num_rows=1,
            status="COMPLETE",
            output_path=str(parquet),
        )

        shred_artifacts(job)
        assert not parquet.exists()

    def test_shred_artifacts_deletes_sig_sidecar(self, tmp_path: Path) -> None:
        """shred_artifacts() must delete the .sig sidecar alongside the Parquet."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.shred import shred_artifacts

        parquet = tmp_path / "out.parquet"
        sig = tmp_path / "out.parquet.sig"
        parquet.write_bytes(b"fake parquet data")
        sig.write_bytes(b"fake signature")

        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/t.parquet",
            total_epochs=1,
            num_rows=1,
            status="COMPLETE",
            output_path=str(parquet),
        )

        shred_artifacts(job)
        assert not parquet.exists()
        assert not sig.exists()

    def test_shred_artifacts_deletes_artifact_path(self, tmp_path: Path) -> None:
        """shred_artifacts() must delete the model artifact pickle file."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.shred import shred_artifacts

        pickle_file = tmp_path / "model.pkl"
        pickle_file.write_bytes(b"fake model pickle")

        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/t.parquet",
            total_epochs=1,
            num_rows=1,
            status="COMPLETE",
            artifact_path=str(pickle_file),
        )

        shred_artifacts(job)
        assert not pickle_file.exists()

    def test_shred_artifacts_tolerates_missing_output_path(self) -> None:
        """shred_artifacts() must not raise if output_path is None."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.shred import shred_artifacts

        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/t.parquet",
            total_epochs=1,
            num_rows=1,
            status="COMPLETE",
            output_path=None,
            artifact_path=None,
        )

        # Must not raise
        shred_artifacts(job)

    def test_shred_artifacts_tolerates_missing_artifact_path(self, tmp_path: Path) -> None:
        """shred_artifacts() must not raise if artifact_path is None."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.shred import shred_artifacts

        parquet = tmp_path / "out.parquet"
        parquet.write_bytes(b"fake parquet data")

        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/t.parquet",
            total_epochs=1,
            num_rows=1,
            status="COMPLETE",
            output_path=str(parquet),
            artifact_path=None,
        )

        # Must not raise
        shred_artifacts(job)
        assert not parquet.exists()

    def test_shred_artifacts_tolerates_already_deleted_files(self) -> None:
        """shred_artifacts() must not raise if files are already gone (idempotent)."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.shred import shred_artifacts

        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/t.parquet",
            total_epochs=1,
            num_rows=1,
            status="COMPLETE",
            output_path="/nonexistent/path/out.parquet",
            artifact_path="/nonexistent/path/model.pkl",
        )

        # Must not raise — NIST 800-88: idempotent erasure is acceptable
        shred_artifacts(job)

    def test_shred_artifacts_tolerates_sig_already_deleted(self, tmp_path: Path) -> None:
        """shred_artifacts() must not raise if only the Parquet exists (no .sig)."""
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.shred import shred_artifacts

        parquet = tmp_path / "out.parquet"
        parquet.write_bytes(b"fake parquet data")
        # No .sig file — unsigned artifact

        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/t.parquet",
            total_epochs=1,
            num_rows=1,
            status="COMPLETE",
            output_path=str(parquet),
        )

        # Must not raise
        shred_artifacts(job)
        assert not parquet.exists()

    def test_shred_artifacts_oserror_is_logged_and_reraised(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """FINDING 3: OSError from Path.unlink must be logged at ERROR and re-raised.

        The log message must use the basename only (never the full path) to
        avoid leaking internal filesystem topology in logs.
        """
        from synth_engine.modules.synthesizer.job_models import SynthesisJob
        from synth_engine.modules.synthesizer.shred import shred_artifacts

        parquet = tmp_path / "secret_data.parquet"
        parquet.write_bytes(b"fake parquet data")

        job = SynthesisJob(
            table_name="t",
            parquet_path="/tmp/t.parquet",
            total_epochs=1,
            num_rows=1,
            status="COMPLETE",
            output_path=str(parquet),
        )

        with (
            patch("pathlib.Path.unlink", side_effect=PermissionError("denied")),
            caplog.at_level(logging.ERROR, logger="synth_engine.modules.synthesizer.shred"),
        ):
            with pytest.raises(OSError, match="denied"):
                shred_artifacts(job)

        assert any("ERROR" in r.levelname for r in caplog.records)
        # Full path must not appear in any log message (basename only).
        for record in caplog.records:
            assert str(tmp_path) not in record.getMessage()
