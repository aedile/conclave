"""Verify that bootstrapper.errors re-exports all public symbols correctly.

This test verifies the public API surface of the bootstrapper.errors package
so that the module-to-package refactor (T36.2) cannot silently break imports.

Task: T36.2 — Split bootstrapper/errors.py Into Focused Modules
CONSTITUTION Priority 3: TDD — RED phase
"""

import inspect

import pytest

pytestmark = pytest.mark.unit


class TestBootstrapperErrorsPublicAPI:
    """Verify all public symbols are importable from bootstrapper.errors."""

    def test_problem_detail_is_callable(self) -> None:
        """problem_detail must be importable and callable."""
        from synth_engine.bootstrapper.errors import problem_detail

        assert callable(problem_detail)

    def test_operator_error_response_is_callable(self) -> None:
        """operator_error_response must be importable and callable."""
        from synth_engine.bootstrapper.errors import operator_error_response

        assert callable(operator_error_response)

    def test_register_error_handlers_is_callable(self) -> None:
        """register_error_handlers must be importable and callable."""
        from synth_engine.bootstrapper.errors import register_error_handlers

        assert callable(register_error_handlers)

    def test_rfc7807_middleware_is_class(self) -> None:
        """RFC7807Middleware must be importable and be a class."""
        from synth_engine.bootstrapper.errors import RFC7807Middleware

        assert inspect.isclass(RFC7807Middleware)

    def test_operator_error_map_is_dict(self) -> None:
        """OPERATOR_ERROR_MAP must be importable and be a dict."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP

        assert isinstance(OPERATOR_ERROR_MAP, dict)
        assert len(OPERATOR_ERROR_MAP) > 0

    def test_operator_error_entry_is_type(self) -> None:
        """OperatorErrorEntry must be importable as a TypedDict class."""
        from synth_engine.bootstrapper.errors import OperatorErrorEntry

        assert OperatorErrorEntry is not None

    def test_operator_error_map_entries_have_required_keys(self) -> None:
        """Every entry in OPERATOR_ERROR_MAP must have all four required keys."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP

        required_keys = {"title", "detail", "status_code", "type_uri"}
        for exc_class, entry in OPERATOR_ERROR_MAP.items():
            missing = required_keys - set(entry.keys())
            assert not missing, f"Entry for {exc_class.__name__} missing keys: {missing}"

    def test_problem_detail_returns_rfc7807_dict(self) -> None:
        """problem_detail() must return a dict with all four RFC 7807 fields."""
        from synth_engine.bootstrapper.errors import problem_detail

        result = problem_detail(status=404, title="Not Found", detail="Resource missing")
        assert result["type"] == "about:blank"
        assert result["title"] == "Not Found"
        assert result["status"] == 404
        assert result["detail"] == "Resource missing"

    def test_rfc7807_middleware_accepts_asgi_app(self) -> None:
        """RFC7807Middleware must be instantiable with an ASGI app."""
        from synth_engine.bootstrapper.errors import RFC7807Middleware

        async def dummy_app(scope: object, receive: object, send: object) -> None:
            pass

        mw = RFC7807Middleware(dummy_app)  # type: ignore[arg-type]
        assert mw.app is dummy_app
