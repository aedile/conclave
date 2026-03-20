"""Unit tests for the rate limiting middleware (T39.3).

Tests exercise RateLimitGateMiddleware in isolation using a minimal FastAPI
app, following the established middleware isolation pattern from the project.

Coverage targets:
- AC1: Rate limiting active on all endpoints.
- AC2: Exceeding limit returns 429 with RFC 7807 body and Retry-After header.
- AC3: /unseal limited to 5/min per IP.
- AC4: Authenticated endpoints limited to 60/min per operator.
- AC5: Rate limit values configurable via ConclaveSettings.
- AC6: Exceed → 429; within limit → 200; different operators have independent limits.

CONSTITUTION Priority 0: Security — brute-force protection on vault/auth
CONSTITUTION Priority 3: TDD
Task: T39.3 — Add Rate Limiting Middleware
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Settings cache isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

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
# App factory helpers
# ---------------------------------------------------------------------------


def _build_isolated_app(
    *,
    unseal_limit: int = 5,
    auth_limit: int = 10,
    general_limit: int = 60,
    download_limit: int = 10,
) -> Any:
    """Build a minimal FastAPI app with only RateLimitGateMiddleware.

    Used for isolation tests — no vault, license, or auth middleware layers.

    Args:
        unseal_limit: Requests per minute allowed on /unseal per IP.
        auth_limit: Requests per minute allowed on /auth/token per IP.
        general_limit: Requests per minute allowed on all other endpoints per operator.
        download_limit: Requests per minute allowed on download endpoints per operator.

    Returns:
        A FastAPI instance with RateLimitGateMiddleware registered.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware

    app = FastAPI()
    app.add_middleware(
        RateLimitGateMiddleware,
        unseal_limit=unseal_limit,
        auth_limit=auth_limit,
        general_limit=general_limit,
        download_limit=download_limit,
    )

    @app.post("/unseal")
    async def _unseal_route() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    @app.post("/auth/token")
    async def _auth_route() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    @app.get("/jobs")
    async def _jobs_route() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    @app.get("/jobs/{job_id}/download")
    async def _download_route(job_id: str) -> JSONResponse:
        return JSONResponse(content={"ok": True})

    @app.get("/health")
    async def _health_route() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    return app


# ---------------------------------------------------------------------------
# AC6: Within-limit request succeeds with 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_within_limit_returns_200() -> None:
    """A single request within the limit must return 200.

    Arrange: build the app with limit=5 on /unseal.
    Act: POST /unseal once.
    Assert: HTTP 200.
    """
    app = _build_isolated_app(unseal_limit=5)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/unseal", headers={"X-Forwarded-For": "1.2.3.4"})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# AC2 + AC3: /unseal per-IP limit enforced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unseal_exceeds_limit_returns_429() -> None:
    """Exceeding the /unseal limit returns 429 Too Many Requests.

    Arrange: build the app with unseal_limit=2 (low for test speed).
    Act: POST /unseal 3 times from the same IP.
    Assert: First two succeed (200), third is rejected (429).
    """
    app = _build_isolated_app(unseal_limit=2)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Forwarded-For": "10.0.0.1"}
        responses = [await client.post("/unseal", headers=headers) for _ in range(3)]

    status_codes = [r.status_code for r in responses]
    assert 429 in status_codes, f"Expected 429 in {status_codes}"
    # First N requests must succeed
    assert responses[0].status_code == 200
    assert responses[1].status_code == 200
    assert responses[2].status_code == 429


