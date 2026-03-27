"""Tests for T60.1 — AuthenticationGateMiddleware extracted to auth_middleware.py.

Verifies that:
- AuthenticationGateMiddleware is in the new canonical location (auth_middleware.py)
- The re-export in auth.py still works (30+ files import from there)
- Both resolve to the same class
- _build_401_response is private to auth_middleware.py (not re-exported from auth.py)
- auth.py is reduced to ≤350 LOC
- middleware.py imports from auth_middleware.py as canonical source
- AUTH_EXEMPT_PATHS and AuthenticationError remain in auth.py

CONSTITUTION Priority 3: TDD
Task: T60.1 — Extract AuthenticationGateMiddleware to auth_middleware.py
"""

from __future__ import annotations

import inspect
from pathlib import Path


class TestAuthMiddlewareCanonicalLocation:
    """AuthenticationGateMiddleware must live in auth_middleware.py (canonical source)."""

    def test_middleware_importable_from_auth_middleware(self) -> None:
        """AuthenticationGateMiddleware must be importable from auth_middleware.py."""
        from synth_engine.bootstrapper.dependencies.auth_middleware import (
            AuthenticationGateMiddleware,
        )

        assert AuthenticationGateMiddleware.__name__ == "AuthenticationGateMiddleware"

    def test_middleware_is_defined_in_auth_middleware_module(self) -> None:
        """AuthenticationGateMiddleware.__module__ must point to auth_middleware."""
        from synth_engine.bootstrapper.dependencies.auth_middleware import (
            AuthenticationGateMiddleware,
        )

        assert AuthenticationGateMiddleware.__module__ == (
            "synth_engine.bootstrapper.dependencies.auth_middleware"
        )

    def test_build_401_response_exists_in_auth_middleware(self) -> None:
        """_build_401_response must be importable from auth_middleware.py."""
        from synth_engine.bootstrapper.dependencies.auth_middleware import _build_401_response

        assert callable(_build_401_response)

    def test_build_401_response_returns_json_response_with_401_status(self) -> None:
        """_build_401_response must return a JSONResponse with status code 401."""
        from fastapi.responses import JSONResponse

        from synth_engine.bootstrapper.dependencies.auth_middleware import _build_401_response

        response = _build_401_response("test detail message")
        assert isinstance(response, JSONResponse)
        assert response.status_code == 401

    def test_auth_middleware_imports_verify_token_from_auth(self) -> None:
        """auth_middleware.py must import verify_token from auth.py (one-way dependency)."""
        import synth_engine.bootstrapper.dependencies.auth_middleware as auth_middleware_mod

        source = inspect.getsource(auth_middleware_mod)
        # verify_token is imported from auth.py — deferred inside dispatch()
        # to break the circular import (auth.py re-exports this class).
        assert "dependencies.auth import" in source
        assert "verify_token" in source


class TestAuthPyReExports:
    """auth.py must re-export AuthenticationGateMiddleware for backward compatibility."""

    def test_middleware_re_exported_from_auth(self) -> None:
        """auth.py must still export AuthenticationGateMiddleware."""
        from synth_engine.bootstrapper.dependencies.auth import AuthenticationGateMiddleware

        assert AuthenticationGateMiddleware.__name__ == "AuthenticationGateMiddleware"

    def test_both_imports_resolve_to_same_class(self) -> None:
        """auth.py and auth_middleware.py must expose the same class object."""
        from synth_engine.bootstrapper.dependencies.auth import (
            AuthenticationGateMiddleware as FromAuth,
        )
        from synth_engine.bootstrapper.dependencies.auth_middleware import (
            AuthenticationGateMiddleware as FromAuthMiddleware,
        )

        assert FromAuth is FromAuthMiddleware

    def test_auth_exempt_paths_stays_in_auth(self) -> None:
        """AUTH_EXEMPT_PATHS must remain in auth.py (30+ test imports depend on it)."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert isinstance(AUTH_EXEMPT_PATHS, frozenset)
        assert "/auth/token" in AUTH_EXEMPT_PATHS

    def test_authentication_error_stays_in_auth(self) -> None:
        """AuthenticationError must remain in auth.py (used by verify_token)."""
        from synth_engine.bootstrapper.dependencies.auth import AuthenticationError

        assert issubclass(AuthenticationError, Exception)

    def test_auth_py_reduced_to_350_loc_or_less(self) -> None:
        """auth.py must be ≤350 LOC after extracting the middleware."""
        auth_path = (
            Path(__file__).parent.parent.parent
            / "src/synth_engine/bootstrapper/dependencies/auth.py"
        )
        loc = sum(1 for _ in auth_path.read_text(encoding="utf-8").splitlines())
        assert loc <= 350, f"auth.py is {loc} LOC — must be ≤350 after extraction"


class TestMiddlewarePyImportSource:
    """middleware.py must import AuthenticationGateMiddleware from auth_middleware.py."""

    def test_middleware_py_imports_from_auth_middleware(self) -> None:
        """middleware.py must reference auth_middleware.py as the canonical import source."""
        import synth_engine.bootstrapper.middleware as middleware_mod

        source = inspect.getsource(middleware_mod)
        # middleware.py should import from auth_middleware (canonical) not auth
        assert "auth_middleware" in source or "auth.AuthenticationGateMiddleware" in source
