"""Scope-based authorization tests: attack/negative tests and feature tests (T47.1, T47.3).

Attack tests (TestScopeAttacks): RED phase committed first.
Feature tests (TestScopeFeatures): RED phase committed second.

Attack vectors tested:
- Unauthenticated requests (no token) → 401
- Valid token with no ``scope`` claim → 403
- Valid token with empty scope list → 403
- Valid token with a wrong scope → 403 on guarded endpoints
- Scope claim is a bare string instead of list (array injection) → 403
- Substring scope name that looks like a match → 403
- Settings GET is NOT scope-gated (any valid token → 200)
- Settings PUT/DELETE without required scope → 403

Feature paths tested:
- Correct ``security:admin`` scope → 200 on /security/shred
- Superset of scopes (additional scopes beyond required) → 200
- ``settings:write`` scope → 200 on PUT /settings/{key}
- ``settings:write`` scope → 204 on DELETE /settings/{key}
- POST /auth/token issues token containing all required scopes
- Pass-through mode (empty JWT_SECRET_KEY) → scope check bypassed

CONSTITUTION Priority 0: Security — authorization enforcement
CONSTITUTION Priority 3: TDD — RED phase
Task: T47.1 — Scope-based auth for security endpoints
Task: T47.3 — Scope-based auth for settings write endpoints
"""

from __future__ import annotations

import time
from typing import Any

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_JWT_SECRET = "test-secret-key-that-is-long-enough-for-hs256"  # nosec B105  # pragma: allowlist secret
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
    the scope guard is exercised end-to-end.

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
# Attack tests: require_scope() — security and settings endpoints
# ---------------------------------------------------------------------------

# scope values that must all return 403 on /security/shred (admin-only endpoint)
_INSUFFICIENT_SCOPE_CASES = [
    pytest.param(None, False, id="missing_scope_claim"),
    pytest.param([], True, id="empty_scope_list"),
    pytest.param(["read"], True, id="wrong_scope"),
    pytest.param("security:admin", True, id="string_not_list"),
    pytest.param(["security:admin_extra"], True, id="substring_no_match"),
]


@pytest.mark.parametrize(("scope_value", "include_scope"), _INSUFFICIENT_SCOPE_CASES)
def test_shred_rejects_insufficient_scope(
    scope_value: Any,
    include_scope: bool,
    security_client: TestClient,
) -> None:
    """POST /security/shred must return 403 for tokens lacking security:admin scope.

    Args:
        scope_value: The scope claim value to embed in the token.
        include_scope: When False, omit the scope key from the token entirely.
        security_client: TestClient for a minimal security app with JWT configured.
    """
    token = _make_token(scope=scope_value, include_scope=include_scope)
    response = security_client.post("/security/shred", headers=_auth_header(token))
    assert response.status_code == 403


def test_scope_rejects_unauthenticated(security_client: TestClient) -> None:
    """POST /security/shred with no Authorization header must return 401.

    No token at all is an authentication failure, not an authorization
    failure.  The middleware (or dependency) must reject before reaching
    scope evaluation.
    """
    response = security_client.post("/security/shred")
    assert response.status_code == 401


# settings endpoints scope attack cases: (method, path, body, expected_status)
_SETTINGS_SCOPE_ATTACK_CASES = [
    pytest.param("GET", "/settings", None, 200, id="read_no_scope_required"),
    pytest.param("PUT", "/settings/max_epochs", {"value": "100"}, 403, id="write_missing_scope"),
    pytest.param("DELETE", "/settings/some_key", None, 403, id="delete_missing_scope"),
]


@pytest.mark.parametrize(
    ("method", "path", "body", "expected_status"), _SETTINGS_SCOPE_ATTACK_CASES
)
def test_settings_scope_enforcement(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    expected_status: int,
    settings_client: TestClient,
) -> None:
    """Settings endpoints must enforce scope:write for mutation operations.

    GET is not scope-gated; PUT/DELETE require settings:write scope.

    Args:
        method: HTTP method.
        path: URL path.
        body: Optional JSON body.
        expected_status: Expected HTTP status code.
        settings_client: TestClient for a minimal settings app with JWT configured.
    """
    token = _make_token(scope=["read"])
    kwargs: dict[str, Any] = {"headers": _auth_header(token)}
    if body is not None:
        kwargs["json"] = body
    response = getattr(settings_client, method.lower())(path, **kwargs)
    assert response.status_code == expected_status


