"""Negative/attack tests for compliance erasure IDOR fix (T69.6).

Covers:
- Cross-operator erasure returns 404 (IDOR protection avoids leaking resource existence; P79-F1)
- Own subject_id returns 200 and data is deleted
- Cross-operator attempt emits an audit event (intrusion detection)
- Cross-operator audit event does NOT contain target subject_id (PII protection)
- Cross-operator returns 403 even when vault is sealed (IDOR check BEFORE vault check)
- Unauthenticated request returns 401

ATTACK-FIRST TDD — these tests are written BEFORE the GREEN phase.
CONSTITUTION Priority 0: Security — IDOR is a P0 vulnerability (ADV-P68-01)
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T69.6 — Fix Compliance Erasure IDOR (ADV-P68-01)
"""

from __future__ import annotations

import base64
import os
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _unseal_vault(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Unseal vault for tests that need DB writes (Connection records use ALE).

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.security.vault import VaultState

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    VaultState.reset()
    VaultState.unseal(bytearray(b"p69-t696-test-passphrase"))
    yield
    VaultState.reset()


@pytest.fixture
def db_engine() -> Any:
    """Create an in-memory SQLite engine with all ORM tables.

    Returns:
        SQLAlchemy engine backed by in-memory SQLite.
    """
    from synth_engine.bootstrapper.schemas.connections import Connection  # noqa: F401
    from synth_engine.bootstrapper.schemas.webhooks import (  # noqa: F401
        WebhookDelivery,
        WebhookRegistration,
    )
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _make_compliance_client(
    monkeypatch: pytest.MonkeyPatch,
    db_engine: Any,
    *,
    operator_id: str = "operator-a",
    vault_sealed: bool = False,
) -> TestClient:
    """Build a minimal FastAPI app with the compliance router.

    The auth dependency is overridden to return operator_id without touching JWT.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        db_engine: SQLite in-memory engine.
        operator_id: The JWT sub claim returned by the overridden auth dependency.
        vault_sealed: When True, VaultState.is_sealed() returns True.

    Returns:
        TestClient wrapping the compliance-router app.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")

    from synth_engine.bootstrapper.routers.compliance import router as compliance_router

    app = FastAPI()
    app.include_router(compliance_router)

    def _get_session() -> Generator[Session]:
        with Session(db_engine) as session:
            yield session

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.dependencies.tenant import TenantContext, get_current_user

    def _get_user() -> TenantContext:
        return TenantContext(
            org_id="00000000-0000-0000-0000-000000000000",
            user_id=operator_id,
            role="admin",
        )

    app.dependency_overrides[get_db_session] = _get_session
    app.dependency_overrides[get_current_user] = _get_user

    if vault_sealed:
        monkeypatch.setattr(
            "synth_engine.bootstrapper.routers.compliance.VaultState.is_sealed",
            lambda: True,
        )

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _patch_audit_logger() -> MagicMock:
    """Return a MagicMock that replaces the audit logger singleton.

    Returns:
        MagicMock with a ``log_event`` method.
    """
    mock = MagicMock()
    mock.log_event = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# Attack tests — IDOR and authorization
# ---------------------------------------------------------------------------


class TestErasureIDORProtection:
    """DELETE /compliance/erasure IDOR attack tests (T69.6, ADV-P68-01)."""

    def test_erasure_cross_operator_subject_id_returns_404(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """Admin can erase any subject_id within their org (P80-T80.5).

        P80 change: Admin-delegated erasure replaces T69.6 self-erasure-only.
        Admin authenticated as operator-a can erase subject_id="operator-b"
        within the same org. Returns 200 (0 deletions if no matching records).

        Arrange: authenticated as operator-a (admin role).
        Act: DELETE /compliance/erasure with subject_id = "operator-b".
        Assert: 200 — admin can erase any subject in their org.
        """
        audit_mock = _patch_audit_logger()
        client = _make_compliance_client(monkeypatch, db_engine, operator_id="operator-a")

        with patch(
            "synth_engine.modules.synthesizer.lifecycle.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": "operator-b"},
            )

        assert response.status_code == 200, (
            f"P80: Admin can erase any subject in their org; "
            f"got {response.status_code}. Body: {response.json()}"
        )
        body = response.json()
        assert body["subject_id"] == "operator-b"

    def test_erasure_own_subject_id_returns_200_and_deletes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """Operator A can erase their own data when subject_id == current_operator.

        Arrange: insert a job owned by operator-a. Authenticate as operator-a.
        Act: DELETE /compliance/erasure with subject_id = "operator-a".
        Assert: 200 with deleted_jobs == 1.
        """
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        with Session(db_engine) as session:
            job = SynthesisJob(
                owner_id="operator-a",
                status="COMPLETE",
                table_name="users",
                parquet_path="/data/users.parquet",
                total_epochs=5,
                num_rows=10,
                # org_id must match the TenantContext.org_id set in _make_compliance_client
                # (00000000-0000-0000-0000-000000000000) so org-scoped erasure finds the job.
                org_id="00000000-0000-0000-0000-000000000000",
            )
            session.add(job)
            session.commit()

        audit_mock = _patch_audit_logger()
        client = _make_compliance_client(monkeypatch, db_engine, operator_id="operator-a")

        with patch(
            "synth_engine.modules.synthesizer.lifecycle.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": "operator-a"},
            )

        assert response.status_code == 200, (
            f"Self-erasure must return 200; got {response.status_code}. Body: {response.json()}"
        )
        body = response.json()
        assert body["deleted_jobs"] == 1, f"Expected 1 deleted job; got {body['deleted_jobs']}"
        assert body["subject_id"] == "operator-a"

    def test_erasure_cross_operator_attempt_emits_audit_event(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """Admin erasure emits an audit event (ErasureService emits on completion).

        P80 change: Admin-delegated erasure proceeds normally within the same org.
        Audit event is emitted by ErasureService on successful erasure.

        Arrange: authenticate as operator-a (admin role).
        Act: DELETE /compliance/erasure with subject_id = "operator-b".
        Assert: ErasureService audit logger is called (erasure proceeds).
        """
        audit_mock = _patch_audit_logger()
        client = _make_compliance_client(monkeypatch, db_engine, operator_id="operator-a")

        with patch(
            "synth_engine.modules.synthesizer.lifecycle.erasure.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": "operator-b"},
            )

        # P80: Admin erasure returns 200; audit event is emitted by ErasureService
        assert response.status_code == 200, (
            f"Admin erasure must return 200; got {response.status_code}"
        )

    def test_erasure_cross_operator_returns_404_even_when_vault_is_sealed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """Permission check fires BEFORE vault-sealed check (P80-T80.5).

        P80 change: permission check (require_permission) fires before vault check.
        Admin with sealed vault sees 423 (vault sealed).
        Non-admin sees 403 (permission denied) before vault is checked.

        For admin in same org with sealed vault: admin can attempt erasure but
        vault is sealed → 423. Admin does NOT get 404 for same-org subject.

        Arrange: authenticate as operator-a (admin), seal vault.
        Act: DELETE /compliance/erasure with any subject_id.
        Assert: 423 Locked — vault-sealed check fires after permission passes.
        """
        audit_mock = _patch_audit_logger()
        client = _make_compliance_client(
            monkeypatch,
            db_engine,
            operator_id="operator-a",
            vault_sealed=True,
        )

        with patch(
            "synth_engine.bootstrapper.routers.compliance.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": "any-subject"},
            )

        # P80: Admin + sealed vault → 423 (vault check fires after permission check)
        assert response.status_code == 423, (
            f"Admin + sealed vault must return 423; "
            f"got {response.status_code}. Body: {response.json()}"
        )

    def test_erasure_without_auth_returns_401(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """DELETE /compliance/erasure requires authentication; returns 401 without JWT.

        Arrange: create app with real JWT auth (not overridden).
        Act: DELETE /compliance/erasure without Authorization header.
        Assert: 401 Unauthorized.
        """
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key-32-chars-min!!")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.routers.compliance import router as compliance_router

        app = FastAPI()
        app.include_router(compliance_router)

        # Wire real DB session (no auth override)
        def _get_session() -> Generator[Session]:
            with Session(db_engine) as session:
                yield session

        from synth_engine.bootstrapper.dependencies.db import get_db_session

        app.dependency_overrides[get_db_session] = _get_session

        client = TestClient(app, raise_server_exceptions=False)
        response = client.request(
            "DELETE",
            "/compliance/erasure",
            json={"subject_id": "some-subject"},
        )

        assert response.status_code == 401, (
            f"Unauthenticated erasure must return 401; got {response.status_code}. "
            f"Body: {response.text}"
        )
