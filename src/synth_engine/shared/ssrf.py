"""SSRF validation utilities for the Conclave Engine.

Extracted from ``modules/synthesizer/webhook_delivery.py`` as a pure security
utility with no synthesizer coupling.  Lives in ``shared/`` so both
``bootstrapper/routers/webhooks.py`` (registration-time check) and
``modules/synthesizer/webhook_delivery.py`` (delivery-time check) can import
from it without crossing the bootstrapper→modules boundary.

IPv4-mapped IPv6 addresses
--------------------------
``ipaddress.ip_address("::ffff:10.0.0.1")`` returns an :class:`IPv6Address`
that does NOT match any :class:`IPv4Network` entry in ``BLOCKED_NETWORKS``.
After resolving the hostname, this module unwraps mapped addresses via
``ipv4_mapped`` before the network-membership test so that SSRF bypass via
``::ffff:<private_ip>`` is blocked.

DNS failure policy
------------------
The ``strict`` parameter controls behaviour when DNS resolution fails:

* ``strict=True`` (default): DNS failure is treated as a security risk and
  raises :class:`ValueError`.  Use this at **registration time** so that
  attackers cannot pre-register an unresolvable hostname and later point its
  DNS record at an internal target (DNS-pinning / time-of-check time-of-use).

* ``strict=False``: DNS failure is treated as safe (fail-open).  Use this at
  **delivery time** so that transient DNS outages do not abort in-flight
  deliveries.  The HTTP request itself will fail if the host is truly
  unreachable.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/`` or ``bootstrapper/``.

CONSTITUTION Priority 0: Security — SSRF prevention
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: P45 review fix F4 — extract SSRF validation to shared/ssrf.py
Task: T55.4 — SSRF registration fail-closed on DNS failure
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from prometheus_client import Counter

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ADV-P55-04 — Prometheus counter for SSRF registration rejections.
# Incremented whenever validate_callback_url() raises ValueError with
# strict=True, covering bad scheme, missing hostname, private IP, and
# DNS-failure rejections.
# ---------------------------------------------------------------------------
SSRF_REGISTRATION_REJECTION_TOTAL: Counter = Counter(
    "ssrf_registration_rejection_total",
    "Total number of callback URL registrations rejected by SSRF validation "
    "(strict=True mode — includes bad scheme, private IP, and DNS failures).",
)

# ---------------------------------------------------------------------------
# Blocked private / reserved IP networks
# ---------------------------------------------------------------------------

#: IP networks that must never be the target of an outbound HTTP delivery.
#: Covers RFC 1918 private ranges, loopback, link-local (including the AWS
#: metadata endpoint 169.254.169.254), IPv6 ULA, and unspecified addresses.
BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
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


def validate_callback_url(url: str, *, strict: bool = True) -> None:
    """Validate that ``url`` does not point to a private or reserved IP address.

    Called at registration time (``strict=True``, the default) and at delivery
    time (``strict=False``, DNS-rebinding protection).

    IPv4-mapped IPv6 addresses (e.g. ``::ffff:10.0.0.1``) are unwrapped to
    their IPv4 form before the blocked-network membership test, preventing
    SSRF bypass via the mapped form.

    DNS failure policy is controlled by ``strict``:

    * ``strict=True`` (default, registration): raises :class:`ValueError` when
      DNS resolution fails so that unresolvable hostnames are rejected at
      registration time (prevents DNS-pinning TOCTOU attacks).
    * ``strict=False`` (delivery): logs at DEBUG and returns without raising so
      that transient DNS failures do not abort in-flight delivery attempts.

    Args:
        url: Absolute HTTP(S) URL to validate.
        strict: When ``True`` (default), DNS resolution failures are treated as
            a security risk and raise :class:`ValueError`.  When ``False``,
            DNS failures are logged and the function returns without raising.

    Raises:
        ValueError: If the URL's hostname resolves to a private/reserved IP,
            if the URL scheme is not ``http`` or ``https``, if the URL has no
            hostname, or if DNS resolution fails and ``strict=True``.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        SSRF_REGISTRATION_REJECTION_TOTAL.inc()
        raise ValueError(
            f"Callback URL scheme must be http or https, got {scheme!r}. "
            "URL is private, reserved, or forbidden."
        )

    hostname = parsed.hostname
    if not hostname:
        SSRF_REGISTRATION_REJECTION_TOTAL.inc()
        raise ValueError("Callback URL has no hostname. URL is private, reserved, or forbidden.")

    # Resolve hostname to IP addresses
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        if strict:
            _logger.warning(
                "SSRF check: DNS resolution failed for %r — rejecting (strict=True).",
                hostname,
            )
            SSRF_REGISTRATION_REJECTION_TOTAL.inc()
            raise ValueError(
                f"Callback URL hostname {hostname!r} could not be resolved. "
                "URL is private, reserved, or forbidden."
            ) from None
        # strict=False: fail-open — transient DNS failure at delivery time is
        # not a reason to abort; the HTTP request itself will surface the error.
        _logger.debug(
            "SSRF check: DNS resolution failed for %r — safe (strict=False): %s",
            hostname,
            exc,
        )
        return

    for addr_info in addr_infos:
        sockaddr = addr_info[4]
        ip_str = sockaddr[0]
        try:
            ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        # Unwrap IPv4-mapped IPv6 addresses (e.g. ::ffff:10.0.0.1)
        # so they are tested against IPv4Network entries in BLOCKED_NETWORKS.
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped

        for network in BLOCKED_NETWORKS:
            if ip in network:
                SSRF_REGISTRATION_REJECTION_TOTAL.inc()
                raise ValueError(
                    f"Callback URL resolves to a private, reserved, or forbidden "
                    f"IP address ({ip}). URL is private, reserved, or forbidden."
                )
