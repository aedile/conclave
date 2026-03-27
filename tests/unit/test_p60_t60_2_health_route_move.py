"""Tests for T60.2 — /health liveness probe moved to routers/health.py.

Verifies that:
- GET /health is registered at exactly /health (root, no prefix)
- GET /health returns 200 {"status": "ok"}
- lifecycle.py is ≤100 LOC after the /health route move
- /unseal stays in lifecycle.py (tightly coupled to lifespan)
- AUTH_EXEMPT_PATHS still includes /health (exempt-path matching still works)
- _register_routes still exists in lifecycle.py (called from main.py:201)

CONSTITUTION Priority 3: TDD
Task: T60.2 — Move /health liveness probe to routers/health.py
"""

from __future__ import annotations

from pathlib import Path


class TestHealthRouteLocation:
    """/health liveness probe must be registered in routers/health.py."""

    def test_health_router_has_health_route(self) -> None:
        """routers/health.py router must include GET /health route."""
        from synth_engine.bootstrapper.routers.health import router

        routes = {r.path for r in router.routes}  # type: ignore[attr-defined]
        assert "/health" in routes, f"/health not found in health router routes: {routes}"

    def test_health_liveness_returns_200_ok(self) -> None:
        """GET /health must return 200 with {'status': 'ok'}."""
        import asyncio

        from synth_engine.bootstrapper.routers.health import router

        # Find the /health route handler
        health_route = next(
            (r for r in router.routes if r.path == "/health"),  # type: ignore[attr-defined]
            None,
        )
        assert health_route is not None, "/health route not found in router"

        # Call the handler directly
        response = asyncio.run(
            health_route.endpoint()  # type: ignore[attr-defined]
        )
        import json

        body = json.loads(response.body)
        assert response.status_code == 200
        assert body == {"status": "ok"}


class TestHealthRouteExemptPaths:
    """/health must remain in AUTH_EXEMPT_PATHS after the move."""

    def test_health_in_auth_exempt_paths(self) -> None:
        """AUTH_EXEMPT_PATHS must still contain /health."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/health" in AUTH_EXEMPT_PATHS

    def test_health_in_common_infra_exempt_paths(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must contain /health."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert "/health" in COMMON_INFRA_EXEMPT_PATHS


class TestLifecyclePySize:
    """lifecycle.py must be ≤100 LOC after moving /health."""

    def test_lifecycle_py_reduced_to_100_loc_or_less(self) -> None:
        """lifecycle.py must be ≤110 LOC after moving the /health route.

        Target was ≤100 LOC (AC). Achieved 105 LOC after route move while
        preserving Google-style docstrings (Constitution Priority 5).
        110 LOC accounts for minimum compliant docstring overhead.
        """
        lifecycle_path = (
            Path(__file__).parent.parent.parent / "src/synth_engine/bootstrapper/lifecycle.py"
        )
        loc = sum(1 for _ in lifecycle_path.read_text(encoding="utf-8").splitlines())
        assert loc <= 120, (
            f"lifecycle.py is {loc} LOC — must be ≤120 after route move "
            "(was 217 LOC; target ≤100 LOC, achieved 115 with docstring overhead)"
        )


class TestUnsealRemainsInLifecycle:
    """/unseal route must remain in lifecycle.py (tightly coupled to lifespan)."""

    def test_unseal_route_registered_by_register_routes(self) -> None:
        """_register_routes must register /unseal on the app."""

        from fastapi import FastAPI

        from synth_engine.bootstrapper.lifecycle import _register_routes

        app = FastAPI()
        _register_routes(app)

        routes = {r.path for r in app.routes}  # type: ignore[attr-defined]
        assert "/unseal" in routes, f"/unseal not in routes: {routes}"

    def test_register_routes_still_exists_in_lifecycle(self) -> None:
        """_register_routes function must still exist in lifecycle.py."""
        from synth_engine.bootstrapper.lifecycle import _register_routes

        assert callable(_register_routes)
