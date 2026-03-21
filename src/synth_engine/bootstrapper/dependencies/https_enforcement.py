"""HTTPS enforcement middleware for the Conclave Engine.

In production mode, synthetic data Parquet files are streamed to operators
over the download endpoint.  If the deployment uses plain ``http://``, that
data is sent in cleartext and is trivially interceptable in flight.

This middleware enforces HTTPS on every request in production deployments.
It inspects the ``X-Forwarded-Proto`` header first (the authoritative scheme
signal in a reverse-proxy deployment) and falls back to the raw ASGI request
scheme when the header is absent.  Any ``http`` request in production mode is
rejected immediately with HTTP 421 Misdirected Request (RFC 7231 §6.5.11).

Development mode (``is_production() == False``) is exempt — operators need to
run the application over plain HTTP during local development and integration
testing.

421 vs 301/302
--------------
The middleware returns 421 rather than redirecting to HTTPS because:

1. A redirect to HTTPS would silently allow cleartext transmission of the
   request line, headers, and any body before the redirect fires — a classic
   SSL-stripping attack surface.
2. RFC 7231 §6.5.11 defines 421 ("Misdirected Request") as "the server is not
   able to produce a response for this combination of scheme, authority, and
   request target".  This is the semantically correct status code.
3. Rejecting with 421 forces the operator to fix their deployment rather than
   silently degrading to HTTP.

Reverse proxy requirement
-------------------------
The Conclave Engine does not terminate TLS directly.  All production deployments
**must** front the ``app`` service with a TLS-terminating reverse proxy (nginx,
Caddy, HAProxy) that:

1. Terminates TLS on port 443.
2. Sets ``X-Forwarded-Proto: https`` on the forwarded request.
3. Strips any ``X-Forwarded-Proto`` header supplied by the client.

The nginx configuration template in ``docs/PRODUCTION_DEPLOYMENT.md`` satisfies
all three requirements.

Startup health check
--------------------
:func:`warn_if_ssl_misconfigured` is called from
:func:`~synth_engine.bootstrapper.config_validation.validate_config` during
application startup.  It emits a ``WARNING`` log when ``CONCLAVE_SSL_REQUIRED``
is ``True`` but no TLS certificate path is configured, indicating a potential
misconfiguration.

CONSTITUTION Priority 0: Security — cleartext synthetic data transmission forbidden
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T42.2 — Add HTTPS Enforcement & Deployment Safety Checks
"""

from __future__ import annotations

import logging

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)

#: HTTP status code for "Misdirected Request" (RFC 7231 §6.5.11).
_STATUS_MISDIRECTED_REQUEST: int = 421

#: Title for the RFC 7807 Problem Details body.
_TITLE_MISDIRECTED_REQUEST: str = "Misdirected Request"

#: Detail message for the RFC 7807 Problem Details body.
_DETAIL_HTTP_NOT_ALLOWED: str = (
    "This endpoint requires HTTPS. Plain HTTP connections are not permitted "
    "in production mode. Configure a TLS-terminating reverse proxy (nginx, "
    "Caddy, or HAProxy) and connect via https://."
)


def _extract_scheme(request: Request) -> str:
    """Extract the effective request scheme from a Starlette ``Request``.

    Prefers the ``X-Forwarded-Proto`` header, which carries the real client
    scheme in a reverse-proxy deployment where the ASGI server sees internal
    ``http://`` traffic even when the client connected over ``https://``.

    Falls back to ``request.url.scheme`` (the raw ASGI scheme) when the
    header is absent — appropriate for direct connections without a proxy.

    Args:
        request: Incoming HTTP request.

    Returns:
        The effective scheme string, normalised to lowercase.
        Typical values: ``"https"`` or ``"http"``.
    """
    forwarded_proto: str | None = request.headers.get("X-Forwarded-Proto")
    if forwarded_proto:
        # Strip whitespace and normalise; proxies may add spaces or mixed case.
        return forwarded_proto.strip().lower()
    return request.url.scheme.lower()


