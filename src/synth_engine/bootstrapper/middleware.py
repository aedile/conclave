"""Middleware stack setup for the Conclave Engine FastAPI application.

Encapsulates the eight middleware additions in registration order.
Middleware is evaluated in LIFO (Last In, First Out) order, so the last
``add_middleware`` call corresponds to the outermost layer on the request
path.

Middleware evaluation order (LIFO — last added = outermost):

1. ``HTTPSEnforcementMiddleware`` — outermost; rejects plain HTTP in
   production with 421 Misdirected Request (RFC 7231 §6.5.11).  Must fire
   BEFORE rate limiting so that insecure requests are dropped without
   consuming rate-limit budget.
2. ``RateLimitGateMiddleware`` — rate-limits per-IP and per-operator
   BEFORE any other processing.  Protects against DoS and brute-force attacks.
   Returns 429 (RFC 7807) with a ``Retry-After`` header when the limit is exceeded.
3. ``RequestBodyLimitMiddleware`` — size + depth gate before any business logic
   runs.  Rejects > 1 MiB (413) or depth > 100 (400).
4. ``CSPMiddleware`` — adds Content-Security-Policy header to all responses.
5. ``SealGateMiddleware`` — returns 423 if vault is sealed.
6. ``LicenseGateMiddleware`` — returns 402 if not licensed.
7. ``AuthenticationGateMiddleware`` — returns 401 if the JWT Bearer token
   is absent or invalid.  Exempt paths bypass this gate.
8. ``IdempotencyMiddleware`` — innermost gate; deduplicates mutating requests
   (POST, PUT, PATCH, DELETE) using Redis-backed ``SET NX EX``.  Fires AFTER
   authentication so that the operator identity is available for key scoping.
   Returns 409 on duplicate keys; degrades gracefully when Redis is down.

Task: T39.1 — Add Authentication Middleware (JWT Bearer Token)
Task: T39.3 — Add Rate Limiting Middleware
Task: T42.2 — Add HTTPS Enforcement & Deployment Safety Checks
Task: T45.1 — Reintroduce Idempotency Middleware (TBD-07)
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS
from synth_engine.bootstrapper.dependencies.auth_middleware import AuthenticationGateMiddleware
from synth_engine.bootstrapper.dependencies.csp import CSPMiddleware
from synth_engine.bootstrapper.dependencies.https_enforcement import HTTPSEnforcementMiddleware
from synth_engine.bootstrapper.dependencies.licensing import LicenseGateMiddleware
from synth_engine.bootstrapper.dependencies.rate_limit import RateLimitGateMiddleware
from synth_engine.bootstrapper.dependencies.redis import get_redis_client
from synth_engine.bootstrapper.dependencies.request_limits import RequestBodyLimitMiddleware
from synth_engine.bootstrapper.dependencies.vault import SealGateMiddleware
from synth_engine.shared.middleware.idempotency import IdempotencyMiddleware
from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)


def setup_middleware(app: FastAPI) -> None:
    """Attach all middleware to the application in the correct LIFO order.

    Middleware is added innermost-first, outermost-last so that the request
    path evaluates outermost → innermost and the response path evaluates
    innermost → outermost.

    Request path (outermost → innermost):
        HTTPSEnforcementMiddleware → RateLimitGateMiddleware
        → RequestBodyLimitMiddleware → CSPMiddleware
        → SealGateMiddleware → LicenseGateMiddleware
        → AuthenticationGateMiddleware → IdempotencyMiddleware → route handler

    Response path (innermost → outermost):
        route handler → IdempotencyMiddleware → AuthenticationGateMiddleware
        → LicenseGateMiddleware → SealGateMiddleware → CSPMiddleware
        → RequestBodyLimitMiddleware → RateLimitGateMiddleware
        → HTTPSEnforcementMiddleware

    Args:
        app: The FastAPI instance to attach middleware to.
    """
    settings = get_settings()

    # Add INNERMOST first, OUTERMOST last (LIFO evaluation order).
    # IdempotencyMiddleware is innermost — it fires closest to the route handler,
    # after authentication has established the operator identity.
    app.add_middleware(
        IdempotencyMiddleware,
        redis_client=get_redis_client(),
        exempt_paths=AUTH_EXEMPT_PATHS,
        ttl_seconds=settings.idempotency_ttl_seconds,
    )
    app.add_middleware(AuthenticationGateMiddleware)
    app.add_middleware(LicenseGateMiddleware)
    app.add_middleware(SealGateMiddleware)
    app.add_middleware(CSPMiddleware)
    app.add_middleware(RequestBodyLimitMiddleware)
    # RateLimitGateMiddleware fires before size/CSP/vault/license/auth/idempotency gates.
    app.add_middleware(RateLimitGateMiddleware)
    # HTTPSEnforcementMiddleware is added LAST so it is the OUTERMOST middleware.
    # It fires FIRST on the request path — before rate limiting, size checks,
    # CSP, vault, license, authentication, or idempotency.  Plain HTTP requests
    # in production are rejected here, before any other processing consumes resources.
    app.add_middleware(HTTPSEnforcementMiddleware)
