"""Negative/attack and feature tests for privacy endpoint auth (ADR-D1).

Tests cover:
- Unauthenticated, expired, empty-sub, wrong-key → 401 for privacy endpoints.
- Pass-through mode allows access for privacy GET budget.
- Authenticated privacy requests succeed.

Split from test_auth_gap_remediation.py (T56.3).
Parametrized in T73.1 to reduce repetition.

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


# ---------------------------------------------------------------------------
# ATTACK: Privacy endpoints — unauthenticated, expired, empty-sub, wrong-key
# Each parametrize value: (method, path, body)
# ---------------------------------------------------------------------------

_PRIVACY_ENDPOINT_CASES = [
    pytest.param("GET", "/api/v1/privacy/budget", None, id="get_budget"),
    pytest.param(
        "POST",
        "/api/v1/privacy/budget/refresh",
        {"justification": "Monthly budget refresh by admin"},
        id="refresh_budget",
    ),
]


@pytest.mark.parametrize(("method", "path", "body"), _PRIVACY_ENDPOINT_CASES)
@pytest.mark.asyncio
async def test_privacy_endpoint_unauthenticated_returns_401(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Privacy endpoints without token must return 401 when JWT is configured.

    Args:
        method: HTTP method to use.
        path: URL path to request.
        body: Optional JSON body.
        monkeypatch: pytest monkeypatch fixture.
    """
    app, _ = _make_privacy_app(monkeypatch)
    patches = _common_patches()

    with patches[0], patches[1]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            kwargs = {"json": body} if body is not None else {}
            response = await getattr(client, method.lower())(path, **kwargs)

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), _PRIVACY_ENDPOINT_CASES)
@pytest.mark.asyncio
async def test_privacy_endpoint_expired_token_returns_401(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Privacy endpoints with expired JWT must return 401.

    Args:
        method: HTTP method to use.
        path: URL path to request.
        body: Optional JSON body.
        monkeypatch: pytest monkeypatch fixture.
    """
    app, _ = _make_privacy_app(monkeypatch)
    token = _make_token(exp_offset=-3600)
    patches = _common_patches()

    with patches[0], patches[1]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await getattr(client, method.lower())(
                path,
                **({"json": body} if body is not None else {}),
                headers={"Authorization": f"Bearer {token}"},
            )

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), _PRIVACY_ENDPOINT_CASES)
@pytest.mark.asyncio
async def test_privacy_endpoint_empty_sub_returns_401(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Privacy endpoints with empty-sub token must return 401.

    Args:
        method: HTTP method to use.
        path: URL path to request.
        body: Optional JSON body.
        monkeypatch: pytest monkeypatch fixture.
    """
    app, _ = _make_privacy_app(monkeypatch)
    token = _make_token(sub="")
    patches = _common_patches()

    with patches[0], patches[1]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await getattr(client, method.lower())(
                path,
                **({"json": body} if body is not None else {}),
                headers={"Authorization": f"Bearer {token}"},
            )

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), _PRIVACY_ENDPOINT_CASES)
@pytest.mark.asyncio
async def test_privacy_endpoint_wrong_key_returns_401(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Privacy endpoints with wrong-key token must return 401.

    Args:
        method: HTTP method to use.
        path: URL path to request.
        body: Optional JSON body.
        monkeypatch: pytest monkeypatch fixture.
    """
    app, _ = _make_privacy_app(monkeypatch)
    token = _make_token(secret=_WRONG_SECRET)
    patches = _common_patches()

    with patches[0], patches[1]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await getattr(client, method.lower())(
                path,
                **({"json": body} if body is not None else {}),
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
                    "/api/v1/privacy/budget",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Feature tests: Audit events use current_operator (JWT sub)
# ---------------------------------------------------------------------------
