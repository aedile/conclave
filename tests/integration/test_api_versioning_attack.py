"""Attack and negative tests for T59.1 — API versioning.

Verifies that:
- Old unversioned business route paths are not registered in the app.
- No /api/v1/ path appears in any exempt-paths set.
- /security/shred remains reachable when the vault is sealed (SEAL_EXEMPT_PATHS
  must still contain the unversioned /security/shred path).
- /auth/token exact path is in AUTH_EXEMPT_PATHS (not a versioned alias).
- OpenAPI paths for business endpoints use the /api/v1/ prefix.

Design note on 404 vs 401 for unregistered paths:
-------------------------------------------------
FastAPI's authentication middleware runs BEFORE route matching. An
unauthenticated request to any path — registered or not — will return 401
when a JWT secret is configured and the path is not in AUTH_EXEMPT_PATHS.
Therefore "route not registered" cannot be inferred from an HTTP status code
alone: both registered-protected and unregistered paths return 401.

The authoritative test is to enumerate ``app.routes`` and assert that the
unversioned business paths are NOT registered as ``APIRoute`` objects.  This
is the programmatic enforcement mechanism for T59.1.

CONSTITUTION Priority 0: Security
Task: T59.1 — API Versioning
"""

from __future__ import annotations

from unittest.mock import patch

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
_TEST_SECRET = (
    "versioning-test-secret-key-long-enough-for-hs256-32chars+"  # pragma: allowlist secret
)

#: Business-logic path roots that must NOT be registered at unversioned paths
#: after /api/v1/ versioning is applied.  These are the exact path prefixes
#: that belonged to the business domain routers before versioning.
_UNVERSIONED_BUSINESS_PATH_PREFIXES = [
    "/jobs",
    "/connections",
    "/settings",
    "/webhooks",
    "/privacy",
    "/admin",
    "/compliance",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def versioned_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build a fully-wired FastAPI test app for versioning tests.

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

    app = create_app()
    get_settings.cache_clear()
    return app


# ---------------------------------------------------------------------------
# ATTACK TESTS
# ---------------------------------------------------------------------------


def test_old_unversioned_business_routes_not_registered(
    versioned_app: FastAPI,
) -> None:
    """Old unversioned business route paths must not be registered in the app.

    After /api/v1/ versioning is applied, the route table must contain NO
    APIRoute whose path starts with a business-domain prefix at root level
    (e.g. "/jobs", "/connections", "/settings", "/webhooks", "/privacy",
    "/admin", "/compliance").

    Note: Auth middleware fires BEFORE route resolution, so an HTTP 401
    response at /jobs does NOT prove the route is absent — it only proves the
    request was intercepted before routing.  The programmatic test is to
    enumerate app.routes directly.

    Arrange: versioned app.
    Assert: no APIRoute path starts with an unversioned business prefix.
    """
    registered_paths = [route.path for route in versioned_app.routes if isinstance(route, APIRoute)]

    violations: list[str] = []
    for path in registered_paths:
        for prefix in _UNVERSIONED_BUSINESS_PATH_PREFIXES:
            if path.startswith(prefix):
                violations.append(path)
                break

    assert not violations, (
        f"The following business-logic routes are registered without the /api/v1/ prefix "
        f"({len(violations)} violation(s)):\n"
        + "\n".join(f"  - {p}" for p in sorted(violations))
        + "\nAll business routes must be under /api/v1/."
    )


def test_versioned_routes_registered_correctly(
    versioned_app: FastAPI,
) -> None:
    """Business routes must be registered under /api/v1/ after versioning.

    Arrange: versioned app.
    Assert: at least the known core business paths exist under /api/v1/.
    """
    registered_paths = {route.path for route in versioned_app.routes if isinstance(route, APIRoute)}

    required_v1_paths = [
        "/api/v1/jobs",
        "/api/v1/connections",
        "/api/v1/settings",
        "/api/v1/webhooks/",
        "/api/v1/privacy/budget",
        "/api/v1/compliance/erasure",
    ]

    missing: list[str] = []
    for path in required_v1_paths:
        if path not in registered_paths:
            missing.append(path)

    assert not missing, (
        f"The following /api/v1/ routes are missing from the app "
        f"({len(missing)} missing):\n"
        + "\n".join(f"  - {p}" for p in missing)
        + f"\nRegistered paths: {sorted(registered_paths)}"
    )


def test_versioned_routes_not_in_exempt_paths() -> None:
    """No /api/v1/ path may appear in any exempt-paths set.

    Exempt paths are infrastructure/bootstrapping routes that bypass
    authentication and the vault seal gate.  Business routes under /api/v1/
    must require authentication and must never be in any exempt-paths set.

    Assert: COMMON_INFRA_EXEMPT_PATHS, SEAL_EXEMPT_PATHS, and AUTH_EXEMPT_PATHS
    contain no path that starts with /api/v1/.
    """
    from synth_engine.bootstrapper.dependencies._exempt_paths import (
        COMMON_INFRA_EXEMPT_PATHS,
        SEAL_EXEMPT_PATHS,
    )
    from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

    for path in COMMON_INFRA_EXEMPT_PATHS:
        assert not path.startswith("/api/v1"), (
            f"COMMON_INFRA_EXEMPT_PATHS contains versioned path {path!r}. "
            "Only infrastructure paths may be exempt from authentication."
        )

    for path in SEAL_EXEMPT_PATHS:
        assert not path.startswith("/api/v1"), (
            f"SEAL_EXEMPT_PATHS contains versioned path {path!r}. "
            "Business routes under /api/v1/ must not bypass the vault seal gate."
        )

    for path in AUTH_EXEMPT_PATHS:
        assert not path.startswith("/api/v1"), (
            f"AUTH_EXEMPT_PATHS contains versioned path {path!r}. "
            "Business routes under /api/v1/ must require authentication."
        )


@pytest.mark.asyncio
async def test_security_shred_reachable_when_sealed_after_versioning(
    versioned_app: FastAPI,
) -> None:
    """POST /security/shred must remain reachable (not 423) when vault is sealed.

    /security/shred is in SEAL_EXEMPT_PATHS at its unversioned path because the
    emergency shred protocol must work even when the vault is sealed.

    After API versioning, the security router stays at root (no /api/v1/
    prefix for security/auth routers), so /security/shred must still be in
    SEAL_EXEMPT_PATHS and must not return 423 when the vault is sealed.

    Arrange: versioned app; vault IS sealed; no token.
    Act: POST /security/shred.
    Assert: NOT 423 (vault seal gate must not block this path).
    """
    with (
        patch(_VAULT_PATCH, return_value=True),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=versioned_app), base_url="http://test"
        ) as client:
            response = await client.post("/security/shred", json={})

    assert response.status_code != 423, (
        f"/security/shred returned 423 when vault is sealed — it must be in SEAL_EXEMPT_PATHS. "
        f"Got {response.status_code}: {response.text}"
    )