def test_rotate_rejects_wrong_scope(security_client: TestClient) -> None:
    """POST /security/keys/rotate with scope=["read"] must return 403.

    The keys/rotate endpoint is also guarded by security:admin.
    """
    token = _make_token(scope=["read"])
    response = security_client.post(
        "/security/keys/rotate",
        json={"new_passphrase": "some-passphrase"},
        headers=_auth_header(token),
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Feature tests: require_scope() — happy paths
# ---------------------------------------------------------------------------


class TestScopeFeatures:
    """Feature tests asserting that correctly-scoped tokens are accepted."""

    def test_scope_passes_with_correct_scope(
        self,
        security_client: TestClient,
    ) -> None:
        """POST /security/shred with ``security:admin`` scope must return 200.

        The shred endpoint is guarded by ``security:admin``.  A token that
        carries that exact scope must be allowed through.

        Arrange: token with ``scope: ["security:admin"]``.
        Act: POST /security/shred.
        Assert: HTTP 200.
        """
        token = _make_token(scope=["security:admin"])
        response = security_client.post("/security/shred", headers=_auth_header(token))
        assert response.status_code == 200

    def test_scope_passes_with_superset(
        self,
        security_client: TestClient,
    ) -> None:
        """POST /security/shred with more than the required scope must succeed.

        Holding extra scopes beyond what the endpoint requires is a normal
        production scenario (the default issued token contains many scopes).

        Arrange: token with ``scope: ["security:admin", "read", "write"]``.
        Act: POST /security/shred.
        Assert: HTTP 200.
        """
        token = _make_token(scope=["security:admin", "read", "write"])
        response = security_client.post("/security/shred", headers=_auth_header(token))
        assert response.status_code == 200

    def test_settings_write_with_scope(
        self,
        settings_client: TestClient,
    ) -> None:
        """PUT /settings/{key} with ``settings:write`` scope must return 200.

        Arrange: token with ``scope: ["settings:write"]``.
        Act: PUT /settings/max_epochs with a valid body.
        Assert: HTTP 200 with the upserted setting in the response.
        """
        token = _make_token(scope=["settings:write"])
        response = settings_client.put(
            "/settings/max_epochs",
            json={"value": "200"},
            headers=_auth_header(token),
        )
        assert response.status_code == 200

    def test_settings_delete_with_scope(
        self,
        settings_client: TestClient,
    ) -> None:
        """DELETE /settings/{key} with ``settings:write`` scope must return 204.

        Arrange: first create the setting via PUT (also with settings:write),
        then DELETE it.
        Act: DELETE /settings/to_remove.
        Assert: HTTP 204.
        """
        token = _make_token(scope=["settings:write"])
        headers = _auth_header(token)
        # Seed the setting
        settings_client.put("/settings/to_remove", json={"value": "x"}, headers=headers)
        response = settings_client.delete("/settings/to_remove", headers=headers)
        assert response.status_code == 204

    def test_token_issuance_includes_new_scopes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /auth/token must issue a token containing all required scopes.

        The default scope list must include ``security:admin`` and
        ``settings:write`` (T47.1/T47.3 requirement).  Operators get all
        scopes because this is a single-operator system.

        Arrange: configure credentials and JWT secret; POST /auth/token.
        Act: decode the returned access_token.
        Assert: scope list contains ``security:admin`` and ``settings:write``.
        """
        import bcrypt
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        monkeypatch.setenv("JWT_SECRET_KEY", _JWT_SECRET)
        monkeypatch.setenv("JWT_ALGORITHM", _JWT_ALGORITHM)
        hashed = bcrypt.hashpw(b"correct-passphrase", bcrypt.gensalt()).decode()
        monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", hashed)

        from synth_engine.bootstrapper.routers.auth import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/auth/token",
            json={"username": "operator", "passphrase": "correct-passphrase"},
        )
        assert response.status_code == 200
        body = response.json()
        token = body["access_token"]
        claims = pyjwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        issued_scope: list[str] = claims.get("scope", [])
        assert "security:admin" in issued_scope, (
            f"Expected 'security:admin' in issued scope, got: {issued_scope}"
        )
        assert "settings:write" in issued_scope, (
            f"Expected 'settings:write' in issued scope, got: {issued_scope}"
        )

    def test_scope_bypass_in_passthrough_mode(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scope check is bypassed when JWT_SECRET_KEY is empty (pass-through mode).

        When the system is unconfigured (JWT_SECRET_KEY=""), the middleware
        allows all requests and ``require_scope`` must also skip the check
        (consistent with ``get_current_operator`` pass-through behavior).

        Arrange: JWT_SECRET_KEY=""; no Authorization header.
        Act: POST /security/shred.
        Assert: HTTP 200 — not 401 or 403.
        """
        monkeypatch.setenv("JWT_SECRET_KEY", "")
        monkeypatch.setenv("JWT_ALGORITHM", _JWT_ALGORITHM)

        from synth_engine.bootstrapper.routers.security import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/security/shred")
        assert response.status_code == 200
