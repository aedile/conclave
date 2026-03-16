"""Request body size and JSON depth limiting middleware for the Conclave Engine.

Provides ``RequestBodyLimitMiddleware`` which protects against two
denial-of-service vectors:

1. **Oversized payloads**: Bodies exceeding ``MAX_BODY_BYTES`` (1 MiB)
   are rejected with HTTP 413 Payload Too Large before the body is fully
   read.  This prevents CPU and memory exhaustion from multi-megabyte
   JSON blobs.

2. **Deeply nested JSON**: JSON with a nesting depth exceeding
   ``MAX_JSON_DEPTH`` (100 levels) is rejected with HTTP 400 Bad Request.
   Deeply nested structures can cause recursive JSON parsers to overflow
   the call stack (CVE-2020-36327-style attacks).

Security context
----------------
Both limits are mandated by the PM review of P6-T6.2 (OWASP / fuzz testing).
The depth limit of 100 is consistent with the default limit used by
``orjson``, ``pydantic``, and ``json-schema`` validators.
The size limit of 1 MiB is a conservative cap for a synthesis-job API
whose JSON payloads should never exceed a few kilobytes.

Middleware implementation: pure ASGI (not BaseHTTPMiddleware)
--------------------------------------------------------------
This middleware is implemented as a pure ASGI callable rather than a
``BaseHTTPMiddleware`` subclass.  The reason: ``BaseHTTPMiddleware``
creates an internal channel for ASGI messages and does NOT use the
middleware-level ``Request`` object when forwarding the body to the inner
app.  If we read the request body in ``BaseHTTPMiddleware.dispatch`` via
``request.stream()`` or ``request.body()``, the body is consumed from the
raw ASGI ``receive`` channel; the inner app's subsequent ``receive()``
calls return an empty body because the stream has been drained.

A pure ASGI middleware has direct access to the ``scope``, ``receive``,
and ``send`` callables and can wrap ``receive`` with a custom implementation
that replays the buffered body bytes.  This pattern (sometimes called the
"body replay" pattern) is the correct way to inspect-and-forward an HTTP
body in an ASGI middleware stack.

Middleware placement
--------------------
``RequestBodyLimitMiddleware`` must be the **outermost** middleware so that
oversized or deeply nested bodies are rejected before any inner middleware
(CSP, SealGate, LicenseGate) or route handler processes them.

FastAPI's ``app.add_middleware()`` adds middleware in LIFO order
(last-added = outermost).  ``RequestBodyLimitMiddleware`` must therefore
be added **last** after all other ``add_middleware()`` calls.

CONSTITUTION Priority 0: Security — DoS protection.
Task: P6-T6.2 — NIST SP 800-88 Erasure, OWASP validation, LLM Fuzz Testing
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

_logger = logging.getLogger(__name__)

#: Maximum request body size in bytes (1 MiB).
#: Requests with a Content-Length header larger than this, or whose body
#: exceeds this size during streaming read, are rejected with HTTP 413.
MAX_BODY_BYTES: int = 1 * 1024 * 1024  # 1 MiB

#: Maximum allowed JSON nesting depth.
#: JSON bodies with more than this many nesting levels are rejected with
#: HTTP 400 before being passed to the route handler.
MAX_JSON_DEPTH: int = 100

# ---------------------------------------------------------------------------
# ASGI type aliases
# ---------------------------------------------------------------------------
Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# ---------------------------------------------------------------------------
# RFC 7807 error response helpers
# ---------------------------------------------------------------------------

_413_BODY: bytes = json.dumps(
    {
        "type": "about:blank",
        "title": "Payload Too Large",
        "status": 413,
        "detail": f"Request body exceeds the maximum allowed size of {MAX_BODY_BYTES} bytes.",
    }
).encode()

_400_DEPTH_BODY_TEMPLATE = (
    '{{"type":"about:blank","title":"Bad Request","status":400,'
    '"detail":"JSON nesting depth {depth} exceeds the maximum allowed depth of {limit}."}}'
)


async def _send_response(
    send: Send,
    status: int,
    body: bytes,
) -> None:
    """Send a minimal HTTP response via the ASGI ``send`` callable.

    Args:
        send: The ASGI send callable.
        status: HTTP status code.
        body: Response body bytes.
    """
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


def _measure_json_depth(text: str) -> int:
    """Measure the maximum nesting depth of a JSON string without full parsing.

    Scans the JSON string character-by-character counting ``{`` and ``[``
    (depth +1) and ``}`` and ``]`` (depth -1) while correctly handling
    strings (ignoring brackets inside double-quoted values).

    This is intentionally a shallow structural scanner — it does NOT
    validate the JSON; it only measures depth.  A structurally invalid
    JSON string may produce an incorrect depth count, but it will fail
    parsing anyway at the route handler level.

    Args:
        text: The raw JSON string to measure.

    Returns:
        The maximum nesting depth encountered.  Returns 0 for an empty
        string or a flat JSON value (number, string, bool, null).
    """
    max_depth = 0
    current_depth = 0
    in_string = False
    escaped = False

    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in "{[":
            current_depth += 1
            if current_depth > max_depth:
                max_depth = current_depth
        elif char in "}]":
            current_depth -= 1

    return max_depth


class RequestBodyLimitMiddleware:
    """Pure ASGI middleware that enforces request body size and JSON depth limits.

    Rejects requests that exceed either:
    - ``MAX_BODY_BYTES`` (1 MiB) with HTTP 413 Payload Too Large.
    - ``MAX_JSON_DEPTH`` (100) nesting levels with HTTP 400 Bad Request.

    Implementation uses the pure ASGI "body replay" pattern:
    1. Intercepts all ``http.request`` ASGI messages from ``receive``.
    2. Accumulates body chunks while checking the size limit.
    3. Checks JSON depth on the complete body (for application/json only).
    4. Wraps the original ``receive`` with a new callable that replays the
       buffered body bytes — ensuring the inner app can read the full body.

    This avoids the ``BaseHTTPMiddleware`` limitation where consuming the
    body via ``request.stream()`` drains the ASGI channel and leaves the
    inner app with an empty body.

    Placement requirement: this middleware must be outermost.  In
    ``create_app()``, add it LAST (highest stack position) via
    ``app.add_middleware(RequestBodyLimitMiddleware)``.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialise the middleware with the inner ASGI app.

        Args:
            app: The inner ASGI application to wrap.
        """
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle an ASGI request, enforcing size and depth limits.

        Only ``http`` scope requests are inspected for body limits.
        WebSocket and lifespan events are forwarded without modification.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI receive callable.
            send: The ASGI send callable.
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Fast-path: check Content-Length header before reading the body
        headers: dict[bytes, bytes] = {k.lower(): v for k, v in scope.get("headers", [])}
        content_length_raw = headers.get(b"content-length")
        if content_length_raw is not None:
            try:
                if int(content_length_raw) > MAX_BODY_BYTES:
                    _logger.warning(
                        "Request rejected: Content-Length %s exceeds limit %d bytes. Path: %s",
                        content_length_raw.decode(),
                        MAX_BODY_BYTES,
                        scope.get("path", ""),
                    )
                    await _send_response(send, 413, _413_BODY)
                    return
            except ValueError:
                _logger.warning(
                    "Non-integer Content-Length header '%s' on %s.",
                    content_length_raw.decode(errors="replace"),
                    scope.get("path", ""),
                )

        # Read the full body by consuming http.request ASGI messages
        body_chunks: list[bytes] = []
        total_bytes = 0
        more_body = True

        while more_body:
            message = await receive()
            if message["type"] == "http.request":
                chunk: bytes = message.get("body", b"")
                total_bytes += len(chunk)
                if total_bytes > MAX_BODY_BYTES:
                    _logger.warning(
                        "Request rejected: body stream exceeded %d bytes. Path: %s",
                        MAX_BODY_BYTES,
                        scope.get("path", ""),
                    )
                    await _send_response(send, 413, _413_BODY)
                    return
                body_chunks.append(chunk)
                more_body = message.get("more_body", False)
            elif message["type"] == "http.disconnect":
                # Client disconnected before sending body — forward normally.
                more_body = False

        body_bytes = b"".join(body_chunks)

        # JSON depth check — only for application/json content type
        content_type = headers.get(b"content-type", b"").decode("latin-1", errors="replace")
        if "application/json" in content_type and body_bytes:
            body_text = body_bytes.decode("utf-8", errors="replace")
            depth = _measure_json_depth(body_text)
            if depth > MAX_JSON_DEPTH:
                _logger.warning(
                    "Request rejected: JSON depth %d exceeds limit %d. Path: %s",
                    depth,
                    MAX_JSON_DEPTH,
                    scope.get("path", ""),
                )
                body_400 = _400_DEPTH_BODY_TEMPLATE.format(
                    depth=depth, limit=MAX_JSON_DEPTH
                ).encode()
                await _send_response(send, 400, body_400)
                return

        # Build a "body replay" receive callable that returns the buffered body.
        # The inner app's first call to receive() gets the full body as a single
        # http.request message with more_body=False.
        body_sent = False

        async def _replay_receive() -> Message:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            # After the body is replayed, fall back to the original receive
            # (for WebSocket upgrades or disconnect events).
            return await receive()

        await self._app(scope, _replay_receive, send)
