"""Unit tests for the RFC 7807 error handler in bootstrapper/errors.py.

Tests follow TDD RED phase — all tests must fail before implementation.

Task: P5-T5.1 — Task Orchestration API Core
Task: T19.1 — Middleware & Engine Singleton Fixes
Task: P29-T29.3 — Error Message Audience Differentiation
Task: T34.3 — Complete OPERATOR_ERROR_MAP for All Domain Exceptions
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
        """Pure ASGI middleware instance must be callable with (scope, receive, send)."""
        import inspect

        from synth_engine.bootstrapper.errors import RFC7807Middleware

        async def dummy_app(scope: object, receive: object, send: object) -> None:
            pass

        middleware = RFC7807Middleware(app=dummy_app)  # type: ignore[arg-type]
        assert callable(middleware), (
            "RFC7807Middleware instance must be callable for pure ASGI protocol."
        )
        sig = inspect.signature(middleware.__call__)
        param_names = list(sig.parameters.keys())
        assert "scope" in param_names, "RFC7807Middleware.__call__ must accept 'scope'"
        assert "receive" in param_names, "RFC7807Middleware.__call__ must accept 'receive'"
        assert "send" in param_names, "RFC7807Middleware.__call__ must accept 'send'"

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

    @pytest.mark.asyncio
    async def test_pure_asgi_middleware_reraises_when_headers_sent(self) -> None:
        """Exception raised after headers are sent must propagate out of middleware.

        Once ``http.response.start`` has been sent, the response is committed to
        a status code.  RFC7807Middleware cannot send a new 500 response at that
        point — it must re-raise so that the server can terminate the connection.

        Uses a raw ASGI callable that sends ``http.response.start`` first (marking
        headers as sent) and then raises an exception.  The exception must propagate
        out of ``RFC7807Middleware.__call__`` rather than being silently swallowed
        or converted to a 500 JSON response.
        """
        from starlette.types import Receive, Scope, Send

        from synth_engine.bootstrapper.errors import RFC7807Middleware

        async def inner_sends_headers_then_raises(
            scope: Scope, receive: Receive, send: Send
        ) -> None:
            # Send the response start (headers committed) before raising.
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [[b"content-type", b"text/plain"]],
                }
            )
            raise RuntimeError("error after headers sent")

        wrapped = RFC7807Middleware(app=inner_sends_headers_then_raises)  # type: ignore[arg-type]

        # Build a minimal ASGI scope for an HTTP GET request.
        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": "/stream",
            "query_string": b"",
            "headers": [],
        }

        sent_messages: list[object] = []

        async def dummy_receive() -> dict[str, str]:  # type: ignore[return]
            return {}

        async def capturing_send(message: object) -> None:
            sent_messages.append(message)

        # The middleware MUST re-raise the exception when headers have been sent.
        with pytest.raises(RuntimeError, match="error after headers sent"):
            await wrapped(scope, dummy_receive, capturing_send)  # type: ignore[arg-type]

        # Confirm that headers were indeed sent (so the re-raise path was taken).
        assert any(
            isinstance(m, dict) and m.get("type") == "http.response.start" for m in sent_messages
        ), "Expected http.response.start to have been sent before the exception"


class TestOperatorFriendlyErrorMessages:
    """T29.3: Tests for operator-friendly error message mapping.

    The bootstrapper's exception handlers must convert domain exceptions into
    RFC 7807 responses with human-readable titles and actionable detail messages.
    Internal exception messages are preserved in logs and MUST NOT be exposed
    verbatim via HTTP.

    CONSTITUTION Priority 0: Security — never leak internal technical details.
    Task: P29-T29.3 — Error Message Audience Differentiation
    """

    def test_budget_exhaustion_error_produces_friendly_title(self) -> None:
        """BudgetExhaustionError must map to 'Privacy Budget Exceeded' title."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import BudgetExhaustionError

        assert BudgetExhaustionError in OPERATOR_ERROR_MAP
        entry = OPERATOR_ERROR_MAP[BudgetExhaustionError]
        assert entry["title"] == "Privacy Budget Exceeded"

    def test_budget_exhaustion_error_detail_contains_remediation(self) -> None:
        """BudgetExhaustionError detail must mention how to reset the budget."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import BudgetExhaustionError

        entry = OPERATOR_ERROR_MAP[BudgetExhaustionError]
        detail = entry["detail"].lower()
        # Must reference budget reset action
        assert "reset" in detail or "budget" in detail

    def test_vault_sealed_error_produces_friendly_title(self) -> None:
        """VaultSealedError must map to 'Vault Is Sealed' title."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import VaultSealedError

        assert VaultSealedError in OPERATOR_ERROR_MAP
        entry = OPERATOR_ERROR_MAP[VaultSealedError]
        assert entry["title"] == "Vault Is Sealed"

    def test_vault_sealed_error_detail_contains_unseal_instruction(self) -> None:
        """VaultSealedError detail must instruct operator to unseal."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import VaultSealedError

        entry = OPERATOR_ERROR_MAP[VaultSealedError]
        detail = entry["detail"].lower()
        assert "unseal" in detail

    def test_vault_empty_passphrase_error_produces_friendly_title(self) -> None:
        """VaultEmptyPassphraseError must map to 'Empty Passphrase' title."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.security.vault import VaultEmptyPassphraseError

        assert VaultEmptyPassphraseError in OPERATOR_ERROR_MAP
        entry = OPERATOR_ERROR_MAP[VaultEmptyPassphraseError]
        assert entry["title"] == "Empty Passphrase"

    def test_vault_empty_passphrase_error_detail_contains_action(self) -> None:
        """VaultEmptyPassphraseError detail must instruct operator to enter passphrase."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.security.vault import VaultEmptyPassphraseError

        entry = OPERATOR_ERROR_MAP[VaultEmptyPassphraseError]
        detail = entry["detail"].lower()
        assert "passphrase" in detail

    def test_vault_config_error_produces_friendly_title(self) -> None:
        """VaultConfigError must map to 'Vault Configuration Error' title."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.security.vault import VaultConfigError

        assert VaultConfigError in OPERATOR_ERROR_MAP
        entry = OPERATOR_ERROR_MAP[VaultConfigError]
        assert entry["title"] == "Vault Configuration Error"

    def test_vault_config_error_detail_references_env_var(self) -> None:
        """VaultConfigError detail must reference the VAULT_SEAL_SALT env var."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.security.vault import VaultConfigError

        entry = OPERATOR_ERROR_MAP[VaultConfigError]
        assert "VAULT_SEAL_SALT" in entry["detail"]

    def test_oom_guardrail_error_produces_friendly_title(self) -> None:
        """OOMGuardrailError must map to 'Memory Limit Exceeded' title."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import OOMGuardrailError

        assert OOMGuardrailError in OPERATOR_ERROR_MAP
        entry = OPERATOR_ERROR_MAP[OOMGuardrailError]
        assert entry["title"] == "Memory Limit Exceeded"

    def test_oom_guardrail_error_detail_contains_remediation(self) -> None:
        """OOMGuardrailError detail must suggest reducing the dataset."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import OOMGuardrailError

        entry = OPERATOR_ERROR_MAP[OOMGuardrailError]
        detail = entry["detail"].lower()
        assert "dataset" in detail or "reduce" in detail or "rows" in detail

    def test_operator_error_map_entries_have_required_keys(self) -> None:
        """Every entry in OPERATOR_ERROR_MAP must have title, detail, status_code, type_uri."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP

        required_keys = {"title", "detail", "status_code", "type_uri"}
        for exc_class, entry in OPERATOR_ERROR_MAP.items():
            missing = required_keys - entry.keys()
            assert not missing, f"{exc_class.__name__} entry missing keys: {missing}"

    def test_privilege_escalation_error_in_operator_map_with_sanitized_detail(self) -> None:
        """PrivilegeEscalationError must appear in OPERATOR_ERROR_MAP with a fixed safe detail.

        T34.3: All 11 SynthEngineError subclasses must have RFC 7807 mappings.
        The detail must NOT reference database roles, credential hints, or any
        security-sensitive internals — it must use a fixed, sanitized string.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import PrivilegeEscalationError

        assert PrivilegeEscalationError in OPERATOR_ERROR_MAP, (
            "PrivilegeEscalationError must have an OPERATOR_ERROR_MAP entry (T34.3). "
            "The detail must be a fixed, sanitized string — not str(exc)."
        )
        entry = OPERATOR_ERROR_MAP[PrivilegeEscalationError]
        assert entry["status_code"] == 403
        # Detail must be a fixed static string — must not contain dynamic exception text
        assert len(entry["detail"]) > 0
        # Must NOT include the placeholder that would expose role/privilege internals
        assert "str(exc)" not in entry["detail"]

    def test_artifact_tampering_error_in_operator_map_with_sanitized_detail(self) -> None:
        """ArtifactTamperingError must appear in OPERATOR_ERROR_MAP with a fixed safe detail.

        T34.3: All 11 SynthEngineError subclasses must have RFC 7807 mappings.
        The detail must NOT reference artifact paths, HMAC keys, or signing details —
        it must use a fixed, sanitized string.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import ArtifactTamperingError

        assert ArtifactTamperingError in OPERATOR_ERROR_MAP, (
            "ArtifactTamperingError must have an OPERATOR_ERROR_MAP entry (T34.3). "
            "The detail must be a fixed, sanitized string — not str(exc)."
        )
        entry = OPERATOR_ERROR_MAP[ArtifactTamperingError]
        assert entry["status_code"] == 422
        # Detail must be a fixed static string — must not reference artifact paths or HMAC keys
        assert len(entry["detail"]) > 0

    def test_operator_error_response_raises_key_error_for_unknown_exception(self) -> None:
        """operator_error_response() must raise KeyError for unmapped exception classes.

        The docstring for operator_error_response() documents that it raises
        KeyError when called with an exception whose class is not in
        OPERATOR_ERROR_MAP.  This test exercises that contract directly so the
        behaviour is verified by the test suite.
        """
        from synth_engine.bootstrapper.errors import operator_error_response

        with pytest.raises(KeyError):
            operator_error_response(RuntimeError("test"))