def _build_421_response() -> JSONResponse:
    """Build an RFC 7807 Problem Details 421 Misdirected Request response.

    Returns:
        JSONResponse with HTTP 421 and an RFC 7807-compliant body.
    """
    return JSONResponse(
        status_code=_STATUS_MISDIRECTED_REQUEST,
        content={
            "type": "about:blank",
            "title": _TITLE_MISDIRECTED_REQUEST,
            "status": _STATUS_MISDIRECTED_REQUEST,
            "detail": _DETAIL_HTTP_NOT_ALLOWED,
        },
    )


def warn_if_ssl_misconfigured(*, ssl_required: bool, tls_cert_configured: bool) -> None:
    """Emit a startup warning when TLS is required but no certificate is configured.

    This function is intended to be called from
    :func:`~synth_engine.bootstrapper.config_validation.validate_config` during
    application startup so that operators are warned before the application
    accepts traffic.

    No exception is raised — the warning is advisory.  Operators must review
    their deployment configuration to ensure TLS certificates are present when
    ``CONCLAVE_SSL_REQUIRED=true``.

    Args:
        ssl_required: ``True`` when ``CONCLAVE_SSL_REQUIRED`` is enabled in
            :class:`~synth_engine.shared.settings.ConclaveSettings`.
        tls_cert_configured: ``True`` when a TLS certificate path is present
            in the deployment configuration.  Pass ``False`` when no cert
            path is available (e.g. when the env var is absent or empty).
    """
    if ssl_required and not tls_cert_configured:
        _logger.warning(
            "CONCLAVE_SSL_REQUIRED=true but no TLS certificate is configured. "
            "Ensure a TLS-terminating reverse proxy is in place and sets "
            "X-Forwarded-Proto: https. See docs/PRODUCTION_DEPLOYMENT.md §2.1 "
            "for nginx/Caddy configuration guidance. "
            "Without TLS, synthetic data will be transmitted in cleartext."
        )


class HTTPSEnforcementMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that rejects plain HTTP in production deployments.

    In production mode (``production=True``), any request whose effective
    scheme is not ``https`` receives a 421 Misdirected Request response with
    an RFC 7807 Problem Details body.  The effective scheme is determined by
    :func:`_extract_scheme` — ``X-Forwarded-Proto`` takes precedence over the
    raw ASGI scheme.

    In development mode (``production=False``), all requests pass through
    unchanged.

    This middleware should be registered as the **outermost** layer
    (added last in LIFO ordering in ``setup_middleware()``) so that it fires
    before any other middleware processing, rejecting insecure requests at the
    earliest possible point.

    Args:
        app: The next ASGI application in the middleware stack.
        production: Whether to enforce HTTPS.  When ``None`` (default), the
            value is read from
            :meth:`~synth_engine.shared.settings.ConclaveSettings.is_production`
            via the :func:`~synth_engine.shared.settings.get_settings` singleton.
            Explicit values override the singleton — this allows tests to inject
            a known production/development state without environment variable
            manipulation.

    Attributes:
        _production: Resolved production flag used for scheme enforcement.
    """

    def __init__(
        self,
        app: object,
        *,
        production: bool | None = None,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._production: bool = (
            production if production is not None else get_settings().is_production()
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Gate every request through HTTPS enforcement in production mode.

        In production mode, requests arriving over plain ``http`` are rejected
        with 421 Misdirected Request.  HTTPS requests and all development-mode
        requests are forwarded to the next middleware or route handler.

        Args:
            request: Incoming HTTP request.
            call_next: ASGI callable for the next middleware or route handler.

        Returns:
            A 421 JSONResponse (RFC 7807) if in production mode and the
            effective scheme is ``http``, otherwise the downstream response.
        """
        if self._production and _extract_scheme(request) != "https":
            _logger.warning(
                "https_enforcement: rejected plain-http request path=%s",
                request.url.path,
            )
            return _build_421_response()

        return await call_next(request)
