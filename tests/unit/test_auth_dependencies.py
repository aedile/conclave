"""Unit tests for the FastAPI dependency factory in bootstrapper/dependencies/auth.py.

CONSTITUTION Priority 3: TDD RED/GREEN Phase
Task: P2-T2.3 — Zero-Trust JWT Authentication & RBAC Scopes (Architecture blocker fix)

Tests the framework-binding layer that translates TokenVerificationError into
FastAPI HTTPException responses.
"""

import asyncio
from unittest.mock import MagicMock, PropertyMock

import pytest
from fastapi import HTTPException

from synth_engine.bootstrapper.dependencies.auth import get_current_user
from synth_engine.shared.auth.jwt import JWTConfig, TokenPayload, create_access_token
from synth_engine.shared.auth.scopes import Scope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Test-only HMAC secret — not a production credential.
_SECRET = "super-secret-key-for-testing-only-32chars!!"  # nosec B105 # pragma: allowlist secret
_ALGORITHM = "HS256"


def _make_config(expire_minutes: int = 30) -> JWTConfig:
    """Return a JWTConfig suitable for test use."""
    return JWTConfig(
        secret_key=_SECRET,
        algorithm=_ALGORITHM,
        access_token_expire_minutes=expire_minutes,
    )


def _mock_request(
    client_host: str = "192.168.1.1",
    forwarded_for: str | None = None,
    mtls_san: str | None = None,
) -> MagicMock:
    """Build a mock Starlette/FastAPI Request with common headers.

    Args:
        client_host: Value for ``request.client.host``.
        forwarded_for: Value for the ``X-Forwarded-For`` header.
        mtls_san: Value for the ``X-Client-Cert-SAN`` header.

    Returns:
        A configured :class:`~unittest.mock.MagicMock` standing in for a
        Starlette ``Request``.
    """
    request = MagicMock()

    client = MagicMock()
    type(client).host = PropertyMock(return_value=client_host)
    request.client = client

    headers: dict[str, str] = {}
    if forwarded_for is not None:
        headers["X-Forwarded-For"] = forwarded_for
    if mtls_san is not None:
        headers["X-Client-Cert-SAN"] = mtls_san

    request.headers = headers
    return request


# ---------------------------------------------------------------------------
# get_current_user — async dependency factory
# ---------------------------------------------------------------------------


def test_get_current_user_returns_callable() -> None:
    """get_current_user() returns an async callable (the inner dependency)."""
    dep = get_current_user(required_scope=Scope.READ_RESULTS)
    assert callable(dep)


def test_get_current_user_dependency_valid_token_and_scope() -> None:
    """Dependency resolves successfully when token is valid and scope matches."""
    config = _make_config()
    client_ip = "10.1.1.1"
    token = create_access_token(
        subject="henry",
        scopes=[Scope.READ_RESULTS],
        client_identifier=client_ip,
        config=config,
    )
    request = _mock_request(client_host=client_ip)

    dep = get_current_user(required_scope=Scope.READ_RESULTS)
    payload = asyncio.run(dep(request, token, config))
    assert isinstance(payload, TokenPayload)
    assert payload.sub == "henry"


def test_get_current_user_dependency_raises_401_on_invalid_token() -> None:
    """Dependency raises HTTPException 401 when the token is invalid."""
    config = _make_config()
    client_ip = "10.1.1.5"
    token = create_access_token(
        subject="henry",
        scopes=[Scope.READ_RESULTS],
        client_identifier=client_ip,
        config=config,
    )
    # Present from a different IP — triggers client binding failure
    request = _mock_request(client_host="10.2.2.2")

    dep = get_current_user(required_scope=Scope.READ_RESULTS)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dep(request, token, config))
    assert exc_info.value.status_code == 401


def test_get_current_user_dependency_raises_403_on_insufficient_scope() -> None:
    """Dependency raises HTTPException 403 when token lacks the required scope."""
    config = _make_config()
    client_ip = "10.1.1.2"
    token = create_access_token(
        subject="iris",
        scopes=[Scope.READ_RESULTS],
        client_identifier=client_ip,
        config=config,
    )
    request = _mock_request(client_host=client_ip)

    dep = get_current_user(required_scope=Scope.SYNTHESIZE)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dep(request, token, config))
    assert exc_info.value.status_code == 403
    assert "scope" in exc_info.value.detail.lower()


def test_get_current_user_no_scope_requirement_accepts_any_valid_token() -> None:
    """Dependency with required_scope=None accepts any valid token."""
    config = _make_config()
    client_ip = "10.1.1.3"
    token = create_access_token(
        subject="jake",
        scopes=[Scope.AUDIT_READ],
        client_identifier=client_ip,
        config=config,
    )
    request = _mock_request(client_host=client_ip)

    dep = get_current_user(required_scope=None)
    payload = asyncio.run(dep(request, token, config))
    assert payload.sub == "jake"


def test_get_current_user_dependency_admin_scope_passes_any_requirement() -> None:
    """Admin token satisfies any scope requirement via hierarchy."""
    config = _make_config()
    client_ip = "10.1.1.4"
    token = create_access_token(
        subject="kate",
        scopes=[Scope.ADMIN],
        client_identifier=client_ip,
        config=config,
    )
    request = _mock_request(client_host=client_ip)

    dep = get_current_user(required_scope=Scope.VAULT_UNSEAL)
    payload = asyncio.run(dep(request, token, config))
    assert payload.sub == "kate"


def test_get_current_user_dependency_raises_401_on_expired_token() -> None:
    """Dependency raises HTTPException 401 when token is expired."""
    config = _make_config(expire_minutes=-1)
    client_ip = "10.1.1.6"
    token = create_access_token(
        subject="liam",
        scopes=[Scope.READ_RESULTS],
        client_identifier=client_ip,
        config=config,
    )
    request = _mock_request(client_host=client_ip)

    dep = get_current_user(required_scope=Scope.READ_RESULTS)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dep(request, token, config))
    assert exc_info.value.status_code == 401
