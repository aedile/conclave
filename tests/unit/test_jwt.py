"""Unit tests for Zero-Trust JWT authentication with client-binding.

CONSTITUTION Priority 3: TDD RED/GREEN Phase
Task: P2-T2.3 — Zero-Trust JWT Authentication & RBAC Scopes

All tests use unittest.mock to mock Request objects.
No running FastAPI app is required.
"""

import asyncio
import hashlib
import os
from unittest.mock import MagicMock, PropertyMock

import pytest
from fastapi import HTTPException

from synth_engine.shared.auth.jwt import (
    JWTConfig,
    TokenPayload,
    _hash_client_identifier,
    create_access_token,
    extract_client_identifier,
    get_current_user,
    get_jwt_config,
    verify_token,
)
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
    """Build a mock Starlette/FastAPI Request with common headers."""
    request = MagicMock()

    # request.client.host
    client = MagicMock()
    type(client).host = PropertyMock(return_value=client_host)
    request.client = client

    # request.headers behaves like a dict
    headers: dict[str, str] = {}
    if forwarded_for is not None:
        headers["X-Forwarded-For"] = forwarded_for
    if mtls_san is not None:
        headers["X-Client-Cert-SAN"] = mtls_san

    request.headers = headers
    return request


# ---------------------------------------------------------------------------
# _hash_client_identifier
# ---------------------------------------------------------------------------


def test_hash_client_identifier_is_deterministic() -> None:
    """Same input always produces the same SHA-256 hex digest."""
    identifier = "192.168.1.1"
    first = _hash_client_identifier(identifier)
    second = _hash_client_identifier(identifier)
    assert first == second


def test_hash_client_identifier_matches_stdlib() -> None:
    """Output matches hashlib.sha256 directly."""
    identifier = "10.0.0.1"
    expected = hashlib.sha256(identifier.encode()).hexdigest()
    assert _hash_client_identifier(identifier) == expected


# ---------------------------------------------------------------------------
# extract_client_identifier
# ---------------------------------------------------------------------------


def test_extract_uses_mtls_san_when_present() -> None:
    """X-Client-Cert-SAN header takes precedence over all other sources."""
    request = _mock_request(
        client_host="192.168.1.1",
        forwarded_for="10.0.0.5",
        mtls_san="client.internal",
    )
    result = extract_client_identifier(request, trusted_proxy_header="X-Forwarded-For")
    assert result == "client.internal"


def test_extract_uses_forwarded_for_first_ip() -> None:
    """When no mTLS SAN, use the first IP from X-Forwarded-For."""
    request = _mock_request(
        client_host="127.0.0.1",
        forwarded_for="203.0.113.1, 10.10.10.1",
    )
    result = extract_client_identifier(request, trusted_proxy_header="X-Forwarded-For")
    assert result == "203.0.113.1"


def test_extract_falls_back_to_client_host() -> None:
    """Without mTLS or proxy headers, use request.client.host."""
    request = _mock_request(client_host="172.16.0.5")
    result = extract_client_identifier(request, trusted_proxy_header="X-Forwarded-For")
    assert result == "172.16.0.5"


# ---------------------------------------------------------------------------
# create_access_token / verify_token — happy path
# ---------------------------------------------------------------------------


def test_valid_token_passes_verification() -> None:
    """Token created for 192.168.1.1 verifies successfully from same IP."""
    config = _make_config()
    client_ip = "192.168.1.1"
    token = create_access_token(
        subject="alice",
        scopes=[Scope.READ_RESULTS],
        client_identifier=client_ip,
        config=config,
    )
    request = _mock_request(client_host=client_ip)
    payload = verify_token(token, request, config)

    assert isinstance(payload, TokenPayload)
    assert payload.sub == "alice"
    assert Scope.READ_RESULTS in payload.scopes


# ---------------------------------------------------------------------------
# Client IP mismatch
# ---------------------------------------------------------------------------


def test_mismatched_client_ip_raises_401() -> None:
    """Token bound to 192.168.1.1 must not verify from 10.0.0.1."""
    config = _make_config()
    token = create_access_token(
        subject="bob",
        scopes=[Scope.READ_RESULTS],
        client_identifier="192.168.1.1",
        config=config,
    )
    request = _mock_request(client_host="10.0.0.1")

    with pytest.raises(HTTPException) as exc_info:
        verify_token(token, request, config)

    assert exc_info.value.status_code == 401
    assert "bound" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Expired token
# ---------------------------------------------------------------------------


