"""Negative/attack tests for T70.7 (path param bounds), T70.8 (audit ordering),
and T70.9 (audit failure Prometheus counter).

ATTACK-FIRST TDD — these tests prove the system REJECTS invalid inputs and
enforces audit-before-mutation semantics.

CONSTITUTION Priority 0: Security — input validation and audit ordering are P0
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T70.7 — Drain Advisory: Unbounded Path Params (ADV-P68-03)
Task: T70.8 — Drain Advisory: Audit Ordering Consistency (ADV-P68-04)
Task: T70.9 — Drain Advisory: Audit Failure Prometheus Counter (ADV-P68-05)
"""

from __future__ import annotations

import base64
import os
from collections.abc import Generator
from contextlib import contextmanager
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
def clear_settings_cache() -> Generator[None, None, None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def unseal_vault_for_tests(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Unseal vault and reset after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.security.vault import VaultState

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    VaultState.reset()
    VaultState.unseal("test-passphrase-for-p70-tests")
    yield
    VaultState.reset()


@contextmanager  # type: ignore[arg-type]
def _bypass_middleware() -> Any:
    """Context manager that patches vault/license/rate-limit middleware to pass through.

    Yields:
        None
    """
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
        yield


def _make_connections_app() -> FastAPI:
    """Build a test FastAPI app wired with the connections router.

    Returns:
        FastAPI instance with connections router and SQLite in-memory DB.
    """
    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.connections import router as connections_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()
    app.include_router(connections_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    def _override_operator() -> str:
        return "test-operator"

    app.dependency_overrides[get_db_session] = _override_session
    app.dependency_overrides[get_current_operator] = _override_operator
    return app


def _make_webhooks_app() -> FastAPI:
    """Build a test FastAPI app wired with the webhooks router.

    Returns:
        FastAPI instance with webhooks router and SQLite in-memory DB.
    """
    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.webhooks import router as webhooks_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()
    app.include_router(webhooks_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    def _override_operator() -> str:
        return "test-operator"

    app.dependency_overrides[get_db_session] = _override_session
    app.dependency_overrides[get_current_operator] = _override_operator
    return app


def _make_jobs_app(engine: Any) -> FastAPI:
    """Build a test FastAPI app wired with the jobs router.

    Args:
        engine: SQLAlchemy engine for the test DB.

    Returns:
        FastAPI instance with jobs router.
    """
    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    app = create_app()
    app.include_router(jobs_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    def _override_operator() -> str:
        return "test-operator"

    app.dependency_overrides[get_db_session] = _override_session
    app.dependency_overrides[get_current_operator] = _override_operator
    return app


def _make_privacy_app(engine: Any) -> FastAPI:
    """Build a test FastAPI app wired with the privacy router.

    Args:
        engine: SQLAlchemy engine for the test DB.

    Returns:
        FastAPI instance with privacy router.
    """
    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.privacy import router as privacy_router

    app = create_app()
    app.include_router(privacy_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    def _override_operator() -> str:
        return "test-operator"

    app.dependency_overrides[get_db_session] = _override_session
    app.dependency_overrides[get_current_operator] = _override_operator
    return app


def _make_admin_app(engine: Any) -> FastAPI:
    """Build a test FastAPI app wired with the admin router.

    Args:
        engine: SQLAlchemy engine for the test DB.

    Returns:
        FastAPI instance with admin router.
    """
    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.admin import router as admin_router

    app = create_app()
    app.include_router(admin_router)

    def _override_session() -> Any:
        with Session(engine) as s:
            yield s

    def _override_operator() -> str:
        return "test-operator"

    app.dependency_overrides[get_db_session] = _override_session
    app.dependency_overrides[get_current_operator] = _override_operator
    return app


# ---------------------------------------------------------------------------
# T70.7 — Unbounded Path Params (ADV-P68-03)
# ---------------------------------------------------------------------------


class TestConnectionIdPathParamBounds:
    """T70.7: connection_id path param must be bounded at max_length=255."""

    def test_connection_id_oversized_returns_422(self) -> None:
        """An oversized connection_id (>255 chars) must return 422.

        This prevents potential DoS from unbounded path param processing.
        """
        app = _make_connections_app()
        client = TestClient(app, raise_server_exceptions=False)
        oversized_id = "a" * 256
        with _bypass_middleware():
            response = client.get(f"/connections/{oversized_id}")
        assert response.status_code == 422

    def test_connection_id_exactly_255_chars_accepted(self) -> None:
        """A connection_id of exactly 255 characters must be accepted (not rejected at 422).

        A 404 (not found) is the expected outcome for a valid-length but non-existent ID.
        The status code must NOT be 422.
        """
        app = _make_connections_app()
        client = TestClient(app, raise_server_exceptions=False)
        boundary_id = "a" * 255
        with _bypass_middleware():
            response = client.get(f"/connections/{boundary_id}")
        assert response.status_code != 422

    def test_connection_id_delete_oversized_returns_422(self) -> None:
        """DELETE with oversized connection_id must return 422."""
        app = _make_connections_app()
        client = TestClient(app, raise_server_exceptions=False)
        oversized_id = "b" * 300
        with _bypass_middleware():
            response = client.delete(f"/connections/{oversized_id}")
        assert response.status_code == 422


class TestWebhookIdPathParamBounds:
    """T70.7: webhook_id path param must be bounded at max_length=255."""

    def test_webhook_id_oversized_returns_422(self) -> None:
        """An oversized webhook_id (>255 chars) must return 422."""
        app = _make_webhooks_app()
        client = TestClient(app, raise_server_exceptions=False)
        oversized_id = "w" * 256
        with _bypass_middleware():
            response = client.delete(f"/webhooks/{oversized_id}")
        assert response.status_code == 422

    def test_webhook_id_exactly_255_chars_accepted(self) -> None:
        """A webhook_id of exactly 255 characters must not be rejected at 422.

        Expect 404 (not found) rather than 422 (validation failure).
        """
        app = _make_webhooks_app()
        client = TestClient(app, raise_server_exceptions=False)
        boundary_id = "w" * 255
        with _bypass_middleware():
            response = client.delete(f"/webhooks/{boundary_id}")
        assert response.status_code != 422

    def test_webhook_deliveries_oversized_webhook_id_returns_422(self) -> None:
        """GET /webhooks/{webhook_id}/deliveries with oversized ID must return 422."""
        app = _make_webhooks_app()
        client = TestClient(app, raise_server_exceptions=False)
        oversized_id = "x" * 300
        with _bypass_middleware():
            response = client.get(f"/webhooks/{oversized_id}/deliveries")
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# T70.8 — Audit Ordering: shred_job
# ---------------------------------------------------------------------------


class TestShredJobAuditOrdering:
    """T70.8: audit write MUST occur BEFORE artifact deletion in shred_job."""

    def _build_complete_job_engine(self) -> tuple[Any, int]:
        """Create an in-memory DB with a COMPLETE job.

        Returns:
            Tuple of (engine, job_id).
        """
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="test_table",
                parquet_path="/tmp/test.parquet",
                total_epochs=1,
                num_rows=10,
                checkpoint_every_n=1,
                enable_dp=False,
                status="COMPLETE",
                owner_id="test-operator",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id: int = job.id  # type: ignore[assignment]

        return engine, job_id

    def test_shred_job_audit_raised_returns_500_artifact_not_deleted(self) -> None:
        """If audit raises, shred_job must return 500 and NOT delete artifacts.

        Sequence under test:
            1. Ownership check passes
            2. Audit RAISES (simulated failure)
            3. shred_artifacts must NOT be called
            4. Response must be 500
        """
        engine, job_id = self._build_complete_job_engine()
        app = _make_jobs_app(engine)
        client = TestClient(app, raise_server_exceptions=False)

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("audit system unavailable")

        with (
            _bypass_middleware(),
            patch(
                "synth_engine.bootstrapper.routers.jobs.get_audit_logger",
                return_value=mock_audit,
            ),
            patch(
                "synth_engine.bootstrapper.routers.jobs.shred_artifacts"
            ) as mock_shred,
        ):
            response = client.post(f"/jobs/{job_id}/shred")

        assert response.status_code == 500
        mock_shred.assert_not_called()

    def test_shred_job_artifact_fails_after_audit_returns_500_with_compensating_event(
        self,
    ) -> None:
        """If artifact deletion fails AFTER audit succeeds, return 500 with compensating event.

        Sequence under test:
            1. Ownership check passes
            2. Audit write SUCCEEDS
            3. shred_artifacts RAISES OSError
            4. Compensating ARTIFACT_SHRED_FAILED audit event must be emitted
            5. Response must be 500
        """
        engine, job_id = self._build_complete_job_engine()
        app = _make_jobs_app(engine)
        client = TestClient(app, raise_server_exceptions=False)

        mock_audit = MagicMock()
        mock_audit.log_event.return_value = None

        with (
            _bypass_middleware(),
            patch(
                "synth_engine.bootstrapper.routers.jobs.get_audit_logger",
                return_value=mock_audit,
            ),
            patch(
                "synth_engine.bootstrapper.routers.jobs.shred_artifacts",
                side_effect=OSError("disk full"),
            ),
        ):
            response = client.post(f"/jobs/{job_id}/shred")

        assert response.status_code == 500
        # Verify a compensating audit event was emitted
        call_args_list = mock_audit.log_event.call_args_list
        event_types = [call.kwargs.get("event_type") or call.args[0] for call in call_args_list]
        assert "ARTIFACT_SHRED_FAILED" in event_types


# ---------------------------------------------------------------------------
# T70.8 — Audit Ordering: refresh_budget
# ---------------------------------------------------------------------------


class TestRefreshBudgetAuditOrdering:
    """T70.8: audit write MUST occur BEFORE budget reset in refresh_budget."""

    def _build_ledger_engine(self) -> Any:
        """Create an in-memory DB with a PrivacyLedger.

        Returns:
            SQLAlchemy engine with a PrivacyLedger row.
        """
        from decimal import Decimal

        from synth_engine.modules.privacy.ledger import PrivacyLedger

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            ledger = PrivacyLedger(
                total_allocated_epsilon=Decimal("10.0"),
                total_spent_epsilon=Decimal("3.0"),
            )
            session.add(ledger)
            session.commit()

        return engine

    def test_refresh_budget_audit_raised_returns_500_budget_unchanged(self) -> None:
        """If audit raises BEFORE reset, refresh_budget returns 500 and budget is unchanged.

        Sequence under test:
            1. Ledger found
            2. Audit write RAISES
            3. _run_reset_budget must NOT be called
            4. Response must be 500
        """
        engine = self._build_ledger_engine()
        app = _make_privacy_app(engine)
        client = TestClient(app, raise_server_exceptions=False)

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("audit offline")

        with (
            _bypass_middleware(),
            patch(
                "synth_engine.bootstrapper.routers.privacy.get_audit_logger",
                return_value=mock_audit,
            ),
            patch(
                "synth_engine.bootstrapper.routers.privacy._run_reset_budget"
            ) as mock_reset,
        ):
            response = client.post(
                "/privacy/budget/refresh",
                json={"justification": "test"},
            )

        assert response.status_code == 500
        mock_reset.assert_not_called()

    def test_refresh_budget_reset_fails_after_audit_returns_500_with_compensating_event(
        self,
    ) -> None:
        """If reset fails AFTER audit, return 500 with BUDGET_RESET_FAILED compensating event.

        Sequence under test:
            1. Audit write SUCCEEDS
            2. _run_reset_budget RAISES
            3. Compensating BUDGET_RESET_FAILED audit event must be emitted
            4. Response must be 500
        """
        engine = self._build_ledger_engine()
        app = _make_privacy_app(engine)
        client = TestClient(app, raise_server_exceptions=False)

        mock_audit = MagicMock()
        mock_audit.log_event.return_value = None

        with (
            _bypass_middleware(),
            patch(
                "synth_engine.bootstrapper.routers.privacy.get_audit_logger",
                return_value=mock_audit,
            ),
            patch(
                "synth_engine.bootstrapper.routers.privacy._run_reset_budget",
                side_effect=RuntimeError("DB connection lost"),
            ),
        ):
            response = client.post(
                "/privacy/budget/refresh",
                json={"justification": "test"},
            )

        assert response.status_code == 500
        call_args_list = mock_audit.log_event.call_args_list
        event_types = [call.kwargs.get("event_type") or call.args[0] for call in call_args_list]
        assert "BUDGET_RESET_FAILED" in event_types


# ---------------------------------------------------------------------------
# T70.9 — Audit Failure Prometheus Counter
# ---------------------------------------------------------------------------


class TestAuditWriteFailureCounter:
    """T70.9: AUDIT_WRITE_FAILURE_TOTAL counter must be incremented on audit failure."""

    def test_audit_write_failure_increments_counter_in_admin(self) -> None:
        """Audit failure in admin.py (legal-hold) must increment AUDIT_WRITE_FAILURE_TOTAL.

        The counter must be incremented by exactly 1 when the audit raises
        in the set_legal_hold endpoint.
        """
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="t",
                parquet_path="/tmp/t.parquet",
                total_epochs=1,
                num_rows=10,
                checkpoint_every_n=1,
                enable_dp=False,
                status="COMPLETE",
                owner_id="test-operator",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id: int = job.id  # type: ignore[assignment]

        app = _make_admin_app(engine)

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("audit unavailable")

        # Import the counter to read its value
        from synth_engine.bootstrapper.routers import admin as admin_module

        counter = admin_module.AUDIT_WRITE_FAILURE_TOTAL
        before = counter._value.get()

        client = TestClient(app, raise_server_exceptions=False)
        with (
            _bypass_middleware(),
            patch(
                "synth_engine.bootstrapper.routers.admin.get_audit_logger",
                return_value=mock_audit,
            ),
        ):
            response = client.patch(
                f"/admin/jobs/{job_id}/legal-hold",
                json={"enable": True},
            )

        assert response.status_code == 500
        after = counter._value.get()
        assert after == before + 1.0

    def test_audit_write_failure_increments_counter_in_security_shred(self) -> None:
        """Audit failure in security.py (shred_vault) must increment AUDIT_WRITE_FAILURE_TOTAL."""
        from synth_engine.bootstrapper.dependencies.auth import require_scope
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.security import router as security_router

        app = create_app()
        app.include_router(security_router)

        def _override_scope_dep() -> str:
            return "test-operator"

        app.dependency_overrides[require_scope("security:admin")] = _override_scope_dep

        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("audit unavailable")

        from synth_engine.bootstrapper.routers import security as security_module

        counter = security_module.AUDIT_WRITE_FAILURE_TOTAL
        before = counter._value.get()

        client = TestClient(app, raise_server_exceptions=False)
        with (
            _bypass_middleware(),
            patch(
                "synth_engine.bootstrapper.routers.security.get_audit_logger",
                return_value=mock_audit,
            ),
        ):
            response = client.post("/security/shred")

        assert response.status_code == 500
        after = counter._value.get()
        assert after == before + 1.0

    def test_audit_write_failure_counter_has_static_endpoint_label(self) -> None:
        """AUDIT_WRITE_FAILURE_TOTAL counter must use static route template labels.

        The label value must be a static path template, not a resolved path with
        real IDs. This bounds Prometheus cardinality to one value per route.
        """
        from synth_engine.bootstrapper.routers import admin as admin_module

        counter = admin_module.AUDIT_WRITE_FAILURE_TOTAL
        # The counter must have an 'endpoint' label defined in its specification
        assert "endpoint" in counter._labelnames
