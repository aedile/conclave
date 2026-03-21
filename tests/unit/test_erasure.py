"""Unit tests for GDPR Right-to-Erasure & CCPA Deletion — T41.2.

Tests verify (in RED order):

- ErasureService.execute_erasure() deletes connection records referencing
  the subject identifier.
- ErasureService.execute_erasure() deletes SynthesisJob records referencing
  the subject identifier.
- ErasureService.execute_erasure() does NOT delete synthesized output
  (non-PII, differentially private).
- Audit trail is preserved (not deleted).
- ErasureService.execute_erasure() returns a DeletionManifest documenting
  what was deleted and what was retained.
- Audit logging is called once per erasure with event type GDPR_ERASURE.
- DELETE /compliance/erasure returns 200 with compliance receipt.
- DELETE /compliance/erasure returns 423 when vault is sealed.
- DELETE /compliance/erasure audit failure does not abort the erasure.
- DELETE /compliance/erasure with no matching records returns 200 with
  empty manifest (idempotent).
- ErasureRequest rejects empty subject_id (QA-B1).
- Whitespace-only subject_id passes validation (opaque identifier — not stripped).
- Auth guard enforces 401 when JWT_SECRET_KEY is configured (QA-B3).

CONSTITUTION Priority 0: Security — no PII in audit details
CONSTITUTION Priority 3: TDD — RED/GREEN/REFACTOR
Task: T41.2 — Implement GDPR Right-to-Erasure & CCPA Deletion Endpoint
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from synth_engine.bootstrapper.schemas.connections import Connection
from synth_engine.modules.synthesizer.job_models import SynthesisJob

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Module-level ALE key fixture
# ---------------------------------------------------------------------------
#
# Connection fields are ALE-encrypted; inserting Connection records requires
# ALE_KEY to be set.  This autouse session fixture ensures a valid Fernet key
# is available for every test in this module without modifying other test files.


@pytest.fixture(autouse=True, scope="module")
def _module_ale_key() -> Any:
    """Set a valid ALE_KEY for the entire test module.

    Connection records have ALE-encrypted fields (host, database, etc.).
    Without ALE_KEY set, inserting a Connection row raises RuntimeError from
    the ALE type processor.  This fixture ensures a stable key is present for
    all tests, without interfering with tests in other modules.

    Yields:
        None — used for side-effect (env var setup) only.
    """
    key = Fernet.generate_key().decode()
    original = os.environ.get("ALE_KEY")
    os.environ["ALE_KEY"] = key
    yield
    if original is None:
        os.environ.pop("ALE_KEY", None)
    else:
        os.environ["ALE_KEY"] = original


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine() -> Any:
    """Create an in-memory SQLite engine with all tables.

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
    """Build a SynthesisJob test fixture.

    Args:
        table_name: Table that was synthesised.
        parquet_path: Source parquet path.
        owner_id: Operator identity.
        status: Job lifecycle status.
        output_path: Path to synthesized output (if complete).

    Returns:
        SynthesisJob instance (not yet persisted).
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


def _make_connection(
    *,
    name: str = "test-conn",
    owner_id: str = "op1",
) -> Connection:
    """Build a Connection test fixture.

    Args:
        name: Display name for the connection.
        owner_id: Operator identity.

    Returns:
        Connection instance (not yet persisted).
    """
    return Connection(
        name=name,
        host="localhost",
        port=5432,
        database="testdb",
        schema_name="public",
        owner_id=owner_id,
    )


# ---------------------------------------------------------------------------
# ErasureService unit tests
# ---------------------------------------------------------------------------


class TestErasureServiceDeletionManifest:
    """Tests for ErasureService returning a correct DeletionManifest."""

    def test_manifest_has_required_fields(self) -> None:
        """DeletionManifest has deleted_connections, deleted_jobs, retained fields."""
        from synth_engine.modules.synthesizer.erasure import DeletionManifest

        manifest = DeletionManifest(
            subject_id="sub-001",
            deleted_connections=2,
            deleted_jobs=1,
            retained_synthesized_output=True,
            retained_audit_trail=True,
            retained_synthesized_output_justification=(
                "Synthesized output is differentially private and non-attributable."
            ),
            retained_audit_trail_justification=(
                "Audit trail is required for compliance proof per GDPR Article 17(3)(b)."
            ),
        )
        assert manifest.subject_id == "sub-001"
        assert manifest.deleted_connections == 2
        assert manifest.deleted_jobs == 1
        assert manifest.retained_synthesized_output is True
        assert manifest.retained_audit_trail is True

    def test_manifest_subject_id_stored(self) -> None:
        """DeletionManifest stores the subject_id used for erasure."""
        from synth_engine.modules.synthesizer.erasure import DeletionManifest

        manifest = DeletionManifest(
            subject_id="data-subject-xyz",
            deleted_connections=0,
            deleted_jobs=0,
            retained_synthesized_output=True,
            retained_audit_trail=True,
            retained_synthesized_output_justification="DP-protected.",
            retained_audit_trail_justification="Compliance proof.",
        )
        assert manifest.subject_id == "data-subject-xyz"


class TestErasureServiceDeletesCorrectRecords:
    """Tests for ErasureService deleting only matching records."""

    def test_deletes_jobs_owned_by_subject(self) -> None:
        """execute_erasure deletes SynthesisJobs whose owner_id matches subject_id."""
        from synth_engine.modules.synthesizer.erasure import ErasureService

        engine = _make_engine()

        with Session(engine) as session:
            job_to_delete = _make_job(owner_id="subject-1")
            job_to_keep = _make_job(owner_id="subject-2")
            session.add_all([job_to_delete, job_to_keep])
            session.commit()

        audit_mock = MagicMock()
        with Session(engine) as session:
            service = ErasureService(session=session)
            with patch(
                "synth_engine.modules.synthesizer.erasure.get_audit_logger",
                return_value=audit_mock,
            ):
                manifest = service.execute_erasure(subject_id="subject-1", actor="operator-admin")

        assert manifest.deleted_jobs == 1

        with Session(engine) as session:
            remaining = session.exec(select(SynthesisJob)).all()
            assert len(remaining) == 1
            assert remaining[0].owner_id == "subject-2"

    def test_deletes_connections_owned_by_subject(self) -> None:
        """execute_erasure deletes Connections whose owner_id matches subject_id."""
        from synth_engine.modules.synthesizer.erasure import ErasureService

        engine = _make_engine()

        with Session(engine) as session:
            conn_to_delete = _make_connection(name="conn-a", owner_id="subject-1")
            conn_to_keep = _make_connection(name="conn-b", owner_id="subject-2")
            session.add_all([conn_to_delete, conn_to_keep])
            session.commit()

        audit_mock = MagicMock()
        with Session(engine) as session:
            service = ErasureService(session=session, connection_model=Connection)
            with patch(
                "synth_engine.modules.synthesizer.erasure.get_audit_logger",
                return_value=audit_mock,
            ):
                manifest = service.execute_erasure(subject_id="subject-1", actor="operator-admin")

        assert manifest.deleted_connections == 1

        with Session(engine) as session:
            remaining = session.exec(select(Connection)).all()
            assert len(remaining) == 1
            assert remaining[0].owner_id == "subject-2"

    def test_no_matching_records_returns_empty_manifest(self) -> None:
        """execute_erasure with no matching records returns manifest with zeros."""
        from synth_engine.modules.synthesizer.erasure import ErasureService

        engine = _make_engine()

        audit_mock = MagicMock()
        with Session(engine) as session:
            service = ErasureService(session=session)
            with patch(
                "synth_engine.modules.synthesizer.erasure.get_audit_logger",
                return_value=audit_mock,
            ):
                manifest = service.execute_erasure(
                    subject_id="nonexistent-subject", actor="operator-admin"
                )

        assert manifest.deleted_connections == 0
        assert manifest.deleted_jobs == 0

    def test_only_matching_subject_deleted_mixed_db(self) -> None:
        """execute_erasure deletes only records matching the subject, not others."""
        from synth_engine.modules.synthesizer.erasure import ErasureService

        engine = _make_engine()

        with Session(engine) as session:
            for i in range(3):
                session.add(_make_job(owner_id=f"subject-{i}"))
                session.add(_make_connection(name=f"conn-{i}", owner_id=f"subject-{i}"))
            session.commit()

        audit_mock = MagicMock()
        with Session(engine) as session:
            service = ErasureService(session=session, connection_model=Connection)
            with patch(
                "synth_engine.modules.synthesizer.erasure.get_audit_logger",
                return_value=audit_mock,
            ):
                manifest = service.execute_erasure(subject_id="subject-1", actor="operator-admin")

        assert manifest.deleted_jobs == 1
        assert manifest.deleted_connections == 1

        with Session(engine) as session:
            remaining_jobs = session.exec(select(SynthesisJob)).all()
            remaining_conns = session.exec(select(Connection)).all()
            assert len(remaining_jobs) == 2
            assert len(remaining_conns) == 2
            assert all(j.owner_id != "subject-1" for j in remaining_jobs)
            assert all(c.owner_id != "subject-1" for c in remaining_conns)


class TestErasureServicePreservation:
    """Tests verifying synthesized output and audit trail are NOT deleted."""

    def test_retained_synthesized_output_flag_is_true(self) -> None:
        """Manifest always reports retained_synthesized_output=True."""
        from synth_engine.modules.synthesizer.erasure import ErasureService

        engine = _make_engine()
        audit_mock = MagicMock()
        with Session(engine) as session:
            service = ErasureService(session=session)
            with patch(
                "synth_engine.modules.synthesizer.erasure.get_audit_logger",
                return_value=audit_mock,
            ):
                manifest = service.execute_erasure(subject_id="any-subject", actor="operator-admin")

        assert manifest.retained_synthesized_output is True

    def test_retained_audit_trail_flag_is_true(self) -> None:
        """Manifest always reports retained_audit_trail=True."""
        from synth_engine.modules.synthesizer.erasure import ErasureService

        engine = _make_engine()
        audit_mock = MagicMock()
        with Session(engine) as session:
            service = ErasureService(session=session)
            with patch(
                "synth_engine.modules.synthesizer.erasure.get_audit_logger",
                return_value=audit_mock,
            ):
                manifest = service.execute_erasure(subject_id="any-subject", actor="operator-admin")

        assert manifest.retained_audit_trail is True

    def test_output_path_not_deleted_from_filesystem(self) -> None:
        """execute_erasure does NOT delete synthesized output files from disk."""
        import inspect

        from synth_engine.modules.synthesizer.erasure import ErasureService

        # Inspect ErasureService to ensure it does NOT delete files.
        # The service must not call unlink or os.remove on output_path.
        source = inspect.getsource(ErasureService.execute_erasure)
        assert "unlink" not in source, (
            "ErasureService.execute_erasure must not call unlink (synthesized output is preserved)."
        )
        assert "os.remove" not in source, (
            "ErasureService.execute_erasure must not call os.remove"
            " (synthesized output is preserved)."
        )

    def test_manifest_justifications_are_non_empty(self) -> None:
        """Manifest justification fields are non-empty strings."""
        from synth_engine.modules.synthesizer.erasure import ErasureService

        engine = _make_engine()
        audit_mock = MagicMock()
        with Session(engine) as session:
            service = ErasureService(session=session)
            with patch(
                "synth_engine.modules.synthesizer.erasure.get_audit_logger",
                return_value=audit_mock,
            ):
                manifest = service.execute_erasure(subject_id="any-subject", actor="operator-admin")

        assert len(manifest.retained_synthesized_output_justification) > 0
        assert len(manifest.retained_audit_trail_justification) > 0


class TestErasureServiceAuditLogging:
    """Tests verifying audit trail is written for every erasure request."""

    def test_audit_event_emitted_on_erasure(self) -> None:
        """execute_erasure emits exactly one GDPR_ERASURE audit event."""
        from synth_engine.modules.synthesizer.erasure import ErasureService

        engine = _make_engine()
        audit_mock = MagicMock()
        with Session(engine) as session:
            service = ErasureService(session=session)
            with patch(
                "synth_engine.modules.synthesizer.erasure.get_audit_logger",
                return_value=audit_mock,
            ):
                service.execute_erasure(subject_id="sub-001", actor="op1")

        audit_mock.log_event.assert_called_once()
        call_kwargs = audit_mock.log_event.call_args.kwargs
        assert call_kwargs["event_type"] == "GDPR_ERASURE"

    def test_audit_event_records_actor(self) -> None:
        """execute_erasure audit event records the requesting actor."""
        from synth_engine.modules.synthesizer.erasure import ErasureService

        engine = _make_engine()
        audit_mock = MagicMock()
        with Session(engine) as session:
            service = ErasureService(session=session)
            with patch(
                "synth_engine.modules.synthesizer.erasure.get_audit_logger",
                return_value=audit_mock,
            ):
                service.execute_erasure(subject_id="sub-001", actor="operator-alice")

        call_kwargs = audit_mock.log_event.call_args.kwargs
        assert call_kwargs["actor"] == "operator-alice"

    def test_audit_event_does_not_contain_raw_subject_id(self) -> None:
        """execute_erasure audit details do not expose the raw subject_id as a value.

        The subject_id is treated as a PII identifier. Audit details must
        only include counts (deleted_jobs, deleted_connections), not the
        raw identifier value.
        """
        from synth_engine.modules.synthesizer.erasure import ErasureService

        engine = _make_engine()
        audit_mock = MagicMock()
        with Session(engine) as session:
            service = ErasureService(session=session)
            with patch(
                "synth_engine.modules.synthesizer.erasure.get_audit_logger",
                return_value=audit_mock,
            ):
                service.execute_erasure(subject_id="pii-email@example.com", actor="op1")

        call_kwargs = audit_mock.log_event.call_args.kwargs
        details: dict[str, str] = call_kwargs["details"]
        # Subject ID must not appear verbatim in any detail value
        for v in details.values():
            assert "pii-email@example.com" not in v

    def test_audit_failure_does_not_abort_erasure(self) -> None:
        """execute_erasure succeeds even if audit logging raises an exception."""
        from synth_engine.modules.synthesizer.erasure import ErasureService

        engine = _make_engine()
        with Session(engine) as session:
            session.add(_make_job(owner_id="subject-x"))
            session.commit()

        audit_mock = MagicMock()
        audit_mock.log_event.side_effect = RuntimeError("audit service down")

        with Session(engine) as session:
            service = ErasureService(session=session)
            with patch(
                "synth_engine.modules.synthesizer.erasure.get_audit_logger",
                return_value=audit_mock,
            ):
                # Must not raise
                manifest = service.execute_erasure(subject_id="subject-x", actor="op1")

        # Deletion still completed despite audit failure
        assert manifest.deleted_jobs == 1


# ---------------------------------------------------------------------------
# Compliance router unit tests
# ---------------------------------------------------------------------------


class TestComplianceEndpointHappy:
    """Tests for DELETE /compliance/erasure success paths."""

    def setup_method(self) -> None:
        """Unseal the vault before each happy-path test."""
        from synth_engine.shared.security.vault import VaultState

        VaultState._is_sealed = False

    def teardown_method(self) -> None:
        """Re-seal the vault after each happy-path test to restore isolation."""
        from synth_engine.shared.security.vault import VaultState

        VaultState.reset()

    def _build_app(self, engine: Any) -> Any:
        """Build a minimal FastAPI app with the compliance router wired.

        Args:
            engine: SQLAlchemy engine for the DB override.

        Returns:
            Configured FastAPI app instance.
        """
        os.environ.setdefault("AUDIT_KEY", "aa" * 32)
        os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")

        from fastapi import FastAPI

        from synth_engine.bootstrapper.dependencies.auth import get_current_operator
        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.routers.compliance import router as compliance_router

        app = FastAPI()
        app.include_router(compliance_router)

        def _override_session() -> Any:
            with Session(engine) as session:
                yield session

        def _override_operator() -> str:
            return "test-operator"

        app.dependency_overrides[get_db_session] = _override_session
        app.dependency_overrides[get_current_operator] = _override_operator
        return app

    def test_erasure_returns_200_with_compliance_receipt(self) -> None:
        """DELETE /compliance/erasure returns HTTP 200 with compliance receipt body."""
        from fastapi.testclient import TestClient

        engine = _make_engine()
        app = self._build_app(engine)
        audit_mock = MagicMock()

        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": "sub-001"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["subject_id"] == "sub-001"
        assert body["retained_synthesized_output"] is True
        assert body["retained_audit_trail"] is True

    def test_erasure_deletes_correct_jobs(self) -> None:
        """DELETE /compliance/erasure removes only jobs owned by the subject."""
        from fastapi.testclient import TestClient

        engine = _make_engine()
        with Session(engine) as session:
            session.add(_make_job(owner_id="sub-001"))
            session.add(_make_job(owner_id="sub-999"))
            session.commit()

        app = self._build_app(engine)
        audit_mock = MagicMock()

        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": "sub-001"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["deleted_jobs"] == 1

        with Session(engine) as session:
            remaining = session.exec(select(SynthesisJob)).all()
            assert len(remaining) == 1
            assert remaining[0].owner_id == "sub-999"

    def test_erasure_audit_event_emitted(self) -> None:
        """DELETE /compliance/erasure emits a GDPR_ERASURE audit event."""
        from fastapi.testclient import TestClient

        engine = _make_engine()
        app = self._build_app(engine)
        audit_mock = MagicMock()

        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            client.request("DELETE", "/compliance/erasure", json={"subject_id": "sub-001"})

        audit_mock.log_event.assert_called_once()
        assert audit_mock.log_event.call_args.kwargs["event_type"] == "GDPR_ERASURE"

    def test_erasure_idempotent_for_nonexistent_subject(self) -> None:
        """DELETE /compliance/erasure returns 200 even if no records exist for subject."""
        from fastapi.testclient import TestClient

        engine = _make_engine()
        app = self._build_app(engine)
        audit_mock = MagicMock()

        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": "no-such-subject"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["deleted_connections"] == 0
        assert body["deleted_jobs"] == 0

    def test_erasure_audit_failure_returns_200(self) -> None:
        """DELETE /compliance/erasure returns 200 even if audit logging fails."""
        from fastapi.testclient import TestClient

        engine = _make_engine()
        app = self._build_app(engine)
        audit_mock = MagicMock()
        audit_mock.log_event.side_effect = RuntimeError("audit service down")

        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": "sub-001"},
            )

        assert response.status_code == 200


class TestComplianceEndpointVaultSealed:
    """Tests for DELETE /compliance/erasure when vault is sealed."""

    def _build_sealed_app(self, engine: Any) -> Any:
        """Build a FastAPI app with compliance router and auth overridden.

        Args:
            engine: SQLAlchemy engine for the DB override.

        Returns:
            Configured FastAPI app instance.
        """
        os.environ.setdefault("AUDIT_KEY", "aa" * 32)
        os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")

        from fastapi import FastAPI

        from synth_engine.bootstrapper.dependencies.auth import get_current_operator
        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.routers.compliance import router as compliance_router

        app = FastAPI()
        app.include_router(compliance_router)

        def _override_session() -> Any:
            with Session(engine) as session:
                yield session

        def _override_operator() -> str:
            return "test-operator"

        app.dependency_overrides[get_db_session] = _override_session
        app.dependency_overrides[get_current_operator] = _override_operator
        return app

    def test_erasure_returns_423_when_vault_sealed(self) -> None:
        """DELETE /compliance/erasure returns 423 when vault is sealed."""
        from fastapi.testclient import TestClient

        from synth_engine.shared.security.vault import VaultState

        engine = _make_engine()
        app = self._build_sealed_app(engine)

        # Ensure vault is sealed
        VaultState.reset()
        assert VaultState.is_sealed()

        client = TestClient(app, raise_server_exceptions=False)
        response = client.request(
            "DELETE",
            "/compliance/erasure",
            json={"subject_id": "sub-001"},
        )

        VaultState._is_sealed = False  # restore

        assert response.status_code == 423

    def test_erasure_returns_423_body_is_rfc7807(self) -> None:
        """DELETE /compliance/erasure 423 response uses RFC 7807 format."""
        from fastapi.testclient import TestClient

        from synth_engine.shared.security.vault import VaultState

        engine = _make_engine()
        app = self._build_sealed_app(engine)

        VaultState.reset()

        client = TestClient(app, raise_server_exceptions=False)
        response = client.request(
            "DELETE",
            "/compliance/erasure",
            json={"subject_id": "sub-001"},
        )

        VaultState._is_sealed = False  # restore

        body = response.json()
        assert "status" in body
        assert body["status"] == 423

    def test_erasure_proceeds_when_vault_unsealed(self) -> None:
        """DELETE /compliance/erasure proceeds normally when vault is NOT sealed."""
        from fastapi.testclient import TestClient

        from synth_engine.shared.security.vault import VaultState

        engine = _make_engine()
        app = self._build_sealed_app(engine)

        # Ensure vault is NOT sealed
        VaultState._is_sealed = False

        audit_mock = MagicMock()
        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": "sub-001"},
            )

        # Restore sealed state
        VaultState.reset()

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# New review-fix test classes
# ---------------------------------------------------------------------------


class TestErasureRequestValidation:
    """Tests for ErasureRequest.subject_id validation (QA-B1 + DevOps-B1 review fix).

    An empty subject_id would match all records whose owner_id is the empty
    string (the default for pre-JWT legacy records), causing bulk deletion of
    ALL pre-JWT resources.  The min_length=1 constraint prevents this.
    """

    def setup_method(self) -> None:
        """Unseal the vault before each test."""
        from synth_engine.shared.security.vault import VaultState

        VaultState._is_sealed = False

    def teardown_method(self) -> None:
        """Re-seal vault after each test."""
        from synth_engine.shared.security.vault import VaultState

        VaultState.reset()

    def _build_app(self, engine: Any) -> Any:
        """Build a FastAPI app with compliance router and auth overridden.

        Args:
            engine: SQLAlchemy engine for the DB override.

        Returns:
            Configured FastAPI app instance.
        """
        os.environ.setdefault("AUDIT_KEY", "aa" * 32)
        os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")

        from fastapi import FastAPI

        from synth_engine.bootstrapper.dependencies.auth import get_current_operator
        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.routers.compliance import router as compliance_router

        app = FastAPI()
        app.include_router(compliance_router)

        def _override_session() -> Any:
            with Session(engine) as session:
                yield session

        def _override_operator() -> str:
            return "test-operator"

        app.dependency_overrides[get_db_session] = _override_session
        app.dependency_overrides[get_current_operator] = _override_operator
        return app

    def test_empty_subject_id_returns_422(self) -> None:
        """DELETE /compliance/erasure with subject_id="" returns HTTP 422.

        An empty subject_id would match all legacy records with owner_id="".
        The min_length=1 constraint prevents accidental bulk erasure.
        """
        from fastapi.testclient import TestClient

        engine = _make_engine()
        app = self._build_app(engine)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.request(
            "DELETE",
            "/compliance/erasure",
            json={"subject_id": ""},
        )

        assert response.status_code == 422

    def test_whitespace_only_subject_id_returns_200_safe_noop(self) -> None:
        """DELETE /compliance/erasure with subject_id="   " documents safe behaviour.

        Pydantic's min_length constraint applies to the raw string value.
        A whitespace-only string has length >= 1 and passes min_length=1.

        Note: We do NOT add strip_whitespace=True because subject_id is
        treated as an opaque identifier and must not be silently modified.
        A whitespace-only ID will match zero real records (safe no-op), so
        this is not a bulk-delete risk — only the empty string ("") is.
        """
        from fastapi.testclient import TestClient

        engine = _make_engine()
        app = self._build_app(engine)
        audit_mock = MagicMock()

        client = TestClient(app)
        with patch(
            "synth_engine.modules.synthesizer.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": "   "},
            )

        # "   " has length 3, passes min_length=1; returns 200 with empty manifest
        assert response.status_code == 200
        body = response.json()
        assert body["deleted_jobs"] == 0
        assert body["deleted_connections"] == 0


class TestComplianceEndpointAuthGuard:
    """Tests for authentication enforcement on DELETE /compliance/erasure (QA-B3).

    When JWT_SECRET_KEY is configured, the get_current_operator dependency
    raises HTTP 401 for unauthenticated requests.
    """

    def setup_method(self) -> None:
        """Unseal vault before each test."""
        from synth_engine.shared.security.vault import VaultState

        VaultState._is_sealed = False

    def teardown_method(self) -> None:
        """Re-seal vault and clear JWT env var after each test."""
        from synth_engine.shared.security.vault import VaultState

        VaultState.reset()
        os.environ.pop("JWT_SECRET_KEY", None)
        # Clear settings cache so subsequent tests get a fresh read
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]

    def test_erasure_requires_auth_when_jwt_configured(self) -> None:
        """DELETE /compliance/erasure returns 401 when JWT is configured but no token given.

        When JWT_SECRET_KEY is set, get_current_operator raises HTTPException(401)
        for requests that omit the Authorization header.  This test verifies that
        the erasure endpoint is protected and cannot be accessed unauthenticated.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.routers.compliance import router as compliance_router

        os.environ.setdefault("AUDIT_KEY", "aa" * 32)
        os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
        # Configure JWT — this activates authentication enforcement in get_current_operator
        os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-auth-guard-test"

        # Force settings cache to re-read the env
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]

        engine = _make_engine()
        app = FastAPI()
        app.include_router(compliance_router)

        def _override_session() -> Any:
            with Session(engine) as session:
                yield session

        # Do NOT override get_current_operator — we want the real auth guard
        app.dependency_overrides[get_db_session] = _override_session

        client = TestClient(app, raise_server_exceptions=False)
        response = client.request(
            "DELETE",
            "/compliance/erasure",
            json={"subject_id": "sub-001"},
        )

        assert response.status_code in {401, 403}
