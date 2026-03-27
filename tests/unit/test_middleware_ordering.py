"""Negative/attack tests for middleware ordering enforcement (T62.4).

Attack tests verifying that:
1. The middleware stack order is programmatically verifiable.
2. Oversized body is rejected BEFORE auth check — proving RequestBodyLimitMiddleware
   fires before AuthenticationGateMiddleware.
3. An oversized body from an unauthenticated request returns 413, not 401.

The key security property: if body-limit checking happened AFTER auth,
an attacker could exhaust server memory before any auth gate fired.

CONSTITUTION Priority 0: Security — middleware ordering prevents pre-auth DoS
CONSTITUTION Priority 3: TDD — Attack tests committed before implementation (Rule 22)
Task: T62.4 — Programmatic Middleware Ordering Assertion

Starlette LIFO semantics (critical to understand for index assertions):
-  ``user_middleware`` is a list where index 0 = outermost (last added, fires first).
-  index N = innermost (first added, fires last on request path).
-  Middleware added LAST (HTTPSEnforcementMiddleware) ends up at a LOW index.
-  Middleware added FIRST (IdempotencyMiddleware) ends up at the HIGHEST index.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _build_app_with_middleware() -> object:
    """Build a minimal FastAPI app with the full middleware stack.

    Returns:
        A FastAPI app instance with all middleware registered via setup_middleware().
    """
    from synth_engine.bootstrapper.main import create_app

    return create_app()


class TestMiddlewareOrderingAssertion:
    """Verify that the middleware ordering can be programmatically checked."""

    def test_middleware_stack_contains_all_eight_layers(self) -> None:
        """create_app() middleware stack must include all 8 middleware classes."""
        from synth_engine.bootstrapper.dependencies.auth_middleware import (
            AuthenticationGateMiddleware,
        )
        from synth_engine.bootstrapper.dependencies.csp import CSPMiddleware
        from synth_engine.bootstrapper.dependencies.https_enforcement import (
            HTTPSEnforcementMiddleware,
        )
        from synth_engine.bootstrapper.dependencies.licensing import LicenseGateMiddleware
        from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware
        from synth_engine.bootstrapper.dependencies.request_limits import (
            RequestBodyLimitMiddleware,
        )
        from synth_engine.bootstrapper.dependencies.vault import SealGateMiddleware
        from synth_engine.shared.middleware.idempotency import IdempotencyMiddleware

        app = _build_app_with_middleware()

        middleware_classes = _collect_middleware_classes(app)

        expected = {
            HTTPSEnforcementMiddleware,
            RateLimitGateMiddleware,
            RequestBodyLimitMiddleware,
            CSPMiddleware,
            SealGateMiddleware,
            LicenseGateMiddleware,
            AuthenticationGateMiddleware,
            IdempotencyMiddleware,
        }

        for cls in expected:
            assert cls in middleware_classes, (
                f"Expected {cls.__name__} in middleware stack but it was missing. "
                f"Found: {[c.__name__ for c in middleware_classes]}"
            )

    def test_body_limit_middleware_fires_before_auth_in_request_path(self) -> None:
        """RequestBodyLimitMiddleware must appear at a LOWER index than Auth in user_middleware.

        In Starlette LIFO semantics, lower index in user_middleware = outer = fires first
        on the request path. RequestBodyLimitMiddleware must be outer relative to
        AuthenticationGateMiddleware so that DoS-sized bodies are rejected before
        any authentication processing consumes CPU.

        In add-order: Auth added before RequestBodyLimit.
        In user_middleware: RequestBodyLimit at lower index (outer), Auth at higher index (inner).
        """
        from synth_engine.bootstrapper.dependencies.auth_middleware import (
            AuthenticationGateMiddleware,
        )
        from synth_engine.bootstrapper.dependencies.request_limits import (
            RequestBodyLimitMiddleware,
        )

        app = _build_app_with_middleware()
        ordered = _collect_middleware_classes_ordered(app)

        body_limit_idx = next(
            (i for i, c in enumerate(ordered) if c is RequestBodyLimitMiddleware), None
        )
        auth_idx = next(
            (i for i, c in enumerate(ordered) if c is AuthenticationGateMiddleware), None
        )

        assert body_limit_idx is not None, "RequestBodyLimitMiddleware not found in stack"
        assert auth_idx is not None, "AuthenticationGateMiddleware not found in stack"

        # In user_middleware: lower index = more outer = fires first on request path.
        # RequestBodyLimitMiddleware must have LOWER index than Auth (fires before Auth).
        assert body_limit_idx < auth_idx, (
            f"RequestBodyLimitMiddleware (idx={body_limit_idx}) must have LOWER index than "
            f"AuthenticationGateMiddleware (idx={auth_idx}) in user_middleware "
            f"(lower index = outer = fires first on request path). "
            f"Current order: {[c.__name__ for c in ordered]}"
        )

    def test_https_enforcement_is_outermost_middleware(self) -> None:
        """HTTPSEnforcementMiddleware must be the outermost non-error middleware layer.

        In Starlette user_middleware, outermost = lowest index (added last, fires first).
        RFC7807Middleware is added separately via register_error_handlers and appears
        at index 0; HTTPSEnforcementMiddleware appears at index 1 as the outermost
        domain middleware layer.
        """
        from synth_engine.bootstrapper.dependencies.https_enforcement import (
            HTTPSEnforcementMiddleware,
        )

        app = _build_app_with_middleware()
        ordered = _collect_middleware_classes_ordered(app)

        assert len(ordered) > 0, "No middleware found in stack"

        # Find HTTPSEnforcementMiddleware index
        https_idx = next(
            (i for i, c in enumerate(ordered) if c is HTTPSEnforcementMiddleware), None
        )
        assert https_idx is not None, "HTTPSEnforcementMiddleware not found in stack"

        # Verify HTTPS has lower index than Auth, Rate Limit, Seal, License, etc.
        # (RFC7807Middleware from register_error_handlers may appear at lower index — exempt)
        from synth_engine.bootstrapper.dependencies.auth_middleware import (
            AuthenticationGateMiddleware,
        )
        from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware

        auth_idx = next(
            (i for i, c in enumerate(ordered) if c is AuthenticationGateMiddleware), None
        )
        rate_idx = next((i for i, c in enumerate(ordered) if c is RateLimitGateMiddleware), None)

        assert https_idx < auth_idx, (  # type: ignore[operator]
            f"HTTPSEnforcementMiddleware (idx={https_idx}) must be outer (lower index) "
            f"than AuthenticationGateMiddleware (idx={auth_idx})"
        )
        assert https_idx < rate_idx, (  # type: ignore[operator]
            f"HTTPSEnforcementMiddleware (idx={https_idx}) must be outer (lower index) "
            f"than RateLimitGateMiddleware (idx={rate_idx})"
        )

    def test_idempotency_is_innermost_middleware(self) -> None:
        """IdempotencyMiddleware must be the innermost middleware layer.

        In Starlette user_middleware, innermost = highest index (added first, fires last).
        """
        from synth_engine.shared.middleware.idempotency import IdempotencyMiddleware

        app = _build_app_with_middleware()
        ordered = _collect_middleware_classes_ordered(app)

        assert len(ordered) > 0, "No middleware found in stack"

        idempotency_idx = next(
            (i for i, c in enumerate(ordered) if c is IdempotencyMiddleware), None
        )
        assert idempotency_idx is not None, "IdempotencyMiddleware not found in stack"

        # All other middleware must have lower index (more outer)
        max_idx = len(ordered) - 1
        assert idempotency_idx == max_idx, (
            f"IdempotencyMiddleware (idx={idempotency_idx}) must be innermost "
            f"(highest index = {max_idx}). "
            f"Current order: {[c.__name__ for c in ordered]}"
        )


class TestOversizedBodyBeforeAuth:
    """Behavioral tests: oversized body rejected before auth check fires."""

    @pytest.mark.asyncio
    async def test_oversized_body_rejected_before_auth_check(self) -> None:
        """A 2 MiB body from an unauthenticated request must return 413, not 401.

        Security property: body-size gate fires BEFORE auth gate.
        An unauthenticated client with an oversized body should hit the
        413 gate first — proving DoS protection fires before any auth cost.
        """
        from unittest.mock import patch

        from httpx import ASGITransport, AsyncClient

        app = _build_app_with_middleware()

        # 2 MiB body — exceeds the 1 MiB limit
        oversized_body = b"x" * (2 * 1024 * 1024)

        # No auth token provided — if auth fired first, we'd get 401
        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/connections",
                    content=oversized_body,
                    headers={"Content-Type": "application/json"},
                    # No Authorization header
                )

        # 413 means body-limit fired before auth
        # 401 would mean auth fired before body-limit (wrong order)
        assert response.status_code == 413, (
            f"Expected 413 (body limit before auth), got {response.status_code}. "
            "This indicates middleware ordering is wrong — auth fires before body-limit."
        )


# ---------------------------------------------------------------------------
# Helpers for middleware stack inspection
# ---------------------------------------------------------------------------


def _collect_middleware_classes(app: object) -> set[type]:
    """Extract the set of middleware classes from a FastAPI/Starlette app.

    Args:
        app: A FastAPI application instance.

    Returns:
        Set of middleware class objects registered on the app.
    """
    return set(_collect_middleware_classes_ordered(app))


def _collect_middleware_classes_ordered(app: object) -> list[type]:
    """Extract ordered list of middleware classes from a FastAPI/Starlette app.

    Inspects ``app.user_middleware`` which is a list of ``Middleware`` objects.

    In Starlette's LIFO semantics:
    - Index 0 = outermost (last added, fires first on request path).
    - Highest index = innermost (first added, fires last on request path).

    This matches the actual Starlette source: new middleware is prepended to
    ``user_middleware`` so the last ``add_middleware()`` call ends up at index 0.

    Args:
        app: A FastAPI application instance.

    Returns:
        List of middleware class objects where index 0 = outermost.

    Raises:
        AttributeError: If the app does not have a ``user_middleware`` attribute,
            indicating an unexpected FastAPI/Starlette internals change.
    """
    user_middleware = getattr(app, "user_middleware", None)
    if user_middleware is None:
        raise AttributeError(
            "FastAPI app has no 'user_middleware' attribute. "
            "The middleware inspection API may have changed in a Starlette update. "
            "Review T62.4 implementation for compatibility."
        )

    classes: list[type] = []
    for m in user_middleware:
        cls = getattr(m, "cls", None)
        if cls is not None:
            classes.append(cls)
    return classes
