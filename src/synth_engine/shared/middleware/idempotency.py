"""Redis-backed idempotency middleware for the Conclave Engine.

Prevents duplicate mutating requests from being processed more than once
within a configurable time window. Uses the X-Idempotency-Key header as
the deduplication token.
"""

import redis as redis_module
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

_IDEMPOTENCY_HEADER = "x-idempotency-key"
_MUTATING_METHODS = frozenset({"POST", "PATCH", "PUT"})
_KEY_PREFIX = "idempotency:"


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Reject duplicate mutating requests using a Redis-backed key store.

    For each POST, PATCH, or PUT request that carries an X-Idempotency-Key
    header, the middleware checks whether that key has been seen within the
    configured TTL.  If the key is already present, a 409 Conflict response
    is returned immediately.  Otherwise, the key is stored and the request
    is forwarded to the next handler.

    GET and other safe methods are never inspected.  Mutating requests
    without the header are passed through unchanged.
    """

    def __init__(
        self,
        app: ASGIApp,
        redis_client: redis_module.Redis,
        ttl_seconds: int = 300,
    ) -> None:
        """Initialise the middleware with a Redis client and TTL.

        Args:
            app: The ASGI application to wrap.
            redis_client: A configured Redis client (real or mock).
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
            A 409 JSONResponse if the key was seen before, otherwise the
            response from the downstream handler.
        """
        if request.method not in _MUTATING_METHODS:
            return await call_next(request)

        key_value = request.headers.get(_IDEMPOTENCY_HEADER)
        if not key_value:
            return await call_next(request)

        redis_key = f"{_KEY_PREFIX}{key_value}"

        if self._redis.exists(redis_key):
            return JSONResponse(
                content={"detail": "Duplicate request", "idempotency_key": key_value},
                status_code=409,
            )

        self._redis.setex(redis_key, self._ttl, "1")
        return await call_next(request)
