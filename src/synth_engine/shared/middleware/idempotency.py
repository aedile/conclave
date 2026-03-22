"""Redis-backed idempotency middleware for the Conclave Engine.

Intercepts all mutating HTTP requests (POST, PUT, PATCH, DELETE) that carry
an ``Idempotency-Key`` header and deduplicates them using an atomic Redis
``SET NX EX`` operation.  Duplicate requests — defined as a second request
carrying a key already present in Redis — are rejected with HTTP 409.

Architectural decisions (from T45.1 spec-challenger + PM resolution)
---------------------------------------------------------------------
1.  Missing header on a mutating request: PASS-THROUGH — the header is
    optional.  Requests without the header are not subject to deduplication.
2.  Methods intercepted: POST, PUT, PATCH, DELETE.  GET, HEAD, and OPTIONS
    always pass through.
3.  Per-operator key scoping: Redis key format is
    ``idempotency:{operator_id}:{user_key}``.  The operator ID is extracted
    from the JWT ``sub`` claim (without signature verification — the auth
    middleware performs the authoritative check).  When no JWT is present,
    the operator ID defaults to ``"anonymous"``.
4.  Middleware ordering: INNERMOST — registered first in ``setup_middleware()``
    (LIFO), so it fires after all other middleware and closest to the route
    handler.  This guarantees the auth middleware has already run when
    idempotency fires.
5.  Redis DB isolation: uses the injected Redis client, which is constructed
    from ``settings.redis_url`` with the ``idempotency:`` key prefix.
6.  409 response body: ``{"detail": "Duplicate request",
    "idempotency_key": "<key>"}`` with ``Content-Type: application/json``.
7.  Response caching: NOT implemented.  Only key existence is tracked.
    Duplicates receive a 409, not a cached original response.
8.  Graceful Redis degradation: any ``redis.RedisError`` on the ``SET``
    call (including ``ConnectionError``, ``AuthenticationError``,
    ``TimeoutError``) is caught, a WARNING is logged, and the request is
    allowed to pass through.
9.  Key release on exception: if the route handler raises, the middleware
    performs a best-effort ``DELETE`` of the Redis key before re-raising,
    making the request retryable.

Import boundary
---------------
This module must NOT import from ``bootstrapper/`` or any ``modules/``
package.  Redis client and exempt paths are injected via constructor
parameters when the middleware is registered in ``bootstrapper/middleware.py``.

Known failure patterns — guard against these
--------------------------------------------
- Redis ``requirepass``: ``AuthenticationError`` is caught inside the broad
  ``redis.RedisError`` handler — no special case needed.
- Do NOT use async Redis: ``BaseHTTPMiddleware`` runs in a thread pool;
  use the sync ``redis-py`` client only.
- Importing ``EXEMPT_PATHS`` from ``bootstrapper/`` would violate the
  import boundary — exempt paths are injected at registration time.

CONSTITUTION Priority 0: Security — prevents duplicate job creation
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T45.1 — Reintroduce Idempotency Middleware (TBD-07)
"""

from __future__ import annotations

import logging
from typing import Any

import jwt as pyjwt
import redis as redis_lib
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_logger = logging.getLogger(__name__)

#: Maximum allowed length for an Idempotency-Key header value.
_MAX_KEY_LENGTH: int = 128

#: HTTP methods subject to idempotency checks.
_MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _extract_operator_id(request: Request) -> str:
    """Extract the operator identity from the JWT Bearer token.

    Decodes the ``sub`` claim without signature verification.  Signature
    integrity is the responsibility of ``AuthenticationGateMiddleware``
    (the outer auth gate that fires before this middleware).  Idempotency
    only needs a stable identity key for per-operator Redis key scoping.

    Returns ``"anonymous"`` when:
    - No ``Authorization`` header is present.
    - The header is not in ``Bearer <token>`` format.
    - The token cannot be decoded (malformed, missing ``sub`` claim).

    Args:
        request: Incoming HTTP request.

    Returns:
        The ``sub`` claim string from the JWT, or ``"anonymous"`` as the
        fallback when no valid Bearer token is present.
    """
    auth_header: str | None = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return "anonymous"
    token = auth_header[len("Bearer ") :]
    try:
        payload: dict[str, Any] = pyjwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["HS256", "HS384", "HS512"],
        )
        sub = payload.get("sub")
        return str(sub) if sub is not None else "anonymous"
    except pyjwt.InvalidTokenError:
        return "anonymous"


def _build_400_response(detail: str) -> JSONResponse:
    """Build an HTTP 400 Bad Request JSON response.

    Args:
        detail: Human-readable explanation of the validation failure.

    Returns:
        JSONResponse with HTTP 400 and a ``{"detail": ...}`` body.
    """
    return JSONResponse(status_code=400, content={"detail": detail})


