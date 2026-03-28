"""Unit tests for JWT authentication: token creation, validation, and algorithm security.

RED Phase — all tests must fail before implementation exists.

Tests cover:
- Token creation with required claims (sub, exp, iat, scope)
- Token validation returns correct claims
- Expired token raises AuthenticationError
- Malformed token raises AuthenticationError
- Algorithm confusion (alg:none) is rejected
- Algorithm confusion (RS256 for HS256-pinned key) is rejected
- AuthenticationGateMiddleware blocks unauthenticated requests (401)
- Exempt paths pass without authentication
- Pass-through mode when JWT_SECRET_KEY is empty
- Malformed bcrypt hash returns False from verify_operator_credentials
- Empty-string Bearer token returns 401
- Middleware dispatch error path (invalid token with valid secret)
- post_auth_token route (valid credentials → 200, invalid credentials → 401)

CONSTITUTION Priority 0: Security — JWT algorithm pinned, no alg:none
CONSTITUTION Priority 3: TDD — RED phase
Task: T39.1 — Add Authentication Middleware (JWT Bearer Token)
"""

from __future__ import annotations

import time
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# State isolation fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Prevents stale cached settings from leaking between tests that
    manipulate environment variables.

    Yields:
        None — setup and teardown only.
    """
    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass
    yield
    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Unit tests: token creation
# ---------------------------------------------------------------------------


def test_create_token_contains_required_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_token() must produce a JWT with sub, exp, iat, and scope claims.

    Arrange: set JWT_SECRET_KEY in the environment.
    Act: call create_token(sub="operator-1", scope=["read"]).
    Assert: decoded token contains sub, exp, iat, scope.
    """
    import jwt as pyjwt

    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.dependencies.auth import create_token

    token = create_token(sub="operator-1", scope=["read", "write"])

    decoded = pyjwt.decode(
        token,
        "test-secret-key-that-is-long-enough-for-hs256",
        algorithms=["HS256"],
    )
    assert decoded["sub"] == "operator-1"
    assert "exp" in decoded
    assert "iat" in decoded
    assert decoded["scope"] == ["read", "write"]


