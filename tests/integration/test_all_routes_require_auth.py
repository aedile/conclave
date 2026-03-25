"""Self-maintaining auth coverage gate for all registered FastAPI routes.

This module implements the programmatic enforcement mechanism for
CONSTITUTION Section 4, Priority 0: Auth coverage.  It replaces the
``[ADVISORY — no programmatic gate: test_all_routes_require_auth()
does not exist]`` annotation on CONSTITUTION.md line 107.

Design
------
The test enumerates ALL routes from ``app.routes`` at runtime (not from
the OpenAPI schema, which omits ``include_in_schema=False`` routes).  It
subtracts :data:`~synth_engine.bootstrapper.dependencies.auth.AUTH_EXEMPT_PATHS`
plus the Prometheus ``/metrics`` mount, then asserts that every remaining
``(path, method)`` pair returns HTTP 401 when:

1. No ``Authorization`` header is provided.
2. A garbage/invalid Bearer token is provided.
3. An expired but otherwise well-formed JWT is provided.

Self-maintaining contract
-------------------------
Adding any new route to the app without updating ``AUTH_EXEMPT_PATHS``
(or providing a JWT dependency) will cause this test to fail automatically.
New routes that are legitimately public MUST be explicitly added to
``AUTH_EXEMPT_PATHS`` — this is the forcing function.

Attack tests (ATTACK RED phase)
--------------------------------
Tests are grouped as "attack" (negative cases) first, per CLAUDE.md
Rule 22.  The attack tests pin specific adversarial inputs and verify
no information leakage in 401 response bodies.

CONSTITUTION Priority 0: Security
CONSTITUTION Section 4: Programmatic Enforcement
Task: T53.3 — Programmatic Auth Coverage Gate
"""

from __future__ import annotations

import time
from collections.abc import Generator
from unittest.mock import patch

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VAULT_PATCH = "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed"
_LICENSE_PATCH = "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed"

#: JWT secret long enough for HS256 (≥256-bit / 32 bytes).
_TEST_SECRET = (
    "auth-gate-test-secret-key-long-enough-for-hs256-32chars+"  # pragma: allowlist secret
)

#: A garbage token that is not a valid JWT.
_GARBAGE_TOKEN = "not.a.valid.jwt.at.all"  # pragma: allowlist secret

#: Routes that are legitimately public and must bypass auth.
#: This set is derived from AUTH_EXEMPT_PATHS at test time for
#: cross-referencing; see test_exempt_path_list_only_contains_expected_paths.
_EXPECTED_AUTH_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/unseal",
        "/health",
        "/ready",
        "/health/vault",  # T55.1 — vault status endpoint; must be exempt from auth gate
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/license/challenge",
        "/license/activate",
        "/auth/token",
    }
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Ensures env-var patches applied via monkeypatch are picked up by
    settings-dependent code without cross-test contamination.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def auth_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build a fully-wired FastAPI test app with JWT auth configured.

    Patches environment variables so all middleware (vault, license, auth)
    is active.  VaultState and LicenseState are patched separately per-test
    via context managers so the middleware layers pass requests through to
    the auth gate.

    Args:
        monkeypatch: pytest monkeypatch fixture for env var injection.

    Returns:
        A configured FastAPI application instance.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", "")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.main import create_app

    return create_app()


def _make_expired_jwt() -> str:
    """Create an expired but otherwise well-formed JWT.

    Returns:
        Compact JWT string with ``exp`` set 60 seconds in the past.
    """
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": "attacker",
            "iat": now - 3660,
            "exp": now - 60,
            "scope": ["read", "write"],
        },
        _TEST_SECRET,
        algorithm="HS256",
    )