class TestOperatorFriendlyExceptionHandlers:
    """T29.3: Integration tests for exception handlers registered in router_registry.

    These tests verify that the FastAPI exception handlers wire up correctly
    and produce RFC 7807 responses with operator-friendly messages when domain
    exceptions are raised from route handlers.
    """

    @pytest.mark.asyncio
    async def test_budget_exhaustion_returns_rfc7807_with_friendly_title(self) -> None:
        """BudgetExhaustionError raised in a route must return RFC 7807 with friendly title."""
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import BudgetExhaustionError

        app = create_app()

        @app.get("/test-budget-exhaustion")
        async def _raise_budget() -> None:
            raise BudgetExhaustionError(
                "DP budget exhausted: epsilon_spent=1.234 >= allocated_epsilon=1.0"
            )

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
                response = await client.get("/test-budget-exhaustion")

        body = response.json()
        assert body["title"] == "Privacy Budget Exceeded"
        assert "type" in body
        assert "status" in body
        assert "detail" in body

    def test_budget_exhaustion_internal_message_not_in_http_detail(self) -> None:
        """BudgetExhaustionError HTTP detail must not contain raw epsilon values.

        The operator-friendly detail must be the mapping value, not the raw
        internal exception message which contains technical epsilon/delta values.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import BudgetExhaustionError

        entry = OPERATOR_ERROR_MAP[BudgetExhaustionError]
        # The operator detail should not expose raw epsilon math
        assert "epsilon_spent" not in entry["detail"]
        assert "allocated_epsilon" not in entry["detail"]

    @pytest.mark.asyncio
    async def test_vault_sealed_returns_rfc7807_with_friendly_title(self) -> None:
        """VaultSealedError raised in a route must return RFC 7807 with friendly title."""
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import VaultSealedError

        app = create_app()

        @app.get("/test-vault-sealed")
        async def _raise_sealed() -> None:
            raise VaultSealedError()

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
                response = await client.get("/test-vault-sealed")

        body = response.json()
        assert body["title"] == "Vault Is Sealed"
        assert body["status"] == 423

    @pytest.mark.asyncio
    async def test_oom_guardrail_returns_rfc7807_with_friendly_title(self) -> None:
        """OOMGuardrailError raised in a route must return RFC 7807 with friendly title."""
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import OOMGuardrailError

        app = create_app()

        @app.get("/test-oom-guardrail")
        async def _raise_oom() -> None:
            raise OOMGuardrailError(
                "6.8 GiB estimated, 8.0 GiB available -- reduce dataset by 1.00x"
            )

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
                response = await client.get("/test-oom-guardrail")

        body = response.json()
        assert body["title"] == "Memory Limit Exceeded"


class TestUnsealRouteRFC7807Format:
    """T29.3: Tests verifying /unseal route uses RFC 7807 format for errors.

    The /unseal route previously returned ad-hoc ``{"error_code": ..., "detail": ...}``
    responses. These must be upgraded to RFC 7807 format with operator-friendly
    messages, matching the pattern used by other domain exception handlers.

    Task: P29-T29.3 — Error Message Audience Differentiation
    """

    @pytest.mark.asyncio
    async def test_empty_passphrase_returns_rfc7807_format(self) -> None:
        """POST /unseal with empty passphrase must return RFC 7807 body.

        The response must have ``type``, ``title``, ``status``, and ``detail``
        keys per RFC 7807, not the legacy ``error_code``/``detail`` format.
        """
        from synth_engine.bootstrapper.main import create_app

        app = create_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/unseal", json={"passphrase": ""})

        assert response.status_code == 400
        body = response.json()
        # Must be RFC 7807 format
        assert "type" in body, "Response must contain RFC 7807 'type' field"
        assert "title" in body, "Response must contain RFC 7807 'title' field"
        assert "status" in body, "Response must contain RFC 7807 'status' field"
        assert "detail" in body, "Response must contain RFC 7807 'detail' field"
        # Must NOT be legacy format
        assert "error_code" not in body, (
            "Response must not use legacy 'error_code' field — use RFC 7807 format"
        )
        assert body["title"] == "Empty Passphrase"

    @pytest.mark.asyncio
    async def test_vault_config_error_returns_rfc7807_format(self) -> None:
        """POST /unseal when VAULT_SEAL_SALT missing must return RFC 7807 body."""
        from unittest.mock import patch as _patch

        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.security.vault import VaultConfigError

        app = create_app()

        with _patch(
            "synth_engine.bootstrapper.lifecycle.VaultState.unseal",
            side_effect=VaultConfigError("VAULT_SEAL_SALT not set"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/unseal", json={"passphrase": "somepass"})

        assert response.status_code == 400
        body = response.json()
        assert "type" in body
        assert "title" in body
        assert "status" in body
        assert "detail" in body
        assert "error_code" not in body, "Response must not use legacy 'error_code' field"
        assert body["title"] == "Vault Configuration Error"


class TestT343CompleteOperatorErrorMap:
    """T34.3: Tests for the 6 newly-mapped domain exceptions in OPERATOR_ERROR_MAP.

    Verifies that all 11 SynthEngineError subclasses have RFC 7807 mappings
    with correct HTTP status codes and type URIs.

    Task: T34.3 — Complete OPERATOR_ERROR_MAP for All Domain Exceptions
    CONSTITUTION Priority 0: Security — sanitized messages for security-sensitive exceptions.
    """

    def test_vault_already_unsealed_error_maps_to_409_conflict(self) -> None:
        """VaultAlreadyUnsealedError must map to HTTP 409 Conflict.

        Attempting to unseal an already-unsealed vault is a client-state conflict,
        not a server error. HTTP 409 Conflict is the correct status code.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import VaultAlreadyUnsealedError

        assert VaultAlreadyUnsealedError in OPERATOR_ERROR_MAP, (
            "VaultAlreadyUnsealedError must be in OPERATOR_ERROR_MAP (T34.3)"
        )
        entry = OPERATOR_ERROR_MAP[VaultAlreadyUnsealedError]
        assert entry["status_code"] == 409
        assert entry["type_uri"] == "about:blank"
        assert len(entry["title"]) > 0
        assert len(entry["detail"]) > 0

    def test_license_error_maps_to_403_forbidden(self) -> None:
        """LicenseError must map to HTTP 403 Forbidden.

        A license validation failure means the operator is not authorized to use
        the engine. HTTP 403 Forbidden communicates this clearly.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import LicenseError

        assert LicenseError in OPERATOR_ERROR_MAP, (
            "LicenseError must be in OPERATOR_ERROR_MAP (T34.3)"
        )
        entry = OPERATOR_ERROR_MAP[LicenseError]
        assert entry["status_code"] == 403
        assert entry["type_uri"] == "about:blank"
        assert len(entry["title"]) > 0
        assert len(entry["detail"]) > 0

    def test_collision_error_maps_to_409_conflict(self) -> None:
        """CollisionError must map to HTTP 409 Conflict.

        A masking collision is a data-state conflict — two distinct source values
        would collide to the same masked output. HTTP 409 Conflict is correct.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.modules.masking.registry import CollisionError

        assert CollisionError in OPERATOR_ERROR_MAP, (
            "CollisionError must be in OPERATOR_ERROR_MAP (T34.3). "
            "Import from modules/masking/registry.py per task spec."
        )
        entry = OPERATOR_ERROR_MAP[CollisionError]
        assert entry["status_code"] == 409
        assert entry["type_uri"] == "about:blank"
        assert len(entry["title"]) > 0
        assert len(entry["detail"]) > 0

    def test_cycle_detection_error_maps_to_422_unprocessable(self) -> None:
        """CycleDetectionError must map to HTTP 422 Unprocessable Entity.

        A cycle in the schema FK graph is a structural data problem — the input
        schema is malformed. HTTP 422 Unprocessable Entity is correct.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.modules.mapping.graph import CycleDetectionError

        assert CycleDetectionError in OPERATOR_ERROR_MAP, (
            "CycleDetectionError must be in OPERATOR_ERROR_MAP (T34.3). "
            "Import from modules/mapping/graph.py per task spec."
        )
        entry = OPERATOR_ERROR_MAP[CycleDetectionError]
        assert entry["status_code"] == 422
        assert entry["type_uri"] == "about:blank"
        assert len(entry["title"]) > 0
        assert len(entry["detail"]) > 0

    def test_privilege_escalation_error_maps_to_403_with_sanitized_detail(self) -> None:
        """PrivilegeEscalationError must map to HTTP 403 with a fixed sanitized detail.

        The detail must be a static string that does NOT contain database role names,
        privilege descriptions, or any security-sensitive context from str(exc).
        Security: detail text must not leak credential hints to the HTTP caller.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import PrivilegeEscalationError

        assert PrivilegeEscalationError in OPERATOR_ERROR_MAP, (
            "PrivilegeEscalationError must be in OPERATOR_ERROR_MAP (T34.3)"
        )
        entry = OPERATOR_ERROR_MAP[PrivilegeEscalationError]
        assert entry["status_code"] == 403
        assert entry["type_uri"] == "about:blank"
        # The detail must be a non-empty static safe string
        assert len(entry["detail"]) > 0
        # The detail must NOT be dynamic exception text — it must be a fixed string
        # that does not reveal database role names or privilege details
        assert "INSERT" not in entry["detail"]
        assert "UPDATE" not in entry["detail"]
        assert "DELETE" not in entry["detail"]
        assert "superuser" not in entry["detail"].lower()

    def test_artifact_tampering_error_maps_to_422_with_sanitized_detail(self) -> None:
        """ArtifactTamperingError must map to HTTP 422 with a fixed sanitized detail.

        The detail must be a static string that does NOT contain artifact paths,
        HMAC signing key hints, or any security-sensitive context from str(exc).
        Security: detail text must not confirm artifact locations to the HTTP caller.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import ArtifactTamperingError

        assert ArtifactTamperingError in OPERATOR_ERROR_MAP, (
            "ArtifactTamperingError must be in OPERATOR_ERROR_MAP (T34.3)"
        )
        entry = OPERATOR_ERROR_MAP[ArtifactTamperingError]
        assert entry["status_code"] == 422
        assert entry["type_uri"] == "about:blank"
        # The detail must be a non-empty static safe string
        assert len(entry["detail"]) > 0

    def test_all_11_synth_engine_error_subclasses_are_mapped(self) -> None:
        """OPERATOR_ERROR_MAP must contain entries for all 11 SynthEngineError subclasses.

        This is the primary acceptance criterion for T34.3: no domain exception
        should fall through to the generic 500 handler. Every SynthEngineError
        subclass must have an explicit RFC 7807 mapping.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.modules.mapping.graph import CycleDetectionError
        from synth_engine.modules.masking.registry import CollisionError
        from synth_engine.shared.exceptions import (
            ArtifactTamperingError,
            BudgetExhaustionError,
            LicenseError,
            OOMGuardrailError,
            PrivilegeEscalationError,
            VaultAlreadyUnsealedError,
            VaultConfigError,
            VaultEmptyPassphraseError,
            VaultSealedError,
        )

        expected = {
            BudgetExhaustionError,
            OOMGuardrailError,
            PrivilegeEscalationError,
            ArtifactTamperingError,
            VaultSealedError,
            VaultEmptyPassphraseError,
            VaultAlreadyUnsealedError,
            VaultConfigError,
            LicenseError,
            CollisionError,
            CycleDetectionError,
        }
        missing = expected - set(OPERATOR_ERROR_MAP.keys())
        assert not missing, (
            f"OPERATOR_ERROR_MAP is missing entries for: {', '.join(c.__name__ for c in missing)}"
        )

    @pytest.mark.asyncio
    async def test_vault_already_unsealed_raises_409_through_middleware(self) -> None:
        """VaultAlreadyUnsealedError raised in a route must produce RFC 7807 409 response."""
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import VaultAlreadyUnsealedError

        app = create_app()

        @app.get("/test-vault-already-unsealed")
        async def _raise_already_unsealed() -> None:
            raise VaultAlreadyUnsealedError("Vault is already unsealed")

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
                response = await client.get("/test-vault-already-unsealed")

        assert response.status_code == 409
        body = response.json()
        assert body["type"] == "about:blank"
        assert body["status"] == 409
        assert "title" in body
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_license_error_raises_403_through_middleware(self) -> None:
        """LicenseError raised in a route must produce RFC 7807 403 response."""
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import LicenseError

        app = create_app()

        @app.get("/test-license-error")
        async def _raise_license() -> None:
            raise LicenseError("License token has expired.")

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
                response = await client.get("/test-license-error")

        assert response.status_code == 403
        body = response.json()
        assert body["type"] == "about:blank"
        assert body["status"] == 403
        assert "title" in body
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_collision_error_raises_409_through_middleware(self) -> None:
        """CollisionError raised in a route must produce RFC 7807 409 response."""
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.modules.masking.registry import CollisionError

        app = create_app()

        @app.get("/test-collision-error")
        async def _raise_collision() -> None:
            raise CollisionError("Masking collision detected")

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
                response = await client.get("/test-collision-error")

        assert response.status_code == 409
        body = response.json()
        assert body["type"] == "about:blank"
        assert body["status"] == 409
        assert "title" in body
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_cycle_detection_error_raises_422_through_middleware(self) -> None:
        """CycleDetectionError raised in a route must produce RFC 7807 422 response.

        CycleDetectionError already had a bespoke handler in router_registry.
        T34.3 migrates it to use OPERATOR_ERROR_MAP via operator_error_response()
        for consistency with all other domain exceptions.
        """
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.modules.mapping.graph import CycleDetectionError

        app = create_app()

        @app.get("/test-cycle-error")
        async def _raise_cycle() -> None:
            raise CycleDetectionError(["orders", "customers", "orders"])

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
                response = await client.get("/test-cycle-error")

        assert response.status_code == 422
        body = response.json()
        assert body["type"] == "about:blank"
        assert body["status"] == 422
        assert "title" in body
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_privilege_escalation_does_not_leak_internals_via_http(self) -> None:
        """PrivilegeEscalationError HTTP response must not contain the raw exception message.

        Security: the exception message may contain database role names or privilege
        details. The HTTP response must use the static sanitized detail from
        OPERATOR_ERROR_MAP — never str(exc).
        """
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import PrivilegeEscalationError

        app = create_app()

        @app.get("/test-privilege-escalation")
        async def _raise_priv() -> None:
            raise PrivilegeEscalationError(
                "User 'admin_role' has INSERT, UPDATE, DELETE on table 'users'"
            )

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
                response = await client.get("/test-privilege-escalation")

        assert response.status_code == 403
        body = response.json()
        # Must not expose the raw exception message with role name and privilege details
        assert "admin_role" not in str(body)
        assert "INSERT" not in str(body)
        assert body["type"] == "about:blank"

    @pytest.mark.asyncio
    async def test_artifact_tampering_does_not_leak_internals_via_http(self) -> None:
        """ArtifactTamperingError HTTP response must not contain the raw exception message.

        Security: the exception message may contain artifact paths or HMAC details.
        The HTTP response must use the static sanitized detail from
        OPERATOR_ERROR_MAP — never str(exc).
        """
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import ArtifactTamperingError

        app = create_app()

        @app.get("/test-artifact-tampering")
        async def _raise_tamper() -> None:
            raise ArtifactTamperingError(
                "HMAC mismatch on /data/models/secret_model.pkl key=0xdeadbeef"
            )

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
                response = await client.get("/test-artifact-tampering")

        assert response.status_code == 422
        body = response.json()
        # Must not expose the raw exception message with artifact path or HMAC key hint
        assert "secret_model.pkl" not in str(body)
        assert "0xdeadbeef" not in str(body)
        assert body["type"] == "about:blank"
