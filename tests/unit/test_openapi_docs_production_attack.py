"""Attack tests for OpenAPI docs exposure in production mode (T66.2).

Tests verify that /docs, /redoc, and /openapi.json are disabled when
CONCLAVE_ENV=production, and that these paths are not in the auth-exempt
set in production mode.

CONSTITUTION Priority 0: Security — API reconnaissance surface reduced in production.
Advisory: ADV-P62-01 — OpenAPI docs exposed without auth.
Task: T66.2 — Disable OpenAPI Docs in Production Mode.

Negative/attack tests (committed before feature tests per Rule 22).

Note on testing strategy: the full-stack TestClient approach is not suitable
here because production mode enables HTTPS enforcement (421 on plain HTTP)
and vault seal gate (423) which fire before the docs routes. Instead, we
verify the FastAPI app's ``docs_url``, ``redoc_url``, and ``openapi_url``
attributes directly — these are set by ``create_app()`` and govern whether
FastAPI registers the documentation routes at all.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    """Clear LRU cache before and after each test."""
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def production_app(monkeypatch: pytest.MonkeyPatch) -> object:
    """Create a FastAPI app configured for production mode."""
    import bcrypt

    passphrase = b"prod-passphrase"
    hashed = bcrypt.hashpw(passphrase, bcrypt.gensalt()).decode()

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "prod-secret-key-32-characters-long!")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", hashed)
    monkeypatch.setenv("AUDIT_KEY", "b" * 64)
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "c" * 64)
    monkeypatch.setenv("MASKING_SALT", "d" * 32)
    _test_db_url = "postgresql+asyncpg://test:test@localhost/test"  # pragma: allowlist secret
    monkeypatch.setenv("DATABASE_URL", _test_db_url)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.main import create_app

    return create_app()


@pytest.fixture
def development_app(monkeypatch: pytest.MonkeyPatch) -> object:
    """Create a FastAPI app configured for development mode."""
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "dev-secret-key-32-characters-long!!")
    monkeypatch.setenv("AUDIT_KEY", "e" * 64)
    _test_db_url = "postgresql+asyncpg://test:test@localhost/test"  # pragma: allowlist secret
    monkeypatch.setenv("DATABASE_URL", _test_db_url)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.main import create_app

    return create_app()


# ---------------------------------------------------------------------------
# Attack tests — verifying FastAPI app configuration attributes
# ---------------------------------------------------------------------------


def test_docs_endpoint_returns_404_in_production_mode(
    production_app: object,
) -> None:
    """FastAPI docs_url must be None in production mode.

    When ``docs_url=None``, FastAPI does not register the ``/docs`` route,
    so any GET /docs request returns 404 regardless of middleware.
    This is the authoritative security check — the route does not exist.

    Exposing /docs in production reveals the full API surface to attackers,
    enabling endpoint discovery and reconnaissance attacks (ADV-P62-01).
    """
    from fastapi import FastAPI

    assert isinstance(production_app, FastAPI)
    assert production_app.docs_url is None, (
        f"Expected docs_url=None in production, got {production_app.docs_url!r}. "
        "The /docs endpoint must be disabled to prevent API reconnaissance."
    )
    assert str(production_app.docs_url) == "None"


def test_redoc_endpoint_returns_404_in_production_mode(
    production_app: object,
) -> None:
    """FastAPI redoc_url must be None in production mode."""
    from fastapi import FastAPI

    assert isinstance(production_app, FastAPI)
    assert production_app.redoc_url is None, (
        f"Expected redoc_url=None in production, got {production_app.redoc_url!r}."
    )
    assert str(production_app.redoc_url) == "None"


def test_openapi_json_endpoint_returns_404_in_production_mode(
    production_app: object,
) -> None:
    """FastAPI openapi_url must be None in production mode."""
    from fastapi import FastAPI

    assert isinstance(production_app, FastAPI)
    assert production_app.openapi_url is None, (
        f"Expected openapi_url=None in production, got {production_app.openapi_url!r}."
    )
    assert str(production_app.openapi_url) == "None"


def test_docs_not_in_exempt_paths_in_production_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMMON_INFRA_EXEMPT_PATHS must not contain /docs, /redoc, /openapi.json.

    If these paths remain in the auth-bypass list, they bypass
    AuthenticationGateMiddleware — even in development mode this is wrong
    because doc paths should require a Bearer token like other endpoints.
    The clean fix (T66.2) removes them unconditionally from the exempt set.
    """
    from synth_engine.bootstrapper.dependencies._exempt_paths import COMMON_INFRA_EXEMPT_PATHS

    doc_paths = {"/docs", "/redoc", "/openapi.json"}
    found = doc_paths & COMMON_INFRA_EXEMPT_PATHS
    assert not found, (
        f"Documentation paths {found} are still in COMMON_INFRA_EXEMPT_PATHS. "
        "These paths must be removed from the auth-bypass list (T66.2, ADV-P62-01)."
    )


def test_docs_endpoint_returns_200_in_development_mode(
    development_app: object,
) -> None:
    """FastAPI docs_url must be '/docs' in development mode.

    Documentation endpoints are valuable for development and must remain
    registered when CONCLAVE_ENV != 'production'.
    """
    from fastapi import FastAPI

    assert isinstance(development_app, FastAPI)
    assert development_app.docs_url == "/docs", (
        f"Expected docs_url='/docs' in development mode, got {development_app.docs_url!r}. "
        "Documentation must be accessible in non-production environments."
    )


def test_all_routes_require_auth_still_passes_after_docs_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The auth gate exemption change must not break existing auth enforcement.

    Removing /docs, /redoc, /openapi.json from COMMON_INFRA_EXEMPT_PATHS
    means those paths now require auth in development. This is intentional.
    Verify that /auth/token (which IS in the exempt set) still has no
    unwanted paths added, and the exemption set remains well-formed.
    """
    from synth_engine.bootstrapper.dependencies._exempt_paths import (
        COMMON_INFRA_EXEMPT_PATHS,
        SEAL_EXEMPT_PATHS,
    )

    # Auth/token path must NOT be exempt (it needs rate limiting, not auth bypass)
    assert "/auth/token" not in COMMON_INFRA_EXEMPT_PATHS, (
        "/auth/token must not be in COMMON_INFRA_EXEMPT_PATHS"
    )

    # SEAL_EXEMPT_PATHS must be a superset of COMMON_INFRA_EXEMPT_PATHS
    assert COMMON_INFRA_EXEMPT_PATHS.issubset(SEAL_EXEMPT_PATHS), (
        "SEAL_EXEMPT_PATHS must be a superset of COMMON_INFRA_EXEMPT_PATHS"
    )

    # Essential infra paths must remain present
    essential_paths = {"/health", "/ready", "/metrics", "/unseal"}
    missing = essential_paths - COMMON_INFRA_EXEMPT_PATHS
    assert not missing, (
        f"Essential infra paths {missing} are missing from COMMON_INFRA_EXEMPT_PATHS"
    )
