"""Integration tests for POST /jobs/{id}/shred — Cryptographic Erasure Endpoint.

Verifies the full shred lifecycle with a real in-memory SQLite database:

1. Create a COMPLETE job with real artifact files on the filesystem.
2. Call POST /jobs/{id}/shred.
3. Assert HTTP 200 and status=SHREDDED in response.
4. Assert job.status == SHREDDED in the database.
5. Assert artifact files have been deleted from the filesystem.
6. Assert a WORM audit event was emitted.

Also verifies that non-COMPLETE jobs receive a 404 (guard conditions).

The in-memory SQLite approach is intentional: this endpoint exercises
filesystem I/O and database state transitions.  No PostgreSQL-specific
features are tested.  An equivalent PostgreSQL test would be redundant.

Task: P23-T23.4 — Cryptographic Erasure Endpoint
CONSTITUTION Priority 3: TDD — integration gate
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared app factory
# ---------------------------------------------------------------------------


def _make_integration_app(engine: Any) -> Any:
    """Build a fully-wired FastAPI app for integration tests.

    Args:
        engine: SQLAlchemy engine with test database tables already created.

    Returns:
        A FastAPI app with jobs router wired and DB dependency overridden.
    """
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    app = create_app()
    register_error_handlers(app)
    app.include_router(jobs_router)

    def _override() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    return app


def _make_engine_with_schema() -> Any:
    """Create an in-memory SQLite engine with all tables created.

    Returns:
        A SQLAlchemy engine with SQLModel metadata applied.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _vault_and_license_patches() -> tuple[Any, Any]:
    """Return vault/license patches used in every integration test."""
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
# Integration test: full shred lifecycle with real filesystem artifacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shred_deletes_real_artifact_files_and_transitions_to_shredded(
    tmp_path: Path,
) -> None:
    """Full lifecycle: COMPLETE job with real files is shredded to SHREDDED status.

    Acceptance Criteria verified:
      - AC1: artifact files deleted from filesystem.
      - AC2: WORM audit event emitted (ARTIFACT_SHREDDED event_type).
      - AC3: job.status transitions to SHREDDED in the database.
      - AC4: endpoint returns 200 with status=SHREDDED.
    """
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob
    from synth_engine.shared.security.audit import AuditLogger, reset_audit_logger

    # Create real artifact files
    parquet_file = tmp_path / "job_1_synthetic.parquet"
    sig_file = tmp_path / "job_1_synthetic.parquet.sig"
    pickle_file = tmp_path / "job_1_epoch_10.pkl"
    parquet_file.write_bytes(b"fake parquet data")
    sig_file.write_bytes(b"fake hmac signature")
    pickle_file.write_bytes(b"fake model pickle")

    engine = _make_engine_with_schema()

    with Session(engine) as session:
        job = SynthesisJob(
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            total_epochs=10,
            num_rows=100,
            status="COMPLETE",
            output_path=str(parquet_file),
            artifact_path=str(pickle_file),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    app = _make_integration_app(engine)

    # Use a real AuditLogger (isolated instance) to verify audit emission
    reset_audit_logger()
    audit_key = bytes.fromhex("a" * 64)
    isolated_audit_logger = AuditLogger(audit_key)

    vault_patch, license_patch = _vault_and_license_patches()
    with (
        vault_patch,
        license_patch,
        patch(
            "synth_engine.bootstrapper.routers.jobs.get_audit_logger",
            return_value=isolated_audit_logger,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/jobs/{job_id}/shred")

    # AC4: HTTP 200 with SHREDDED body
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SHREDDED"
    assert body["job_id"] == job_id

    # AC1: All artifact files must be gone
    assert not parquet_file.exists(), "Parquet output must be deleted by shred"
    assert not sig_file.exists(), "Signature sidecar must be deleted by shred"
    assert not pickle_file.exists(), "Model artifact pickle must be deleted by shred"

    # AC3: Database record must show SHREDDED
    with Session(engine) as session:
        updated = session.get(SynthesisJob, job_id)
        assert updated is not None
        assert updated.status == "SHREDDED"

    reset_audit_logger()


@pytest.mark.asyncio
async def test_shred_emits_worm_audit_event_with_correct_fields(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC2: WORM audit event emitted with ARTIFACT_SHREDDED type and job ID.

    Uses caplog to capture the audit log JSON emitted at INFO level from
    the ``synth_engine.security.audit`` logger.
    """
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob
    from synth_engine.shared.security.audit import AuditLogger, reset_audit_logger

    parquet_file = tmp_path / "audit_test.parquet"
    parquet_file.write_bytes(b"fake parquet data")

    engine = _make_engine_with_schema()

    with Session(engine) as session:
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

    app = _make_integration_app(engine)

    reset_audit_logger()
    audit_key = bytes.fromhex("b" * 64)
    isolated_audit_logger = AuditLogger(audit_key)

    vault_patch, license_patch = _vault_and_license_patches()
    with (
        vault_patch,
        license_patch,
        patch(
            "synth_engine.bootstrapper.routers.jobs.get_audit_logger",
            return_value=isolated_audit_logger,
        ),
        caplog.at_level(logging.INFO, logger="synth_engine.security.audit"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(f"/jobs/{job_id}/shred")

    # AC2: Verify the audit log contains the expected event
    audit_records = [r for r in caplog.records if r.name == "synth_engine.security.audit"]
    assert len(audit_records) >= 1, "Expected at least one WORM audit log entry"

    import json

    audit_event = json.loads(audit_records[0].message)
    assert audit_event["event_type"] == "ARTIFACT_SHREDDED"
    assert audit_event["details"]["job_id"] == str(job_id)

    reset_audit_logger()


@pytest.mark.asyncio
async def test_shred_non_complete_jobs_return_404() -> None:
    """AC4: Non-COMPLETE / already-SHREDDED jobs must return 404."""
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    non_eligible_statuses = ["QUEUED", "TRAINING", "GENERATING", "FAILED", "SHREDDED"]
    engine = _make_engine_with_schema()

    job_ids: list[tuple[int, str]] = []
    with Session(engine) as session:
        for status in non_eligible_statuses:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/t.parquet",
                total_epochs=1,
                num_rows=1,
                status=status,
            )
            session.add(job)
        session.commit()

        from sqlmodel import select

        jobs = session.exec(select(SynthesisJob)).all()
        for j in jobs:
            job_ids.append((j.id, j.status))  # type: ignore[arg-type]

    app = _make_integration_app(engine)

    vault_patch, license_patch = _vault_and_license_patches()
    with vault_patch, license_patch:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for jid, status in job_ids:
                response = await client.post(f"/jobs/{jid}/shred")
                assert response.status_code == 404, (
                    f"Expected 404 for status={status!r}, got {response.status_code}"
                )