def test_create_token_exp_is_in_the_future(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_token() must set exp to a future timestamp.

    Arrange: set JWT_SECRET_KEY.
    Act: call create_token().
    Assert: decoded exp > current Unix timestamp.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    import jwt as pyjwt

    from synth_engine.bootstrapper.dependencies.auth import create_token

    token = create_token(sub="operator-1", scope=["read"])
    decoded = pyjwt.decode(
        token,
        "test-secret-key-that-is-long-enough-for-hs256",
        algorithms=["HS256"],
    )
    assert decoded["exp"] > int(time.time()), "exp must be a future timestamp"


# ---------------------------------------------------------------------------
# Unit tests: token validation
# ---------------------------------------------------------------------------


def test_verify_token_returns_claims_for_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify_token() must return claims dict for a valid, unexpired JWT.

    Arrange: create a valid token with known sub/scope.
    Act: call verify_token(token).
    Assert: returned claims contain correct sub and scope.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.dependencies.auth import create_token, verify_token

    token = create_token(sub="operator-42", scope=["admin"])
    claims = verify_token(token)

    assert claims["sub"] == "operator-42"
    assert claims["scope"] == ["admin"]


def test_verify_token_raises_for_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify_token() must raise AuthenticationError for an expired JWT.

    Arrange: craft an expired JWT using PyJWT directly.
    Act: call verify_token(expired_token).
    Assert: AuthenticationError is raised.
    """
    import jwt as pyjwt

    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    now = int(time.time())
    expired_token = pyjwt.encode(
        {
            "sub": "operator-1",
            "exp": now - 10,
            "iat": now - 3610,
            "scope": ["read"],
        },
        "test-secret-key-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationError, verify_token

    with pytest.raises(AuthenticationError) as exc_info:
        verify_token(expired_token)

    assert "expired" in str(exc_info.value).lower()


def test_verify_token_raises_for_malformed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify_token() must raise AuthenticationError for a garbage token.

    Arrange: pass a non-JWT string as the token.
    Act: call verify_token("not.a.token").
    Assert: AuthenticationError is raised.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationError, verify_token

    with pytest.raises(AuthenticationError):
        verify_token("not.a.valid.jwt.token")


def test_verify_token_raises_for_wrong_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify_token() must raise AuthenticationError when signature is wrong.

    Arrange: encode a JWT with a different secret key.
    Act: call verify_token(token) where settings use a different secret.
    Assert: AuthenticationError is raised.
    """
    import jwt as pyjwt

    monkeypatch.setenv("JWT_SECRET_KEY", "correct-secret-key-that-is-long-enough-x")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    now = int(time.time())
    wrong_key_token = pyjwt.encode(
        {
            "sub": "attacker",
            "exp": now + 3600,
            "iat": now,
            "scope": ["admin"],
        },
        "wrong-secret-key-that-is-completely-different",
        algorithm="HS256",
    )

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationError, verify_token

    with pytest.raises(AuthenticationError):
        verify_token(wrong_key_token)


# ---------------------------------------------------------------------------
# Unit tests: algorithm confusion prevention
# ---------------------------------------------------------------------------


def test_verify_token_rejects_alg_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify_token() must reject tokens claiming alg: none.

    Security: algorithm confusion where an attacker sets alg=none to bypass
    signature verification. The decoder must never accept this.

    Arrange: craft a JWT header with alg=none manually.
    Act: call verify_token(forged_token).
    Assert: AuthenticationError is raised — never accepts alg:none.
    """
    import base64
    import json

    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    now = int(time.time())
    # Craft a token with alg:none header
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        .rstrip(b"=")
        .decode()
    )
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sub": "attacker",
                    "exp": now + 3600,
                    "iat": now,
                    "scope": ["admin"],
                }
            ).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    none_alg_token = f"{header}.{payload}."

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationError, verify_token

    with pytest.raises(AuthenticationError):
        verify_token(none_alg_token)


def test_verify_token_rejects_algorithm_confusion_rs256(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify_token() must reject tokens signed with RS256 when pinned to HS256.

    Security: algorithm confusion where an attacker uses a different algorithm
    than the one the server expects. Pinning prevents downgrade attacks.

    Arrange: settings use HS256; craft a token encoding with RS256 claims.
    Act: call verify_token() with a token claiming RS256.
    Assert: AuthenticationError is raised.
    """
    import base64
    import json

    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    now = int(time.time())
    # Craft a malformed token with RS256 header but no real signature
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        .rstrip(b"=")
        .decode()
    )
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sub": "attacker",
                    "exp": now + 3600,
                    "iat": now,
                    "scope": ["admin"],
                }
            ).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    rs256_token = f"{header}.{payload}.fakesignature"

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationError, verify_token

    with pytest.raises(AuthenticationError):
        verify_token(rs256_token)


# ---------------------------------------------------------------------------
# Unit tests: operator credential verification
# ---------------------------------------------------------------------------


def test_verify_operator_credentials_returns_true_for_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify_operator_credentials() returns True when passphrase matches stored hash.

    Arrange: set OPERATOR_CREDENTIALS_HASH to a known bcrypt hash of "short-pass".
    Act: call verify_operator_credentials("short-pass").
    Assert: returns True.
    """
    import bcrypt

    hashed = bcrypt.hashpw(b"short-pass", bcrypt.gensalt()).decode()
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", hashed)
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")

    from synth_engine.bootstrapper.dependencies.auth import verify_operator_credentials

    result = verify_operator_credentials("short-pass")
    assert result is True


def test_verify_operator_credentials_returns_false_for_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify_operator_credentials() returns False when passphrase is wrong.

    Arrange: set OPERATOR_CREDENTIALS_HASH to a hash of "correct-pass".
    Act: call verify_operator_credentials("wrong-pass").
    Assert: returns False.
    """
    import bcrypt

    hashed = bcrypt.hashpw(b"correct-pass", bcrypt.gensalt()).decode()
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", hashed)
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")

    from synth_engine.bootstrapper.dependencies.auth import verify_operator_credentials

    result = verify_operator_credentials("wrong-pass")
    assert result is False


