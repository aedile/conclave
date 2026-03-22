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

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/`` or ``bootstrapper/``.

CONSTITUTION Priority 0: Security — SSRF prevention
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: P45 review fix F4 — extract SSRF validation to shared/ssrf.py
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

_logger = logging.getLogger(__name__)

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


def validate_callback_url(url: str) -> None:
    """Validate that ``url`` does not point to a private or reserved IP address.

    Called at registration time AND at delivery time (DNS-rebinding protection).

    IPv4-mapped IPv6 addresses (e.g. ``::ffff:10.0.0.1``) are unwrapped to
    their IPv4 form before the blocked-network membership test, preventing
    SSRF bypass via the mapped form.

    DNS failures are treated as safe (fail-open) so that valid webhooks can
    be registered even in environments where the target host is not yet
    reachable from the engine.  Hosts that are unresolvable at delivery time
    will still fail when the HTTP request is attempted.

    Args:
        url: Absolute HTTP(S) URL to validate.

    Raises:
        ValueError: If the URL's hostname resolves to a private/reserved IP,
            if the URL scheme is not ``http`` or ``https``, or if the URL has
            no hostname.
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

    # Resolve hostname to IP addresses
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # DNS resolution failed — fail open for connectivity; the host will
        # fail at HTTP delivery time anyway.
        _logger.debug(
            "SSRF check: DNS resolution failed for hostname %r — treating as safe.",
            hostname,
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
                raise ValueError(
                    f"Callback URL resolves to a private, reserved, or forbidden "
                    f"IP address ({ip}). URL is private, reserved, or forbidden."
                )