def test_auth_token_path_matches_exempt_paths() -> None:
    """/auth/token exact path must be in AUTH_EXEMPT_PATHS.

    The /auth/token endpoint is a pre-authentication bootstrapping endpoint —
    it must be exempt from authentication (operators call it to GET a token).
    After versioning, /auth/token must remain at the root (no /api/v1/ prefix)
    and AUTH_EXEMPT_PATHS must contain exactly "/auth/token" (not a versioned alias).

    Assert: "/auth/token" in AUTH_EXEMPT_PATHS.
    Assert: "/api/v1/auth/token" not in AUTH_EXEMPT_PATHS.
    """
    from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

    assert "/auth/token" in AUTH_EXEMPT_PATHS, (
        f"'/auth/token' is missing from AUTH_EXEMPT_PATHS: {AUTH_EXEMPT_PATHS}. "
        "The operator login endpoint must be exempt from authentication."
    )
    assert "/api/v1/auth/token" not in AUTH_EXEMPT_PATHS, (
        "AUTH_EXEMPT_PATHS contains versioned '/api/v1/auth/token'. "
        "The auth endpoint stays at root — auth is a pre-versioning bootstrapping concern."
    )


def test_openapi_paths_use_api_v1_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAPI spec must list /api/v1/ prefixed paths for business endpoints.

    After versioning, the generated OpenAPI schema must show /api/v1/jobs,
    /api/v1/connections, etc. for all business-logic routes.

    Arrange: build the app.
    Assert: at least one /api/v1/ path exists in openapi_schema["paths"].
    Assert: no unversioned business path (/jobs, /connections) exists in paths.
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
    schema = app.openapi()
    paths = set(schema.get("paths", {}).keys())

    versioned_paths = {p for p in paths if p.startswith("/api/v1/")}
    assert len(versioned_paths) >= 1, (
        f"Expected at least 1 /api/v1/ path in OpenAPI schema; got paths: {sorted(paths)}"
    )

    # Unversioned business paths must not appear in the OpenAPI schema
    unversioned_business = {"/jobs", "/connections", "/settings", "/webhooks"}
    for path in unversioned_business:
        assert path not in paths, (
            f"Unversioned business path {path!r} found in OpenAPI schema after versioning. "
            "All business routes must be under /api/v1/."
        )

    get_settings.cache_clear()