def test_verify_operator_credentials_returns_false_when_hash_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify_operator_credentials() returns False when no hash is configured.

    Arrange: OPERATOR_CREDENTIALS_HASH is empty string (default).
    Act: call verify_operator_credentials("anything").
    Assert: returns False — no credentials configured means no access.
    """
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")

    from synth_engine.bootstrapper.dependencies.auth import verify_operator_credentials

    result = verify_operator_credentials("anything")
    assert result is False


def test_verify_operator_credentials_returns_false_for_malformed_bcrypt_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify_operator_credentials() returns False when stored_hash is not a valid bcrypt format.

    Security: if an operator misconfigures OPERATOR_CREDENTIALS_HASH with a
    non-bcrypt string, the function must deny access rather than raise an
    unhandled exception. The broad except clause in auth.py covers this path.

    Arrange: set OPERATOR_CREDENTIALS_HASH to a garbage (non-bcrypt) string.
    Act: call verify_operator_credentials("any-passphrase").
    Assert: returns False without raising.
    """
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "not-a-valid-bcrypt-hash-at-all")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")

    from synth_engine.bootstrapper.dependencies.auth import verify_operator_credentials

    result = verify_operator_credentials("any-passphrase")
    assert result is False


# ---------------------------------------------------------------------------
# Unit tests: AuthenticationGateMiddleware dispatch logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_allows_exempt_path_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AuthenticationGateMiddleware must pass exempt paths without any token.

    Arrange: mock a request to /health (exempt path) with no Authorization header.
    Act: call dispatch() with a mocked call_next.
    Assert: call_next is called (request passes through).
    """
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationGateMiddleware

    mock_app = MagicMock()
    middleware = AuthenticationGateMiddleware(mock_app)

    mock_request = MagicMock()
    mock_request.url.path = "/health"
    mock_request.headers = {}

    expected_response = MagicMock()
    mock_call_next = AsyncMock(return_value=expected_response)

    response = await middleware.dispatch(mock_request, mock_call_next)

    mock_call_next.assert_called_once_with(mock_request)
    assert response is expected_response


@pytest.mark.asyncio
async def test_middleware_allows_auth_token_path_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AuthenticationGateMiddleware must pass /auth/token without authentication.

    /auth/token is the token issuance endpoint — it must be pre-auth exempt.

    Arrange: mock a request to /auth/token with no Authorization header.
    Act: call dispatch() with a mocked call_next.
    Assert: call_next is called (request passes through).
    """
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationGateMiddleware

    mock_app = MagicMock()
    middleware = AuthenticationGateMiddleware(mock_app)

    mock_request = MagicMock()
    mock_request.url.path = "/auth/token"
    mock_request.headers = {}

    expected_response = MagicMock()
    mock_call_next = AsyncMock(return_value=expected_response)

    response = await middleware.dispatch(mock_request, mock_call_next)

    mock_call_next.assert_called_once_with(mock_request)
    assert response is expected_response


