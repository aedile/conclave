"""Unit tests for the HTTPS enforcement middleware (T42.2).

Tests exercise HTTPSEnforcementMiddleware in isolation using a minimal FastAPI
app, following the established middleware isolation pattern from the project
(T5.2 retro, rate_limit tests).

Coverage targets:
- AC1: Production mode rejects HTTP requests with 421 Misdirected Request.
- AC2: Development mode allows HTTP requests (pass-through).
- AC3: Production mode allows HTTPS requests (pass-through).
- AC4: 421 response body is RFC 7807 Problem Details format.
- AC5: Scheme detection via X-Forwarded-Proto header (reverse proxy scenario).
- AC6: Scheme detection falls back to direct request scheme.
- AC7: Startup warning when CONCLAVE_SSL_REQUIRED=true but TLS not configured.

CONSTITUTION Priority 0: Security — synthetic data must never stream unencrypted
CONSTITUTION Priority 3: TDD
Task: T42.2 — Add HTTPS Enforcement & Deployment Safety Checks
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Settings cache isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
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


def _build_production_app() -> FastAPI:
    """Build a minimal FastAPI app with HTTPS enforcement in production mode.

    Returns:
        A minimal FastAPI instance with HTTPSEnforcementMiddleware registered
        and the is_production callable returning True.
    """
    from synth_engine.bootstrapper.dependencies.https_enforcement import (
        HTTPSEnforcementMiddleware,
    )

    app = FastAPI()
    app.add_middleware(HTTPSEnforcementMiddleware, production=True)

    @app.get("/test")
    async def _test_route() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    return app


def _build_development_app() -> FastAPI:
    """Build a minimal FastAPI app with HTTPS enforcement in development mode.

    Returns:
        A minimal FastAPI instance with HTTPSEnforcementMiddleware registered
        and the is_production callable returning False.
    """
    from synth_engine.bootstrapper.dependencies.https_enforcement import (
        HTTPSEnforcementMiddleware,
    )

    app = FastAPI()
    app.add_middleware(HTTPSEnforcementMiddleware, production=False)

    @app.get("/test")
    async def _test_route() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    return app


# ---------------------------------------------------------------------------
# AC1 — Production mode rejects HTTP with 421
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_http_rejected_via_forwarded_proto() -> None:
    """Production mode: X-Forwarded-Proto: http must return 421."""
    app = _build_production_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test", headers={"X-Forwarded-Proto": "http"})

    assert response.status_code == 421


@pytest.mark.asyncio
async def test_production_http_rejected_without_forwarded_proto() -> None:
    """Production mode: direct http:// scheme (no X-Forwarded-Proto) must return 421."""
    app = _build_production_app()
    # base_url=http://test → scheme is http, no X-Forwarded-Proto header
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test")

    assert response.status_code == 421


# ---------------------------------------------------------------------------
# AC2 — Development mode allows HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_development_http_allowed_via_forwarded_proto() -> None:
    """Development mode: X-Forwarded-Proto: http must pass through (200)."""
    app = _build_development_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test", headers={"X-Forwarded-Proto": "http"})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_development_http_allowed_without_forwarded_proto() -> None:
    """Development mode: direct http:// scheme must pass through (200)."""
    app = _build_development_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test")

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# AC3 — Production mode allows HTTPS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_https_allowed_via_forwarded_proto() -> None:
    """Production mode: X-Forwarded-Proto: https must pass through (200)."""
    app = _build_production_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test", headers={"X-Forwarded-Proto": "https"})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_development_https_allowed_via_forwarded_proto() -> None:
    """Development mode: X-Forwarded-Proto: https must pass through (200)."""
    app = _build_development_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test", headers={"X-Forwarded-Proto": "https"})

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# AC4 — 421 response body is RFC 7807 Problem Details format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_421_body_is_rfc7807() -> None:
    """Production mode: 421 response body must be RFC 7807 Problem Details."""
    app = _build_production_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test", headers={"X-Forwarded-Proto": "http"})

    assert response.status_code == 421
    body = response.json()
    assert "type" in body
    assert "title" in body
    assert "status" in body
    assert "detail" in body
    assert body["status"] == 421


@pytest.mark.asyncio
async def test_production_421_title_is_misdirected_request() -> None:
    """421 RFC 7807 body must have title 'Misdirected Request'."""
    app = _build_production_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test", headers={"X-Forwarded-Proto": "http"})

    body = response.json()
    assert body["title"] == "Misdirected Request"


@pytest.mark.asyncio
async def test_production_421_detail_mentions_https() -> None:
    """421 detail message must mention HTTPS so operator can diagnose."""
    app = _build_production_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test", headers={"X-Forwarded-Proto": "http"})

    body = response.json()
    assert "https" in body["detail"].lower() or "HTTPS" in body["detail"]


