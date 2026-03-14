"""Unit tests for the FastAPI application bootstrapper.

Tests for the create_app() factory function, health endpoint, and
basic application structure.

CONSTITUTION Priority 3: TDD RED Phase
Task: P2-T2.1 — Module Bootstrapper, OTEL, Idempotency, Orphan Task Reaper
Task: P3.5-T3.5.4 — Bootstrapper Wiring & Minimal CLI Entrypoint
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint_returns_200() -> None:
    """GET /health returns HTTP 200 with status ok body.

    The health endpoint is the minimal liveness probe for the service.
    It must return a 200 with a JSON body containing {"status": "ok"}.
    """
    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_is_fastapi_instance() -> None:
    """create_app() must return a FastAPI instance, not a module-level singleton.

    The factory pattern allows test isolation — each call creates a
    fresh application with no shared state.
    """
    from synth_engine.bootstrapper.main import create_app

    assert isinstance(create_app(), FastAPI)


def test_create_app_returns_new_instance_each_call() -> None:
    """create_app() must return a new FastAPI instance on each invocation.

    This ensures test isolation and prevents shared state between
    different call sites.
    """
    from synth_engine.bootstrapper.main import create_app

    app1 = create_app()
    app2 = create_app()

    assert app1 is not app2


# ---------------------------------------------------------------------------
# CycleDetectionError → 422 RFC 7807
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_detection_error_returns_422_rfc7807() -> None:
    """CycleDetectionError raised by a subsetting engine handler returns HTTP 422.

    The bootstrapper must intercept CycleDetectionError (ADV-022) and
    return an RFC 7807 Problem Details response with status 422, not 500.

    RFC 7807 required fields: type, title, status, detail.

    The vault is patched to the unsealed state so the SealGateMiddleware
    does not intercept the test route with a 423.
    """
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.modules.mapping import CycleDetectionError

    app = create_app()

    # Register a test route that raises CycleDetectionError so we can verify
    # the exception handler is wired correctly.
    # CycleDetectionError takes a list[str] cycle path — not a bare string.
    @app.get("/test-cycle-error")
    async def _trigger_cycle_error() -> None:
        raise CycleDetectionError(["table_a", "table_b", "table_a"])

    # Patch the vault seal check so SealGateMiddleware allows the request.
    with patch(
        "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
        return_value=False,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/test-cycle-error")

    assert response.status_code == 422
    body = response.json()

    # RFC 7807 required fields
    assert body.get("status") == 422
    assert "title" in body
    assert "detail" in body
    assert "type" in body
    # The detail must carry a meaningful cycle description
    assert "table_a" in body["detail"]


@pytest.mark.asyncio
async def test_cycle_detection_error_not_a_500() -> None:
    """CycleDetectionError must never produce HTTP 500.

    A generic unhandled exception produces 500. This test verifies the
    bootstrapper's exception handler intercepts CycleDetectionError before
    FastAPI's default 500 handler fires.
    """
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.modules.mapping import CycleDetectionError

    app = create_app()

    @app.get("/test-cycle-not-500")
    async def _raise_cycle() -> None:
        raise CycleDetectionError(["orders", "line_items", "orders"])

    with patch(
        "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
        return_value=False,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/test-cycle-not-500")

    assert response.status_code != 500


# ---------------------------------------------------------------------------
# Pytest mark
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit
