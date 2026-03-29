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

DNS pinning (T69.1)
-------------------
:func:`resolve_and_pin_ips` resolves a hostname at **registration time** and
returns the validated set of IP strings for storage in
``WebhookRegistration.pinned_ips``.  All returned IPs have already been
validated against :data:`BLOCKED_NETWORKS` — storing them implies they are
safe public addresses.  At delivery time, :func:`validate_delivery_ips`
re-resolves the hostname and validates each new IP against
:data:`BLOCKED_NETWORKS`.  If any resolved IP is blocked, delivery is
rejected and :data:`SSRF_DELIVERY_REJECTION_TOTAL` is incremented.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/`` or ``bootstrapper/``.

CONSTITUTION Priority 0: Security — SSRF prevention
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: P45 review fix F4 — extract SSRF validation to shared/ssrf.py
Task: T55.4 — SSRF registration fail-closed on DNS failure
Task: T69.1 — DNS Pinning for Webhook SSRF Protection
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
# T69.1 — Prometheus counter for SSRF delivery rejections.
# Incremented when DNS re-resolution at delivery time detects that a
# previously-safe hostname now resolves to a private/blocked IP (DNS rebinding).
# Separate from registration rejections to allow targeted alerting.
# ---------------------------------------------------------------------------
SSRF_DELIVERY_REJECTION_TOTAL: Counter = Counter(
    "ssrf_delivery_rejection_total",
    "Total number of webhook deliveries rejected by SSRF validation "
    "(delivery-time DNS re-resolution detected a private or blocked IP).",
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


def _is_blocked(ip_str: str) -> bool:
    """Return True if ``ip_str`` resolves to a private or blocked network.

    Handles IPv4-mapped IPv6 addresses (e.g. ``::ffff:10.0.0.1``) by
    unwrapping them to their IPv4 form before the blocked-network test.

    Args:
        ip_str: IP address string (IPv4 or IPv6).

    Returns:
        ``True`` if the IP falls within any :data:`BLOCKED_NETWORKS` entry;
        ``False`` if the IP is public / not blocked.  Returns ``False`` on
        parse errors to avoid crashing on unexpected address formats.
    """
    try:
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    # Unwrap IPv4-mapped IPv6 (::ffff:10.0.0.1) → 10.0.0.1
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    return any(ip in network for network in BLOCKED_NETWORKS)


def resolve_and_pin_ips(hostname: str) -> list[str]:
    """Resolve ``hostname`` and return a validated list of public IP strings.

    Called at **registration time** to store pinned IPs in
    ``WebhookRegistration.pinned_ips``.  All returned IPs have been validated
    against :data:`BLOCKED_NETWORKS` — callers may store them as safe.

    Dual-stack hosts (A + AAAA records) will produce both IPv4 and IPv6
    addresses in the returned list.  Duplicate addresses are de-duplicated
    while preserving insertion order.

    Args:
        hostname: Hostname to resolve (bare hostname, not a URL).

    Returns:
        List of unique public IP address strings (IPv4 and/or IPv6) that
        the hostname resolves to.

    Raises:
        ValueError: If DNS resolution fails (fail-closed at registration time),
            or if any resolved IP falls within :data:`BLOCKED_NETWORKS`.
    """
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise ValueError(
            f"Hostname {hostname!r} could not be resolved. URL is private, reserved, or forbidden."
        ) from None

    seen: set[str] = set()
    result: list[str] = []
    for addr_info in addr_infos:
        sockaddr = addr_info[4]
        ip_str = str(sockaddr[0])
        if ip_str in seen:
            continue
        seen.add(ip_str)

        if _is_blocked(ip_str):
            raise ValueError(
                f"Hostname {hostname!r} resolves to a private, reserved, or forbidden "
                f"IP address ({ip_str}). URL is private, reserved, or forbidden."
            )
        result.append(ip_str)

    return result


def validate_delivery_ips(
    hostname: str,
    *,
    pinned_ips: list[str] | None = None,
) -> None:
    """Validate that ``hostname`` resolves only to public IPs at delivery time.

    Called before each HTTP delivery attempt (DNS-rebinding protection).
    Unlike :func:`validate_callback_url`, this function is DNS-resolution-
    only — it does not re-check URL scheme or hostname presence (those were
    validated at registration time).

    If DNS resolution fails, this function raises :class:`ValueError` so the
    delivery is marked FAILED (not SKIPPED).  This closes the TOCTOU gap
    where ``strict=False`` in :func:`validate_callback_url` would silently
    pass DNS failures through to httpx.

    When ``pinned_ips`` is provided and non-empty (from
    ``WebhookRegistration.pinned_ips`` stored at registration time), the
    freshly resolved set is compared against the pinned set.  A change
    (IP drift) is logged at WARNING for operational visibility but does NOT
    block delivery — the security gate is the :data:`BLOCKED_NETWORKS` check,
    not IP stability.  This tolerates legitimate CDN IP rotation while
    surfacing unexpected changes in dashboards.

    Args:
        hostname: Bare hostname to re-resolve (extracted from callback URL).
        pinned_ips: Optional list of IP strings pinned at registration time
            (from ``WebhookRegistration.pinned_ips`` parsed from JSON).
            When provided and non-empty, drift from the pinned set is logged
            at WARNING level.  Pass ``None`` or ``[]`` to skip drift detection
            (e.g. for legacy registrations that have no pinned IPs).

    Raises:
        ValueError: If DNS resolution fails (fail-closed — DNS failure at
            delivery time is treated as suspicious, not a transient network
            blip), or if any resolved IP falls within :data:`BLOCKED_NETWORKS`.
            :data:`SSRF_DELIVERY_REJECTION_TOTAL` is incremented on rejection.
    """
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        SSRF_DELIVERY_REJECTION_TOTAL.inc()
        raise ValueError(
            f"Delivery DNS resolution failed for hostname {hostname!r}. "
            "Delivery rejected — DNS failure treated as suspicious."
        ) from None

    resolved_ips: set[str] = set()
    for addr_info in addr_infos:
        sockaddr = addr_info[4]
        ip_str = str(sockaddr[0])
        resolved_ips.add(ip_str)
        if _is_blocked(ip_str):
            SSRF_DELIVERY_REJECTION_TOTAL.inc()
            raise ValueError(
                f"Delivery rejected: hostname {hostname!r} resolved to a private, "
                f"reserved, or forbidden IP address ({ip_str}) at delivery time. "
                "DNS rebinding attack detected."
            )

    # IP drift detection: compare against pinned IPs for operational visibility.
    # This is a monitoring check, NOT a security gate — the security check is
    # the BLOCKED_NETWORKS validation above.  CDN IP rotation is legitimate;
    # we log a WARNING so operators can investigate unexpected changes.
    if pinned_ips:
        pinned_set = set(pinned_ips)
        if resolved_ips != pinned_set:
            _logger.warning(
                "IP drift detected for hostname %r: "
                "pinned=%r resolved=%r — delivery proceeding (security gate passed).",
                hostname,
                sorted(pinned_set),
                sorted(resolved_ips),
            )


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

    Note: At delivery time, prefer :func:`validate_delivery_ips` which is
    fail-closed for DNS failures (treats DNS failure as suspicious, not safe).
    ``validate_callback_url(strict=False)`` is retained for backward
    compatibility but should be replaced with :func:`validate_delivery_ips`
    in new delivery code.

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