@pytest.mark.asyncio
async def test_middleware_pass_through_when_jwt_secret_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AuthenticationGateMiddleware passes requests through when JWT_SECRET_KEY is empty.

    Unconfigured mode: when JWT_SECRET_KEY is empty, the middleware logs a WARNING
    and allows all requests without token verification. This supports development
    and initial setup before credentials are configured.

    Arrange: JWT_SECRET_KEY is empty; request to a protected path with no token.
    Act: call dispatch() with a mocked call_next.
    Assert: call_next is invoked (request passes through despite no token).
    """
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationGateMiddleware

    mock_app = MagicMock()
    middleware = AuthenticationGateMiddleware(mock_app)

    mock_request = MagicMock()
    mock_request.url.path = "/api/v1/jobs"
    mock_request.headers = MagicMock()
    mock_request.headers.get = MagicMock(return_value=None)

    expected_response = MagicMock()
    mock_call_next = AsyncMock(return_value=expected_response)

    response = await middleware.dispatch(mock_request, mock_call_next)

    mock_call_next.assert_called_once_with(mock_request)
    assert response is expected_response


@pytest.mark.asyncio
async def test_middleware_returns_401_for_missing_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AuthenticationGateMiddleware must return 401 when Authorization header is absent.

    Arrange: mock a request to /jobs (protected path) with no Authorization header.
    Act: call dispatch().
    Assert: returns a 401 JSONResponse (RFC 7807 format).
    """
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationGateMiddleware

    mock_app = MagicMock()
    middleware = AuthenticationGateMiddleware(mock_app)

    mock_request = MagicMock()
    mock_request.url.path = "/api/v1/jobs"
    mock_request.headers = MagicMock()
    mock_request.headers.get = MagicMock(return_value=None)

    mock_call_next = AsyncMock()

    response = await middleware.dispatch(mock_request, mock_call_next)

    assert response.status_code == 401
    mock_call_next.assert_not_called()


@pytest.mark.asyncio
async def test_middleware_returns_401_for_invalid_bearer_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AuthenticationGateMiddleware must return 401 for malformed Authorization headers.

    Arrange: set Authorization header to "Token abc" (not Bearer scheme).
    Act: call dispatch() on a protected path.
    Assert: returns 401.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationGateMiddleware

    mock_app = MagicMock()
    middleware = AuthenticationGateMiddleware(mock_app)

    mock_request = MagicMock()
    mock_request.url.path = "/api/v1/jobs"
    mock_request.headers = MagicMock()
    mock_request.headers.get = MagicMock(return_value="Token not-bearer-scheme")

    mock_call_next = AsyncMock()

    response = await middleware.dispatch(mock_request, mock_call_next)

    assert response.status_code == 401
    mock_call_next.assert_not_called()


@pytest.mark.asyncio
async def test_middleware_returns_401_for_empty_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AuthenticationGateMiddleware must return 401 for 'Authorization: Bearer ' with empty token.

    A header value of "Bearer " (with trailing space but no token) presents an
    empty string to verify_token(), which must be rejected with 401.

    Arrange: JWT_SECRET_KEY is set; Authorization header is "Bearer " (empty token).
    Act: call dispatch() on a protected path.
    Assert: returns 401 — empty token string is not a valid JWT.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationGateMiddleware

    mock_app = MagicMock()
    middleware = AuthenticationGateMiddleware(mock_app)

    mock_request = MagicMock()
    mock_request.url.path = "/api/v1/jobs"
    mock_request.headers = MagicMock()
    # "Bearer " with a trailing space but no token — token string is ""
    mock_request.headers.get = MagicMock(return_value="Bearer ")

    mock_call_next = AsyncMock()

    response = await middleware.dispatch(mock_request, mock_call_next)

    assert response.status_code == 401
    mock_call_next.assert_not_called()


@pytest.mark.asyncio
async def test_middleware_returns_401_for_invalid_token_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AuthenticationGateMiddleware must return 401 when Bearer token is invalid.

    This covers the dispatch error path in auth.py lines 296-300: when a
    syntactically-structured Bearer header is present but the token is not
    a valid JWT, verify_token() raises AuthenticationError which the
    middleware catches and converts to a 401.

    Arrange: JWT_SECRET_KEY is set to a valid value; token is "invalid-token".
    Act: call dispatch() on a protected path.
    Assert: returns 401; call_next is not invoked.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationGateMiddleware

    mock_app = MagicMock()
    middleware = AuthenticationGateMiddleware(mock_app)

    mock_request = MagicMock()
    mock_request.url.path = "/api/v1/jobs"
    mock_request.headers = MagicMock()
    mock_request.headers.get = MagicMock(return_value="Bearer this-is-not-a-valid-jwt-token")

    mock_call_next = AsyncMock()

    response = await middleware.dispatch(mock_request, mock_call_next)

    assert response.status_code == 401
    mock_call_next.assert_not_called()


