"""Property-based tests for SSRF validation (T58.7).

Uses Hypothesis ``st.ip_addresses()`` to generate representative samples from
IPv4 and IPv6 address spaces and verify that:

1. All RFC 1918 addresses (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) are
   rejected by :func:`validate_callback_url`.
2. Loopback addresses (127.0.0.0/8, ::1) are rejected.
3. Link-local addresses (169.254.0.0/16 — includes AWS IMDS 169.254.169.254)
   are rejected.
4. IPv6-mapped IPv4 private addresses (e.g. ::ffff:10.0.0.1) are rejected.

The tests construct ``http://`` URLs pointing at the generated IP to drive
:func:`validate_callback_url` through its IP-check path, bypassing DNS
resolution (which would require network access).

Strategy note: instead of filtering ``st.ip_addresses()``, we generate
integer offsets within each network and construct addresses from those offsets.
This avoids the filter_too_much health check while still testing a wide
distribution within each CIDR range.

Task: T58.7 — Property-Based Testing (Hypothesis)
"""

from __future__ import annotations

import ipaddress

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Strategy helpers — generate IPs directly within a CIDR range
# ---------------------------------------------------------------------------


def _ipv4_strategy(network_str: str) -> st.SearchStrategy[ipaddress.IPv4Address]:
    """Generate IPv4 addresses directly within *network_str* via integer arithmetic.

    Avoids ``filter()`` which triggers the filter_too_much health check for
    large CIDR ranges like /8 or /12.
    """
    network = ipaddress.IPv4Network(network_str)
    first_int = int(network.network_address)
    last_int = int(network.broadcast_address)
    return st.integers(min_value=first_int, max_value=last_int).map(ipaddress.IPv4Address)


def _url_for_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """Build an http:// URL targeting *ip* directly (no DNS lookup needed)."""
    if isinstance(ip, ipaddress.IPv6Address):
        return f"http://[{ip}]/callback"
    return f"http://{ip}/callback"


# ---------------------------------------------------------------------------
# Property 1: RFC 1918 private IPv4 addresses are rejected
# ---------------------------------------------------------------------------


@given(ip=_ipv4_strategy("10.0.0.0/8"))
@settings(max_examples=100)
def test_ssrf_rejects_rfc1918_10_block(ip: ipaddress.IPv4Address) -> None:
    """SSRF: all 10.0.0.0/8 addresses are rejected."""
    from synth_engine.shared.ssrf import validate_callback_url

    url = _url_for_ip(ip)
    with pytest.raises(ValueError, match="private, reserved, or forbidden"):
        validate_callback_url(url, strict=False)


@given(ip=_ipv4_strategy("172.16.0.0/12"))
@settings(max_examples=100)
def test_ssrf_rejects_rfc1918_172_block(ip: ipaddress.IPv4Address) -> None:
    """SSRF: all 172.16.0.0/12 addresses are rejected."""
    from synth_engine.shared.ssrf import validate_callback_url

    url = _url_for_ip(ip)
    with pytest.raises(ValueError, match="private, reserved, or forbidden"):
        validate_callback_url(url, strict=False)


@given(ip=_ipv4_strategy("192.168.0.0/16"))
@settings(max_examples=100)
def test_ssrf_rejects_rfc1918_192_168_block(ip: ipaddress.IPv4Address) -> None:
    """SSRF: all 192.168.0.0/16 addresses are rejected."""
    from synth_engine.shared.ssrf import validate_callback_url

    url = _url_for_ip(ip)
    with pytest.raises(ValueError, match="private, reserved, or forbidden"):
        validate_callback_url(url, strict=False)


# ---------------------------------------------------------------------------
# Property 2: Loopback addresses are rejected
# ---------------------------------------------------------------------------


@given(ip=_ipv4_strategy("127.0.0.0/8"))
@settings(max_examples=100)
def test_ssrf_rejects_ipv4_loopback(ip: ipaddress.IPv4Address) -> None:
    """SSRF: all 127.0.0.0/8 loopback addresses are rejected."""
    from synth_engine.shared.ssrf import validate_callback_url

    url = _url_for_ip(ip)
    with pytest.raises(ValueError, match="private, reserved, or forbidden"):
        validate_callback_url(url, strict=False)


