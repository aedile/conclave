"""Negative/attack tests for admin RBAC and audit-before-destructive (T68.2, T68.3).

Covers:
- T68.2: Admin set_legal_hold IDOR — operator B cannot access operator A's job
- T68.3: Audit must succeed before any destructive operation proceeds

ATTACK-FIRST TDD — these tests are written before the GREEN phase.
CONSTITUTION Priority 0: Security — IDOR and audit-bypass are P0 vulnerabilities
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T68.2 — RBAC Guard on Admin Endpoints
Task: T68.3 — Mandatory Audit Before Destructive Operations
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
def clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_vault_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Unseal vault for tests that need it and reset after.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.security.vault import VaultState

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    VaultState.reset()
    yield
    VaultState.reset()


@pytest.fixture
def db_engine() -> Any:
    """Create an in-memory SQLite engine for admin tests.

    Returns:
        SQLAlchemy engine with all tables created.
    """
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def admin_client(
    monkeypatch: pytest.MonkeyPatch,
    db_engine: Any,
) -> TestClient:
    """Build a minimal FastAPI app with the admin router (pass-through auth).

    Args:
        monkeypatch: pytest monkeypatch fixture.
        db_engine: SQLite in-memory engine.

    Returns:
        TestClient wrapping the admin-router app.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")

    from synth_engine.bootstrapper.routers.admin import router as admin_router

    app = FastAPI()
    app.include_router(admin_router)

    def _get_session() -> Generator[Session]:
        with Session(db_engine) as session:
            yield session

    def _get_operator() -> str:
        return "operator-a"

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session

    app.dependency_overrides[get_db_session] = _get_session
    app.dependency_overrides[get_current_operator] = _get_operator

    return TestClient(app, raise_server_exceptions=False)


