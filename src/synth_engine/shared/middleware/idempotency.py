"""Redis-backed idempotency middleware for the Conclave Engine.

Prevents duplicate mutating requests from being processed more than once
within a configurable time window. Uses the X-Idempotency-Key header as
the deduplication token.

Security design notes:
- Uses a single atomic ``SET NX EX`` call to eliminate the TOCTOU race
  condition present in a check-then-set pattern.
- Keys exceeding 128 characters are rejected with HTTP 400 to prevent
  Redis memory bloat from oversized keys.
- When Redis is unavailable, the middleware degrades gracefully: it logs
  a warning and passes the request through rather than blocking service.
- The idempotency key is only stored after a successful downstream response.
  If the handler raises, the key is not committed so the caller can retry.
"""

import logging
from typing import Any

import redis.exceptions
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

_IDEMPOTENCY_HEADER = "x-idempotency-key"
_MUTATING_METHODS = frozenset({"POST", "PATCH", "PUT"})
_KEY_PREFIX = "idempotency:"
_MAX_KEY_LENGTH = 128

logger = logging.getLogger(__name__)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Reject duplicate mutating requests using a Redis-backed key store.

    For each POST, PATCH, or PUT request that carries an X-Idempotency-Key
    header, the middleware atomically checks-and-sets the key in Redis using
    SET NX EX.  If the key already exists (SET returns None), a 409 Conflict
    response is returned immediately.  Otherwise, the key is stored and the
    request is forwarded to the next handler.

    The key is stored only after the downstream handler returns successfully.
    If the handler raises, the key is not committed so the caller may retry.

    GET and other safe methods are never inspected.  Mutating requests
    without the header are passed through unchanged.

    When Redis is unavailable, the middleware logs a warning and passes
    the request through rather than blocking service.
    """

    def __init__(
        self,
        app: ASGIApp,
        redis_client: Any,
        ttl_seconds: int = 300,
    ) -> None:
        """Initialise the middleware with a Redis client and TTL.

        Args:
            app: The ASGI application to wrap.
            redis_client: A configured ``redis.asyncio.Redis`` client
                (real or mock).  The type is ``Any`` so that test mocks
                can be injected without subclassing the Redis client class.
            ttl_seconds: How long (in seconds) to remember a key before
                allowing the same key to be reused.
        """
        super().__init__(app)
        self._redis = redis_client
        self._ttl = ttl_seconds

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process a request through the idempotency gate.

        Args:
            request: The incoming HTTP request.
            call_next: Callable that forwards the request to the next handler.

        Returns:
            - HTTP 400 if the idempotency key exceeds 128 characters.
            - HTTP 409 if the key was already seen (duplicate request).
            - The downstream handler's response for new or unkeyed requests.
            - The downstream handler's response if Redis is unavailable
              (degraded-mode pass-through).
        """
        if request.method not in _MUTATING_METHODS:
            return await call_next(request)

        key_value = request.headers.get(_IDEMPOTENCY_HEADER)
        if not key_value:
            return await call_next(request)

        if len(key_value) > _MAX_KEY_LENGTH:
            return JSONResponse(
                content={"detail": f"Idempotency key too long (max {_MAX_KEY_LENGTH} characters)"},
                status_code=400,
            )

        redis_key = f"{_KEY_PREFIX}{key_value}"

        # Atomic check: SET key "1" NX EX ttl
        # Returns True if the key was freshly set (new request).
        # Returns None if the key already existed (duplicate request).
        # Raises RedisError if the connection is unavailable.
        try:
            result = await self._redis.set(redis_key, "1", nx=True, ex=self._ttl)
        except redis.exceptions.RedisError as exc:
            logger.warning(
                "Idempotency middleware: Redis unavailable (%s); passing request through",
                exc,
            )
            return await call_next(request)

        if result is None:
            # Key already existed — duplicate request
            return JSONResponse(
                content={"detail": "Duplicate request", "idempotency_key": key_value},
                status_code=409,
            )

        # Key was freshly set (SET NX succeeded).  Forward to the handler.
        # If the handler raises, delete the key (best-effort) so the caller
        # can retry with the same idempotency key.
        try:
            response = await call_next(request)
        # Broad catch intentional: trap any handler error to release idempotency key, then re-raise
        except Exception:
            try:
                await self._redis.delete(redis_key)
            except redis.exceptions.RedisError:
                logger.warning(
                    "Idempotency middleware: could not release key '%s' after handler exception",
                    redis_key,
                )
            raise

        return response
