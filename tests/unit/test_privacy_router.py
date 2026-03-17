"""Unit tests for the Privacy Budget Management API.

Tests follow TDD RED phase — all tests must fail before implementation.

Task: P22-T22.4 — Budget Management API
CONSTITUTION Priority 3: TDD — RED phase
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit


def _make_test_app() -> tuple[Any, Any]:
    """Build a test FastAPI app with an in-memory SQLite database.

    Returns:
        A 2-tuple of (app, engine) for use in tests.
    """
    from sqlalchemy.pool import StaticPool

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

    # Seed a default ledger row (id=1)
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
    return app, engine


def _make_empty_app() -> tuple[Any, Any]:
    """Build a test FastAPI app with NO ledger row seeded.

    Returns:
        A 2-tuple of (app, engine) for use in tests.
    """
    from sqlalchemy.pool import StaticPool

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
    return app, engine


def _common_patches() -> list[Any]:
    """Return common mock patches shared by all endpoint tests.

    Returns:
        List of patch context managers for vault and licensing checks.
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

        # Seed a fully exhausted budget
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


class TestBudgetRefreshEndpoint:
    """Tests for POST /privacy/budget/refresh."""

    @pytest.mark.asyncio
    async def test_refresh_budget_returns_200(self) -> None:
        """POST /privacy/budget/refresh must return HTTP 200 on success."""
        app, _ = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1]:
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
        app, _ = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1]:
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
        app, _ = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1]:
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
        app, _ = _make_test_app()
        patches = _common_patches()

        with patches[0], patches[1]:
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
    async def test_refresh_budget_emits_worm_audit_event(self) -> None:
        """POST /privacy/budget/refresh must emit a WORM HMAC-signed audit event."""
        app, _ = _make_test_app()
        patches = _common_patches()

        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        with (
            patches[0],
            patches[1],
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

    @pytest.mark.asyncio
    async def test_refresh_budget_audit_event_includes_actor_from_header(
        self,
    ) -> None:
        """Refresh audit event must capture the X-Operator-Id header as actor."""
        app, _ = _make_test_app()
        patches = _common_patches()

        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        with (
            patches[0],
            patches[1],
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
                    headers={"X-Operator-Id": "admin-user-42"},
                )

        call_kwargs = mock_audit.log_event.call_args.kwargs
        assert call_kwargs["actor"] == "admin-user-42"

    @pytest.mark.asyncio
    async def test_refresh_budget_audit_event_defaults_actor_when_no_header(
        self,
    ) -> None:
        """Refresh audit event must use a fallback actor when X-Operator-Id is absent."""
        app, _ = _make_test_app()
        patches = _common_patches()

        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        with (
            patches[0],
            patches[1],
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
                    json={"justification": "No-header actor fallback test"},
                )

        call_kwargs = mock_audit.log_event.call_args.kwargs
        # Actor must not be empty — it should have a non-empty fallback value.
        assert call_kwargs["actor"] != ""

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


class TestBudgetSchemas:
    """Tests for schema validation behaviour (BudgetRefreshRequest)."""

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
        "env_key",
        [
            "total_allocated_epsilon",
            "total_spent_epsilon",
            "remaining_epsilon",
            "is_exhausted",
        ],
    )
    def test_budget_response_has_required_field(self, env_key: str) -> None:
        """BudgetResponse schema must expose all four required fields."""
        from synth_engine.bootstrapper.schemas.privacy import BudgetResponse

        fields = BudgetResponse.model_fields
        assert env_key in fields