def _create_job_for_owner(session: Session, owner_id: str) -> int:
    """Insert a SynthesisJob owned by owner_id and return its primary key.

    Args:
        session: Active SQLModel session.
        owner_id: The operator sub claim that owns the job.

    Returns:
        The integer primary key of the created job.
    """
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    job = SynthesisJob(
        owner_id=owner_id,
        status="COMPLETE",
        table_name="users",
        parquet_path="/data/users.parquet",
        total_epochs=10,
        num_rows=100,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    assert job.id is not None
    return int(job.id)


# ---------------------------------------------------------------------------
# T68.2: Admin IDOR tests
# ---------------------------------------------------------------------------


class TestAdminRBACOwnershipCheck:
    """Admin set_legal_hold must enforce ownership-scoping."""

    def test_set_legal_hold_rejects_wrong_owner(
        self,
        db_engine: Any,
        admin_client: TestClient,
    ) -> None:
        """Operator B's token cannot set legal hold on operator A's job.

        Arrange: create a job owned by operator-b (different from operator-a who is
        authenticated via the admin_client fixture).
        Act: PATCH /admin/jobs/{id}/legal-hold with operator-a's credentials.
        Assert: 404 — resource not found (not 403, to avoid leaking existence).
        """
        with Session(db_engine) as session:
            job_id = _create_job_for_owner(session, owner_id="operator-b")

        response = admin_client.patch(
            f"/admin/jobs/{job_id}/legal-hold",
            json={"enable": True},
        )
        assert response.status_code == 404, (
            f"Operator B's job must return 404 for operator A; got {response.status_code}. "
            f"Body: {response.json()}"
        )

    def test_set_legal_hold_empty_owner_id_not_bypassed(
        self,
        db_engine: Any,
        admin_client: TestClient,
    ) -> None:
        """A job with owner_id='' must not be accessible by arbitrary operators.

        An empty owner_id could be treated as a wildcard — it must not be.
        Operator-a (the authenticated operator) must get 404 for a job with
        an empty owner_id (since '' != 'operator-a').
        """
        with Session(db_engine) as session:
            job_id = _create_job_for_owner(session, owner_id="")

        response = admin_client.patch(
            f"/admin/jobs/{job_id}/legal-hold",
            json={"enable": True},
        )
        assert response.status_code == 404, (
            f"Job with empty owner_id must return 404 for any authenticated operator; "
            f"got {response.status_code}. Body: {response.json()}"
        )

    def test_set_legal_hold_nonexistent_job_returns_404(
        self,
        admin_client: TestClient,
    ) -> None:
        """Regression: non-existent job_id must still return 404.

        Verifies the ownership check does not break the existing 404-on-missing-job path.
        """
        response = admin_client.patch(
            "/admin/jobs/99999999/legal-hold",
            json={"enable": True},
        )
        assert response.status_code == 404, (
            f"Non-existent job must return 404; got {response.status_code}"
        )

    def test_set_legal_hold_single_operator_can_access_own_job(
        self,
        db_engine: Any,
        admin_client: TestClient,
    ) -> None:
        """Authenticated operator can set legal hold on their own job.

        Arrange: create a job owned by operator-a (same as authenticated operator).
        Act: PATCH /admin/jobs/{id}/legal-hold.
        Assert: 200 with updated legal_hold value.
        """
        with Session(db_engine) as session:
            job_id = _create_job_for_owner(session, owner_id="operator-a")

        with patch(
            "synth_engine.bootstrapper.routers.admin.get_audit_logger",
            return_value=MagicMock(log_event=MagicMock()),
        ):
            response = admin_client.patch(
                f"/admin/jobs/{job_id}/legal-hold",
                json={"enable": True},
            )

        assert response.status_code == 200, (
            f"Operator should access their own job; got {response.status_code}. "
            f"Body: {response.json()}"
        )
        body = response.json()
        assert body["job_id"] == job_id
        assert body["legal_hold"] is True


# ---------------------------------------------------------------------------
# T68.3: Audit-before-destructive tests
# ---------------------------------------------------------------------------


class TestAuditBeforeDestructiveShred:
    """POST /security/shred must not proceed if audit write fails."""

    @pytest.fixture
    def security_client(self) -> TestClient:
        """Build a minimal FastAPI app with only the security router.

        Returns:
            TestClient wrapping the security-router app.
        """
        from synth_engine.bootstrapper.dependencies.auth import require_scope
        from synth_engine.bootstrapper.routers.security import router as security_router

        app = FastAPI()
        app.include_router(security_router)
        app.dependency_overrides[require_scope("security:admin")] = lambda: "test-operator"

        return TestClient(app, raise_server_exceptions=False)

    def test_shred_audit_fail_returns_500_and_vault_remains_unsealed(
        self,
        security_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If audit write raises ANY exception, shred must return 500 and NOT seal vault.

        Arrange: unseal the vault; mock audit to raise RuntimeError.
        Act: POST /security/shred.
        Assert: response status is 500.
        Assert: vault is NOT sealed (shred did not proceed).
        """
        from synth_engine.shared.security.vault import VaultState

        # Unseal the vault before the test
        VaultState.unseal(bytearray(b"test-passphrase-for-shred"))
        assert not VaultState.is_sealed(), "vault must be unsealed before shred"

        with patch(
            "synth_engine.bootstrapper.routers.security.get_audit_logger",
            return_value=MagicMock(
                log_event=MagicMock(side_effect=RuntimeError("audit backend unavailable"))
            ),
        ):
            response = security_client.post("/security/shred")

        assert response.status_code == 500, (
            f"Audit failure must return 500; got {response.status_code}. Body: {response.json()}"
        )
        assert not VaultState.is_sealed(), (
            "Vault must NOT be sealed when audit write fails — shred must not proceed"
        )


class TestAuditBeforeDestructiveKeyRotation:
    """POST /security/keys/rotate must not enqueue task if audit write fails."""

    @pytest.fixture
    def security_client(self) -> TestClient:
        """Build a minimal FastAPI app with only the security router.

        Returns:
            TestClient wrapping the security-router app.
        """
        from synth_engine.bootstrapper.dependencies.auth import require_scope
        from synth_engine.bootstrapper.routers.security import router as security_router

        app = FastAPI()
        app.include_router(security_router)
        app.dependency_overrides[require_scope("security:admin")] = lambda: "test-operator"

        return TestClient(app, raise_server_exceptions=False)

    def test_key_rotation_audit_fail_returns_500_and_task_not_enqueued(
        self,
        security_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If audit write raises ANY exception, rotation must return 500, task NOT enqueued.

        Arrange: unseal vault; mock audit to raise RuntimeError; mock Huey task.
        Act: POST /security/keys/rotate.
        Assert: response status is 500.
        Assert: Huey task was NOT called.
        """
        from synth_engine.shared.security.vault import VaultState

        VaultState.unseal(bytearray(b"test-passphrase-rotation"))

        mock_task = MagicMock()

        with (
            patch(
                "synth_engine.bootstrapper.routers.security.get_audit_logger",
                return_value=MagicMock(
                    log_event=MagicMock(side_effect=RuntimeError("audit unavailable"))
                ),
            ),
            patch(
                "synth_engine.bootstrapper.routers.security.rotate_ale_keys_task",
                mock_task,
            ),
            patch(
                "synth_engine.bootstrapper.routers.security.get_fernet",
                return_value=MagicMock(encrypt=MagicMock(return_value=b"wrapped-key")),
            ),
        ):
            response = security_client.post(
                "/security/keys/rotate",
                json={"new_passphrase": "new-secure-passphrase-for-rotation"},
            )

        assert response.status_code == 500, (
            f"Audit failure must return 500; got {response.status_code}. Body: {response.json()}"
        )
        (
            mock_task.assert_not_called(),
            ("Huey rotation task must NOT be enqueued when audit write fails"),
        )


class TestAuditBeforeDestructiveLegalHold:
    """Admin set_legal_hold must roll back DB commit if audit write fails."""

    @pytest.fixture
    def legal_hold_client(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_engine: Any,
    ) -> TestClient:
        """Build admin app wired to operator-a, yielding the TestClient.

        Args:
            monkeypatch: pytest monkeypatch fixture.
            db_engine: In-memory SQLite engine.

        Returns:
            TestClient for the admin router.
        """
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.bootstrapper.dependencies.auth import get_current_operator
        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.routers.admin import router as admin_router

        app = FastAPI()
        app.include_router(admin_router)

        def _get_session() -> Generator[Session]:
            with Session(db_engine) as session:
                yield session

        def _get_operator() -> str:
            return "operator-a"

        app.dependency_overrides[get_db_session] = _get_session
        app.dependency_overrides[get_current_operator] = _get_operator

        return TestClient(app, raise_server_exceptions=False)

    def test_legal_hold_audit_fail_db_rolled_back(
        self,
        db_engine: Any,
        legal_hold_client: TestClient,
    ) -> None:
        """If audit write raises, DB commit must be rolled back (legal hold unchanged).

        Arrange: create a job with legal_hold=False; mock audit to raise.
        Act: PATCH /admin/jobs/{id}/legal-hold with enable=True.
        Assert: response is 500.
        Assert: job.legal_hold is still False in the database.
        """
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        with Session(db_engine) as session:
            job_id = _create_job_for_owner(session, owner_id="operator-a")

        with patch(
            "synth_engine.bootstrapper.routers.admin.get_audit_logger",
            return_value=MagicMock(
                log_event=MagicMock(side_effect=RuntimeError("audit backend down"))
            ),
        ):
            response = legal_hold_client.patch(
                f"/admin/jobs/{job_id}/legal-hold",
                json={"enable": True},
            )

        assert response.status_code == 500, (
            f"Audit failure must return 500; got {response.status_code}. Body: {response.json()}"
        )

        # Verify DB was rolled back (legal_hold still False)
        with Session(db_engine) as session:
            job = session.get(SynthesisJob, job_id)
            assert job is not None
            assert job.legal_hold is False, (
                f"legal_hold must remain False after audit failure; got {job.legal_hold}"
            )

    def test_destructive_ops_audit_fail_catches_any_exception_type(
        self,
        db_engine: Any,
        legal_hold_client: TestClient,
    ) -> None:
        """Any exception from audit (not just RuntimeError) must return 500.

        Ensures the fix catches BaseException subclasses beyond RuntimeError —
        e.g. IOError, OSError, ConnectionError.
        """
        with Session(db_engine) as session:
            job_id = _create_job_for_owner(session, owner_id="operator-a")

        for exc_type in (IOError, OSError, ConnectionError, ValueError):
            with patch(
                "synth_engine.bootstrapper.routers.admin.get_audit_logger",
                return_value=MagicMock(log_event=MagicMock(side_effect=exc_type("audit error"))),
            ):
                response = legal_hold_client.patch(
                    f"/admin/jobs/{job_id}/legal-hold",
                    json={"enable": True},
                )
            assert response.status_code == 500, (
                f"Audit {exc_type.__name__} must return 500; got {response.status_code}"
            )

    def test_shred_requires_security_admin_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /security/shred must require security:admin scope (regression check).

        Verifies the scope enforcement is preserved after T68.3 refactor.
        Requires JWT_SECRET_KEY so that require_scope activates enforcement mode
        (dev pass-through is not active when JWT is configured).
        """
        from synth_engine.shared.settings import get_settings

        monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key-32-chars-minimum!!")
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        get_settings.cache_clear()

        from synth_engine.bootstrapper.routers.security import router as security_router

        app = FastAPI()
        app.include_router(security_router)
        # No dependency overrides — scope enforcement must activate

        client = TestClient(app, raise_server_exceptions=False)

        # Request without Authorization header (JWT configured, so auth fires)
        response = client.post("/security/shred")
        # Must get 401 — missing Bearer token
        assert response.status_code == 401, (
            f"POST /security/shred without auth must return 401; got {response.status_code}"
        )

    def test_legal_hold_audit_order_audit_before_commit(
        self,
        db_engine: Any,
        legal_hold_client: TestClient,
    ) -> None:
        """Verify that audit is called BEFORE DB commit, not after.

        If audit happens after commit, a failure would leave a committed change
        without an audit trail.

        Approach: mock audit to capture call order vs session.commit().
        If audit raises, DB must be unchanged — proving audit was attempted before commit.
        """
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        with Session(db_engine) as session:
            job_id = _create_job_for_owner(session, owner_id="operator-a")

        # Audit raises immediately — if DB was already committed, job would be changed
        with patch(
            "synth_engine.bootstrapper.routers.admin.get_audit_logger",
            return_value=MagicMock(log_event=MagicMock(side_effect=Exception("audit failure"))),
        ):
            response = legal_hold_client.patch(
                f"/admin/jobs/{job_id}/legal-hold",
                json={"enable": True},
            )

        assert response.status_code == 500

        # If audit happens after commit, legal_hold would be True; if before, False
        with Session(db_engine) as session:
            job = session.get(SynthesisJob, job_id)
            assert job is not None
            assert job.legal_hold is False, (
                "Audit must be called BEFORE DB commit — if legal_hold changed, "
                "audit happened after commit (wrong order)"
            )
