"""Unit tests for the RFC 7807 error handler in bootstrapper/errors.py.

Tests follow TDD RED phase — all tests must fail before implementation.

Task: P5-T5.1 — Task Orchestration API Core
Task: T19.1 — Middleware & Engine Singleton Fixes
CONSTITUTION Priority 3: TDD — RED phase
"""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.unit


class TestRFC7807ErrorHandler:
    """Tests for RFC 7807 Problem Details error formatting."""

    @pytest.mark.asyncio
    async def test_unhandled_exception_returns_rfc7807_body(self) -> None:
        """Unhandled exception must produce RFC 7807 JSON with required fields.

        Required fields per RFC 7807: type, title, status, detail.
        """
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app

        app = create_app()
        register_error_handlers(app)

        @app.get("/test-unhandled")
        async def _raise_unhandled() -> None:
            raise RuntimeError("Something went wrong internally")

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
                response = await client.get("/test-unhandled")

        assert response.status_code == 500
        body = response.json()
        assert "type" in body
        assert "title" in body
        assert "status" in body
        assert "detail" in body
        assert body["status"] == 500

    @pytest.mark.asyncio
    async def test_rfc7807_detail_is_sanitized(self) -> None:
        """RFC 7807 detail field must use safe_error_msg() — no raw paths or SQL."""
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app

        app = create_app()
        register_error_handlers(app)

        @app.get("/test-path-leak")
        async def _raise_with_path() -> None:
            raise RuntimeError("Error at /etc/passwd line 1")

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
                response = await client.get("/test-path-leak")

        body = response.json()
        assert "/etc/passwd" not in body.get("detail", "")

    @pytest.mark.asyncio
    async def test_rfc7807_type_is_about_blank(self) -> None:
        """RFC 7807 type field must default to 'about:blank' for generic errors."""
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app

        app = create_app()
        register_error_handlers(app)

        @app.get("/test-type")
        async def _raise_generic() -> None:
            raise ValueError("Bad value")

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
                response = await client.get("/test-type")

        body = response.json()
        assert body["type"] == "about:blank"

    def test_problem_detail_function_returns_correct_structure(self) -> None:
        """problem_detail() must return a dict with all RFC 7807 required fields."""
        from synth_engine.bootstrapper.errors import problem_detail

        result = problem_detail(
            status=422,
            title="Validation Error",
            detail="Field 'x' is required",
        )
        assert result["type"] == "about:blank"
        assert result["title"] == "Validation Error"
        assert result["status"] == 422
        assert result["detail"] == "Field 'x' is required"

    def test_problem_detail_accepts_custom_type(self) -> None:
        """problem_detail() must use custom type URI when provided."""
        from synth_engine.bootstrapper.errors import problem_detail

        result = problem_detail(
            status=404,
            title="Not Found",
            detail="Job not found",
            type_uri="https://api.example.com/errors/not-found",
        )
        assert result["type"] == "https://api.example.com/errors/not-found"


