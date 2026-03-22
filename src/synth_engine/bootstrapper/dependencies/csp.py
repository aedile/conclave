"""FastAPI/Starlette middleware for security response header enforcement.

This module adds a strict Content-Security-Policy header and an
X-Content-Type-Options: nosniff header to every HTTP response.

The CSP header denies all external CDN references for scripts, fonts, and
stylesheets.  This is a defence-in-depth measure that complements the
air-gapped deployment model — even if a XSS vulnerability were exploited,
the browser would refuse to load external resources.

The X-Content-Type-Options: nosniff header prevents MIME-type sniffing
attacks where a browser might interpret a response as a different content
type than declared by the server.

CONSTITUTION Priority 0: Security
Task: P5-T5.3 — Build Accessible React SPA & "Vault Unseal" (ADV-016+017)

CSP Policy rationale:
  default-src 'self'            — catch-all: only same-origin resources
  script-src 'self'             — no external scripts, no inline (XSS guard)
  style-src 'self' 'unsafe-inline' — 'unsafe-inline' required for Vite dev
                                    CSS injection; production should migrate
                                    to nonce-based policy (ADR-0021)
  font-src 'self'               — local WOFF2 files only; no Google Fonts
  img-src 'self' data:          — data: URIs for embedded images (e.g. QR)
  connect-src 'self'            — fetch/XHR to same origin only
  frame-ancestors 'none'        — prevent clickjacking / iframe embedding
  base-uri 'self'               — prevent base-tag injection attacks
  form-action 'self'            — prevent form exfiltration to 3rd parties
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

#: The full Content-Security-Policy header value.
#:
#: 'unsafe-inline' in style-src is necessary for Vite's dev-mode CSS
#: injection.  For production builds, the static CSS files are bundled and
#: served from the same origin, but Vite's HMR runtime injects <style> tags
#: at runtime during development.  A nonce-based approach is tracked as
#: ADR-0021 for a future hardening sprint.
_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class CSPMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that adds security hardening headers to every response.

    Two headers are set on all responses, regardless of status code or path:

    * ``Content-Security-Policy`` — restricts resource loading to same-origin,
      preventing XSS exploitation of external CDN resources.
    * ``X-Content-Type-Options: nosniff`` — prevents browsers from
      MIME-sniffing a response away from the declared content type.

    The middleware is purely additive — it does not inspect the request,
    block any paths, or modify the response body.

    The middleware must be added AFTER SealGateMiddleware and
    LicenseGateMiddleware in create_app() so that security headers appear even
    on 423 / 402 error responses (LIFO middleware ordering means CSP fires last
    and wraps all other middleware).

    Example registration in create_app()::

        app.add_middleware(LicenseGateMiddleware)
        app.add_middleware(SealGateMiddleware)
        app.add_middleware(CSPMiddleware)   # outermost — fires first on request
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Attach security hardening headers to the outgoing response.

        Args:
            request: Incoming HTTP request (not modified).
            call_next: ASGI callable for the next middleware or route handler.

        Returns:
            The downstream response with Content-Security-Policy and
            X-Content-Type-Options headers attached.
        """
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