@pytest.mark.asyncio
async def test_middleware_passes_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """AuthenticationGateMiddleware passes requests with a valid Bearer token.

    Arrange: create a valid JWT token; add as Authorization: Bearer <token>.
    Act: call dispatch() on a protected path.
    Assert: call_next is invoked (request reaches the route handler).
    """
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.dependencies.auth import (
        AuthenticationGateMiddleware,
        create_token,
    )

    token = create_token(sub="operator-1", scope=["read"])

    mock_app = MagicMock()
    middleware = AuthenticationGateMiddleware(mock_app)

    mock_request = MagicMock()
    mock_request.url.path = "/api/v1/jobs"
    mock_request.headers = MagicMock()
    mock_request.headers.get = MagicMock(return_value=f"Bearer {token}")

    expected_response = MagicMock()
    mock_call_next = AsyncMock(return_value=expected_response)

    response = await middleware.dispatch(mock_request, mock_call_next)

    mock_call_next.assert_called_once_with(mock_request)
    assert response is expected_response


# ---------------------------------------------------------------------------
# Unit tests: AUTH_EXEMPT_PATHS completeness
# ---------------------------------------------------------------------------


def test_auth_exempt_paths_includes_all_required_endpoints() -> None:
    """AUTH_EXEMPT_PATHS must include all pre-authentication endpoints.

    This test asserts that the full required set of exempt paths is present,
    preventing a regression where a required endpoint is accidentally removed.

    Updated in P50 review fix: /security/shred and /security/keys/rotate are
    removed from the required set because they must NOT bypass authentication.
    Both routes require JWT auth with security:admin scope (ADV-P47-04).

    Updated in T66.2 (ADV-P62-01): /docs, /redoc, /openapi.json are removed
    from the required exempt set.  These paths are now protected by the auth
    gate in development mode and return 404 in production mode.  They must
    NOT be in AUTH_EXEMPT_PATHS because bypassing auth on doc endpoints
    allows unauthenticated API schema reconnaissance.
    """
    from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

    required_exempt: set[str] = {
        "/unseal",
        "/health",
        "/metrics",
        "/license/challenge",
        "/license/activate",
        "/auth/token",
    }
    missing = required_exempt - AUTH_EXEMPT_PATHS
    assert not missing, f"AUTH_EXEMPT_PATHS is missing required paths: {missing}"

    # SECURITY: both security routes must require JWT auth — they must NOT
    # bypass AuthenticationGateMiddleware (ADV-P47-04, P50 review fix).
    assert "/security/shred" not in AUTH_EXEMPT_PATHS, (
        "/security/shred must NOT be in AUTH_EXEMPT_PATHS (requires JWT auth)"
    )
    assert "/security/keys/rotate" not in AUTH_EXEMPT_PATHS, (
        "/security/keys/rotate must NOT be in AUTH_EXEMPT_PATHS (requires JWT auth)"
    )

    # SECURITY: documentation paths must NOT be in AUTH_EXEMPT_PATHS (T66.2).
    # /docs and /redoc return 404 in production (disabled by create_app()).
    # In development they require a Bearer token like any other GET endpoint.
    assert "/docs" not in AUTH_EXEMPT_PATHS, (
        "/docs must NOT be in AUTH_EXEMPT_PATHS (T66.2 — ADV-P62-01)"
    )
    assert "/redoc" not in AUTH_EXEMPT_PATHS, (
        "/redoc must NOT be in AUTH_EXEMPT_PATHS (T66.2 — ADV-P62-01)"
    )
    assert "/openapi.json" not in AUTH_EXEMPT_PATHS, (
        "/openapi.json must NOT be in AUTH_EXEMPT_PATHS (T66.2 — ADV-P62-01)"
    )


