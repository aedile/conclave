"""Middleware stack setup for the Conclave Engine FastAPI application.

Encapsulates the four middleware additions in registration order.
Middleware is evaluated in LIFO (Last In, First Out) order, so the last
``add_middleware`` call corresponds to the outermost layer on the request
path.

Middleware evaluation order (LIFO — last added = outermost):

1. ``RequestBodyLimitMiddleware`` — outermost; size + depth gate before any
   business logic runs.  Rejects > 1 MiB (413) or depth > 100 (400).
2. ``CSPMiddleware`` — adds Content-Security-Policy header to all responses.
3. ``SealGateMiddleware`` — returns 423 if vault is sealed.
4. ``LicenseGateMiddleware`` — innermost gate; returns 402 if not licensed.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from synth_engine.bootstrapper.dependencies.csp import CSPMiddleware
from synth_engine.bootstrapper.dependencies.licensing import LicenseGateMiddleware
from synth_engine.bootstrapper.dependencies.request_limits import RequestBodyLimitMiddleware
from synth_engine.bootstrapper.dependencies.vault import SealGateMiddleware

_logger = logging.getLogger(__name__)


def setup_middleware(app: FastAPI) -> None:
    """Attach all middleware to the application in the correct LIFO order.

    Middleware is added innermost-first, outermost-last so that the request
    path evaluates outermost → innermost and the response path evaluates
    innermost → outermost.

    Request path (outermost → innermost):
        RequestBodyLimitMiddleware → CSPMiddleware → SealGateMiddleware
        → LicenseGateMiddleware → route handler

    Response path (innermost → outermost):
        route handler → LicenseGateMiddleware → SealGateMiddleware
        → CSPMiddleware → RequestBodyLimitMiddleware

    Args:
        app: The FastAPI instance to attach middleware to.
    """
    # Add INNERMOST first, OUTERMOST last (LIFO evaluation order).
    app.add_middleware(LicenseGateMiddleware)
    app.add_middleware(SealGateMiddleware)
    app.add_middleware(CSPMiddleware)
    # RequestBodyLimitMiddleware is added LAST so it is the OUTERMOST middleware.
    # It must run before any other middleware to prevent DoS from oversized bodies.
    app.add_middleware(RequestBodyLimitMiddleware)
