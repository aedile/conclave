"""ASGI middleware dispatch for rate limiting.

Provides :class:`RateLimitGateMiddleware`, the outermost ASGI middleware
that gates every request through tier-appropriate rate limits, plus
:func:`_build_429_response` and :func:`_build_retry_after` helpers.

Must be registered LAST in ``setup_middleware()`` (LIFO = outermost) to
provide DoS/brute-force protection before any downstream processing.

Circular-import note
--------------------
:mod:`.rate_limit` re-exports :class:`RateLimitGateMiddleware` for backward
compat.  To break the cycle, this module imports identity/config helpers
from :mod:`.rate_limit` only inside method bodies (deferred imports),
never at module scope.

CONSTITUTION Priority 0: Security — outermost DoS/brute-force gate.
Task: T39.3 — Add Rate Limiting Middleware
Task: T48.1 — Redis-Backed Rate Limiting
Task: T63.3 — Rate Limiter Fail-Closed on Redis Failure
Task: T64.3 — Decompose rate_limit.py
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time

import redis as redis_lib
from fastapi.responses import JSONResponse
from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from synth_engine.bootstrapper.dependencies.rate_limit_backend import (
    RATE_LIMIT_REDIS_FALLBACK_TOTAL,
    _memory_hit,
    _redis_hit,
)
from synth_engine.bootstrapper.dependencies.redis import get_redis_client

_logger = logging.getLogger(__name__)

#: Seconds after first Redis failure before fail-closed kicks in (T63.3).
#: Non-resettable on re-failure — clock resets only on genuine Redis recovery.
_REDIS_GRACE_PERIOD_SECONDS: int = 5

#: Retry-After seconds in the fail-closed 429 response after grace period expires.
_DEFAULT_RETRY_AFTER_SECONDS: int = _REDIS_GRACE_PERIOD_SECONDS

__all__ = [
    "RateLimitGateMiddleware",
    "_build_429_response",
    "_build_retry_after",
]


def _build_429_response(retry_after_seconds: int) -> JSONResponse:
    """Build an RFC 7807 Problem Details 429 Too Many Requests response.

    Args:
        retry_after_seconds: Seconds until the rate limit window resets.

    Returns:
        JSONResponse with HTTP 429, RFC 7807 body, and Retry-After header.
    """
    return JSONResponse(
        status_code=429,
        content={
            "type": "about:blank",
            "status": 429,
            "title": "Too Many Requests",
            "detail": (f"Rate limit exceeded. Retry after {retry_after_seconds} second(s)."),
        },
        headers={"Retry-After": str(retry_after_seconds)},
    )


def _build_retry_after(
    fallback_limiter: FixedWindowRateLimiter,
    limit: RateLimitItem,
    key: str,
) -> int:
    """Compute seconds until the rate limit window resets.

    Args:
        fallback_limiter: In-memory fixed-window rate limiter for window stats.
        limit: The rate limit item whose window to inspect.
        key: The rate limit bucket key.

    Returns:
        Non-negative integer seconds until reset; 60 if stats unavailable.
    """
    try:
        stats = fallback_limiter.get_window_stats(limit, key)
        # Cast to float: limits library does not ship py.typed.
        reset_time: float = float(stats.reset_time)
        seconds = math.ceil(reset_time - time.time())
        return max(0, seconds)
    except Exception as e:
        # Defensive fallback. Hash key before logging (CONSTITUTION Priority 0).
        hashed_key = hashlib.sha256(key.encode()).hexdigest()[:12]
        _logger.warning("rate_limit: window stats unavailable for key=%s: %s", hashed_key, e)
        return 60


class RateLimitGateMiddleware(BaseHTTPMiddleware):
    """ASGI middleware enforcing per-IP and per-operator rate limits.

    Uses Redis ``INCR``+``EXPIRE`` pipeline for distributed counting.
    Dispatches the sync call via ``asyncio.to_thread()`` to avoid blocking
    the event loop.

    Fail-closed behavior (T63.3): when Redis is down and ``fail_open=False``:
    - Grace period (5 s): in-memory fallback with limits enforced.
    - Post-grace-period: all requests rejected 429 until Redis recovers.
    Grace period clock is non-renewable (resets only on genuine recovery).

    Fail-open mode (``CONCLAVE_RATE_LIMIT_FAIL_OPEN=true``): pre-P63
    behavior — in-memory fallback always permitted. Security WARNING emitted.

    Args:
        app: The next ASGI application in the stack.
        redis_client: Injected sync Redis client (``None`` = use shared pool).
        unseal_limit: req/min on ``/unseal`` per IP (default from settings).
        auth_limit: req/min on ``/auth/token`` per IP (default from settings).
        general_limit: req/min on all other endpoints (default from settings).
        download_limit: req/min on download endpoints (default from settings).
    """

    def __init__(
        self,
        app: object,
        *,
        redis_client: redis_lib.Redis | None = None,
        unseal_limit: int | None = None,
        auth_limit: int | None = None,
        general_limit: int | None = None,
        download_limit: int | None = None,
    ) -> None:
        from synth_engine.shared.settings import get_settings

        super().__init__(app)  # type: ignore[arg-type]
        settings = get_settings()
        _unseal = (
            unseal_limit if unseal_limit is not None else settings.rate_limit_unseal_per_minute
        )
        _auth = auth_limit if auth_limit is not None else settings.rate_limit_auth_per_minute
        _general = (
            general_limit if general_limit is not None else settings.rate_limit_general_per_minute
        )
        _download = (
            download_limit
            if download_limit is not None
            else settings.rate_limit_download_per_minute
        )
        # T63.3: fail-open restores pre-P63 in-memory fallback (no grace/fail-closed).
        self._fail_open: bool = settings.conclave_rate_limit_fail_open
        # T63.3: monotonic timestamp of first Redis failure; None = healthy.
        # Reset to None only on genuine Redis recovery (non-renewable grace period).
        self._redis_first_failure_time: float | None = None
        if redis_client is not None:
            self._redis: redis_lib.Redis = redis_client
        else:
            self._redis = get_redis_client()
        self._fallback_storage = MemoryStorage()
        self._fallback_limiter: FixedWindowRateLimiter = FixedWindowRateLimiter(
            self._fallback_storage
        )
        self._unseal_limit: RateLimitItem = parse(f"{_unseal}/minute")
        self._auth_limit: RateLimitItem = parse(f"{_auth}/minute")
        self._general_limit: RateLimitItem = parse(f"{_general}/minute")
        self._download_limit: RateLimitItem = parse(f"{_download}/minute")

    def _resolve_limit_and_key(self, request: Request) -> tuple[RateLimitItem, str]:
        """Determine the rate limit tier and identity key for a request.

        Deferred imports from :mod:`.rate_limit` prevent circular imports.

        Args:
            request: Incoming HTTP request.

        Returns:
            Tuple of (rate_limit_item, identity_key).
        """
        # Deferred: rate_limit imports this module at module scope (circular).
        from synth_engine.bootstrapper.dependencies.rate_limit import (
            _DOWNLOAD_PATH_SUFFIX,
            _extract_client_ip,
            _extract_operator_id,
        )

        path = request.url.path
        if path == "/unseal":
            return self._unseal_limit, f"ip:{_extract_client_ip(request)}"
        if path == "/auth/token":
            return self._auth_limit, f"ip:{_extract_client_ip(request)}"
        operator_id = _extract_operator_id(request)
        key = f"op:{operator_id}" if operator_id else f"ip:{_extract_client_ip(request)}"
        if path.endswith(_DOWNLOAD_PATH_SUFFIX):
            return self._download_limit, key
        return self._general_limit, key

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Gate every request through the rate limit tier.

        Attempts Redis-backed counting first; falls back to in-memory on
        ``redis.RedisError`` with grace-period / fail-closed logic (T63.3).

        Args:
            request: Incoming HTTP request.
            call_next: ASGI callable for downstream middleware or handler.

        Returns:
            429 JSONResponse (RFC 7807) when rate limited or Redis is down
            past the grace period; otherwise the downstream response with
            ``X-RateLimit-Remaining`` header.
        """
        # Deferred: rate_limit imports this module at module scope (circular).
        from synth_engine.bootstrapper.dependencies.rate_limit import _resolve_tier

        limit, key = self._resolve_limit_and_key(request)
        limit_str = f"{limit.amount}/{limit.multiples or 1}minute"
        tier = _resolve_tier(request)
        count: int
        allowed: bool
        try:
            count, allowed = await asyncio.to_thread(_redis_hit, self._redis, limit_str, key)
            self._redis_first_failure_time = None  # genuine recovery
        except redis_lib.RedisError as e:
            now = time.monotonic()
            if self._redis_first_failure_time is None:
                self._redis_first_failure_time = now
            RATE_LIMIT_REDIS_FALLBACK_TOTAL.labels(tier=tier).inc()
            hashed_key = hashlib.sha256(key.encode()).hexdigest()[:12]
            _logger.warning(
                "rate_limit: redis fallback for key=%s path=%s: %s",
                hashed_key,
                request.url.path,
                e,
            )
            if self._fail_open:
                count, allowed = _memory_hit(self._fallback_limiter, limit, key)
            else:
                elapsed = now - self._redis_first_failure_time
                if elapsed <= _REDIS_GRACE_PERIOD_SECONDS:
                    count, allowed = _memory_hit(self._fallback_limiter, limit, key)
                else:
                    _logger.warning(
                        "rate_limit: redis grace period expired (%.1fs); "
                        "rejecting request fail-closed for key=%s path=%s",
                        elapsed,
                        hashed_key,
                        request.url.path,
                    )
                    error_response: Response = _build_429_response(_DEFAULT_RETRY_AFTER_SECONDS)
                    error_response.headers["X-RateLimit-Remaining"] = "0"
                    return error_response

        if not allowed:
            retry_after = _build_retry_after(self._fallback_limiter, limit, key)
            hashed_key = hashlib.sha256(key.encode()).hexdigest()[:12]
            _logger.warning(
                "rate_limit: exceeded for key=%s path=%s retry_after=%ds",
                hashed_key,
                request.url.path,
                retry_after,
            )
            error_response = _build_429_response(retry_after)
            error_response.headers["X-RateLimit-Remaining"] = "0"
            return error_response

        downstream: Response = await call_next(request)
        remaining = max(0, limit.amount - count)
        downstream.headers["X-RateLimit-Remaining"] = str(remaining)
        return downstream
