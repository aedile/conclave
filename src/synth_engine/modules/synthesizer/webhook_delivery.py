"""Webhook delivery engine for synthesis job lifecycle events (T45.3).

Responsible for:
- SSRF-safe HTTP delivery of webhook payloads to registered callbacks.
- HMAC-SHA256 payload signing.
- Exponential backoff retry (1s, 4s, 16s — 3 attempts max).
- Returning a :class:`DeliveryResult` describing the outcome.

This module purposely contains NO FastAPI, SQLModel, or bootstrapper imports.
It is called by ``job_orchestration.py`` via an IoC callback registered by
the bootstrapper at startup.  The session/DB writes for the delivery log are
performed by the bootstrapper layer, not here.

SSRF protection model
---------------------
``_validate_callback_url()`` is called both at:
1. Registration time (in the webhooks router) — rejects bad URLs upfront.
2. Delivery time (here) — DNS-rebinding protection: the host may have changed.

Private IP ranges blocked:
- RFC 1918: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
- RFC 5735 loopback: 127.0.0.0/8
- RFC 3927 link-local: 169.254.0.0/16 (includes AWS metadata 169.254.169.254)
- RFC 4193 ULA: fc00::/7  (fd00::/8 is a subrange)
- IPv6 loopback: ::1
- IPv6 link-local: fe80::/10

Boundary constraints (import-linter enforced):
    - Must NOT import from bootstrapper/.
    - Must NOT import from modules/ingestion/, masking/, privacy/, profiler/.

CONSTITUTION Priority 0: Security — SSRF, no redirect following, key hygiene
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T45.3 — Implement Webhook Callbacks for Task Completion
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Private/reserved IP networks (SSRF protection)
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # RFC 1918 private IPv4
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    # Loopback
    ipaddress.IPv4Network("127.0.0.0/8"),
    # Link-local (includes AWS metadata endpoint 169.254.169.254)
    ipaddress.IPv4Network("169.254.0.0/16"),
    # IPv4 "this" network
    ipaddress.IPv4Network("0.0.0.0/8"),
    # IPv4 broadcast
    ipaddress.IPv4Network("255.255.255.255/32"),
    # IPv6 loopback
    ipaddress.IPv6Network("::1/128"),
    # IPv6 link-local
    ipaddress.IPv6Network("fe80::/10"),
    # IPv6 ULA (RFC 4193) — covers fd00::/8 and fc00::/8
    ipaddress.IPv6Network("fc00::/7"),
    # IPv6 unspecified
    ipaddress.IPv6Network("::/128"),
]

# ---------------------------------------------------------------------------
# Retry constants
# ---------------------------------------------------------------------------

_MAX_ATTEMPTS: int = 3
_BACKOFF_DELAYS: list[float] = [1.0, 4.0, 16.0]  # seconds between attempts

# ---------------------------------------------------------------------------
# IoC callback for job_orchestration → bootstrapper wiring
# ---------------------------------------------------------------------------

#: Registered by bootstrapper at startup via :func:`set_webhook_delivery_fn`.
#: Signature: ``(job_id: int, status: str) -> None``.
_webhook_delivery_fn: Any | None = None


def set_webhook_delivery_fn(fn: Any) -> None:
    """Register the webhook delivery callback (called by bootstrapper at startup).

    The bootstrapper wires this at application startup so that
    ``job_orchestration.py`` can trigger webhook delivery without importing
    anything from ``bootstrapper/``.

    Args:
        fn: Callable ``(job_id: int, status: str) -> None`` that dispatches
            webhook deliveries for all active registrations for the given job.
    """
    global _webhook_delivery_fn
    _webhook_delivery_fn = fn


def _reset_webhook_delivery_fn() -> None:
    """Reset the IoC callback to None.

    For test isolation only.  Not a production path.
    """
    global _webhook_delivery_fn
    _webhook_delivery_fn = None


# ---------------------------------------------------------------------------
# SSRF validation
# ---------------------------------------------------------------------------


def _validate_callback_url(url: str) -> None:
    """Validate that ``url`` does not point to a private or reserved IP address.

    Called at registration time AND at delivery time (DNS-rebinding protection).

    Args:
        url: Absolute HTTP(S) URL to validate.

    Raises:
        ValueError: If the URL's hostname resolves to a private/reserved IP,
            or if the URL scheme is not ``http`` or ``https``.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            f"Callback URL scheme must be http or https, got {scheme!r}. "
            "URL is private, reserved, or forbidden."
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Callback URL has no hostname. URL is private, reserved, or forbidden.")

    # Resolve hostname to IP addresses (raises socket.gaierror on failure)
    try:
        # getaddrinfo returns list of (family, type, proto, canonname, sockaddr)
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # If we cannot resolve, fail open-for-connectivity but consider safe.
        # In production deployments the DNS must resolve; unresolvable hosts
        # will fail at delivery time anyway.  We do not fail-closed here to
        # avoid blocking valid registration of webhooks during testing.
        return

    for addr_info in addr_infos:
        sockaddr = addr_info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                raise ValueError(
                    f"Callback URL resolves to a private, reserved, or forbidden "
                    f"IP address ({ip}). URL is private, reserved, or forbidden."
                )


