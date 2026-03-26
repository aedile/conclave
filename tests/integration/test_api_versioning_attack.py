"""Attack and negative tests for T59.1 — API versioning.

Verifies that:
- Old unversioned business routes return 404 after versioning.
- No /api/v1/ path appears in any exempt-paths set.
- /security/shred remains reachable when the vault is sealed (SEAL_EXEMPT_PATHS
  must still contain the unversioned /security/shred path).
- /auth/token exact path is in AUTH_EXEMPT_PATHS (not a versioned alias).
- OpenAPI paths for business endpoints use the /api/v1/ prefix.

All tests are written BEFORE the feature implementation (ATTACK RED phase),
per CLAUDE.md Rule 22.

CONSTITUTION Priority 0: Security
Task: T59.1 — API Versioning
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
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

#: Business-logic path roots that must NOT be reachable at unversioned paths
#: after /api/v1/ versioning is applied.
_UNVERSIONED_BUSINESS_PATHS = [
    "/jobs",
    "/connections",
    "/settings",
    "/webhooks",
    "/privacy/budget",
    "/admin/jobs/0/legal-hold",
    "/compliance/erasure",
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


@pytest.mark.asyncio
async def test_old_unversioned_business_routes_return_404(
    versioned_app: FastAPI,
) -> None:
    """Old unversioned business routes must return 404 after /api/v1/ versioning.

    When the /api/v1/ prefix is applied, the unversioned paths (/jobs,
    /connections, /settings, /webhooks, /privacy/budget) must no longer be
    registered in the app and must return 404.

    A 200 or 401 at an unversioned path would indicate the route still exists
    at the old path — a backwards-compatibility gap that breaks API contract
    stability.

    Arrange: versioned app; vault open, license active.
    Act: GET each unversioned business path with no token.
    Assert: 404 (not 401, not 200).
    """
    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=versioned_app), base_url="http://test"
        ) as client:
            for path in _UNVERSIONED_BUSINESS_PATHS:
                response = await client.get(path, follow_redirects=False)
                assert response.status_code == 404, (
                    f"Unversioned path {path!r} must return 404 after versioning; "
                    f"got {response.status_code}. "
                    "This indicates the route is still registered at the old path."
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
