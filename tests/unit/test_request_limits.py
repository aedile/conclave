"""Unit tests for the RequestBodyLimitMiddleware and _measure_json_depth.

Tests cover:
- _measure_json_depth: flat JSON, nested dicts, nested arrays, escaped
  brackets in strings, mixed structures.
- RequestBodyLimitMiddleware: Content-Length fast-path rejection, streaming
  size rejection, JSON depth rejection, non-integer Content-Length warning,
  http.disconnect handling, body replay for valid requests, non-HTTP scopes.

CONSTITUTION Priority 3: TDD
CONSTITUTION Priority 4: 90%+ coverage
Task: P6-T6.2 — NIST SP 800-88 Erasure, OWASP validation, LLM Fuzz Testing
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synth_engine.bootstrapper.dependencies.request_limits import (
    MAX_BODY_BYTES,
    MAX_JSON_DEPTH,
    RequestBodyLimitMiddleware,
    _measure_json_depth,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# _measure_json_depth tests
# ---------------------------------------------------------------------------


class TestMeasureJsonDepth:
    """Unit tests for the _measure_json_depth function."""

    @pytest.mark.parametrize(
        ("text", "expected_depth"),
        [
            pytest.param("", 0, id="empty_string"),
            pytest.param("42", 0, id="flat_number"),
            pytest.param('{"key": "val"}', 1, id="flat_object"),
            pytest.param('{"a": {"b": 1}}', 2, id="nested_object_2"),
            pytest.param("[1, 2, 3]", 1, id="flat_array"),
            pytest.param("[[1, 2]]", 2, id="nested_array_2"),
        ],
    )
    def test_measure_json_depth_simple_cases(self, text: str, expected_depth: int) -> None:
        """_measure_json_depth returns the correct nesting depth for simple inputs.

        Tests zero-depth (no brackets), single-level, and two-level nesting for
        both object and array types. Consolidates 6 near-identical assertions.

        Args:
            text: JSON string to measure.
            expected_depth: Expected maximum nesting depth.
        """
        result = _measure_json_depth(text)
        assert result == expected_depth, (
            f"_measure_json_depth({text!r}) returned {result}, expected {expected_depth}"
        )

    def test_nested_object_depth_10(self) -> None:
        """10 levels of nesting returns depth 10."""
        obj: dict[str, Any] = {"v": 1}
        for _ in range(9):
            obj = {"a": obj}
        text = json.dumps(obj)
        assert _measure_json_depth(text) == 10

    def test_brackets_inside_strings_ignored(self) -> None:
        """Brackets inside a string literal must NOT increase depth.

        A JSON string containing ``{`` or ``[`` must not count as nesting.
        """
        # The string value "{ [" contains brackets but they are inside quotes
        text = '{"key": "{ [ nested-looking string ]}"}'
        # Only depth is the outer object = 1
        assert _measure_json_depth(text) == 1

    def test_escaped_quote_inside_string(self) -> None:
        """An escaped quote ``\\"`` inside a string does not close the string.

        This tests the escape sequence handling in _measure_json_depth.
        """
        # The value is: {"key": "val with \\"escaped\\" quotes and {braces}"}
        # The {braces} inside the string must NOT count as nesting.
        text = '{"key": "val with \\"escaped\\" quotes and {braces}"}'
        assert _measure_json_depth(text) == 1

    def test_backslash_before_non_quote(self) -> None:
        """Backslash before a non-quote character does not open escape mode."""
        # "\n" inside a string — backslash followed by n (not a quote)
        text = '{"key": "line\\nbreak"}'
        assert _measure_json_depth(text) == 1

    def test_mixed_nesting_returns_max_depth(self) -> None:
        """Mixed arrays and objects return the maximum depth encountered."""
        # {"a": [{"b": 1}]} — depth: 1 (outer dict) → 2 (array) → 3 (inner dict)
        text = '{"a": [{"b": 1}]}'
        assert _measure_json_depth(text) == 3

    def test_depth_101_exceeds_limit(self) -> None:
        """A depth-101 object exceeds MAX_JSON_DEPTH=100."""
        obj: dict[str, Any] = {"v": 1}
        for _ in range(100):
            obj = {"a": obj}
        text = json.dumps(obj)
        depth = _measure_json_depth(text)
        assert depth == 101
        assert depth > MAX_JSON_DEPTH


# ---------------------------------------------------------------------------
# RequestBodyLimitMiddleware helper: build minimal ASGI scope
# ---------------------------------------------------------------------------


def _make_http_scope(
    path: str = "/test",
    method: str = "POST",
    content_type: str = "application/json",
    content_length: int | None = None,
) -> dict[str, Any]:
    """Build a minimal ASGI HTTP scope dict.

    Args:
        path: The request path.
        method: HTTP method string.
        content_type: Content-Type header value.
        content_length: Optional Content-Length header value.

    Returns:
        A dict suitable as an ASGI scope for HTTP requests.
    """
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", content_type.encode()),
    ]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers,
    }


def _make_receive(body: bytes) -> Any:
    """Build an async ASGI receive callable that returns the given body.

    Args:
        body: Body bytes to return.

    Returns:
        An async callable returning a single ``http.request`` message.
    """
    called = [False]

    async def _receive() -> dict[str, Any]:
        if not called[0]:
            called[0] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return _receive


def _make_send() -> tuple[Any, list[dict[str, Any]]]:
    """Build an ASGI send callable that records messages sent to it.

    Returns:
        Tuple of (send callable, messages list).
    """
    messages: list[dict[str, Any]] = []

    async def _send(message: dict[str, Any]) -> None:
        messages.append(message)

    return _send, messages


# ---------------------------------------------------------------------------
# RequestBodyLimitMiddleware tests
# ---------------------------------------------------------------------------


class TestRequestBodyLimitMiddleware:
    """Unit tests for RequestBodyLimitMiddleware."""

    @pytest.mark.asyncio
    async def test_non_http_scope_forwarded_without_inspection(self) -> None:
        """Non-HTTP scopes (e.g., lifespan) must be forwarded unchanged."""
        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        scope = {"type": "lifespan"}
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        inner.assert_awaited_once_with(scope, receive, send)
        assert inner.await_count == 1, "non-HTTP scope must forward to inner app exactly once"

    @pytest.mark.asyncio
    async def test_content_length_over_limit_returns_413(self) -> None:
        """Request with Content-Length > MAX_BODY_BYTES is rejected with 413."""
        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        scope = _make_http_scope(content_length=MAX_BODY_BYTES + 1)
        receive = AsyncMock()
        send, messages = _make_send()

        await middleware(scope, receive, send)

        # Inner app must NOT be called — rejected before body read
        inner.assert_not_awaited()

        # First message must be http.response.start with status 413
        assert messages[0]["type"] == "http.response.start"
        assert messages[0]["status"] == 413

    @pytest.mark.asyncio
    async def test_non_integer_content_length_warns_and_continues(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Non-integer Content-Length logs a warning and proceeds normally."""
        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        scope: dict[str, Any] = {
            "type": "http",
            "method": "POST",
            "path": "/test",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", b"not-a-number"),
            ],
        }
        body = b'{"key": "value"}'
        receive = _make_receive(body)
        send, messages = _make_send()

        with caplog.at_level(logging.WARNING):
            await middleware(scope, receive, send)

        # Warning must be logged
        assert any("Non-integer Content-Length" in record.message for record in caplog.records)

        # Inner app must be called (warning, not rejection)
        inner.assert_awaited_once()
        assert inner.await_count == 1, "inner app must be called exactly once"

    @pytest.mark.asyncio
    async def test_streaming_body_over_limit_returns_413(self) -> None:
        """Body exceeding limit via streaming (no Content-Length) returns 413."""
        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        scope = _make_http_scope()  # No content-length header

        # Body chunk that exceeds the limit
        oversized_chunk = b"x" * (MAX_BODY_BYTES + 1)

        async def _receive() -> dict[str, Any]:
            return {"type": "http.request", "body": oversized_chunk, "more_body": False}

        send, messages = _make_send()

        await middleware(scope, _receive, send)

        inner.assert_not_awaited()
        assert messages[0]["status"] == 413

    @pytest.mark.asyncio
    async def test_http_disconnect_message_exits_body_loop(self) -> None:
        """http.disconnect message in body loop must exit cleanly."""
        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        scope = _make_http_scope()

        async def _receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        send, messages = _make_send()

        await middleware(scope, _receive, send)

        # Inner app is called with empty body after disconnect
        inner.assert_awaited_once()
        assert inner.await_count == 1, "inner app must be called exactly once"

    @pytest.mark.asyncio
    async def test_valid_json_body_forwarded_to_inner_app(self) -> None:
        """A valid JSON body under the depth and size limits is forwarded."""
        received_bodies: list[bytes] = []

        async def inner_app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            message = await receive()
            received_bodies.append(message.get("body", b""))
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [],
                }
            )
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        middleware = RequestBodyLimitMiddleware(inner_app)

        body = b'{"table_name": "fictional_users", "total_epochs": 5}'
        scope = _make_http_scope(content_length=len(body))
        receive = _make_receive(body)
        send, messages = _make_send()

        await middleware(scope, receive, send)

        # Inner app must receive the full body
        assert received_bodies == [body]
        assert messages[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_json_depth_over_limit_returns_400(self) -> None:
        """JSON body with depth > MAX_JSON_DEPTH is rejected with 400."""
        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        obj: dict[str, Any] = {"v": 1}
        for _ in range(MAX_JSON_DEPTH):
            obj = {"a": obj}
        body = json.dumps(obj).encode()

        scope = _make_http_scope(content_length=len(body))
        receive = _make_receive(body)
        send, messages = _make_send()

        await middleware(scope, receive, send)

        inner.assert_not_awaited()
        assert messages[0]["status"] == 400

    @pytest.mark.asyncio
    async def test_json_depth_at_limit_passes(self) -> None:
        """JSON body with depth == MAX_JSON_DEPTH is NOT rejected."""
        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        # Build a depth-100 object
        obj: dict[str, Any] = {"v": 1}
        for _ in range(MAX_JSON_DEPTH - 1):
            obj = {"a": obj}
        body = json.dumps(obj).encode()

        scope = _make_http_scope(content_length=len(body))
        receive = _make_receive(body)
        send, _ = _make_send()

        await middleware(scope, receive, send)

        inner.assert_awaited_once()
        assert inner.await_count == 1, "inner app must be called exactly once"

    @pytest.mark.asyncio
    async def test_non_json_content_type_skips_depth_check(self) -> None:
        """Non-JSON content types bypass the depth check."""
        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        # A deeply nested object as form data (application/x-www-form-urlencoded)
        obj: dict[str, Any] = {"v": 1}
        for _ in range(MAX_JSON_DEPTH):
            obj = {"a": obj}
        body = json.dumps(obj).encode()

        scope = _make_http_scope(content_type="application/x-www-form-urlencoded")
        receive = _make_receive(body)
        send, _ = _make_send()

        await middleware(scope, receive, send)

        # Inner app must be called — depth check is skipped for non-JSON
        inner.assert_awaited_once()
        assert inner.await_count == 1, "inner app must be called exactly once"

    @pytest.mark.asyncio
    async def test_replay_receive_fallback_called_after_body_sent(self) -> None:
        """The _replay_receive fallback to original receive works on second call.

        This tests the branch in _replay_receive where body_sent=True and
        the original receive is called for subsequent messages (e.g. disconnect).
        """
        subsequent_messages: list[dict[str, Any]] = []

        async def inner_app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            # First receive: gets the body
            msg1 = await receive()
            assert msg1["type"] == "http.request"
            # Second receive: falls back to original receive (disconnect)
            msg2 = await receive()
            subsequent_messages.append(msg2)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        middleware = RequestBodyLimitMiddleware(inner_app)

        call_count = [0]
        body = b'{"test": 1}'

        async def _receive_with_disconnect() -> dict[str, Any]:
            call_count[0] += 1
            if call_count[0] == 1:
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        scope = _make_http_scope()
        send, _ = _make_send()

        await middleware(scope, _receive_with_disconnect, send)

        # The second receive call should have returned http.disconnect
        assert subsequent_messages == [{"type": "http.disconnect"}]

    @pytest.mark.asyncio
    async def test_empty_body_not_depth_checked(self) -> None:
        """Empty request body bypasses the JSON depth check."""
        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        body = b""
        scope = _make_http_scope(content_length=0)
        receive = _make_receive(body)
        send, messages = _make_send()

        await middleware(scope, receive, send)

        # Inner app must be called — no body to check
        inner.assert_awaited_once()
        assert inner.await_count == 1, "inner app must be called exactly once"

    @pytest.mark.asyncio
    async def test_valid_json_body_under_depth_limit_forwarded(self) -> None:
        """A valid JSON body under the depth limit is forwarded to the inner app.

        Verifies that the depth check does not incorrectly reject well-formed
        JSON.  The dead ``except (UnicodeDecodeError, ValueError)`` branch that
        previously wrapped this block has been removed (ADV-064); this test
        remains as a straightforward regression guard for the happy path.
        """
        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        body = b'{"key": "value"}'
        scope = _make_http_scope()
        receive = _make_receive(body)
        send, _ = _make_send()

        await middleware(scope, receive, send)

        inner.assert_awaited_once()
        assert inner.await_count == 1, (
            "valid JSON under depth limit must be forwarded to inner app once"
        )

    @pytest.mark.asyncio
    async def test_depth_check_regression_after_dead_branch_removal(self) -> None:
        """JSON depth check must reject over-limit requests after ADV-064 cleanup.

        ADV-064 removes the unreachable ``except (UnicodeDecodeError, ValueError)``
        branch from the JSON depth check block.  This test is an explicit regression
        guard: it confirms that removing the dead branch does NOT break the happy
        path (valid JSON forwarded) or the rejection path (over-limit JSON returns 400).

        Covers both sub-cases in one test:
        - A depth-101 body (over limit) returns HTTP 400.
        - A depth-1 body (under limit) is forwarded to the inner app.
        """
        inner = AsyncMock()
        middleware = RequestBodyLimitMiddleware(inner)

        # Sub-case A: over-limit depth → must return 400
        deep_obj: dict[str, Any] = {"v": 1}
        for _ in range(MAX_JSON_DEPTH):
            deep_obj = {"a": deep_obj}
        deep_body = json.dumps(deep_obj).encode()
        scope_a = _make_http_scope(content_length=len(deep_body))
        receive_a = _make_receive(deep_body)
        send_a, messages_a = _make_send()

        await middleware(scope_a, receive_a, send_a)

        inner.assert_not_awaited()
        assert messages_a[0]["status"] == 400, (
            "depth check regression: over-limit JSON must return 400 after dead branch removal"
        )

        # Sub-case B: under-limit depth → must forward to inner app
        inner.reset_mock()
        shallow_body = b'{"key": "value"}'
        scope_b = _make_http_scope(content_length=len(shallow_body))
        receive_b = _make_receive(shallow_body)
        send_b, _ = _make_send()

        await middleware(scope_b, receive_b, send_b)

        inner.assert_awaited_once()
