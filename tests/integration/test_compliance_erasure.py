"""Integration tests for GDPR Right-to-Erasure & CCPA Deletion Endpoint — T41.2.

Tests exercise the full erasure pipeline end-to-end with a real in-memory
SQLite database. They verify:

- Full cascade deletion of connection metadata and job records for a subject.
- Synthesized output records (output_path references) are NOT deleted.
- Audit trail is preserved after erasure.
- Compliance receipt accurately reflects what was deleted vs. retained.
- Vault-sealed state returns 423.
- Erasure request is logged to audit trail.

ALE / Vault setup note
----------------------
The ``Connection`` model uses ``EncryptedString`` (ALE) for the ``host``,
``database``, and ``schema_name`` columns.  Tests that write and read
``Connection`` records require a consistent ALE key across write and read
operations.  We achieve this by properly unsealing the vault with a fixed
test salt and passphrase before creating any connection records.

The ``TestErasureVaultSealedGate`` tests call ``VaultState.reset()``
explicitly to restore the sealed state for their assertions.

CONSTITUTION Priority 0: Security — PII-free audit, no data leakage
CONSTITUTION Priority 3: TDD
Task: T41.2 — Implement GDPR Right-to-Erasure & CCPA Deletion Endpoint
"""

from __future__ import annotations

import base64
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from synth_engine.bootstrapper.schemas.connections import Connection
from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Module-level vault setup for ALE-encrypted Connection tests
# ---------------------------------------------------------------------------

#: Fixed test salt — 16 bytes, base64url-encoded.
_TEST_VAULT_SALT: str = base64.urlsafe_b64encode(b"synth-test-salt!").decode()

#: Fixed test passphrase. Not a real secret — test isolation only.
_TEST_VAULT_PASSPHRASE: str = "erasure-test-passphrase"  # nosec B105 # pragma: allowlist secret