# ---------------------------------------------------------------------------
# Unit tests: ConclaveSettings JWT fields
# ---------------------------------------------------------------------------


def test_conclave_settings_jwt_algorithm_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.jwt_algorithm defaults to 'HS256'.

    Arrange: no JWT_ALGORITHM in environment.
    Act: construct ConclaveSettings().
    Assert: jwt_algorithm == "HS256".
    """
    monkeypatch.delenv("JWT_ALGORITHM", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    settings = ConclaveSettings()
    assert settings.jwt_algorithm == "HS256"


def test_conclave_settings_jwt_expiry_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.jwt_expiry_seconds defaults to 3600.

    Arrange: no JWT_EXPIRY_SECONDS in environment.
    Act: construct ConclaveSettings().
    Assert: jwt_expiry_seconds == 3600.
    """
    monkeypatch.delenv("JWT_EXPIRY_SECONDS", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    settings = ConclaveSettings()
    assert settings.jwt_expiry_seconds == 3600


def test_conclave_settings_operator_credentials_hash_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings.operator_credentials_hash defaults to empty string.

    Arrange: no OPERATOR_CREDENTIALS_HASH in environment.
    Act: construct ConclaveSettings().
    Assert: operator_credentials_hash == "".
    """
    monkeypatch.delenv("OPERATOR_CREDENTIALS_HASH", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    settings = ConclaveSettings()
    assert settings.operator_credentials_hash == ""


def test_conclave_settings_jwt_secret_key_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConclaveSettings.jwt_secret_key defaults to empty string.

    Arrange: no JWT_SECRET_KEY in environment.
    Act: construct ConclaveSettings().
    Assert: jwt_secret_key == "".
    """
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    settings = ConclaveSettings()
    assert settings.jwt_secret_key.get_secret_value() == ""


def test_conclave_settings_jwt_fields_accept_env_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings JWT fields read from environment variables.

    Arrange: set all JWT env vars to non-default values.
    Act: construct ConclaveSettings().
    Assert: all fields reflect the env values.
    """
    monkeypatch.setenv("JWT_ALGORITHM", "HS384")
    monkeypatch.setenv("JWT_EXPIRY_SECONDS", "7200")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "$2b$12$fakehash")
    monkeypatch.setenv("JWT_SECRET_KEY", "my-custom-secret-key")

    from synth_engine.shared.settings import ConclaveSettings

    settings = ConclaveSettings()
    assert settings.jwt_algorithm == "HS384"
    assert settings.jwt_expiry_seconds == 7200
    assert settings.operator_credentials_hash == "$2b$12$fakehash"
    secret_key_value = settings.jwt_secret_key.get_secret_value()
    assert secret_key_value == "my-custom-secret-key"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Unit tests: AuthenticationError
# ---------------------------------------------------------------------------


def test_authentication_error_is_exception() -> None:
    """AuthenticationError must be a subclass of Exception.

    This verifies the exception type is correctly defined as a catchable
    exception — not a base class or unrelated type.
    """
    from synth_engine.bootstrapper.dependencies.auth import AuthenticationError

    err = AuthenticationError("test message")
    assert isinstance(err, Exception)
    assert str(err) == "test message"


# ---------------------------------------------------------------------------
# Unit tests: post_auth_token route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_auth_token_returns_200_and_token_response_for_valid_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """post_auth_token() returns 200 with a TokenResponse for valid credentials.

    Arrange: set OPERATOR_CREDENTIALS_HASH to a known bcrypt hash; set JWT_SECRET_KEY.
    Act: POST /auth/token with valid username and passphrase via TestClient.
    Assert: response status is 200; body contains access_token and token_type=="bearer".
    """
    import bcrypt
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    hashed = bcrypt.hashpw(b"correct-passphrase", bcrypt.gensalt()).decode()
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", hashed)
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

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
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str)
    assert len(body["access_token"]) > 0


@pytest.mark.asyncio
async def test_post_auth_token_returns_401_for_invalid_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """post_auth_token() returns 401 for wrong passphrase.

    Arrange: set OPERATOR_CREDENTIALS_HASH to a known bcrypt hash; set JWT_SECRET_KEY.
    Act: POST /auth/token with correct username but wrong passphrase via TestClient.
    Assert: response status is 401; no access_token in body.
    """
    import bcrypt
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    hashed = bcrypt.hashpw(b"correct-passphrase", bcrypt.gensalt()).decode()
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", hashed)
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.routers.auth import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/auth/token",
        json={"username": "operator", "passphrase": "wrong-passphrase"},
    )

    assert response.status_code == 401
    body = response.json()
    assert "access_token" not in body


# ---------------------------------------------------------------------------
# T58.2: Eliminate JWT double-decode — request.state.jwt_claims cache
# ---------------------------------------------------------------------------


class TestJWTClaimsCache:
    """T58.2: get_current_operator must cache claims on request.state.jwt_claims.

    After T58.2:
    - get_current_operator stores decoded claims on request.state.jwt_claims
    - require_scope._check_scope reads from request.state.jwt_claims
    - verify_token is called exactly once per request (not twice)
    - Pass-through mode (empty JWT secret, non-production) stores {} as claims
    - If jwt_claims is absent when _check_scope runs, raise 401
    """

    def test_get_current_operator_stores_claims_on_request_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_current_operator must store decoded claims in request.state.jwt_claims.

        After successful token verification, the claims dict must be stored on
        request.state so that require_scope can read it without re-decoding.
        """
        from unittest.mock import MagicMock

        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")

        from synth_engine.bootstrapper.dependencies.auth import create_token, get_current_operator

        token = create_token(sub="operator-42", scope=["read", "write"])

        mock_request = MagicMock()
        mock_request.headers.get = MagicMock(return_value=f"Bearer {token}")
        mock_request.state = MagicMock()

        result = get_current_operator(mock_request)

        assert result == "operator-42", (
            f"get_current_operator must return sub claim, got {result!r}"
        )
        # Claims must be stored on request.state.jwt_claims
        assert mock_request.state.jwt_claims is not None, (
            "get_current_operator must set request.state.jwt_claims"
        )
        stored_claims = mock_request.state.jwt_claims
        assert stored_claims["sub"] == "operator-42", (
            f"Stored claims must contain sub='operator-42', got {stored_claims!r}"
        )
        assert stored_claims["scope"] == ["read", "write"], (
            f"Stored claims must contain scope=['read', 'write'], got {stored_claims!r}"
        )

    def test_get_current_operator_pass_through_stores_empty_claims(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In pass-through mode (empty JWT secret, non-production), jwt_claims must be {}.

        This prevents AttributeError in require_scope when it reads
        request.state.jwt_claims after a pass-through get_current_operator call.
        """
        from unittest.mock import MagicMock

        monkeypatch.setenv("JWT_SECRET_KEY", "")
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.bootstrapper.dependencies.auth import get_current_operator

        mock_request = MagicMock()
        mock_request.state = MagicMock()

        result = get_current_operator(mock_request)

        assert result == "", f"Pass-through mode must return empty string sub, got {result!r}"
        assert mock_request.state.jwt_claims == {}, (
            f"Pass-through mode must store empty dict as jwt_claims, "
            f"got {mock_request.state.jwt_claims!r}"
        )

    def test_require_scope_reads_claims_from_request_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """require_scope._check_scope must read jwt_claims from request.state, not re-parse header.

        Arrange: pre-populate request.state.jwt_claims with claims containing the
        required scope.  Assert that verify_token is NOT called by _check_scope.
        """
        from unittest.mock import MagicMock, patch

        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")

        from synth_engine.bootstrapper.dependencies.auth import require_scope

        mock_request = MagicMock()
        mock_request.state = MagicMock()
        mock_request.state.jwt_claims = {"sub": "op-99", "scope": ["read:data"]}

        check_scope = require_scope("read:data")

        with patch("synth_engine.bootstrapper.dependencies.auth.verify_token") as mock_verify:
            result = check_scope(request=mock_request, operator="op-99")

        # verify_token must NOT be called — claims already in request.state
        mock_verify.assert_not_called()
        assert result == "op-99", f"require_scope must return operator sub, got {result!r}"

    def test_require_scope_raises_403_when_scope_not_in_claims(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """require_scope._check_scope raises 403 when required scope is absent.

        The scope is absent from the cached claims on request.state.
        """
        from unittest.mock import MagicMock

        from fastapi import HTTPException

        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")

        from synth_engine.bootstrapper.dependencies.auth import require_scope

        mock_request = MagicMock()
        mock_request.state = MagicMock()
        mock_request.state.jwt_claims = {"sub": "op-99", "scope": ["read:data"]}

        check_scope = require_scope("security:admin")

        with pytest.raises(HTTPException) as exc_info:
            check_scope(request=mock_request, operator="op-99")

        assert exc_info.value.status_code == 403, (
            f"Missing scope must raise 403, got {exc_info.value.status_code}"
        )

    def test_require_scope_raises_401_when_jwt_claims_absent_from_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """require_scope._check_scope raises 401 when request.state.jwt_claims is absent.

        This guards against middleware reordering where get_current_operator did
        not run before require_scope.  An absent jwt_claims attribute must not
        cause AttributeError — instead it must raise 401 "Authentication required".
        """
        from unittest.mock import MagicMock

        from fastapi import HTTPException
        from starlette.datastructures import State

        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")

        from synth_engine.bootstrapper.dependencies.auth import require_scope

        mock_request = MagicMock()
        # Provide a real State object so hasattr returns False for jwt_claims
        mock_request.state = State()

        check_scope = require_scope("read:data")

        with pytest.raises(HTTPException) as exc_info:
            check_scope(request=mock_request, operator="op-99")

        assert exc_info.value.status_code == 401, (
            f"Absent jwt_claims must raise 401, got {exc_info.value.status_code}"
        )
        detail_lower = exc_info.value.detail.lower()
        assert "authentication" in detail_lower or "required" in detail_lower, (
            f"401 detail must reference authentication, got {exc_info.value.detail!r}"
        )

    def test_verify_token_called_once_per_request_with_require_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """verify_token must be called exactly once per request when require_scope is used.

        With T58.2, get_current_operator decodes once and caches.
        require_scope reads from the cache.  Total verify_token calls == 1.
        """
        from unittest.mock import MagicMock, patch

        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")

        from synth_engine.bootstrapper.dependencies.auth import (
            create_token,
            get_current_operator,
            require_scope,
        )

        token = create_token(sub="operator-7", scope=["read:data"])

        mock_request = MagicMock()
        mock_request.headers.get = MagicMock(return_value=f"Bearer {token}")
        mock_request.state = MagicMock()

        with patch(
            "synth_engine.bootstrapper.dependencies.auth.verify_token",
            wraps=__import__(
                "synth_engine.bootstrapper.dependencies.auth",
                fromlist=["verify_token"],
            ).verify_token,
        ) as mock_verify:
            # Step 1: get_current_operator decodes the token
            operator = get_current_operator(mock_request)
            first_call_count = mock_verify.call_count

            # Step 2: require_scope reads from cache (must NOT call verify_token again)
            check_scope = require_scope("read:data")
            check_scope(request=mock_request, operator=operator)
            second_call_count = mock_verify.call_count

        assert first_call_count == 1, (
            f"get_current_operator must call verify_token exactly once, called {first_call_count}"
        )
        assert second_call_count == 1, (
            f"verify_token must be called exactly once total (not re-called by require_scope), "
            f"called {second_call_count} times"
        )
