"""Pure ASGI middleware that converts unhandled exceptions to RFC 7807 responses.

Implementation note — Pure ASGI middleware (T19.1):
    ``RFC7807Middleware`` is implemented as a pure ASGI middleware class rather
    than extending Starlette's ``BaseHTTPMiddleware``.

    ``BaseHTTPMiddleware`` buffers the **entire response body** before returning
    to the outer stack.  This breaks SSE (Server-Sent Events) streaming because
    the SSE generator yields chunks incrementally — buffering defeats the purpose
    and causes the client to receive all events at once (or not at all) when the
    stream terminates.

    Pure ASGI middleware calls ``await self.app(scope, receive, send)`` directly
    with a wrapped ``send`` callable, which allows each SSE chunk to pass through
    to the client as soon as it is yielded.

    The wrapped ``send`` callable tracks whether response headers have already
    been sent (``headers_sent`` flag).  If an exception is raised after headers
    have been sent, we cannot send a new 500 response — the connection is already
    committed to a different status code.  In that case the exception is re-raised
    and will be handled by Starlette's ``ServerErrorMiddleware``.

Task: T19.1 — Middleware & Engine Singleton Fixes
    (Converted from BaseHTTPMiddleware to pure ASGI middleware)
Task: T36.2 — Split bootstrapper/errors.py Into Focused Modules
"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from synth_engine.bootstrapper.errors.formatter import (
    _STATUS_TITLES,
    problem_detail,
)
from synth_engine.shared.errors import safe_error_msg

_logger = logging.getLogger(__name__)


class RFC7807Middleware:
    """Pure ASGI middleware that converts unhandled exceptions to RFC 7807 responses.

    Wraps the entire request/response cycle using the raw ASGI protocol.
    Unlike ``BaseHTTPMiddleware``, this implementation does NOT buffer the
    response body, which allows SSE (Server-Sent Events) streaming to work
    correctly — each chunk is forwarded to the client immediately.

    If any exception escapes from the inner app (including route handlers),
    it is caught here and converted to an HTTP 500 response with an RFC 7807
    Problem Details JSON body, provided headers have not yet been sent.

    If the exception occurs after response headers have been sent (i.e., the
    status line and headers are already committed), the exception is re-raised
    so that Starlette's ``ServerErrorMiddleware`` can handle it.  This is the
    correct behaviour: we cannot overwrite a response that is already in flight.

    Error details are sanitized via :func:`~synth_engine.shared.errors.safe_error_msg`
    before exposure (ADV-036+044).

    This middleware must be added LAST (outermost) so it catches exceptions
    from all inner middleware layers and route handlers.

    Non-HTTP scope types (e.g., ``lifespan``, ``websocket``) are passed through
    to the inner app without modification.

    Args:
        app: The inner ASGI application to wrap.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle an ASGI request, catching unhandled exceptions.

        For ``http`` scopes: wraps the inner app call in a try/except.
        On exception: if headers have not yet been sent, returns an RFC 7807
        500 JSON response.  If headers were already sent, re-raises.

        For non-``http`` scopes: passes directly to the inner app unchanged.

        Args:
            scope: The ASGI connection scope (type, path, headers, etc.).
            receive: The ASGI receive callable.
            send: The ASGI send callable.

        Raises:
            Exception: Any unhandled exception from the inner app is re-raised
                when response headers have already been sent.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers_sent = False

        async def _send_wrapper(message: Any) -> None:
            nonlocal headers_sent
            if message.get("type") == "http.response.start":
                headers_sent = True
            await send(message)

        try:
            await self.app(scope, receive, _send_wrapper)
        # Broad catch intentional: ASGI middleware must convert all unhandled errors to RFC 7807
        except Exception as exc:
            if headers_sent:
                # Cannot send a new response — headers already committed.
                raise

            _logger.exception(
                "Unhandled exception on %s %s",
                scope.get("method", "UNKNOWN"),
                scope.get("path", "/"),
            )
            safe_detail = safe_error_msg(str(exc))
            body = problem_detail(
                status=500,
                title=_STATUS_TITLES.get(500, "Internal Server Error"),
                detail=safe_detail,
            )
            body_bytes = json.dumps(body).encode("utf-8")

            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        [b"content-type", b"application/json"],
                        [b"content-length", str(len(body_bytes)).encode("ascii")],
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": body_bytes,
                    "more_body": False,
                }
            )
