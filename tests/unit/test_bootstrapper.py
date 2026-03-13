"""Unit tests for the FastAPI application bootstrapper.

Tests for the create_app() factory function, health endpoint, and
basic application structure.

CONSTITUTION Priority 3: TDD RED Phase
Task: P2-T2.1 — Module Bootstrapper, OTEL, Idempotency, Orphan Task Reaper
"""

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
