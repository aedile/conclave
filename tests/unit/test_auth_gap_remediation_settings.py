"""Negative/attack and feature tests for settings endpoint auth (ADR-D1).

Tests cover:
- /security/shred and /security/keys/rotate exempt path membership.
- Unauthenticated, expired, empty-sub, wrong-key → 401 for settings endpoints.
- Authenticated settings requests succeed.

Split from test_auth_gap_remediation.py (T56.3).

CONSTITUTION Priority 0: Security
Task: ADR-D1 — Add Authentication to Settings, Security & Privacy Routers
"""

from __future__ import annotations

import time
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


def _make_settings_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a test FastAPI app with the settings router, auth configured.

    Args:
        monkeypatch: pytest monkeypatch for env var injection.

    Returns:
        FastAPI app instance.
    """
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.settings import router as settings_router

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()
    register_error_handlers(app)
    app.include_router(settings_router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    # Remove any override for get_current_operator so the real dependency is used
    app.dependency_overrides.pop(get_current_operator, None)
    return app


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
# AC: Security endpoint vault-layer bypass (layered exemption model, P50 fix)
# ---------------------------------------------------------------------------


def test_security_shred_is_in_seal_exempt_paths() -> None:
    """/security/shred must be in SEAL_EXEMPT_PATHS (vault/license bypass).

    After the P50 layered exemption model: /security/shred is no longer in
    COMMON_INFRA_EXEMPT_PATHS (auth baseline).  Instead it lives in
    SEAL_EXEMPT_PATHS so that SealGateMiddleware and LicenseGateMiddleware
    allow emergency shred through, while AuthenticationGateMiddleware still
    requires JWT auth (the route uses require_scope("security:admin")).
    """
    from synth_engine.bootstrapper.dependencies._exempt_paths import SEAL_EXEMPT_PATHS

    assert "/security/shred" in SEAL_EXEMPT_PATHS


def test_security_shred_not_in_common_infra_exempt_paths() -> None:
    """/security/shred must NOT be in COMMON_INFRA_EXEMPT_PATHS (requires JWT auth).

    COMMON_INFRA_EXEMPT_PATHS is the auth baseline.  Security routes must
    not bypass AuthenticationGateMiddleware (ADV-P47-04, P50 review fix).
    """
    from synth_engine.bootstrapper.dependencies._exempt_paths import COMMON_INFRA_EXEMPT_PATHS

    assert "/security/shred" not in COMMON_INFRA_EXEMPT_PATHS


def test_security_keys_rotate_not_in_any_exempt_paths() -> None:
    """/security/keys/rotate must NOT be in COMMON_INFRA_EXEMPT_PATHS or SEAL_EXEMPT_PATHS.

    Key rotation requires an unsealed vault and JWT auth.  It must not bypass
    either SealGateMiddleware or AuthenticationGateMiddleware.
    """
    from synth_engine.bootstrapper.dependencies._exempt_paths import (
        COMMON_INFRA_EXEMPT_PATHS,
        SEAL_EXEMPT_PATHS,
    )

    assert "/security/keys/rotate" not in COMMON_INFRA_EXEMPT_PATHS
    assert "/security/keys/rotate" not in SEAL_EXEMPT_PATHS


# ---------------------------------------------------------------------------
# ATTACK RED: Settings endpoints — unauthenticated → 401
# ---------------------------------------------------------------------------


class TestSettingsUnauthenticatedReturns401:
    """Unauthenticated requests to all settings endpoints must return 401."""

    @pytest.mark.asyncio
    async def test_list_settings_unauthenticated_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /settings without token must return 401 when JWT is configured."""
        app = _make_settings_app(monkeypatch)
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/settings")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_setting_unauthenticated_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /settings/{key} without token must return 401 when JWT is configured."""
        app = _make_settings_app(monkeypatch)
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/settings/some_key")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_upsert_setting_unauthenticated_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PUT /settings/{key} without token must return 401 when JWT is configured."""
        app = _make_settings_app(monkeypatch)
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.put("/settings/some_key", json={"value": "v"})

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_setting_unauthenticated_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DELETE /settings/{key} without token must return 401 when JWT is configured."""
        app = _make_settings_app(monkeypatch)
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.delete("/settings/some_key")

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# ATTACK RED: Settings endpoints — expired JWT → 401
# ---------------------------------------------------------------------------


class TestSettingsExpiredTokenReturns401:
    """Expired JWT tokens must return 401 on settings endpoints."""

    @pytest.mark.asyncio
    async def test_list_settings_expired_token_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /settings with expired JWT must return 401."""
        app = _make_settings_app(monkeypatch)
        token = _make_token(exp_offset=-3600)  # already expired
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/settings", headers={"Authorization": f"Bearer {token}"}
                )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_upsert_setting_expired_token_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PUT /settings/{key} with expired JWT must return 401."""
        app = _make_settings_app(monkeypatch)
        token = _make_token(exp_offset=-3600)
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.put(
                    "/settings/some_key",
                    json={"value": "v"},
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# ATTACK RED: Settings endpoints — empty sub → 401
# ---------------------------------------------------------------------------


class TestSettingsEmptySubReturns401:
    """Tokens with empty sub claim must return 401 on settings endpoints."""

    @pytest.mark.asyncio
    async def test_list_settings_empty_sub_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /settings with token sub="" must return 401."""
        app = _make_settings_app(monkeypatch)
        token = _make_token(sub="")
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/settings", headers={"Authorization": f"Bearer {token}"}
                )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# ATTACK RED: Settings endpoints — wrong signing key → 401
# ---------------------------------------------------------------------------


class TestSettingsWrongKeyReturns401:
    """Tokens signed with wrong key must return 401 on settings endpoints."""

    @pytest.mark.asyncio
    async def test_list_settings_wrong_key_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /settings with token signed by wrong key must return 401."""
        app = _make_settings_app(monkeypatch)
        token = _make_token(secret=_WRONG_SECRET)
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/settings", headers={"Authorization": f"Bearer {token}"}
                )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# ATTACK RED: Security endpoints — unauthenticated → 401
# ---------------------------------------------------------------------------


class TestSettingsAuthenticatedSucceeds:
    """Authenticated requests to settings endpoints must succeed."""

    @pytest.mark.asyncio
    async def test_list_settings_authenticated_returns_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /settings with valid JWT must return 200."""
        app = _make_settings_app(monkeypatch)
        token = _make_token()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/settings", headers={"Authorization": f"Bearer {token}"}
                )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_upsert_setting_authenticated_returns_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PUT /settings/{key} with valid JWT must return 200."""
        app = _make_settings_app(monkeypatch)
        token = _make_token()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.put(
                    "/settings/test_key",
                    json={"value": "test_value"},
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_setting_authenticated_returns_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /settings/{key} with valid JWT must return 200 when key exists."""
        app = _make_settings_app(monkeypatch)
        token = _make_token()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # First create the setting
                await client.put(
                    "/settings/existing_key",
                    json={"value": "some_value"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                response = await client.get(
                    "/settings/existing_key",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_setting_authenticated_returns_204(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DELETE /settings/{key} with valid JWT must return 204 when key exists."""
        app = _make_settings_app(monkeypatch)
        token = _make_token()
        patches = _common_patches()

        with patches[0], patches[1]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.put(
                    "/settings/key_to_delete",
                    json={"value": "v"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                response = await client.delete(
                    "/settings/key_to_delete",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 204