class TestRFC7807PureASGIMiddleware:
    """T19.1: Tests verifying RFC7807Middleware is a pure ASGI middleware.

    Pure ASGI middleware does not buffer the response body, which is required
    for SSE (Server-Sent Events) streaming to work correctly.

    For exception-handling tests, the inner app is a raw ASGI callable (not
    FastAPI) so that Starlette's ServerErrorMiddleware does not intercept the
    exception before our middleware can catch it.  ServerErrorMiddleware is
    only present in a full FastAPI app stack and would handle the exception
    itself (sending a 500 HTML response and re-raising), which would prevent
    RFC7807Middleware from producing a RFC 7807 response.

    The full-stack behavior (RFC7807Middleware inside a FastAPI app) is
    verified by the existing TestRFC7807ErrorHandler tests which use
    create_app() + register_error_handlers().
    """

    def test_rfc7807_middleware_is_not_base_http_middleware(self) -> None:
        """RFC7807Middleware must NOT extend BaseHTTPMiddleware.

        BaseHTTPMiddleware buffers the entire response body before returning,
        which breaks SSE streaming. The middleware must be a pure ASGI class.
        """
        from starlette.middleware.base import BaseHTTPMiddleware

        from synth_engine.bootstrapper.errors import RFC7807Middleware

        assert not issubclass(RFC7807Middleware, BaseHTTPMiddleware), (
            "RFC7807Middleware must be a pure ASGI middleware, not BaseHTTPMiddleware. "
            "BaseHTTPMiddleware buffers responses and breaks SSE streaming."
        )

    def test_rfc7807_middleware_has_call_method(self) -> None:
        """Pure ASGI middleware must implement __call__(scope, receive, send)."""
        from synth_engine.bootstrapper.errors import RFC7807Middleware

        assert callable(RFC7807Middleware), (
            "RFC7807Middleware must implement __call__ for pure ASGI protocol."
        )

    def test_rfc7807_middleware_does_not_have_dispatch_method(self) -> None:
        """Pure ASGI middleware must NOT have a dispatch() method.

        dispatch() is the BaseHTTPMiddleware pattern. Pure ASGI uses __call__.
        """
        from synth_engine.bootstrapper.errors import RFC7807Middleware

        assert not hasattr(RFC7807Middleware, "dispatch"), (
            "RFC7807Middleware must not have dispatch() — that is the BaseHTTPMiddleware "
            "pattern. Pure ASGI middleware uses __call__(scope, receive, send)."
        )

    @pytest.mark.asyncio
    async def test_pure_asgi_middleware_passes_normal_responses_through(self) -> None:
        """Normal (non-error) HTTP responses must pass through unmodified.

        Uses a raw ASGI app that sends a simple 200 JSON response directly.
        """
        import json as _json

        from starlette.types import Receive, Scope, Send

        from synth_engine.bootstrapper.errors import RFC7807Middleware

        async def inner_ok(scope: Scope, receive: Receive, send: Send) -> None:
            body = _json.dumps({"message": "hello"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        [b"content-type", b"application/json"],
                        [b"content-length", str(len(body)).encode()],
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body, "more_body": False})

        wrapped = RFC7807Middleware(app=inner_ok)  # type: ignore[arg-type]

        async with AsyncClient(
            transport=ASGITransport(app=wrapped), base_url="http://test"
        ) as client:
            response = await client.get("/ok")

        assert response.status_code == 200
        assert response.json() == {"message": "hello"}

    @pytest.mark.asyncio
    async def test_pure_asgi_middleware_returns_rfc7807_on_exception(self) -> None:
        """Exception from inner ASGI app must produce RFC 7807 JSON response.

        Uses a raw ASGI callable (no ServerErrorMiddleware) so the exception
        propagates directly to RFC7807Middleware without being intercepted.
        """
        from starlette.types import Receive, Scope, Send

        from synth_engine.bootstrapper.errors import RFC7807Middleware

        async def inner_raises(scope: Scope, receive: Receive, send: Send) -> None:
            raise RuntimeError("intentional error")

        wrapped = RFC7807Middleware(app=inner_raises)  # type: ignore[arg-type]

        async with AsyncClient(
            transport=ASGITransport(app=wrapped), base_url="http://test"
        ) as client:
            response = await client.get("/boom")

        assert response.status_code == 500
        body = response.json()
        assert body.get("type") == "about:blank"
        assert body.get("status") == 500
        assert "title" in body
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_pure_asgi_middleware_sets_json_content_type_on_error(self) -> None:
        """Error responses must have content-type: application/json header.

        Uses a raw ASGI callable (no ServerErrorMiddleware) so the exception
        propagates directly to RFC7807Middleware without being intercepted.
        """
        from starlette.types import Receive, Scope, Send

        from synth_engine.bootstrapper.errors import RFC7807Middleware

        async def inner_raises(scope: Scope, receive: Receive, send: Send) -> None:
            raise ValueError("bad input")

        wrapped = RFC7807Middleware(app=inner_raises)  # type: ignore[arg-type]

        async with AsyncClient(
            transport=ASGITransport(app=wrapped), base_url="http://test"
        ) as client:
            response = await client.get("/error")

        assert response.status_code == 500
        content_type = response.headers.get("content-type", "")
        assert "application/json" in content_type

    @pytest.mark.asyncio
    async def test_pure_asgi_middleware_passes_through_non_http_scopes(self) -> None:
        """Non-HTTP scope types (e.g., lifespan) must pass through untouched."""
        from starlette.types import Receive, Scope, Send

        from synth_engine.bootstrapper.errors import RFC7807Middleware

        received_scopes: list[str] = []

        async def inner_app(scope: Scope, receive: Receive, send: Send) -> None:
            received_scopes.append(scope["type"])

        middleware = RFC7807Middleware(app=inner_app)  # type: ignore[arg-type]

        # Simulate a lifespan scope
        scope: Scope = {"type": "lifespan"}

        async def dummy_receive() -> dict[str, str]:  # type: ignore[return]
            return {}

        async def dummy_send(message: dict[str, object]) -> None:  # type: ignore[type-arg]
            pass

        await middleware(scope, dummy_receive, dummy_send)  # type: ignore[arg-type]
        assert "lifespan" in received_scopes