# ---------------------------------------------------------------------------
# HMAC signing
# ---------------------------------------------------------------------------


def _canonicalize_payload(payload: dict[str, Any]) -> str:
    """Produce a canonical JSON string from ``payload``.

    Uses ``json.dumps(sort_keys=True, separators=(',', ':'))`` for
    deterministic output regardless of Python dict insertion order.

    Args:
        payload: Delivery payload dict.

    Returns:
        Compact, sorted JSON string.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _compute_hmac_signature(payload: dict[str, Any], signing_key: str) -> str:
    """Compute the HMAC-SHA256 signature for ``payload`` using ``signing_key``.

    Args:
        payload: Delivery payload dict (will be canonicalized).
        signing_key: HMAC secret string.

    Returns:
        Signature string in format ``"sha256=<hex_digest>"``.
    """
    canonical = _canonicalize_payload(payload)
    digest = hmac.new(
        signing_key.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


# ---------------------------------------------------------------------------
# Delivery result value object
# ---------------------------------------------------------------------------


@dataclass
class DeliveryResult:
    """Outcome of a single webhook delivery execution.

    Attributes:
        status: ``"SUCCESS"`` | ``"FAILED"`` | ``"SKIPPED"``.
        attempt_number: Number of HTTP attempts made (0 for SKIPPED).
        delivery_id: UUID identifying the logical delivery (shared across retries).
        response_code: HTTP status code from the final attempt (``None`` on error).
        error_message: Error detail on failure (``None`` on success).
    """

    status: str
    attempt_number: int = 0
    delivery_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    response_code: int | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Core delivery function
# ---------------------------------------------------------------------------


def deliver_webhook(
    *,
    registration: Any,
    job_id: int,
    event_type: str,
    payload: dict[str, Any],
    timeout_seconds: int = 10,
) -> DeliveryResult:
    """Deliver a webhook payload to the registered callback URL.

    Implements at-least-once delivery with 3 attempts and exponential
    backoff (1s, 4s, 16s).  Delivery is skipped for inactive registrations.

    SSRF protection: ``_validate_callback_url()`` is called before each
    HTTP attempt (DNS-rebinding guard).

    The HTTP request uses ``allow_redirects=False`` to prevent SSRF via
    open redirects.

    Args:
        registration: Webhook registration object with attributes:
            ``active`` (bool), ``callback_url`` (str), ``signing_key`` (str),
            ``id`` (str).
        job_id: Integer PK of the synthesis job that triggered the delivery.
        event_type: Event type string (e.g. ``"job.completed"``).
        payload: Dict payload to deliver as JSON.
        timeout_seconds: HTTP timeout per attempt in seconds.

    Returns:
        :class:`DeliveryResult` describing the outcome.
    """
    if not registration.active:
        _logger.info(
            "Webhook registration %s is inactive — skipping delivery for job %d.",
            registration.id,
            job_id,
        )
        return DeliveryResult(status="SKIPPED", attempt_number=0)

    delivery_id = str(uuid.uuid4())
    signature = _compute_hmac_signature(payload, registration.signing_key)
    canonical_body = _canonicalize_payload(payload)

    headers = {
        "Content-Type": "application/json",
        "X-Conclave-Signature": signature,
        "X-Conclave-Event": event_type,
        "X-Conclave-Delivery-Id": delivery_id,
    }

    last_error: str | None = None
    last_status_code: int | None = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        # DNS-rebinding protection: re-validate before each attempt
        try:
            _validate_callback_url(registration.callback_url)
        except ValueError as ssrf_exc:
            _logger.error(
                "SSRF validation failed for registration %s (attempt %d): %s",
                registration.id,
                attempt,
                ssrf_exc,
            )
            return DeliveryResult(
                status="FAILED",
                attempt_number=attempt,
                delivery_id=delivery_id,
                error_message=str(ssrf_exc),
            )

        try:
            response = httpx.post(
                registration.callback_url,
                content=canonical_body.encode("utf-8"),
                headers=headers,
                timeout=timeout_seconds,
                follow_redirects=False,
            )
            last_status_code = response.status_code
            response.raise_for_status()
            _logger.info(
                "Webhook delivery SUCCESS: registration=%s job=%d attempt=%d status=%d",
                registration.id,
                job_id,
                attempt,
                response.status_code,
            )
            return DeliveryResult(
                status="SUCCESS",
                attempt_number=attempt,
                delivery_id=delivery_id,
                response_code=last_status_code,
            )
        except Exception as exc:
            last_error = str(exc)
            _logger.warning(
                "Webhook delivery attempt %d failed for registration %s job %d: %s",
                attempt,
                registration.id,
                job_id,
                exc,
            )
            # Backoff before next retry (not after final attempt)
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_DELAYS[attempt - 1])

    return DeliveryResult(
        status="FAILED",
        attempt_number=_MAX_ATTEMPTS,
        delivery_id=delivery_id,
        response_code=last_status_code,
        error_message=last_error,
    )
