"""Negative/attack and feature tests for settings endpoint auth (ADR-D1).

Tests cover:
- /security/shred and /security/keys/rotate exempt path membership.
- Unauthenticated, expired, empty-sub, wrong-key → 401 for settings endpoints.
- Authenticated settings requests succeed.

Split from test_auth_gap_remediation.py (T56.3).
Parametrized in T73.1 to reduce repetition.

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
#: Valid org UUID for JWT org_id claim — must pass P79-F3 UUID validation.
_ORG_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    sub: str = _OPERATOR_SUB,
    secret: str = _TEST_SECRET,
    exp_offset: int = 3600,
) -> str:
    """Create a JWT token for testing.

    Includes org_id (valid UUID) and role claims required by get_current_user
    (P79-T79.2 migration).

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
            "org_id": _ORG_UUID,
            "role": "admin",
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


@pytest.mark.parametrize(
    ("path", "container", "expected_membership"),
    [
        pytest.param(
            "/security/shred",
            "SEAL_EXEMPT_PATHS",
            True,
            id="shred_in_seal_exempt",
        ),
        pytest.param(
            "/security/shred",
            "COMMON_INFRA_EXEMPT_PATHS",
            False,
            id="shred_not_in_common_infra",
        ),
        pytest.param(
            "/security/keys/rotate",
            "COMMON_INFRA_EXEMPT_PATHS",
            False,
            id="rotate_not_in_common_infra",
        ),
        pytest.param(
            "/security/keys/rotate",
            "SEAL_EXEMPT_PATHS",
            False,
            id="rotate_not_in_seal_exempt",
        ),
    ],
)
def test_security_path_exempt_membership(
    path: str, container: str, expected_membership: bool
) -> None:
    """Security paths must be in the correct exempt-path sets.

    Args:
        path: URL path to check.
        container: Name of the exempt-paths constant to check.
        expected_membership: True if path should be in the set, False otherwise.
    """
    from synth_engine.bootstrapper.dependencies._exempt_paths import (
        COMMON_INFRA_EXEMPT_PATHS,
        SEAL_EXEMPT_PATHS,
    )

    containers = {
        "SEAL_EXEMPT_PATHS": SEAL_EXEMPT_PATHS,
        "COMMON_INFRA_EXEMPT_PATHS": COMMON_INFRA_EXEMPT_PATHS,
    }
    actual = path in containers[container]
    assert actual == expected_membership, (
        f"Expected {path!r} in {container}={expected_membership}, got {actual}"
    )


# ---------------------------------------------------------------------------
# ATTACK: Settings endpoints — unauthenticated, expired, empty-sub, wrong-key
# Each parametrize value: (method, path, body)
# ---------------------------------------------------------------------------

_SETTINGS_ATTACK_CASES = [
    pytest.param("GET", "/api/v1/settings", None, id="list"),
    pytest.param("GET", "/api/v1/settings/some_key", None, id="get_key"),
    pytest.param("PUT", "/api/v1/settings/some_key", {"value": "v"}, id="upsert"),
    pytest.param("DELETE", "/api/v1/settings/some_key", None, id="delete"),
]


@pytest.mark.parametrize(("method", "path", "body"), _SETTINGS_ATTACK_CASES)
@pytest.mark.asyncio
async def test_settings_endpoint_unauthenticated_returns_401(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings endpoints without token must return 401 when JWT is configured.

    Args:
        method: HTTP method to use.
        path: URL path to request.
        body: Optional JSON body.
        monkeypatch: pytest monkeypatch fixture.
    """
    app = _make_settings_app(monkeypatch)
    patches = _common_patches()

    with patches[0], patches[1]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            kwargs = {"json": body} if body is not None else {}
            response = await getattr(client, method.lower())(path, **kwargs)

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), _SETTINGS_ATTACK_CASES)
@pytest.mark.asyncio
async def test_settings_endpoint_expired_token_returns_401(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings endpoints with expired JWT must return 401.

    Args:
        method: HTTP method to use.
        path: URL path to request.
        body: Optional JSON body.
        monkeypatch: pytest monkeypatch fixture.
    """
    app = _make_settings_app(monkeypatch)
    token = _make_token(exp_offset=-3600)
    patches = _common_patches()

    with patches[0], patches[1]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await getattr(client, method.lower())(
                path,
                **({"json": body} if body is not None else {}),
                headers={"Authorization": f"Bearer {token}"},
            )

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), _SETTINGS_ATTACK_CASES)
@pytest.mark.asyncio
async def test_settings_endpoint_empty_sub_returns_401(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings endpoints with empty-sub token must return 401.

    Args:
        method: HTTP method to use.
        path: URL path to request.
        body: Optional JSON body.
        monkeypatch: pytest monkeypatch fixture.
    """
    app = _make_settings_app(monkeypatch)
    token = _make_token(sub="")
    patches = _common_patches()

    with patches[0], patches[1]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await getattr(client, method.lower())(
                path,
                **({"json": body} if body is not None else {}),
                headers={"Authorization": f"Bearer {token}"},
            )

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), _SETTINGS_ATTACK_CASES)
@pytest.mark.asyncio
async def test_settings_endpoint_wrong_key_returns_401(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings endpoints with wrong-key token must return 401.

    Args:
        method: HTTP method to use.
        path: URL path to request.
        body: Optional JSON body.
        monkeypatch: pytest monkeypatch fixture.
    """
    app = _make_settings_app(monkeypatch)
    token = _make_token(secret=_WRONG_SECRET)
    patches = _common_patches()

    with patches[0], patches[1]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await getattr(client, method.lower())(
                path,
                **({"json": body} if body is not None else {}),
                headers={"Authorization": f"Bearer {token}"},
            )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Feature: Authenticated requests succeed
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
                    "/api/v1/settings", headers={"Authorization": f"Bearer {token}"}
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
                    "/api/v1/settings/test_key",
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
                    "/api/v1/settings/existing_key",
                    json={"value": "some_value"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                response = await client.get(
                    "/api/v1/settings/existing_key",
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
                    "/api/v1/settings/key_to_delete",
                    json={"value": "v"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                response = await client.delete(
                    "/api/v1/settings/key_to_delete",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert response.status_code == 204
