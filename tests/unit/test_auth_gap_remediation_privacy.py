"""Negative/attack and feature tests for privacy endpoint auth (ADR-D1).

Tests cover:
- Unauthenticated, expired, empty-sub, wrong-key → 401 for privacy endpoints.
- Pass-through mode allows access for privacy GET budget.
- Authenticated privacy requests succeed.

Split from test_auth_gap_remediation.py (T56.3).

CONSTITUTION Priority 0: Security
Task: ADR-D1 — Add Authentication to Settings, Security & Privacy Routers
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = (  # pragma: allowlist secret
    "unit-test-jwt-secret-key-long-enough-for-hs256-32chars+"
)
_WRONG_SECRET = (  # pragma: allowlist secret
    "wrong-secret-key-that-is-long-enough-for-hs256-32chars+"
)
_OPERATOR_SUB = "test-operator-remediation"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    sub: str = _OPERATOR_SUB,
    secret: str = _TEST_SECRET,
    exp_offset: int = 3600,
) -> str:
    """Create a JWT token for testing.

    Args:
        sub: Subject claim value.
        secret: HMAC secret to sign with.
        exp_offset: Seconds from now for expiry (negative = already expired).

    Returns:
        Compact JWT string.
    """
    import jwt as pyjwt

    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": sub,
            "iat": now,
            "exp": now + exp_offset,
            "scope": ["read", "write", "security:admin", "settings:write"],
        },
        secret,
        algorithm="HS256",
    )


def _make_security_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a test FastAPI app with the security router, auth configured.

    Args:
        monkeypatch: pytest monkeypatch for env var injection.

    Returns:
        FastAPI app instance.
    """
    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.security import router as security_router

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    app = create_app()
    register_error_handlers(app)
    app.include_router(security_router)

    # Remove any override for get_current_operator so the real dependency is used
    app.dependency_overrides.pop(get_current_operator, None)
    return app


def _make_privacy_app(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    """Build a test FastAPI app with the privacy router and seeded ledger, auth configured.

    Args:
        monkeypatch: pytest monkeypatch for env var injection.

    Returns:
        Tuple of (app, engine).
    """
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.privacy import router as privacy_router
    from synth_engine.modules.privacy.ledger import PrivacyLedger

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("AUDIT_KEY", os.urandom(32).hex())

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.shared.security.audit import reset_audit_logger

    reset_audit_logger()

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
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    # Remove any override for get_current_operator so the real dependency is used
    app.dependency_overrides.pop(get_current_operator, None)
    return app, engine


def _common_patches() -> list[Any]:
    """Return common mock patches for vault-seal and licensing checks.

    Returns:
        List of patch context managers.
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


class TestPrivacyUnauthenticatedReturns401:
    """Unauthenticated requests to privacy budget endpoints must return 401."""

    @pytest.mark.asyncio
    async def test_get_budget_unauthenticated_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /privacy/budget without token must return 401 when JWT is configured."""
        app, _ = _make_privacy_app(monkeypatch)
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/privacy/budget")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_budget_unauthenticated_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /privacy/budget/refresh without token must return 401 when JWT is configured."""
        app, _ = _make_privacy_app(monkeypatch)
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "Monthly budget refresh by admin"},
                )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# ATTACK RED: Privacy endpoints — expired JWT → 401
# ---------------------------------------------------------------------------


class TestPrivacyExpiredTokenReturns401:
    """Expired JWT tokens must return 401 on privacy endpoints."""

    @pytest.mark.asyncio
    async def test_get_budget_expired_token_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /privacy/budget with expired JWT must return 401."""
        app, _ = _make_privacy_app(monkeypatch)
        token = _make_token(exp_offset=-3600)
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/privacy/budget",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_budget_expired_token_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /privacy/budget/refresh with expired JWT must return 401."""
        app, _ = _make_privacy_app(monkeypatch)
        token = _make_token(exp_offset=-3600)
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "Monthly budget refresh by admin"},
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# ATTACK RED: Privacy endpoints — empty sub → 401
# ---------------------------------------------------------------------------


class TestPrivacyEmptySubReturns401:
    """Tokens with empty sub must return 401 on privacy endpoints."""

    @pytest.mark.asyncio
    async def test_get_budget_empty_sub_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /privacy/budget with token sub="" must return 401."""
        app, _ = _make_privacy_app(monkeypatch)
        token = _make_token(sub="")
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/privacy/budget",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_budget_empty_sub_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /privacy/budget/refresh with token sub="" must return 401."""
        app, _ = _make_privacy_app(monkeypatch)
        token = _make_token(sub="")
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/privacy/budget/refresh",
                    json={"justification": "Monthly budget refresh by admin"},
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# ATTACK RED: Privacy endpoints — wrong signing key → 401
# ---------------------------------------------------------------------------


class TestPrivacyWrongKeyReturns401:
    """Tokens signed with wrong key must return 401 on privacy endpoints."""

    @pytest.mark.asyncio
    async def test_get_budget_wrong_key_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /privacy/budget with token signed by wrong key must return 401."""
        app, _ = _make_privacy_app(monkeypatch)
        token = _make_token(secret=_WRONG_SECRET)
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/privacy/budget",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Authenticated requests succeed
# ---------------------------------------------------------------------------


class TestPrivacyAuthenticatedSucceeds:
    """Authenticated requests to privacy endpoints must succeed."""

    @pytest.mark.asyncio
    async def test_get_budget_authenticated_returns_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /privacy/budget with valid JWT must return 200."""
        app, _ = _make_privacy_app(monkeypatch)
        token = _make_token()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/privacy/budget",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Feature tests: Audit events use current_operator (JWT sub)
# ---------------------------------------------------------------------------