# ---------------------------------------------------------------------------
# AC2: RFC 7807 response format for 429
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_response_is_rfc7807_format() -> None:
    """A 429 response body must conform to RFC 7807 Problem Details format.

    Arrange: build with limit=1; exhaust it.
    Act: make the second request to trigger 429.
    Assert: body has type, status, title, detail fields; Retry-After header present.
    """
    app = _build_isolated_app(unseal_limit=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Forwarded-For": "10.0.0.2"}
        await client.post("/unseal", headers=headers)
        response = await client.post("/unseal", headers=headers)

    assert response.status_code == 429
    body = response.json()
    assert "type" in body, f"RFC 7807 requires 'type' field; got: {body}"
    assert "status" in body, f"RFC 7807 requires 'status' field; got: {body}"
    assert "title" in body, f"RFC 7807 requires 'title' field; got: {body}"
    assert "detail" in body, f"RFC 7807 requires 'detail' field; got: {body}"
    assert body["status"] == 429


# ---------------------------------------------------------------------------
# AC2: Retry-After header present on 429
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_response_has_retry_after_header() -> None:
    """A 429 response must include a Retry-After header.

    Arrange: build with limit=1; exhaust it.
    Act: make the second request.
    Assert: Retry-After header is present and is a positive integer string.
    """
    app = _build_isolated_app(unseal_limit=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Forwarded-For": "10.0.0.3"}
        await client.post("/unseal", headers=headers)
        response = await client.post("/unseal", headers=headers)

    assert response.status_code == 429
    assert "retry-after" in response.headers, (
        f"429 response must include Retry-After header; headers: {dict(response.headers)}"
    )
    retry_after = response.headers["retry-after"]
    assert int(retry_after) >= 0, f"Retry-After must be a non-negative integer; got: {retry_after}"


# ---------------------------------------------------------------------------
# AC6: Different IPs have independent limits on /unseal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_ips_have_independent_limits_on_unseal() -> None:
    """Two different IPs must have independent rate limit buckets on /unseal.

    Arrange: build with limit=1.
    Act: exhaust limit for IP A, then make a request from IP B.
    Assert: IP A gets 429 on second request; IP B still gets 200.
    """
    app = _build_isolated_app(unseal_limit=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Exhaust IP A
        await client.post("/unseal", headers={"X-Forwarded-For": "10.0.1.1"})
        response_a2 = await client.post("/unseal", headers={"X-Forwarded-For": "10.0.1.1"})
        # IP B should still be allowed
        response_b1 = await client.post("/unseal", headers={"X-Forwarded-For": "10.0.1.2"})

    assert response_a2.status_code == 429, "IP A must be rate limited after exceeding"
    assert response_b1.status_code == 200, "IP B must NOT be affected by IP A's limit"


# ---------------------------------------------------------------------------
# AC6: Different operators have independent limits on authenticated endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_operators_have_independent_limits() -> None:
    """Two different authenticated operators have independent rate limit buckets.

    Uses real JWT tokens with distinct 'sub' claims.  The middleware decodes
    the sub claim without signature verification to identify the operator.

    Arrange: build with general_limit=1; two operators with distinct JWTs.
    Act: exhaust limit for operator A; make request from operator B.
    Assert: operator A gets 429; operator B gets 200.
    """
    import time

    import jwt as pyjwt

    secret = "test-secret-key-long-enough-32ch"  # pragma: allowlist secret
    now = int(time.time())

    def _make_token(sub: str) -> str:
        """Create a test JWT token for the given subject."""
        return pyjwt.encode(
            {"sub": sub, "iat": now, "exp": now + 3600, "scope": []},
            secret,
            algorithm="HS256",
        )

    token_a = _make_token("operator-alpha")
    token_b = _make_token("operator-beta")

    app = _build_isolated_app(general_limit=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Exhaust operator A
        await client.get("/jobs", headers=headers_a)
        response_a2 = await client.get("/jobs", headers=headers_a)
        # Operator B should still be allowed
        response_b1 = await client.get("/jobs", headers=headers_b)

    assert response_a2.status_code == 429, "Operator A must be rate limited after exceeding"
    assert response_b1.status_code == 200, "Operator B must NOT be affected by operator A's limit"


# ---------------------------------------------------------------------------
# AC3: /auth/token per-IP limit enforced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_token_exceeds_limit_returns_429() -> None:
    """Exceeding the /auth/token limit returns 429.

    Arrange: build the app with auth_limit=1.
    Act: POST /auth/token twice from the same IP.
    Assert: Second request returns 429.
    """
    app = _build_isolated_app(auth_limit=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Forwarded-For": "10.0.2.1"}
        await client.post("/auth/token", headers=headers)
        response = await client.post("/auth/token", headers=headers)

    assert response.status_code == 429


# ---------------------------------------------------------------------------
# AC5: Settings-driven configuration (integration point)
# ---------------------------------------------------------------------------


def test_rate_limit_settings_fields_exist() -> None:
    """ConclaveSettings must expose rate limit configuration fields.

    Verifies that the four new rate limit fields are present on the settings
    model and have the correct default values per the task spec.
    """
    from synth_engine.shared.settings import ConclaveSettings

    settings = ConclaveSettings(
        database_url="sqlite:///:memory:",
        audit_key="a" * 64,
    )
    assert settings.rate_limit_unseal_per_minute == 5
    assert settings.rate_limit_auth_per_minute == 10
    assert settings.rate_limit_general_per_minute == 60
    assert settings.rate_limit_download_per_minute == 10


# ---------------------------------------------------------------------------
# AC1: Middleware is registered in the full middleware stack
# ---------------------------------------------------------------------------


def test_rate_limit_middleware_is_importable() -> None:
    """RateLimitGateMiddleware must be importable from its canonical path.

    Verifies that the module exists and the class is exported.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware

    assert RateLimitGateMiddleware is not None


def test_rate_limit_middleware_registered_in_setup_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """setup_middleware() must register RateLimitGateMiddleware on the app.

    Starlette wraps each middleware in a ``Middleware`` namedtuple-like object.
    The actual class is accessible via ``m.cls``.

    Arrange: build the full app with settings configured.
    Assert: the middleware stack includes RateLimitGateMiddleware via m.cls.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    middleware_class_names = [m.cls.__name__ for m in app.user_middleware]
    assert "RateLimitGateMiddleware" in middleware_class_names, (
        f"RateLimitGateMiddleware must be in middleware stack; found: {middleware_class_names}"
    )


# ---------------------------------------------------------------------------
# IP extraction: X-Forwarded-For precedence
# ---------------------------------------------------------------------------


def test_extract_client_ip_uses_x_forwarded_for() -> None:
    """_extract_client_ip must prefer X-Forwarded-For over client.host.

    The first IP in the X-Forwarded-For header is the client IP in a proxied
    deployment (the rightmost is the last proxy, leftmost is the real client).
    """
    from unittest.mock import MagicMock

    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_client_ip

    request = MagicMock()
    request.headers = {"X-Forwarded-For": "203.0.113.1, 192.168.1.1"}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    ip = _extract_client_ip(request)
    assert ip == "203.0.113.1"


def test_extract_client_ip_falls_back_to_client_host() -> None:
    """_extract_client_ip falls back to request.client.host when no X-Forwarded-For.

    Covers the direct (non-proxied) case.
    """
    from unittest.mock import MagicMock

    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_client_ip

    request = MagicMock()
    request.headers = {}
    request.client = MagicMock()
    request.client.host = "192.168.99.1"

    ip = _extract_client_ip(request)
    assert ip == "192.168.99.1"


def test_extract_client_ip_returns_unknown_when_no_client() -> None:
    """_extract_client_ip returns 'unknown' when client is None and no forwarded header.

    Guards against None request.client (e.g. testclient without socket).
    """
    from unittest.mock import MagicMock

    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_client_ip

    request = MagicMock()
    request.headers = {}
    request.client = None

    ip = _extract_client_ip(request)
    assert ip == "unknown"


# ---------------------------------------------------------------------------
# Operator extraction: JWT sub claim
# ---------------------------------------------------------------------------


def test_extract_operator_id_returns_sub_from_valid_token() -> None:
    """_extract_operator_id returns the 'sub' claim when a valid JWT is present.

    Decodes the JWT without verifying the signature (rate limiting does not
    re-authenticate; it uses the identity already accepted by AuthGate).
    """
    import time

    import jwt as pyjwt

    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_operator_id

    secret = "test-secret-key-long-enough-32ch"  # pragma: allowlist secret
    token = pyjwt.encode(
        {"sub": "op-test-123", "iat": int(time.time()), "exp": int(time.time()) + 3600},
        secret,
        algorithm="HS256",
    )
    from unittest.mock import MagicMock

    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {token}"}

    result = _extract_operator_id(request)
    assert result == "op-test-123"


def test_extract_operator_id_returns_none_when_no_auth_header() -> None:
    """_extract_operator_id returns None when no Authorization header is present.

    Covers unauthenticated paths (/unseal, /auth/token) that use IP-based limits.
    """
    from unittest.mock import MagicMock

    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_operator_id

    request = MagicMock()
    request.headers = {}

    result = _extract_operator_id(request)
    assert result is None


def test_extract_operator_id_returns_none_for_malformed_token() -> None:
    """_extract_operator_id returns None for a malformed or undecodable JWT.

    Rate limiting must never block on a decode error — fall back gracefully.
    """
    from unittest.mock import MagicMock

    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_operator_id

    request = MagicMock()
    request.headers = {"Authorization": "Bearer not.a.valid.jwt"}

    result = _extract_operator_id(request)
    assert result is None
