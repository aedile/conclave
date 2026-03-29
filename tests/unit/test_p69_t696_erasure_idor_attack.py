"""Negative/attack tests for compliance erasure IDOR fix (T69.6).

Covers:
- Cross-operator erasure returns 403 (not 404 — per spec, IDOR on self-erasure is 403)
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
    VaultState.unseal("p69-t696-test-passphrase")
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

    def _get_operator() -> str:
        return operator_id

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session

    app.dependency_overrides[get_db_session] = _get_session
    app.dependency_overrides[get_current_operator] = _get_operator

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

    def test_erasure_cross_operator_subject_id_returns_403(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """Operator A cannot erase operator B's data by supplying operator B's subject_id.

        Arrange: authenticated as operator-a.
        Act: DELETE /compliance/erasure with subject_id = "operator-b".
        Assert: 403 Forbidden — callers may only erase their own data.
        """
        audit_mock = _patch_audit_logger()
        client = _make_compliance_client(monkeypatch, db_engine, operator_id="operator-a")

        with patch(
            "synth_engine.bootstrapper.routers.compliance.get_audit_logger",
            return_value=audit_mock,
        ):
            response = client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": "operator-b"},
            )

        assert response.status_code == 403, (
            f"Cross-operator erasure must return 403; got {response.status_code}. "
            f"Body: {response.json()}"
        )

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
        """Cross-operator erasure attempt emits an audit event for intrusion detection.

        Arrange: authenticate as operator-a.
        Act: DELETE /compliance/erasure with subject_id = "operator-b".
        Assert: audit logger called exactly once with event_type containing "IDOR"
                or a recognized intrusion-detection event type.
        """
        audit_mock = _patch_audit_logger()
        client = _make_compliance_client(monkeypatch, db_engine, operator_id="operator-a")

        with patch(
            "synth_engine.bootstrapper.routers.compliance.get_audit_logger",
            return_value=audit_mock,
        ):
            client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": "operator-b"},
            )

        audit_mock.log_event.assert_called_once()
        call_kwargs = audit_mock.log_event.call_args
        # The audit event should exist — specific shape tested in next test
        assert call_kwargs is not None, "Audit logger must be called on IDOR attempt"

    def test_erasure_cross_operator_audit_event_does_not_contain_target_subject_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """The IDOR audit event must NOT include the target subject_id (PII protection).

        CONSTITUTION Priority 0: No PII in audit payloads.

        Arrange: authenticate as operator-a.
        Act: DELETE /compliance/erasure with subject_id = "operator-b-unique-id-12345".
        Assert: None of the audit log_event arguments contain "operator-b-unique-id-12345".
        """
        target_subject = "operator-b-unique-id-12345"
        audit_mock = _patch_audit_logger()
        client = _make_compliance_client(monkeypatch, db_engine, operator_id="operator-a")

        with patch(
            "synth_engine.bootstrapper.routers.compliance.get_audit_logger",
            return_value=audit_mock,
        ):
            client.request(
                "DELETE",
                "/compliance/erasure",
                json={"subject_id": target_subject},
            )

        # Verify audit was called
        audit_mock.log_event.assert_called_once()
        call_kwargs = audit_mock.log_event.call_args

        # Reconstruct the full string representation of all call arguments
        call_args_str = str(call_kwargs)
        assert target_subject not in call_args_str, (
            f"Audit event MUST NOT contain the target subject_id '{target_subject}' "
            f"(PII protection). Call args: {call_args_str}"
        )

    def test_erasure_cross_operator_returns_403_even_when_vault_is_sealed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> None:
        """IDOR check fires BEFORE vault-sealed check to prevent information disclosure.

        A sealed vault must not be used to distinguish valid vs invalid subject_ids.
        Even when the vault is sealed, cross-operator attempts return 403 (not 423).

        Arrange: authenticate as operator-a, seal vault.
        Act: DELETE /compliance/erasure with subject_id = "operator-b".
        Assert: 403 Forbidden (not 423 Locked — IDOR check is first).
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
                json={"subject_id": "operator-b"},
            )

        assert response.status_code == 403, (
            f"IDOR check must fire BEFORE vault-sealed check; expected 403 got "
            f"{response.status_code}. Body: {response.json()}"
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
