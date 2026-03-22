"""Negative/attack tests for scope-based authorization (T47.1, T47.3).

Attack test commit — RED phase.

These tests assert the security invariants BEFORE any feature implementation.
All tests must FAIL before implementation and PASS after.

Attack vectors tested:
- Unauthenticated requests (no token) → 401
- Valid token with no ``scope`` claim → 403
- Valid token with empty scope list → 403
- Valid token with a wrong scope → 403 on guarded endpoints
- Scope claim is a bare string instead of list (array injection) → 403
- Substring scope name that looks like a match → 403
- Settings GET is NOT scope-gated (any valid token → 200)
- Settings PUT/DELETE without required scope → 403
- Settings DELETE without required scope → 403

CONSTITUTION Priority 0: Security — authorization enforcement
CONSTITUTION Priority 3: TDD — ATTACK RED phase
Task: T47.1 — Scope-based auth for security endpoints
Task: T47.3 — Scope-based auth for settings write endpoints
"""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_JWT_SECRET = "test-secret-key-that-is-long-enough-for-hs256"  # noqa: S105 # nosec B105
_JWT_ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    scope: Any,
    *,
    include_scope: bool = True,
    secret: str = _JWT_SECRET,
) -> str:
    """Mint a test JWT with controllable scope.

    Args:
        scope: The value to use for the ``scope`` claim.
        include_scope: When ``False``, the ``scope`` key is omitted entirely.
        secret: HMAC secret to sign the token.

    Returns:
        Compact JWT string.
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": "test-operator",
        "iat": now,
        "exp": now + 3600,
    }
    if include_scope:
        payload["scope"] = scope
    return pyjwt.encode(payload, secret, algorithm=_JWT_ALGORITHM)


def _auth_header(token: str) -> dict[str, str]:
    """Build an Authorization header dict for the given token.

    Args:
        token: Compact JWT string.

    Returns:
        Headers dict with ``Authorization: Bearer <token>``.
    """
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    """Clear get_settings lru_cache before and after every test.

    Yields:
        None — setup/teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def jwt_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure JWT environment variables for the test.

    Sets JWT_SECRET_KEY and JWT_ALGORITHM so that scope enforcement is active
    (not in pass-through mode).

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", _JWT_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", _JWT_ALGORITHM)


@pytest.fixture
def security_client(jwt_env: None) -> TestClient:
    """Build a minimal FastAPI app mounting only the security router.

    The fixture registers the router with NO dependency overrides so that
    the scope guard is active.

    Args:
        jwt_env: Ensures JWT is configured before the client is built.

    Returns:
        TestClient for the minimal security app.
    """
    from synth_engine.bootstrapper.routers.security import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def settings_client(jwt_env: None) -> TestClient:
    """Build a minimal FastAPI app mounting only the settings router.

    Wires a real in-memory SQLite session but does NOT override
    ``get_current_operator`` or the scope dependency so authorization
    is tested end-to-end.

    Args:
        jwt_env: Ensures JWT is configured before the client is built.

    Returns:
        TestClient for the minimal settings app.
    """
    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.settings import router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = FastAPI()
    app.include_router(router)

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Attack tests: require_scope() — security endpoints
# ---------------------------------------------------------------------------


