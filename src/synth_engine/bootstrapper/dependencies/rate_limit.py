"""Rate limiting configuration, identity resolution, and public API.

This module is the public face of the rate limiting subsystem.  It provides:

- Tier configuration constants (paths, limits, window duration).
- Identity resolution helpers (:func:`_extract_client_ip`,
  :func:`_extract_operator_id`).
- Tier classification (:func:`_resolve_tier`).
- A silent re-export of :class:`RateLimitGateMiddleware` for backward
  compatibility (canonical import:
  ``synth_engine.bootstrapper.dependencies.rate_limit_middleware``).

The implementation is split across three focused modules (T64.3):

- :mod:`.rate_limit_backend` — Redis counter and in-memory fallback
  primitives.
- :mod:`.rate_limit_middleware` — ASGI middleware dispatch (the
  ``RateLimitGateMiddleware`` class).
- This module — configuration, identity resolution, and public re-exports.

Rate limit tiers (per T39.3 specification)
-----------------------------------------
``/unseal``:
    5 requests/minute per client IP.  This endpoint is a high-value target
    (vault unseal) and must be protected against brute-force attacks even
    before authentication is available.

``/auth/token``:
    10 requests/minute per client IP.  Credential stuffing protection.

``/jobs/{id}/download``:
    10 requests/minute per authenticated operator.  Bandwidth protection.
    Matched with ``path.endswith("/download")`` to enforce the specific
    route contract rather than a broader substring match.

All other endpoints:
    60 requests/minute per authenticated operator.

Identity resolution
-------------------
For ``/unseal`` and ``/auth/token`` the client IP address is used as the
rate-limit key because these are pre-authentication endpoints.  The IP is
extracted using the trusted-proxy model (T66.3):

- ``trusted_proxy_count=0`` (default, zero-trust): ``X-Forwarded-For`` is
  ignored entirely; the socket IP (``request.client.host``) is always used.
  This prevents IP spoofing via header manipulation.
- ``trusted_proxy_count=N``: the Nth-from-right entry in the
  ``X-Forwarded-For`` list is used as the client IP.  If the list has
  fewer than N+1 entries, or if the extracted value is not a valid IP
  address, the socket IP is used as a fail-closed fallback.

For all other endpoints the JWT ``sub`` claim is used when a Bearer token
is present.  The token is decoded without signature verification — the
:class:`AuthenticationGateMiddleware` (inner layer) performs the authoritative
signature check.  This avoids double-verification overhead and ensures the
rate-limit key is stable across token refresh cycles.  When no token is
present (unconfigured JWT mode), the client IP is used as the fallback key.

CONSTITUTION Priority 0: Security — brute-force and DoS protection
Task: T39.3 — Add Rate Limiting Middleware
Task: T48.1 — Redis-Backed Rate Limiting
Task: T63.3 — Rate Limiter Fail-Closed on Redis Failure
Task: T64.3 — Decompose rate_limit.py
Task: T66.3 — Trusted Proxy Validation for X-Forwarded-For
"""

from __future__ import annotations

import ipaddress
import logging

import jwt as pyjwt
from starlette.requests import Request

#: Path suffix that triggers the download-specific (lower) rate limit tier.
#: Uses endswith() to enforce the specific /jobs/{id}/download route contract.
_DOWNLOAD_PATH_SUFFIX: str = "/download"

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backward-compat re-export — canonical: rate_limit_middleware.RateLimitGateMiddleware
# Silent re-export, no deprecation warning (T64.3 spec).
# ---------------------------------------------------------------------------
from synth_engine.bootstrapper.dependencies.rate_limit_middleware import (  # noqa: E402
    RateLimitGateMiddleware,
)

__all__ = [
    "_DOWNLOAD_PATH_SUFFIX",
    "RateLimitGateMiddleware",
    "_extract_client_ip",
    "_extract_operator_id",
    "_resolve_tier",
]