def test_expired_token_raises_401() -> None:
    """A token with expire_minutes=-1 is immediately expired."""
    config = _make_config(expire_minutes=-1)
    token = create_access_token(
        subject="carol",
        scopes=[Scope.READ_RESULTS],
        client_identifier="192.168.1.1",
        config=config,
    )
    request = _mock_request(client_host="192.168.1.1")

    with pytest.raises(HTTPException) as exc_info:
        verify_token(token, request, config)

    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# mTLS SAN takes precedence
# ---------------------------------------------------------------------------


def test_mtls_san_takes_precedence_over_ip() -> None:
    """Token bound to mTLS SAN verifies via SAN; fails when bound to IP."""
    config = _make_config()
    san_value = "client.internal"
    ip_value = "192.168.99.1"

    # Token bound to the SAN value
    token_san = create_access_token(
        subject="dave",
        scopes=[Scope.SYNTHESIZE],
        client_identifier=san_value,
        config=config,
    )
    # Token bound to the IP
    token_ip = create_access_token(
        subject="dave",
        scopes=[Scope.SYNTHESIZE],
        client_identifier=ip_value,
        config=config,
    )

    request = _mock_request(client_host=ip_value, mtls_san=san_value)

    # Token bound to the SAN passes because extract_client_identifier returns SAN
    payload = verify_token(token_san, request, config)
    assert payload.sub == "dave"

    # Token bound to the IP must fail because extracted identifier is SAN
    with pytest.raises(HTTPException) as exc_info:
        verify_token(token_ip, request, config)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Invalid token (tampered signature)
# ---------------------------------------------------------------------------


def test_invalid_signature_raises_401() -> None:
    """A token with a tampered signature must raise HTTPException 401."""
    config = _make_config()
    token = create_access_token(
        subject="eve",
        scopes=[Scope.READ_RESULTS],
        client_identifier="192.168.1.1",
        config=config,
    )
    # Tamper: flip one character in the signature (last segment)
    parts = token.split(".")
    tampered = parts[2][:-1] + ("A" if parts[2][-1] != "A" else "B")
    bad_token = ".".join(parts[:2] + [tampered])

    request = _mock_request(client_host="192.168.1.1")
    with pytest.raises(HTTPException) as exc_info:
        verify_token(bad_token, request, config)

    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Scope enforcement inside verify_token (get_current_user dependency)
# ---------------------------------------------------------------------------


def test_missing_scope_raises_403() -> None:
    """Token with only synth:read cannot satisfy Scope.SYNTHESIZE.

    This tests the scope gate applied by the caller after verify_token returns
    a valid payload — caller raises 403 when scope check fails.
    """
    from synth_engine.shared.auth.scopes import has_required_scope

    config = _make_config()
    token = create_access_token(
        subject="frank",
        scopes=[Scope.READ_RESULTS],
        client_identifier="192.168.1.1",
        config=config,
    )
    request = _mock_request(client_host="192.168.1.1")
    payload = verify_token(token, request, config)

    # Simulates what get_current_user dependency does
    assert not has_required_scope(payload.scopes, Scope.SYNTHESIZE)


def test_admin_scope_satisfies_all() -> None:
    """Token with admin:* passes any required scope check via hierarchy."""
    from synth_engine.shared.auth.scopes import has_required_scope

    config = _make_config()
    token = create_access_token(
        subject="grace",
        scopes=[Scope.ADMIN],
        client_identifier="192.168.1.1",
        config=config,
    )
    request = _mock_request(client_host="192.168.1.1")
    payload = verify_token(token, request, config)

    assert has_required_scope(payload.scopes, Scope.SYNTHESIZE)
    assert has_required_scope(payload.scopes, Scope.VAULT_UNSEAL)


# ---------------------------------------------------------------------------
# get_jwt_config — environment variable reading
# ---------------------------------------------------------------------------


def test_get_jwt_config_raises_when_env_var_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_jwt_config raises RuntimeError when JWT_SECRET_KEY is absent."""
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        get_jwt_config()


def test_get_jwt_config_returns_config_when_env_var_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_jwt_config returns a JWTConfig populated from the environment."""
    test_val = "env-test-only"  # nosec B105 # pragma: allowlist secret
    monkeypatch.setenv("JWT_SECRET_KEY", test_val)
    config = get_jwt_config()
    assert isinstance(config, JWTConfig)
    assert config.secret_key == test_val


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
    assert payload.sub == "henry"


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


def test_get_current_user_os_env_read() -> None:
    """get_jwt_config reads the secret key from the OS environment."""
    secret = "env-only-test-val"  # nosec B105 # pragma: allowlist secret
    os.environ["JWT_SECRET_KEY"] = secret
    try:
        cfg = get_jwt_config()
        assert cfg.secret_key == secret
    finally:
        del os.environ["JWT_SECRET_KEY"]
