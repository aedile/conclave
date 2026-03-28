"""Attack tests for OpenAPI docs exposure in production mode (T66.2).

Tests verify that /docs, /redoc, and /openapi.json are disabled when
CONCLAVE_ENV=production, and that these paths are not in the auth-exempt
set in production mode.

CONSTITUTION Priority 0: Security — API reconnaissance surface reduced in production.
Advisory: ADV-P62-01 — OpenAPI docs exposed without auth.
Task: T66.2 — Disable OpenAPI Docs in Production Mode.

Negative/attack tests (committed before feature tests per Rule 22).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None, None, None]:
    """Clear LRU cache before and after each test."""
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def production_app(monkeypatch: pytest.MonkeyPatch) -> object:
    """Create a FastAPI app configured for production mode (no lifespan startup)."""
    import bcrypt

    passphrase = b"prod-passphrase"
    hashed = bcrypt.hashpw(passphrase, bcrypt.gensalt()).decode()

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "prod-secret-key-32-characters-long!")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", hashed)
    monkeypatch.setenv("AUDIT_KEY", "b" * 64)
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "c" * 64)
    monkeypatch.setenv("MASKING_SALT", "d" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.main import create_app

    return create_app()


@pytest.fixture()
def development_app(monkeypatch: pytest.MonkeyPatch) -> object:
    """Create a FastAPI app configured for development mode."""
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "dev-secret-key-32-characters-long!!")
    monkeypatch.setenv("AUDIT_KEY", "e" * 64)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.main import create_app

    return create_app()


# ---------------------------------------------------------------------------
# Attack tests — FAIL (RED) before T66.2 implementation
# ---------------------------------------------------------------------------


def test_docs_endpoint_returns_404_in_production_mode(
    production_app: object,
) -> None:
    """GET /docs must return 404 in production mode.

    Exposing /docs in production reveals the full API surface to attackers,
    enabling endpoint discovery and reconnaissance attacks (ADV-P62-01).
    """
    from fastapi import FastAPI

    assert isinstance(production_app, FastAPI)
    # Use raise_server_exceptions=False to avoid 404 being raised
    client = TestClient(production_app, raise_server_exceptions=False)
    response = client.get("/docs", follow_redirects=False)
    assert response.status_code == 404, (
        f"Expected /docs to be disabled (404) in production, got {response.status_code}"
    )


def test_redoc_endpoint_returns_404_in_production_mode(
    production_app: object,
) -> None:
    """GET /redoc must return 404 in production mode."""
    from fastapi import FastAPI

    assert isinstance(production_app, FastAPI)
    client = TestClient(production_app, raise_server_exceptions=False)
    response = client.get("/redoc", follow_redirects=False)
    assert response.status_code == 404, (
        f"Expected /redoc to be disabled (404) in production, got {response.status_code}"
    )


def test_openapi_json_endpoint_returns_404_in_production_mode(
    production_app: object,
) -> None:
    """GET /openapi.json must return 404 in production mode."""
    from fastapi import FastAPI

    assert isinstance(production_app, FastAPI)
    client = TestClient(production_app, raise_server_exceptions=False)
    response = client.get("/openapi.json", follow_redirects=False)
    assert response.status_code == 404, (
        f"Expected /openapi.json to be disabled (404) in production, got {response.status_code}"
    )


def test_docs_not_in_exempt_paths_in_production_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMMON_INFRA_EXEMPT_PATHS must not contain /docs, /redoc, /openapi.json in production.

    If these paths remain in the auth-bypass list, they will bypass
    AuthenticationGateMiddleware even when the FastAPI app disables them.
    The clean solution is to remove doc paths from the exempt set unconditionally
    (they 404 in prod, and in dev they're reachable only by the auth gate allowing
    them through as any other GET request would be).
    """
    import bcrypt

    passphrase = b"prod-passphrase-2"
    hashed = bcrypt.hashpw(passphrase, bcrypt.gensalt()).decode()

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "prod-secret-key-32-characters-long2")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", hashed)
    monkeypatch.setenv("AUDIT_KEY", "f" * 64)
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "g" * 64)
    monkeypatch.setenv("MASKING_SALT", "h" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    # Try importing the new mode-aware function; fall back to constant.
    try:
        from synth_engine.bootstrapper.dependencies._exempt_paths import (  # type: ignore[attr-defined]
            get_infra_exempt_paths,
        )

        exempt = get_infra_exempt_paths()
    except (ImportError, AttributeError):
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        exempt = COMMON_INFRA_EXEMPT_PATHS

    doc_paths = {"/docs", "/redoc", "/openapi.json"}
    found = doc_paths & exempt
    assert not found, (
        f"Documentation paths {found} are still in the production exempt set. "
        "These paths must be removed from the auth-bypass list."
    )
    get_settings.cache_clear()


def test_docs_endpoint_returns_200_in_development_mode(
    development_app: object,
) -> None:
    """GET /docs must return 200 in development mode.

    Documentation endpoints are valuable for development and must remain
    enabled when CONCLAVE_ENV != 'production'.
    """
    from fastapi import FastAPI

    assert isinstance(development_app, FastAPI)
    client = TestClient(development_app, raise_server_exceptions=False)
    response = client.get("/docs", follow_redirects=False)
    assert response.status_code == 200, (
        f"Expected /docs to be accessible (200) in development mode, got {response.status_code}"
    )
