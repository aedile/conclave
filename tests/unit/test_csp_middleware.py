"""Unit tests for the CSP middleware.

Follows the middleware testing isolation pattern from T5.2 retro:
test CSP middleware in isolation using a minimal FastAPI app,
NOT wrapped by SealGateMiddleware or LicenseGateMiddleware.

CONSTITUTION Priority 0: Security
Task: P5-T5.3 — Build Accessible React SPA & "Vault Unseal"
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient


def _build_minimal_app() -> FastAPI:
    """Build a minimal FastAPI app with only CSP middleware attached.

    This isolation pattern (from T5.2 retro) ensures that SealGate or
    LicenseGate behaviour does not interfere with CSP header assertions.

    Returns:
        A minimal FastAPI instance with CSPMiddleware registered.
    """
    from synth_engine.bootstrapper.dependencies.csp import CSPMiddleware

    app = FastAPI()
    app.add_middleware(CSPMiddleware)

    @app.get("/test")
    async def _test_route() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    @app.get("/health")
    async def _health_route() -> JSONResponse:
        return JSONResponse(content={"status": "ok"})

    return app


@pytest.mark.asyncio
async def test_csp_header_present_on_normal_route() -> None:
    """CSPMiddleware must add a Content-Security-Policy header to all responses.

    A GET /test request on a minimal app should return the header.
    """
    app = _build_minimal_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test")

    assert response.status_code == 200
    assert "content-security-policy" in response.headers


@pytest.mark.asyncio
async def test_csp_header_present_on_health_route() -> None:
    """CSP header is additive — it must appear on /health too."""
    app = _build_minimal_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert "content-security-policy" in response.headers


@pytest.mark.asyncio
async def test_csp_policy_denies_external_scripts() -> None:
    """CSP header must contain script-src 'self' — no external CDNs.

    This ensures that the browser will block any script not served from
    the same origin.
    """
    app = _build_minimal_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test")

    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp


@pytest.mark.asyncio
async def test_csp_policy_denies_external_fonts() -> None:
    """CSP header must contain font-src 'self' — no external font CDNs."""
    app = _build_minimal_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test")

    csp = response.headers["content-security-policy"]
    assert "font-src 'self'" in csp


@pytest.mark.asyncio
async def test_csp_policy_denies_external_styles() -> None:
    """CSP header must contain style-src directive.

    The policy may include 'unsafe-inline' for Vite dev mode CSS injection,
    but must NOT reference any external stylesheet CDN.
    """
    app = _build_minimal_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test")

    csp = response.headers["content-security-policy"]
    assert "style-src" in csp
    # External CDN references are forbidden — no http/https URIs in style-src
    assert "https://" not in csp.split("style-src")[1].split(";")[0]


@pytest.mark.asyncio
async def test_csp_policy_contains_frame_ancestors_none() -> None:
    """CSP must include frame-ancestors 'none' to prevent clickjacking."""
    app = _build_minimal_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test")

    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp


@pytest.mark.asyncio
async def test_csp_policy_contains_default_src_self() -> None:
    """CSP must include default-src 'self' as the catch-all fallback."""
    app = _build_minimal_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test")

    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp


@pytest.mark.asyncio
async def test_csp_header_on_error_response() -> None:
    """CSP header must be present even on 404 error responses.

    The middleware is purely additive — it should attach the header to
    every response regardless of status code.
    """
    app = _build_minimal_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/nonexistent")

    assert response.status_code == 404
    assert "content-security-policy" in response.headers


@pytest.mark.asyncio
async def test_csp_middleware_does_not_modify_response_body() -> None:
    """CSP middleware must not alter the JSON response body."""
    app = _build_minimal_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test")

    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_x_content_type_options_nosniff_on_normal_route() -> None:
    """CSPMiddleware must add X-Content-Type-Options: nosniff to all responses.

    This header prevents MIME-type sniffing attacks where a browser might
    interpret a response as a different content type than declared.  It is
    a required security hardening header per CONSTITUTION Priority 0.
    """
    app = _build_minimal_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test")

    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.asyncio
async def test_x_content_type_options_nosniff_on_health_route() -> None:
    """X-Content-Type-Options: nosniff must appear on /health too."""
    app = _build_minimal_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.asyncio
async def test_x_content_type_options_nosniff_on_error_response() -> None:
    """X-Content-Type-Options: nosniff must appear even on 404 error responses."""
    app = _build_minimal_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/nonexistent")

    assert response.status_code == 404
    assert response.headers.get("x-content-type-options") == "nosniff"