class TestScopeAttacks:
    """Negative/attack tests asserting scope enforcement rejects unauthorized callers."""

    def test_scope_rejects_unauthenticated(self, security_client: TestClient) -> None:
        """POST /security/shred with no Authorization header must return 401.

        No token at all is an authentication failure, not an authorization
        failure.  The middleware (or dependency) must reject before reaching
        scope evaluation.

        Arrange: security_client has JWT configured; no Authorization header.
        Act: POST /security/shred without any token.
        Assert: HTTP 401.
        """
        response = security_client.post("/security/shred")
        assert response.status_code == 401

    def test_scope_rejects_missing_scope_claim(self, security_client: TestClient) -> None:
        """POST /security/shred with a valid token but no ``scope`` key → 403.

        An attacker who obtains a token without a scope claim (perhaps issued
        by an older version of the system) must not gain access to guarded
        endpoints.

        Arrange: token signed with correct secret, ``scope`` key absent.
        Act: POST /security/shred.
        Assert: HTTP 403.
        """
        token = _make_token(scope=None, include_scope=False)
        response = security_client.post("/security/shred", headers=_auth_header(token))
        assert response.status_code == 403

    def test_scope_rejects_empty_scope_list(self, security_client: TestClient) -> None:
        """POST /security/shred with scope=[] must return 403.

        An empty scope list grants no permissions — the endpoint must not
        match a missing scope against an empty list.

        Arrange: token with ``scope: []``.
        Act: POST /security/shred.
        Assert: HTTP 403.
        """
        token = _make_token(scope=[])
        response = security_client.post("/security/shred", headers=_auth_header(token))
        assert response.status_code == 403

    def test_scope_rejects_wrong_scope(self, security_client: TestClient) -> None:
        """POST /security/shred with scope=["read"] must return 403.

        ``read`` is not ``security:admin``; any scope not in the required set
        must be treated as unauthorized.

        Arrange: token with ``scope: ["read"]``.
        Act: POST /security/shred.
        Assert: HTTP 403.
        """
        token = _make_token(scope=["read"])
        response = security_client.post("/security/shred", headers=_auth_header(token))
        assert response.status_code == 403

    def test_scope_array_injection_string(self, security_client: TestClient) -> None:
        """Scope claim as a bare string (not list) must be rejected with 403.

        Attack vector: an adversary crafts a token where ``scope`` is the
        string ``"security:admin"`` rather than the list ``["security:admin"]``.
        A naive ``in`` check would pass a string against a string-scope claim
        (``"security:admin" in "security:admin"`` is True).  The implementation
        must validate that the scope claim is always a ``list`` first.

        Arrange: token with ``scope: "security:admin"`` (string, not list).
        Act: POST /security/shred.
        Assert: HTTP 403.
        """
        token = _make_token(scope="security:admin")
        response = security_client.post("/security/shred", headers=_auth_header(token))
        assert response.status_code == 403

    def test_scope_substring_no_match(self, security_client: TestClient) -> None:
        """Scope ``"security:admin_extra"`` must NOT match ``"security:admin"``.

        Substring/prefix attacks: ``"security:admin" in "security:admin_extra"``
        is False for list membership, but an incorrect implementation using
        string ``in`` on a concatenated scope string could be tricked.

        Arrange: token with ``scope: ["security:admin_extra"]``.
        Act: POST /security/shred.
        Assert: HTTP 403 — not a valid match.
        """
        token = _make_token(scope=["security:admin_extra"])
        response = security_client.post("/security/shred", headers=_auth_header(token))
        assert response.status_code == 403

    def test_settings_get_no_scope_required(self, settings_client: TestClient) -> None:
        """GET /settings with any valid token (scope=["read"]) must return 200.

        Read endpoints are not scope-gated — any authenticated operator can
        list settings.

        Arrange: token with ``scope: ["read"]``.
        Act: GET /settings.
        Assert: HTTP 200.
        """
        token = _make_token(scope=["read"])
        response = settings_client.get("/settings", headers=_auth_header(token))
        assert response.status_code == 200

    def test_settings_write_without_scope(self, settings_client: TestClient) -> None:
        """PUT /settings/{key} without ``settings:write`` scope must return 403.

        Arrange: token with ``scope: ["read"]`` — no write scope.
        Act: PUT /settings/max_epochs with a valid body.
        Assert: HTTP 403.
        """
        token = _make_token(scope=["read"])
        response = settings_client.put(
            "/settings/max_epochs",
            json={"value": "100"},
            headers=_auth_header(token),
        )
        assert response.status_code == 403

    def test_settings_delete_without_scope(self, settings_client: TestClient) -> None:
        """DELETE /settings/{key} without ``settings:write`` scope must return 403.

        Arrange: token with ``scope: ["read"]`` — no write scope.
        Act: DELETE /settings/some_key.
        Assert: HTTP 403.
        """
        token = _make_token(scope=["read"])
        response = settings_client.delete(
            "/settings/some_key",
            headers=_auth_header(token),
        )
        assert response.status_code == 403

    def test_rotate_rejects_wrong_scope(self, security_client: TestClient) -> None:
        """POST /security/keys/rotate with scope=["read"] must return 403.

        The keys/rotate endpoint is also guarded by ``security:admin``.

        Arrange: token with ``scope: ["read"]``.
        Act: POST /security/keys/rotate with valid body.
        Assert: HTTP 403.
        """
        token = _make_token(scope=["read"])
        response = security_client.post(
            "/security/keys/rotate",
            json={"new_passphrase": "some-passphrase"},
            headers=_auth_header(token),
        )
        assert response.status_code == 403
