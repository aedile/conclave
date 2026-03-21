"""Unit tests for the Privacy Budget Management API.

Tests cover GET /privacy/budget and POST /privacy/budget/refresh, verifying:
- Correct field values and HTTP status codes
- RFC 7807 error format on failure paths
- WORM HMAC-signed audit event emission on refresh
- Schema validation (BudgetResponse, BudgetRefreshRequest)

Task: P22-T22.4 — Budget Management API
CONSTITUTION Priority 3: TDD
"""

from __future__ import annotations

import os
from collections.abc import Generator
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_audit_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Set a valid AUDIT_KEY env var and reset the singleton after each test.

    Required because :func:`~synth_engine.shared.security.audit.get_audit_logger`
    reads ``AUDIT_KEY`` on first call.  Without this fixture, tests that call
    the real audit path raise ``ValueError``.

    Yields:
        None — setup/teardown fixture with no yielded value.
    """
    monkeypatch.setenv("AUDIT_KEY", os.urandom(32).hex())
    yield
    from synth_engine.shared.security.audit import reset_audit_logger

    reset_audit_logger()


# ---------------------------------------------------------------------------
# Test app factories
# ---------------------------------------------------------------------------


def _make_test_app() -> tuple[Any, Any]:
    """Build a test FastAPI app with an in-memory SQLite database and seeded ledger.

    Overrides ``get_current_operator`` to bypass JWT auth in functional tests
    that are not testing authentication itself (ADV-024).

    Returns:
        A 2-tuple of ``(app, engine)`` ready for use in HTTP tests.
    """
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.privacy import router as privacy_router
    from synth_engine.modules.privacy.ledger import PrivacyLedger

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    # Seed a default ledger row
    with Session(engine) as session:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("10.0"),
            total_spent_epsilon=Decimal("3.5"),
        )
        session.add(ledger)
        session.commit()

    app = create_app()
    register_error_handlers(app)
    app.include_router(privacy_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    # Override auth for non-auth-focused tests — they test budget logic, not authn
    app.dependency_overrides[get_current_operator] = lambda: "test-operator"
    return app, engine


def _make_empty_app() -> tuple[Any, Any]:
    """Build a test FastAPI app with an in-memory SQLite database and NO ledger row.

    Overrides ``get_current_operator`` to bypass JWT auth in functional tests
    that are not testing authentication itself (ADV-024).

    Returns:
        A 2-tuple of ``(app, engine)`` for testing missing-ledger error paths.
    """
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.privacy import router as privacy_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()
    register_error_handlers(app)
    app.include_router(privacy_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    # Override auth for non-auth-focused tests
    app.dependency_overrides[get_current_operator] = lambda: "test-operator"
    return app, engine


def _common_patches() -> list[Any]:
    """Return common mock patches shared by all endpoint tests.

    Returns:
        List of patch context managers for vault-seal and licensing checks.
    """
    return [
        patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ),
        patch(
            "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
            return_value=True,
        ),
    ]


def _make_reset_budget_patch(engine: Any) -> Any:
    """Return a patch for ``_run_reset_budget`` that uses the given sync engine.

    The router's ``_run_reset_budget`` opens its own async engine. In unit tests
    we redirect it to run against the test's in-memory SQLite engine instead,
    using the sync SQLModel session directly.

    Args:
        engine: The SQLAlchemy sync engine backing the test's in-memory DB.

    Returns:
        A ``unittest.mock.patch`` context manager for
        ``synth_engine.bootstrapper.routers.privacy._run_reset_budget``.
    """
    from synth_engine.modules.privacy.ledger import PrivacyLedger

    def _fake_run_reset_budget(
        *,
        ledger_id: int,
        new_allocated_epsilon: Decimal | None,
    ) -> tuple[Decimal, Decimal]:
        with Session(engine) as s:
            ledger = s.get(PrivacyLedger, ledger_id)
            if ledger is None:
                from sqlalchemy.exc import NoResultFound

                raise NoResultFound(f"No PrivacyLedger with id={ledger_id}")
            ledger.total_spent_epsilon = Decimal("0.0")
            if new_allocated_epsilon is not None:
                ledger.total_allocated_epsilon = new_allocated_epsilon
            s.add(ledger)
            s.commit()
            s.refresh(ledger)
            return ledger.total_allocated_epsilon, ledger.total_spent_epsilon

    return patch(
        "synth_engine.bootstrapper.routers.privacy._run_reset_budget",
        side_effect=_fake_run_reset_budget,
    )


# ---------------------------------------------------------------------------
# GET /privacy/budget
# ---------------------------------------------------------------------------


class TestBudgetQueryEndpoint:
    """Tests for GET /privacy/budget."""

    @pytest.mark.asyncio
    async def test_get_budget_returns_200(self) -> None:
        """GET /privacy/budget must return HTTP 200."""
        app, _ = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/privacy/budget")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_budget_returns_required_fields(self) -> None:
        """GET /privacy/budget must return all required ledger state fields."""
        app, _ = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/privacy/budget")

        body = response.json()
        assert "total_allocated_epsilon" in body
        assert "total_spent_epsilon" in body
        assert "remaining_epsilon" in body
        assert "is_exhausted" in body

    @pytest.mark.asyncio
    async def test_get_budget_returns_correct_values(self) -> None:
        """GET /privacy/budget must return values matching the seeded ledger."""
        app, _ = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/privacy/budget")

        body = response.json()
        assert body["total_allocated_epsilon"] == pytest.approx(10.0)
        assert body["total_spent_epsilon"] == pytest.approx(3.5)
        assert body["remaining_epsilon"] == pytest.approx(6.5)
        assert body["is_exhausted"] is False

    @pytest.mark.asyncio
    async def test_get_budget_is_exhausted_true_when_spent_equals_allocated(
        self,
    ) -> None:
        """GET /privacy/budget must set is_exhausted=True when spent >= allocated."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.auth import get_current_operator
        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.privacy import router as privacy_router
        from synth_engine.modules.privacy.ledger import PrivacyLedger

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            ledger = PrivacyLedger(
                total_allocated_epsilon=Decimal("5.0"),
                total_spent_epsilon=Decimal("5.0"),
            )
            session.add(ledger)
            session.commit()

        app = create_app()
        register_error_handlers(app)
        app.include_router(privacy_router)

        def _override() -> Any:
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_db_session] = _override
        app.dependency_overrides[get_current_operator] = lambda: "test-operator"

        patches = _common_patches()
        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/privacy/budget")

        body = response.json()
        assert body["is_exhausted"] is True
        assert body["remaining_epsilon"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_get_budget_is_exhausted_true_when_spent_exceeds_allocated(
        self,
    ) -> None:
        """GET /privacy/budget must set is_exhausted=True when spent > allocated (overspend)."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.auth import get_current_operator
        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.privacy import router as privacy_router
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
                total_spent_epsilon=Decimal("15.0"),
            )
            session.add(ledger)
            session.commit()

        app = create_app()
        register_error_handlers(app)
        app.include_router(privacy_router)

        def _override() -> Any:
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_db_session] = _override
        app.dependency_overrides[get_current_operator] = lambda: "test-operator"

        patches = _common_patches()
        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/privacy/budget")

        body = response.json()
        assert body["is_exhausted"] is True
        assert body["remaining_epsilon"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_get_budget_returns_404_when_no_ledger(self) -> None:
        """GET /privacy/budget must return RFC 7807 404 when no ledger row exists."""
        app, _ = _make_empty_app()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/privacy/budget")

        assert response.status_code == 404
        body = response.json()
        assert body.get("status") == 404
        assert "type" in body
        assert "title" in body
        assert "detail" in body


# ---------------------------------------------------------------------------
# POST /privacy/budget/refresh
# ---------------------------------------------------------------------------


class TestBudgetRefreshEndpoint:
    """Tests for POST /privacy/budget/refresh."""

    @pytest.mark.asyncio
    async def test_refresh_budget_returns_200(self) -> None:
        """POST /privacy/budget/refresh must return HTTP 200 on success."""
        app, engine = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1], _make_reset_budget_patch(engine):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "Monthly budget refresh by admin"},
                )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_refresh_budget_resets_spent_to_zero(self) -> None:
        """POST /privacy/budget/refresh must reset total_spent_epsilon to 0."""
        app, engine = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1], _make_reset_budget_patch(engine):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "Monthly budget refresh by admin"},
                )

        body = response.json()
        assert body["total_spent_epsilon"] == pytest.approx(0.0)
        assert body["is_exhausted"] is False

    @pytest.mark.asyncio
    async def test_refresh_budget_preserves_allocated(self) -> None:
        """POST /privacy/budget/refresh without new_allocated keeps original value."""
        app, engine = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1], _make_reset_budget_patch(engine):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "Monthly budget refresh by admin"},
                )

        body = response.json()
        assert body["total_allocated_epsilon"] == pytest.approx(10.0)
        assert body["remaining_epsilon"] == pytest.approx(10.0)

    @pytest.mark.asyncio
    async def test_refresh_budget_with_new_allocated_sets_new_value(self) -> None:
        """POST /privacy/budget/refresh with new_allocated_epsilon sets new budget."""
        app, engine = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1], _make_reset_budget_patch(engine):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={
                        "justification": "Q2 allocation increase by admin",
                        "new_allocated_epsilon": 20.0,
                    },
                )

        body = response.json()
        assert body["total_allocated_epsilon"] == pytest.approx(20.0)
        assert body["total_spent_epsilon"] == pytest.approx(0.0)
        assert body["remaining_epsilon"] == pytest.approx(20.0)

    @pytest.mark.asyncio
    async def test_refresh_budget_requires_justification(self) -> None:
        """POST /privacy/budget/refresh without justification returns 422."""
        app, _ = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={},
                )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_refresh_budget_rejects_short_justification(self) -> None:
        """POST /privacy/budget/refresh with justification < 10 chars returns 422."""
        app, _ = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "short"},
                )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_refresh_budget_rejects_zero_new_allocated(self) -> None:
        """POST /privacy/budget/refresh with new_allocated_epsilon=0.0 returns 422."""
        app, _ = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={
                        "justification": "Testing zero allocation rejection",
                        "new_allocated_epsilon": 0.0,
                    },
                )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_refresh_budget_rejects_negative_new_allocated(self) -> None:
        """POST /privacy/budget/refresh with new_allocated_epsilon=-5.0 returns 422."""
        app, _ = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={
                        "justification": "Testing negative allocation rejection",
                        "new_allocated_epsilon": -5.0,
                    },
                )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_refresh_budget_emits_worm_audit_event(self) -> None:
        """POST /privacy/budget/refresh must emit a WORM HMAC-signed audit event."""
        app, engine = _make_test_app()
        patches = _common_patches()

        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        with (
            patches[0],
            patches[1],
            _make_reset_budget_patch(engine),
            patch(
                "synth_engine.bootstrapper.routers.privacy.get_audit_logger",
                return_value=mock_audit,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "Audit test justification"},
                )

        mock_audit.log_event.assert_called_once()
        call_kwargs = mock_audit.log_event.call_args.kwargs
        assert call_kwargs["event_type"] == "PRIVACY_BUDGET_REFRESH"
        assert call_kwargs["action"] == "refresh_budget"
        assert "justification" in call_kwargs["details"]
        assert call_kwargs["details"]["justification"] == "Audit test justification"
        assert call_kwargs["resource"].startswith("privacy_ledger/")

    @pytest.mark.asyncio
    async def test_refresh_budget_audit_event_uses_current_operator_as_actor(
        self,
    ) -> None:
        """Refresh audit event must capture current_operator (JWT sub) as actor.

        The privacy router was updated (ADV-024) to use the JWT sub via
        get_current_operator rather than the X-Operator-Id header.  This test
        verifies the dependency-overridden operator identity is used.
        """
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.auth import get_current_operator
        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.privacy import router as privacy_router
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
                total_spent_epsilon=Decimal("3.5"),
            )
            session.add(ledger)
            session.commit()

        app = create_app()
        register_error_handlers(app)
        app.include_router(privacy_router)

        def _override_session() -> Any:
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_db_session] = _override_session
        # Inject a specific operator identity via dependency override
        app.dependency_overrides[get_current_operator] = lambda: "specific-operator-id"

        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        patches = _common_patches()
        with (
            patches[0],
            patches[1],
            _make_reset_budget_patch(engine),
            patch(
                "synth_engine.bootstrapper.routers.privacy.get_audit_logger",
                return_value=mock_audit,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "Operator identity test"},
                )

        call_kwargs = mock_audit.log_event.call_args.kwargs
        assert call_kwargs["actor"] == "specific-operator-id"

    @pytest.mark.asyncio
    async def test_refresh_budget_returns_404_when_no_ledger(self) -> None:
        """POST /privacy/budget/refresh must return RFC 7807 404 when no ledger exists."""
        app, _ = _make_empty_app()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "This ledger does not exist yet"},
                )

        assert response.status_code == 404
        body = response.json()
        assert body.get("status") == 404
        assert "type" in body

    @pytest.mark.asyncio
    async def test_refresh_budget_returns_500_when_audit_fails(self) -> None:
        """POST /privacy/budget/refresh returns 500 if audit emission raises."""
        app, engine = _make_test_app()
        patches = _common_patches()

        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock(side_effect=RuntimeError("audit backend down"))

        with (
            patches[0],
            patches[1],
            _make_reset_budget_patch(engine),
            patch(
                "synth_engine.bootstrapper.routers.privacy.get_audit_logger",
                return_value=mock_audit,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "Testing audit failure path"},
                )

        assert response.status_code == 500

        # Verify the DB write was committed: ledger spent should be 0
        from synth_engine.modules.privacy.ledger import PrivacyLedger

        with Session(engine) as s:
            ledger = s.exec(
                __import__("sqlmodel", fromlist=["select"]).select(PrivacyLedger)
            ).first()
            assert ledger is not None
            assert ledger.total_spent_epsilon == Decimal("0.0")


# ---------------------------------------------------------------------------
# RFC 7807 error format conformance
# ---------------------------------------------------------------------------


class TestBudgetRFC7807ErrorFormat:
    """Tests confirming RFC 7807 error format is used on failure cases."""

    @pytest.mark.asyncio
    async def test_get_budget_404_has_rfc7807_type_field(self) -> None:
        """404 response from GET /privacy/budget must have RFC 7807 'type' field."""
        app, _ = _make_empty_app()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/privacy/budget")

        body = response.json()
        assert body["type"] == "about:blank"

    @pytest.mark.asyncio
    async def test_refresh_422_has_detail_field(self) -> None:
        """422 validation error from POST /privacy/budget/refresh has detail field."""
        app, _ = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "tiny"},
                )

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body


# ---------------------------------------------------------------------------
# Schema unit tests (no HTTP layer)
# ---------------------------------------------------------------------------


class TestBudgetSchemas:
    """Tests for schema validation behaviour (BudgetRefreshRequest, BudgetResponse)."""

    def test_budget_refresh_request_requires_justification(self) -> None:
        """BudgetRefreshRequest must raise ValidationError if justification is absent."""
        from pydantic import ValidationError

        from synth_engine.bootstrapper.schemas.privacy import BudgetRefreshRequest

        with pytest.raises(ValidationError):
            BudgetRefreshRequest()  # type: ignore[call-arg]

    def test_budget_refresh_request_rejects_short_justification(self) -> None:
        """BudgetRefreshRequest must reject justification shorter than 10 characters."""
        from pydantic import ValidationError

        from synth_engine.bootstrapper.schemas.privacy import BudgetRefreshRequest

        with pytest.raises(ValidationError):
            BudgetRefreshRequest(justification="short")

    def test_budget_refresh_request_accepts_valid_input(self) -> None:
        """BudgetRefreshRequest must accept a valid justification."""
        from synth_engine.bootstrapper.schemas.privacy import BudgetRefreshRequest

        req = BudgetRefreshRequest(justification="Valid justification string")
        assert req.justification == "Valid justification string"
        assert req.new_allocated_epsilon is None

    def test_budget_refresh_request_accepts_new_allocated(self) -> None:
        """BudgetRefreshRequest must accept an optional new_allocated_epsilon."""
        from synth_engine.bootstrapper.schemas.privacy import BudgetRefreshRequest

        req = BudgetRefreshRequest(
            justification="Quarterly budget increase", new_allocated_epsilon=25.0
        )
        assert req.new_allocated_epsilon == pytest.approx(25.0)

    def test_budget_response_model_fields(self) -> None:
        """BudgetResponse must be constructable with required fields."""
        from synth_engine.bootstrapper.schemas.privacy import BudgetResponse

        resp = BudgetResponse(
            total_allocated_epsilon=10.0,
            total_spent_epsilon=3.5,
            remaining_epsilon=6.5,
            is_exhausted=False,
        )
        assert resp.remaining_epsilon == pytest.approx(6.5)
        assert resp.is_exhausted is False

    @pytest.mark.parametrize(
        "field_name",
        [
            "total_allocated_epsilon",
            "total_spent_epsilon",
            "remaining_epsilon",
            "is_exhausted",
        ],
    )
    def test_budget_response_has_required_field(self, field_name: str) -> None:
        """BudgetResponse schema must expose all four required fields."""
        from synth_engine.bootstrapper.schemas.privacy import BudgetResponse

        fields = BudgetResponse.model_fields
        assert field_name in fields