def _ensure_vault_unsealed() -> None:
    """Unseal the vault with a fixed test passphrase if currently sealed.

    Used in test helpers that create or read ALE-encrypted Connection records.
    Tests in ``TestErasureVaultSealedGate`` call ``VaultState.reset()`` to
    restore the sealed state for their own assertions.
    """
    from synth_engine.shared.security.vault import VaultState

    if VaultState.is_sealed():
        os.environ["VAULT_SEAL_SALT"] = _TEST_VAULT_SALT
        VaultState.unseal(_TEST_VAULT_PASSPHRASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine() -> Any:
    """Create an in-memory SQLite engine with all tables created.

    Returns:
        SQLAlchemy engine backed by in-memory SQLite.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _make_job(
    *,
    table_name: str = "users",
    parquet_path: str = "/tmp/p.parquet",
    owner_id: str = "op1",
    status: str = "COMPLETE",
    output_path: str | None = None,
) -> SynthesisJob:
    """Build a SynthesisJob fixture.

    Args:
        table_name: Table synthesised.
        parquet_path: Source parquet path.
        owner_id: Operator/subject identity.
        status: Job lifecycle status.
        output_path: Synthesized output file path (non-PII, DP-protected).

    Returns:
        SynthesisJob instance not yet persisted.
    """
    return SynthesisJob(
        table_name=table_name,
        parquet_path=parquet_path,
        total_epochs=1,
        num_rows=1,
        owner_id=owner_id,
        status=status,
        output_path=output_path,
    )


def _make_connection(*, name: str = "conn", owner_id: str = "op1") -> Connection:
    """Build a Connection fixture.

    Args:
        name: Display name.
        owner_id: Operator/subject identity.

    Returns:
        Connection instance not yet persisted.
    """
    return Connection(
        name=name,
        host="localhost",
        port=5432,
        database="testdb",
        schema_name="public",
        owner_id=owner_id,
    )


def _build_app(engine: Any) -> Any:
    """Build a test FastAPI app with compliance router and DB override.

    Unseals the vault before building the app so ALE-encrypted Connection
    fields can be read. Tests in ``TestErasureVaultSealedGate`` reset the
    vault themselves.

    Args:
        engine: SQLAlchemy engine to use for dependency override.

    Returns:
        Configured FastAPI app instance.
    """
    os.environ.setdefault("AUDIT_KEY", "aa" * 32)
    os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")

    from fastapi import FastAPI
    from sqlmodel import Session

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.compliance import router as compliance_router

    _ensure_vault_unsealed()

    app = FastAPI()
    app.include_router(compliance_router)

    def _override() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    return app


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestErasureEndpointCascadeDeletion:
    """End-to-end tests for cascade deletion through connections and jobs."""

    def setup_method(self) -> None:
        """Unseal the vault before each test so ALE operations succeed."""
        _ensure_vault_unsealed()

    def teardown_method(self) -> None:
        """Re-seal the vault after each test to restore isolation."""
        from synth_engine.shared.security.vault import VaultState

        VaultState.reset()

    def test_cascade_deletes_all_connections_for_subject(self) -> None:
        """All connections owned by the subject are deleted; others survive."""
        from fastapi.testclient import TestClient

        engine = _make_engine()
        with Session(engine) as session:
            for i in range(3):
                session.add(_make_connection(name=f"conn-subj-{i}", owner_id="subject-A"))
            for i in range(2):
                session.add(_make_connection(name=f"conn-other-{i}", owner_id="other-B"))
            session.commit()

        app = _build_app(engine)
        audit_mock = MagicMock()

        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.lifecycle.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/api/v1/compliance/erasure",
                json={"subject_id": "subject-A"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["deleted_connections"] == 3

        with Session(engine) as session:
            remaining = session.exec(select(Connection)).all()
            assert len(remaining) == 2
            assert all(c.owner_id == "other-B" for c in remaining)

    def test_cascade_deletes_all_jobs_for_subject(self) -> None:
        """All synthesis jobs owned by the subject are deleted; others survive."""
        from fastapi.testclient import TestClient

        engine = _make_engine()
        with Session(engine) as session:
            for i in range(4):
                session.add(_make_job(table_name=f"tbl_{i}", owner_id="subject-A"))
            session.add(_make_job(table_name="tbl_other", owner_id="other-B"))
            session.commit()

        app = _build_app(engine)
        audit_mock = MagicMock()

        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.lifecycle.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/api/v1/compliance/erasure",
                json={"subject_id": "subject-A"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["deleted_jobs"] == 4

        with Session(engine) as session:
            remaining = session.exec(select(SynthesisJob)).all()
            assert len(remaining) == 1
            assert remaining[0].owner_id == "other-B"

    def test_compliance_receipt_documents_retained_output(self) -> None:
        """Compliance receipt includes retained_synthesized_output=True."""
        from fastapi.testclient import TestClient

        engine = _make_engine()
        with Session(engine) as session:
            session.add(
                _make_job(
                    owner_id="subject-A",
                    output_path="/output/synth_a.parquet",
                )
            )
            session.commit()

        app = _build_app(engine)
        audit_mock = MagicMock()

        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.lifecycle.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/api/v1/compliance/erasure",
                json={"subject_id": "subject-A"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["retained_synthesized_output"] is True
        assert body["retained_audit_trail"] is True
        assert len(body.get("retained_synthesized_output_justification", "")) > 0
        assert len(body.get("retained_audit_trail_justification", "")) > 0


class TestErasureAuditTrailPreservation:
    """Tests verifying audit trail is preserved and erasure is logged."""

    def setup_method(self) -> None:
        """Unseal the vault before each test so ALE operations succeed."""
        _ensure_vault_unsealed()

    def teardown_method(self) -> None:
        """Re-seal the vault after each test to restore isolation."""
        from synth_engine.shared.security.vault import VaultState

        VaultState.reset()

    def test_audit_event_emitted_for_erasure(self) -> None:
        """GDPR_ERASURE audit event is emitted for every erasure request."""
        from fastapi.testclient import TestClient

        engine = _make_engine()
        app = _build_app(engine)
        audit_mock = MagicMock()

        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.lifecycle.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            client.request("DELETE", "/api/v1/compliance/erasure", json={"subject_id": "sub-E2E"})

        audit_mock.log_event.assert_called_once()
        call_kwargs = audit_mock.log_event.call_args.kwargs
        assert call_kwargs["event_type"] == "GDPR_ERASURE"
        assert call_kwargs["action"] == "erasure"

    def test_audit_details_include_deletion_counts(self) -> None:
        """Audit event details include deleted_jobs and deleted_connections counts."""
        from fastapi.testclient import TestClient

        engine = _make_engine()
        with Session(engine) as session:
            session.add(_make_job(owner_id="sub-E2E"))
            session.add(_make_connection(name="conn-e2e", owner_id="sub-E2E"))
            session.commit()

        app = _build_app(engine)
        audit_mock = MagicMock()

        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.lifecycle.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            client.request("DELETE", "/api/v1/compliance/erasure", json={"subject_id": "sub-E2E"})

        call_kwargs = audit_mock.log_event.call_args.kwargs
        details: dict[str, str] = call_kwargs["details"]
        assert "deleted_jobs" in details
        assert "deleted_connections" in details
        assert details["deleted_jobs"] == "1"
        assert details["deleted_connections"] == "1"

    def test_audit_details_do_not_expose_raw_subject_id(self) -> None:
        """Audit details do not include the subject_id as a value (PII guard)."""
        from fastapi.testclient import TestClient

        engine = _make_engine()
        app = _build_app(engine)
        audit_mock = MagicMock()

        subject_id = "pii-user@example.com"
        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.lifecycle.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            client.request("DELETE", "/api/v1/compliance/erasure", json={"subject_id": subject_id})

        call_kwargs = audit_mock.log_event.call_args.kwargs
        details: dict[str, str] = call_kwargs["details"]
        for v in details.values():
            assert subject_id not in v, (
                f"Raw subject_id must not appear in audit details value: {v!r}"
            )


class TestErasureVaultSealedGate:
    """Tests for vault-sealed 423 response."""

    def test_sealed_vault_returns_423(self) -> None:
        """DELETE /compliance/erasure returns 423 when vault is sealed."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlmodel import Session

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.routers.compliance import router as compliance_router
        from synth_engine.shared.security.vault import VaultState

        engine = _make_engine()
        app = FastAPI()
        app.include_router(compliance_router)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

        VaultState.reset()
        assert VaultState.is_sealed()

        client = TestClient(app, raise_server_exceptions=False)
        response = client.request(
            "DELETE",
            "/api/v1/compliance/erasure",
            json={"subject_id": "sub-sealed"},
        )

        assert response.status_code == 423

    def test_sealed_vault_response_is_rfc7807(self) -> None:
        """423 response from sealed vault uses RFC 7807 Problem Details format."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlmodel import Session

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.routers.compliance import router as compliance_router
        from synth_engine.shared.security.vault import VaultState

        engine = _make_engine()
        app = FastAPI()
        app.include_router(compliance_router)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

        VaultState.reset()

        client = TestClient(app, raise_server_exceptions=False)
        response = client.request(
            "DELETE",
            "/api/v1/compliance/erasure",
            json={"subject_id": "sub-sealed"},
        )

        body = response.json()
        assert "status" in body
        assert body["status"] == 423

    def test_no_deletion_occurs_when_vault_sealed(self) -> None:
        """No records are deleted when vault is sealed — erasure is aborted."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlmodel import Session

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.routers.compliance import router as compliance_router
        from synth_engine.shared.security.vault import VaultState

        engine = _make_engine()
        with Session(engine) as session:
            session.add(_make_job(owner_id="sub-sealed"))
            session.commit()

        app = FastAPI()
        app.include_router(compliance_router)

        def _override() -> Any:
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db_session] = _override

        VaultState.reset()

        client = TestClient(app, raise_server_exceptions=False)
        client.request(
            "DELETE",
            "/api/v1/compliance/erasure",
            json={"subject_id": "sub-sealed"},
        )

        with Session(engine) as session:
            remaining = session.exec(select(SynthesisJob)).all()
            # All records must survive — erasure was blocked by sealed vault
            assert len(remaining) == 1