def test_ssrf_rejects_ipv6_loopback() -> None:
    """SSRF: IPv6 loopback ::1 is rejected."""
    from synth_engine.shared.ssrf import validate_callback_url

    url = "http://[::1]/callback"
    with pytest.raises(ValueError, match="private, reserved, or forbidden"):
        validate_callback_url(url, strict=False)


# ---------------------------------------------------------------------------
# Property 3: Link-local addresses are rejected (includes AWS IMDS)
# ---------------------------------------------------------------------------


@given(ip=_ipv4_strategy("169.254.0.0/16"))
@settings(max_examples=100)
def test_ssrf_rejects_link_local(ip: ipaddress.IPv4Address) -> None:
    """SSRF: all 169.254.0.0/16 link-local addresses are rejected.

    This range includes the AWS EC2 Instance Metadata Service (IMDS)
    endpoint at 169.254.169.254 — a critical SSRF target for credential theft.
    """
    from synth_engine.shared.ssrf import validate_callback_url

    url = _url_for_ip(ip)
    with pytest.raises(ValueError, match="private, reserved, or forbidden"):
        validate_callback_url(url, strict=False)


def test_ssrf_rejects_aws_imds_specifically() -> None:
    """SSRF: the specific AWS IMDS endpoint 169.254.169.254 is rejected."""
    from synth_engine.shared.ssrf import validate_callback_url

    url = "http://169.254.169.254/latest/meta-data/"
    with pytest.raises(ValueError, match="private, reserved, or forbidden"):
        validate_callback_url(url, strict=False)


# ---------------------------------------------------------------------------
# Property 4: IPv6-mapped IPv4 private addresses are rejected
# ---------------------------------------------------------------------------


@given(ip=_ipv4_strategy("10.0.0.0/8"))
@settings(max_examples=100)
def test_ssrf_rejects_ipv6_mapped_rfc1918_10_block(ip: ipaddress.IPv4Address) -> None:
    """SSRF: IPv6-mapped form ::ffff:<10.x.x.x> is rejected.

    An attacker can bypass naive SSRF checks that only test IPv4 addresses by
    sending ``::ffff:10.0.0.1`` — which is an IPv6 address that ``ipaddress``
    does not automatically match against IPv4Network("10.0.0.0/8").

    :func:`validate_callback_url` must unwrap ``ipv4_mapped`` before testing.
    """
    from synth_engine.shared.ssrf import validate_callback_url

    mapped = ipaddress.IPv6Address(f"::ffff:{ip}")
    url = f"http://[{mapped}]/callback"
    with pytest.raises(ValueError, match="private, reserved, or forbidden"):
        validate_callback_url(url, strict=False)


@given(ip=_ipv4_strategy("192.168.0.0/16"))
@settings(max_examples=100)
def test_ssrf_rejects_ipv6_mapped_rfc1918_192_168_block(ip: ipaddress.IPv4Address) -> None:
    """SSRF: IPv6-mapped form ::ffff:<192.168.x.x> is rejected."""
    from synth_engine.shared.ssrf import validate_callback_url

    mapped = ipaddress.IPv6Address(f"::ffff:{ip}")
    url = f"http://[{mapped}]/callback"
    with pytest.raises(ValueError, match="private, reserved, or forbidden"):
        validate_callback_url(url, strict=False)


@given(ip=_ipv4_strategy("127.0.0.0/8"))
@settings(max_examples=100)
def test_ssrf_rejects_ipv6_mapped_loopback(ip: ipaddress.IPv4Address) -> None:
    """SSRF: IPv6-mapped form ::ffff:<127.x.x.x> is rejected."""
    from synth_engine.shared.ssrf import validate_callback_url

    mapped = ipaddress.IPv6Address(f"::ffff:{ip}")
    url = f"http://[{mapped}]/callback"
    with pytest.raises(ValueError, match="private, reserved, or forbidden"):
        validate_callback_url(url, strict=False)