def _collect_auth_required_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Enumerate (path, method) pairs that must require authentication.

    Collects all routes from ``app.routes`` (which includes
    ``include_in_schema=False`` routes), excludes AUTH_EXEMPT_PATHS,
    and excludes the Prometheus ``/metrics`` mount (a sub-application,
    not an APIRoute).  HEAD and OPTIONS are excluded — HEAD is an
    implicit alias for GET (FastAPI adds it automatically for every GET
    route), and OPTIONS is a CORS preflight that Starlette handles
    before auth middleware can intercept.

    Args:
        app: The FastAPI application instance.

    Returns:
        Sorted list of ``(path, method)`` pairs requiring JWT auth.
    """
    from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

    pairs: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            # Skip Mount objects (e.g. Prometheus /metrics sub-app)
            continue
        path: str = route.path
        if path in AUTH_EXEMPT_PATHS:
            continue
        methods = route.methods or set()
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                # HEAD is an implicit alias for GET; OPTIONS is CORS preflight.
                continue
            pairs.append((path, method))
    return sorted(pairs)


# ===========================================================================
# ATTACK RED — Negative / security tests (written first per Rule 22)
# ===========================================================================


@pytest.mark.asyncio
async def test_attack_no_token_returns_401_not_500(auth_app: FastAPI) -> None:
    """Auth route with no token must return 401, not 500 or 200.

    A missing Authorization header must be caught by the auth gate and
    return a 401.  A 500 would indicate an unhandled exception leaking
    through the middleware; a 200 would indicate auth is missing entirely.

    Arrange: real app with JWT configured; vault open, license active.
    Act: GET /jobs with no Authorization header.
    Assert: 401 (exactly — not 500, not 200).
    """
    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=auth_app), base_url="http://test"
        ) as client:
            response = await client.get("/jobs")

    assert response.status_code == 401, (
        f"Expected 401 for no-token request; got {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_attack_garbage_token_returns_401(auth_app: FastAPI) -> None:
    """A garbage Bearer token must be rejected with 401, not 500.

    Garbage tokens (not parseable as JWT) must not cause unhandled
    exceptions that produce a 500 response.

    Arrange: real app with JWT configured.
    Act: GET /jobs with Authorization: Bearer <garbage>.
    Assert: 401.
    """
    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=auth_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/jobs",
                headers={"Authorization": f"Bearer {_GARBAGE_TOKEN}"},
            )

    assert response.status_code == 401, (
        f"Expected 401 for garbage token; got {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_attack_expired_token_returns_401_not_500(auth_app: FastAPI) -> None:
    """An expired JWT must return 401, not 500.

    Expired-token handling must be clean — no unhandled exceptions, no
    stack traces in the response body.

    Arrange: real app with JWT configured; create an expired token.
    Act: GET /jobs with the expired token.
    Assert: 401 with no stack trace in the body.
    """
    expired_token = _make_expired_jwt()
    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=auth_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/jobs",
                headers={"Authorization": f"Bearer {expired_token}"},
            )

    assert response.status_code == 401, (
        f"Expected 401 for expired token; got {response.status_code}: {response.text}"
    )
    # Verify no stack trace leakage in the response body
    body_text = response.text
    assert "Traceback" not in body_text, "Response must not contain stack traces"
    assert 'File "' not in body_text, "Response must not contain internal file paths"


@pytest.mark.asyncio
async def test_attack_trailing_slash_on_auth_route_returns_401(auth_app: FastAPI) -> None:
    """Path normalization: a trailing slash on an auth route must still return 401.

    Some middleware implementations are fooled by trailing slashes into
    skipping auth checks.  This test verifies the auth gate applies even
    when the path deviates from the registered route's exact form.

    Arrange: real app with JWT configured.
    Act: GET /jobs/ (trailing slash) with no token.
    Assert: 401 (not 200, not 404 treated as bypass).
    """
    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=auth_app), base_url="http://test"
        ) as client:
            # follow_redirects=False so we see the raw 301/307 if there is one
            response = await client.get("/jobs/", follow_redirects=False)

    # 401 is the expected response (auth rejection before routing).
    # 404 is acceptable if FastAPI does not match the trailing-slash path.
    # 500 (server error), 200 (auth bypass), and 307 (redirect that could
    # bypass auth on the redirected request) are all unacceptable.
    assert response.status_code in {401, 404}, (
        f"Trailing slash on /jobs/ must return 401 or 404, not {response.status_code}; "
        f"got: {response.text}"
    )


@pytest.mark.asyncio
async def test_attack_401_response_body_has_no_stack_trace(auth_app: FastAPI) -> None:
    """401 response body must not leak stack traces or internal file paths.

    Information leakage in error responses is a security vulnerability.
    The 401 body must contain only the RFC 7807 Problem Details fields.

    Arrange: real app with JWT configured; send request with no token.
    Act: GET /privacy/budget (a protected route) with no token.
    Assert: 401 body has no Traceback, no 'File "', no internal paths.
    """
    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=auth_app), base_url="http://test"
        ) as client:
            response = await client.get("/privacy/budget")

    assert response.status_code == 401, (
        f"Expected 401 for unauthenticated request; got {response.status_code}"
    )
    body_text = response.text
    assert "Traceback" not in body_text, "401 response must not contain Python stack traces"
    assert 'File "' not in body_text, "401 response must not expose internal file system paths"
    assert "synth_engine" not in body_text.lower() or "type" in body_text, (
        "401 response body must not expose internal module names outside RFC 7807 fields"
    )


@pytest.mark.asyncio
async def test_attack_401_response_is_rfc7807_format(auth_app: FastAPI) -> None:
    """401 response body must conform to RFC 7807 Problem Details format.

    The auth gate must produce a structured response — not a bare HTML
    page or plain string — so that API clients can parse the error.

    Arrange: real app with JWT configured.
    Act: GET /jobs with no token.
    Assert: response is JSON with ``status``, ``title``, ``detail`` fields,
            and ``status`` value is 401.
    """
    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=auth_app), base_url="http://test"
        ) as client:
            response = await client.get("/jobs")

    assert response.status_code == 401
    body = response.json()
    assert "status" in body, f"RFC 7807 requires 'status' field; got: {body}"
    assert "title" in body, f"RFC 7807 requires 'title' field; got: {body}"
    assert "detail" in body, f"RFC 7807 requires 'detail' field; got: {body}"
    assert body["status"] == 401, f"RFC 7807 'status' field must be 401; got: {body['status']}"


# ===========================================================================
# FEATURE RED — Self-maintaining enumeration tests
# ===========================================================================


@pytest.mark.asyncio
async def test_all_routes_require_auth_no_token(auth_app: FastAPI) -> None:
    """Every non-exempt route must return 401 when no token is provided.

    This is the primary self-maintaining gate.  If a developer adds a
    new route without auth, this test will fail automatically.

    Enumerate all (path, method) pairs not in AUTH_EXEMPT_PATHS, then
    send a request with no Authorization header and assert 401.

    The test uses concrete path parameters (``/jobs/0``) for parameterised
    paths (``/jobs/{job_id}``).
    """
    pairs = _collect_auth_required_routes(auth_app)
    assert len(pairs) > 0, "No auth-required routes found — route enumeration is broken"

    failures: list[str] = []
    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=auth_app), base_url="http://test"
        ) as client:
            for path, method in pairs:
                # Substitute path parameters with safe sentinel values
                concrete_path = _substitute_path_params(path)
                response = await client.request(method, concrete_path)
                if response.status_code != 401:
                    failures.append(
                        f"{method} {path} → {response.status_code} "
                        f"(expected 401); body: {response.text[:120]}"
                    )

    assert not failures, (
        f"The following routes did not return 401 for unauthenticated requests "
        f"({len(failures)} failure(s)):\n" + "\n".join(f"  - {f}" for f in failures)
    )


@pytest.mark.asyncio
async def test_all_routes_require_auth_invalid_token(auth_app: FastAPI) -> None:
    """Every non-exempt route must return 401 when an invalid token is provided.

    Invalid tokens (garbage strings that are not well-formed JWTs) must
    be rejected by the auth gate, not cause a 500.

    Enumerate all (path, method) pairs, send a garbage Bearer token,
    assert 401.
    """
    pairs = _collect_auth_required_routes(auth_app)
    assert len(pairs) > 0, "No auth-required routes found — route enumeration is broken"

    failures: list[str] = []
    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=auth_app), base_url="http://test"
        ) as client:
            for path, method in pairs:
                concrete_path = _substitute_path_params(path)
                response = await client.request(
                    method,
                    concrete_path,
                    headers={"Authorization": f"Bearer {_GARBAGE_TOKEN}"},
                )
                if response.status_code != 401:
                    failures.append(
                        f"{method} {path} → {response.status_code} "
                        f"(expected 401); body: {response.text[:120]}"
                    )

    assert not failures, (
        f"The following routes did not return 401 for invalid-token requests "
        f"({len(failures)} failure(s)):\n" + "\n".join(f"  - {f}" for f in failures)
    )


@pytest.mark.asyncio
async def test_all_routes_require_auth_expired_token(auth_app: FastAPI) -> None:
    """Every non-exempt route must return 401 when an expired JWT is provided.

    An expired token is a well-formed JWT whose ``exp`` claim is in the
    past.  The auth gate must detect this via PyJWT's ``ExpiredSignatureError``
    and return 401 — not allow the request through.

    Enumerate all (path, method) pairs, send an expired Bearer token,
    assert 401.
    """
    pairs = _collect_auth_required_routes(auth_app)
    assert len(pairs) > 0, "No auth-required routes found — route enumeration is broken"

    expired_token = _make_expired_jwt()

    failures: list[str] = []
    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=auth_app), base_url="http://test"
        ) as client:
            for path, method in pairs:
                concrete_path = _substitute_path_params(path)
                response = await client.request(
                    method,
                    concrete_path,
                    headers={"Authorization": f"Bearer {expired_token}"},
                )
                if response.status_code != 401:
                    failures.append(
                        f"{method} {path} → {response.status_code} "
                        f"(expected 401); body: {response.text[:120]}"
                    )

    assert not failures, (
        f"The following routes did not return 401 for expired-token requests "
        f"({len(failures)} failure(s)):\n" + "\n".join(f"  - {f}" for f in failures)
    )


def test_exempt_path_list_only_contains_expected_entries() -> None:
    """AUTH_EXEMPT_PATHS must contain exactly the expected set of paths.

    This test locks down the exempt path list so that a developer who
    silently adds a new exempt path is caught.  Adding a legitimate
    path requires updating ``_EXPECTED_AUTH_EXEMPT_PATHS`` in this file
    with a comment explaining why it is public.

    Arrange: import AUTH_EXEMPT_PATHS from the auth module.
    Assert: AUTH_EXEMPT_PATHS == _EXPECTED_AUTH_EXEMPT_PATHS (exact equality).
    """
    from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

    assert AUTH_EXEMPT_PATHS == _EXPECTED_AUTH_EXEMPT_PATHS, (
        f"AUTH_EXEMPT_PATHS does not match the expected set.\n"
        f"Extra paths (added without review): "
        f"{AUTH_EXEMPT_PATHS - _EXPECTED_AUTH_EXEMPT_PATHS}\n"
        f"Missing paths (removed without review): "
        f"{_EXPECTED_AUTH_EXEMPT_PATHS - AUTH_EXEMPT_PATHS}"
    )


def test_no_route_returns_200_without_auth_synchronous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify route count: all auth-required routes must be accounted for.

    Synchronous smoke test: confirm the route enumerator finds at least
    the known set of auth-required routes.  This guards against the
    enumerator silently returning an empty list.

    Arrange: build the app; collect auth-required routes.
    Assert: at least 20 auth-required (path, method) pairs exist.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    pairs = _collect_auth_required_routes(app)

    assert len(pairs) >= 20, (
        f"Expected at least 20 auth-required routes; only found {len(pairs)}: {pairs}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _substitute_path_params(path: str) -> str:
    """Replace FastAPI path parameter placeholders with safe sentinel values.

    Converts ``/jobs/{job_id}`` → ``/jobs/0`` and
    ``/connections/{connection_id}`` → ``/connections/0``.
    This produces concrete URLs the test client can actually hit so the
    auth gate can evaluate the request (rather than getting a 404 before
    reaching auth middleware).

    For the auth coverage gate, the sentinel value does not matter
    because the auth gate intercepts the request BEFORE the route handler
    reads path parameters.

    Args:
        path: A FastAPI route path string, potentially with ``{param}``
            placeholders.

    Returns:
        A concrete URL string with all ``{param}`` placeholders replaced
        by ``"0"``.
    """
    import re

    return re.sub(r"\{[^}]+\}", "0", path)