def _build_409_response(user_key: str) -> JSONResponse:
    """Build an HTTP 409 Conflict JSON response for duplicate requests.

    Args:
        user_key: The user-supplied idempotency key that was duplicated.

    Returns:
        JSONResponse with HTTP 409 and the canonical duplicate-request body.
    """
    return JSONResponse(
        status_code=409,
        content={"detail": "Duplicate request", "idempotency_key": user_key},
    )


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Starlette middleware providing Redis-backed request deduplication.

    Intercepts mutating HTTP requests (POST, PUT, PATCH, DELETE) that carry
    an ``Idempotency-Key`` header and performs an atomic Redis ``SET NX EX``
    to detect duplicates.  Duplicate requests receive a **409 Conflict**
    JSON response.  All other requests pass through unchanged.

    The middleware is designed to be registered INNERMOST in the stack (after
    authentication) so that the operator identity is available for key scoping.
    Redis failures degrade gracefully — the request passes through with a
    WARNING log rather than failing hard.

    Args:
        app: The next ASGI application in the middleware stack.
        redis_client: Sync ``redis.Redis`` client instance (injected from
            ``bootstrapper/dependencies/redis.py`` at registration time).
        exempt_paths: Frozenset of URL paths that bypass idempotency checks
            entirely (e.g. ``/health``, ``/unseal``).  Injected from
            ``COMMON_INFRA_EXEMPT_PATHS`` at registration time.
        ttl_seconds: Time-to-live in seconds for idempotency keys in Redis.
            Injected from ``ConclaveSettings.idempotency_ttl_seconds``.

    Attributes:
        _redis: Sync :class: client for SET NX EX and DELETE operations.
        _exempt_paths: Paths that bypass idempotency checks.
        _ttl_seconds: Key TTL passed as the ``ex`` argument to Redis SET.
    """

    def __init__(
        self,
        app: Any,
        *,
        redis_client: redis_lib.Redis,
        exempt_paths: frozenset[str],
        ttl_seconds: int,
    ) -> None:
        super().__init__(app)
        self._redis: redis_lib.Redis = redis_client
        self._exempt_paths: frozenset[str] = exempt_paths
        self._ttl_seconds: int = ttl_seconds

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Gate mutating requests through Redis-backed idempotency check.

        Flow:
        1. Skip safe methods (GET, HEAD, OPTIONS).
        2. Skip exempt paths (e.g. /health, /unseal).
        3. Skip if ``Idempotency-Key`` header is absent.
        4. Validate key: reject empty, whitespace-only, or >128-char keys (400).
        5. Extract operator ID from JWT (best-effort, no signature check).
        6. Build scoped Redis key: ``idempotency:{operator_id}:{user_key}``.
        7. Attempt atomic ``SET NX EX``:
           - Returns ``True`` → new key; call next handler; key stays until TTL.
           - Returns ``None`` → duplicate; return 409.
           - Raises ``redis.RedisError`` → degrade gracefully; pass through.
        8. On handler exception: DELETE key (best-effort), then re-raise.

        Args:
            request: Incoming HTTP request.
            call_next: ASGI callable for the next middleware or route handler.

        Returns:
            A 400 JSONResponse if the key is invalid,
            a 409 JSONResponse if the key already exists in Redis,
            or the normal downstream response otherwise.

        Raises:
            Exception: Re-raises any exception from the downstream handler
                after a best-effort delete of the idempotency key in Redis.
        """
        # Step 1: Skip safe (non-mutating) methods.
        if request.method not in _MUTATING_METHODS:
            return await call_next(request)

        # Step 2: Skip exempt paths (infrastructure + pre-auth endpoints).
        if request.url.path in self._exempt_paths:
            return await call_next(request)

        # Step 3: Header is optional — pass through if absent.
        user_key: str | None = request.headers.get("Idempotency-Key")
        if user_key is None:
            return await call_next(request)

        # Step 4: Validate key format.
        if not user_key or not user_key.strip():
            return _build_400_response("Idempotency-Key must not be empty or whitespace-only.")
        if len(user_key) > _MAX_KEY_LENGTH:
            return _build_400_response(
                f"Idempotency-Key must not exceed {_MAX_KEY_LENGTH} characters "
                f"(got {len(user_key)})."
            )

        # Step 5: Extract operator ID for per-operator key scoping.
        operator_id = _extract_operator_id(request)

        # Step 6: Build scoped Redis key.
        redis_key = f"idempotency:{operator_id}:{user_key}"

        # Step 7: Attempt atomic SET NX EX.
        try:
            result: bool | None = self._redis.set(redis_key, "1", nx=True, ex=self._ttl_seconds)  # type: ignore[assignment]
        except redis_lib.RedisError as exc:
            _logger.warning(
                "idempotency: Redis unavailable — degrading to pass-through. Error: %s",
                type(exc).__name__,
            )
            return await call_next(request)

        if result is None:
            # Key already existed in Redis → duplicate request.
            return _build_409_response(user_key)

        # Step 8: Key acquired — call the handler.  On exception, release key.
        try:
            return await call_next(request)
        except Exception:
            # Best-effort DELETE so the client can retry with the same key.
            try:
                self._redis.delete(redis_key)
            except redis_lib.RedisError as del_exc:
                _logger.warning(
                    "idempotency: failed to delete key %r after handler exception. Error: %s",
                    redis_key,
                    type(del_exc).__name__,
                )
            raise