def _extract_client_ip(request: Request, *, trusted_proxy_count: int = 0) -> str:
    """Extract the client IP address from the request.

    Uses the trusted-proxy model to safely interpret the ``X-Forwarded-For``
    header (T66.3 — ADV-P62-02):

    - **trusted_proxy_count=0** (zero-trust default): the ``X-Forwarded-For``
      header is ignored entirely.  The socket IP (``request.client.host``)
      is always returned.  This prevents attackers from spoofing their IP by
      setting a forged ``X-Forwarded-For`` header.

    - **trusted_proxy_count=N**: the Nth-from-right entry in the
      comma-separated ``X-Forwarded-For`` list is used.  With N trusted
      proxies each appending their IP to the right, index ``-(N+1)`` in the
      split list is the real client IP.  If the list has fewer than ``N+1``
      entries, the socket IP is returned (fail-closed).

    In both cases, the extracted value is validated as a valid IP address
    using ``ipaddress.ip_address()``.  Values that are not valid IPs (e.g.
    log-injection payloads like ``"; DROP TABLE"``) fall back to the socket IP.

    Supports both IPv4 and IPv6 addresses.

    Args:
        request: Incoming HTTP request.
        trusted_proxy_count: Number of trusted reverse proxies.  Must be
            ``>= 0``.  Defaults to ``0`` (zero-trust — ignore XFF entirely).

    Returns:
        A valid IP address string (IPv4 or IPv6), or ``"unknown"`` if neither
        the XFF header nor the socket provides a usable address.
    """
    socket_ip: str = request.client.host if request.client is not None else "unknown"

    if trusted_proxy_count == 0:
        # Zero-trust: ignore X-Forwarded-For entirely.
        return socket_ip

    forwarded_for: str | None = request.headers.get("X-Forwarded-For")
    if not forwarded_for:
        return socket_ip

    # Split and strip whitespace from each entry.
    parts: list[str] = [p.strip() for p in forwarded_for.split(",")]

    # With N proxies, the real client IP is at index -(N+1) from the right.
    # Example: XFF = "client, proxy1, proxy2" with N=2 → index -3 = "client".
    target_index: int = -(trusted_proxy_count + 1)
    if len(parts) < trusted_proxy_count + 1:
        # Undercount: fewer entries than expected — fail-closed to socket IP.
        _logger.debug(
            "XFF undercount: expected %d+ entries for trusted_proxy_count=%d, "
            "got %d — falling back to socket IP",
            trusted_proxy_count + 1,
            trusted_proxy_count,
            len(parts),
        )
        return socket_ip

    candidate: str = parts[target_index]
    if not candidate:
        # Empty string (e.g. leading comma) — fail-closed.
        return socket_ip

    try:
        # Validates both IPv4 and IPv6; raises ValueError for non-IP strings.
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        _logger.warning(
            "XFF entry at index %d is not a valid IP address: %r — "
            "falling back to socket IP (possible log injection attempt)",
            target_index,
            candidate[:80],  # Truncate to prevent oversized log entries.
        )
        return socket_ip


def _extract_operator_id(request: Request) -> str | None:
    """Extract the operator identity from the JWT Bearer token.

    Decodes the ``sub`` claim without signature verification.  Signature
    integrity is the responsibility of :class:`AuthenticationGateMiddleware`
    (the inner auth gate).  Rate limiting only needs a stable identity key.

    Returns ``None`` when:
    - No ``Authorization`` header is present.
    - The header is not in ``Bearer <token>`` format.
    - The token cannot be decoded (malformed, missing ``sub`` claim).

    Args:
        request: Incoming HTTP request.

    Returns:
        The ``sub`` claim string, or ``None`` if the token is absent or
        undecodable.
    """
    auth_header: str | None = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer ") :]
    try:
        # options={"verify_signature": False} decodes without key validation.
        # The AuthGate (inner layer) performs the authoritative signature check.
        payload: dict[str, object] = pyjwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["HS256", "HS384", "HS512"],
        )
        sub = payload.get("sub")
        return str(sub) if sub is not None else None
    except pyjwt.InvalidTokenError:
        return None


def _resolve_tier(request: Request) -> str:
    """Return the rate limit tier name for the given request path.

    Tier names are stable labels used for Prometheus metrics.  They do NOT
    include raw path parameters to prevent unbounded label cardinality
    (which would OOM the Prometheus registry under adversarial input).

    Args:
        request: Incoming HTTP request.

    Returns:
        One of: ``"unseal"``, ``"auth"``, ``"download"``, ``"general"``.
    """
    path = request.url.path
    if path == "/unseal":
        return "unseal"
    if path == "/auth/token":
        return "auth"
    if path.endswith(_DOWNLOAD_PATH_SUFFIX):
        return "download"
    return "general"
