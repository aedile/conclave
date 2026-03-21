"""Verify that bootstrapper.errors re-exports all public symbols correctly.

This test verifies the public API surface of the bootstrapper.errors package
so that the module-to-package refactor (T36.2) cannot silently break imports.

Task: T36.2 — Split bootstrapper/errors.py Into Focused Modules
Task: T40.1 — Replace Shallow Assertions With Value-Checking Tests
CONSTITUTION Priority 3: TDD — RED phase
"""

import inspect

import pytest

pytestmark = pytest.mark.unit


class TestBootstrapperErrorsPublicAPI:
    """Verify all public symbols are importable from bootstrapper.errors."""

    def test_problem_detail_returns_rfc7807_body(self) -> None:
        """problem_detail() returns a dict with all required RFC 7807 fields."""
        from synth_engine.bootstrapper.errors import problem_detail

        result = problem_detail(status=400, title="Bad Request", detail="Input invalid")
        assert result["type"] == "about:blank"
        assert result["title"] == "Bad Request"
        assert result["status"] == 400
        assert result["detail"] == "Input invalid"

    def test_operator_error_response_returns_json_response(self) -> None:
        """operator_error_response() returns a JSONResponse for a known domain exception."""
        from fastapi.responses import JSONResponse

        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP, operator_error_response

        # Pick any exception class registered in the map
        exc_class = next(iter(OPERATOR_ERROR_MAP))
        exc = exc_class("test error")
        result = operator_error_response(exc)
        assert isinstance(result, JSONResponse)
        # Status code must match what the map specifies
        expected_status = OPERATOR_ERROR_MAP[exc_class]["status_code"]
        assert result.status_code == expected_status

    def test_register_error_handlers_adds_middleware(self) -> None:
        """register_error_handlers() adds RFC7807Middleware to the FastAPI app."""
        from fastapi import FastAPI

        from synth_engine.bootstrapper.errors import RFC7807Middleware, register_error_handlers

        app = FastAPI()
        register_error_handlers(app)
        # Middleware stack is stored in app.user_middleware; verify RFC7807Middleware present
        middleware_classes = [m.cls for m in app.user_middleware]
        assert RFC7807Middleware in middleware_classes, (
            "register_error_handlers() must add RFC7807Middleware to the app middleware stack"
        )

    def test_rfc7807_middleware_is_class(self) -> None:
        """RFC7807Middleware must be importable and be a class."""
        from synth_engine.bootstrapper.errors import RFC7807Middleware

        assert inspect.isclass(RFC7807Middleware)

    def test_operator_error_map_is_dict(self) -> None:
        """OPERATOR_ERROR_MAP must be importable and be a dict."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP

        assert isinstance(OPERATOR_ERROR_MAP, dict)
        assert len(OPERATOR_ERROR_MAP) > 0

    def test_operator_error_entry_is_typed_dict_class(self) -> None:
        """OperatorErrorEntry must be importable as a TypedDict subclass."""
        import typing

        from synth_engine.bootstrapper.errors import OperatorErrorEntry

        # TypedDict classes are recognised by typing.get_type_hints returning a dict
        hints = typing.get_type_hints(OperatorErrorEntry)
        assert isinstance(hints, dict), "OperatorErrorEntry must have type annotations (TypedDict)"
        assert len(hints) > 0, "OperatorErrorEntry must have at least one typed field"

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