# ---------------------------------------------------------------------------
# AC5 — X-Forwarded-Proto header takes precedence over direct scheme
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forwarded_proto_https_overrides_http_base_url() -> None:
    """X-Forwarded-Proto: https must allow request even when base URL is http://.

    In a reverse-proxy deployment the ASGI transport sees http:// on the
    internal network; only X-Forwarded-Proto carries the real client scheme.
    """
    app = _build_production_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test", headers={"X-Forwarded-Proto": "https"})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_forwarded_proto_http_rejected_in_production() -> None:
    """X-Forwarded-Proto: http must be rejected in production even from https base URL."""
    app = _build_production_app()
    # base_url uses https:// but proxy says http — treat the proxy claim as authoritative
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        response = await client.get("/test", headers={"X-Forwarded-Proto": "http"})

    assert response.status_code == 421


# ---------------------------------------------------------------------------
# AC6 — Startup warning: SSL required but TLS not configured
# ---------------------------------------------------------------------------


def test_warn_ssl_required_but_no_tls_cert(caplog: pytest.LogCaptureFixture) -> None:
    """warn_if_ssl_misconfigured must log WARNING when ssl_required=True but cert absent."""
    from synth_engine.bootstrapper.dependencies.https_enforcement import (
        warn_if_ssl_misconfigured,
    )

    with caplog.at_level(logging.WARNING, logger="synth_engine.bootstrapper.dependencies.https_enforcement"):
        warn_if_ssl_misconfigured(ssl_required=True, tls_cert_configured=False)

    assert any(
        "ssl" in record.message.lower() or "tls" in record.message.lower()
        for record in caplog.records
    )
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) >= 1


def test_no_warn_ssl_required_and_tls_configured(caplog: pytest.LogCaptureFixture) -> None:
    """warn_if_ssl_misconfigured must NOT log when ssl_required=True and cert is present."""
    from synth_engine.bootstrapper.dependencies.https_enforcement import (
        warn_if_ssl_misconfigured,
    )

    with caplog.at_level(logging.WARNING, logger="synth_engine.bootstrapper.dependencies.https_enforcement"):
        warn_if_ssl_misconfigured(ssl_required=True, tls_cert_configured=True)

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 0


def test_no_warn_ssl_not_required(caplog: pytest.LogCaptureFixture) -> None:
    """warn_if_ssl_misconfigured must NOT log when ssl_required=False."""
    from synth_engine.bootstrapper.dependencies.https_enforcement import (
        warn_if_ssl_misconfigured,
    )

    with caplog.at_level(logging.WARNING, logger="synth_engine.bootstrapper.dependencies.https_enforcement"):
        warn_if_ssl_misconfigured(ssl_required=False, tls_cert_configured=False)

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 0


# ---------------------------------------------------------------------------
# AC7 — Integration with settings: production flag reads from ConclaveSettings
# ---------------------------------------------------------------------------


def test_middleware_reads_production_flag_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPSEnforcementMiddleware default production flag reads from get_settings()."""
    from synth_engine.bootstrapper.dependencies.https_enforcement import (
        HTTPSEnforcementMiddleware,
    )
    from synth_engine.shared.settings import get_settings

    # Clear cache so monkeypatched env vars take effect
    get_settings.cache_clear()
    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)
    get_settings.cache_clear()

    app = FastAPI()
    # No explicit production= kwarg → reads from settings
    middleware = HTTPSEnforcementMiddleware.__new__(HTTPSEnforcementMiddleware)
    HTTPSEnforcementMiddleware.__init__(middleware, MagicMock())

    assert middleware._production is True

    get_settings.cache_clear()


def test_middleware_is_not_production_when_env_is_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPSEnforcementMiddleware default production flag is False for dev env."""
    from synth_engine.bootstrapper.dependencies.https_enforcement import (
        HTTPSEnforcementMiddleware,
    )
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)
    get_settings.cache_clear()

    app = FastAPI()
    middleware = HTTPSEnforcementMiddleware.__new__(HTTPSEnforcementMiddleware)
    HTTPSEnforcementMiddleware.__init__(middleware, MagicMock())

    assert middleware._production is False

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# AC8 — 421 response body is not empty and has correct content-type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_421_content_type_is_json() -> None:
    """421 response must have Content-Type: application/json."""
    app = _build_production_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test", headers={"X-Forwarded-Proto": "http"})

    assert response.status_code == 421
    assert "application/json" in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# AC9 — Middleware registered in setup_middleware is the outermost gate
# ---------------------------------------------------------------------------


def test_setup_middleware_registers_https_enforcement() -> None:
    """setup_middleware must register HTTPSEnforcementMiddleware on the app.

    Verifies that the middleware.py wiring calls add_middleware with
    HTTPSEnforcementMiddleware.
    """
    from unittest.mock import MagicMock, call

    from synth_engine.bootstrapper.middleware import setup_middleware

    mock_app = MagicMock()
    setup_middleware(mock_app)

    from synth_engine.bootstrapper.dependencies.https_enforcement import (
        HTTPSEnforcementMiddleware,
    )

    added_middleware_classes = [c.args[0] for c in mock_app.add_middleware.call_args_list]
    assert HTTPSEnforcementMiddleware in added_middleware_classes
