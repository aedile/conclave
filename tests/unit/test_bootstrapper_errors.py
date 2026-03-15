"""Unit tests for the RFC 7807 error handler in bootstrapper/errors.py.

Tests follow TDD RED phase — all tests must fail before implementation.

Task: P5-T5.1 — Task Orchestration API Core
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

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
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

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
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

        with patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
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
