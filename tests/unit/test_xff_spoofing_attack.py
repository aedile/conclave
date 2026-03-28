"""Attack tests for X-Forwarded-For spoofing in rate limiter (T66.3).

Tests verify that the rate limiter correctly validates the X-Forwarded-For
header against the trusted_proxy_count setting, preventing IP spoofing to
bypass per-IP rate limits.

CONSTITUTION Priority 0: Security — rate limit bypass prevention.
Advisory: ADV-P62-02 — X-Forwarded-For accepted without trust validation.
Task: T66.3 — Trusted Proxy Validation for X-Forwarded-For.

Negative/attack tests (committed before feature tests per Rule 22).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from starlette.requests import Request


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    xff: str | None = None,
    client_host: str = "10.0.0.1",
) -> Request:
    """Build a minimal Starlette Request with the given XFF header and client host."""
    scope: dict[object, object] = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "query_string": b"",
        "headers": [],
    }
    if xff is not None:
        scope["headers"] = [(b"x-forwarded-for", xff.encode())]
    mock_conn_info = MagicMock()
    mock_conn_info.host = client_host
    scope["client"] = mock_conn_info  # type: ignore[assignment]
    return Request(scope)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Attack tests — FAIL (RED) before T66.3 implementation
# ---------------------------------------------------------------------------


def test_xff_ignored_when_trusted_proxy_count_zero() -> None:
    """When trusted_proxy_count=0, XFF must be ignored entirely.

    An attacker can set X-Forwarded-For to any value to spoof their source
    IP and bypass per-IP rate limits. When trusted_proxy_count=0 (zero-trust
    default), the header must be completely ignored.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_client_ip

    real_socket_ip = "192.168.1.50"
    spoofed_xff = "1.2.3.4"

    request = _make_request(xff=spoofed_xff, client_host=real_socket_ip)
    # With trusted_proxy_count=0, must return socket IP, not spoofed XFF
    result = _extract_client_ip(request, trusted_proxy_count=0)

    assert result == real_socket_ip, (
        f"Expected socket IP {real_socket_ip!r} when trusted_proxy_count=0, "
        f"but got {result!r} (likely the spoofed XFF value)"
    )


def test_xff_nth_from_right_extracted_correctly() -> None:
    """With trusted_proxy_count=N, the Nth-from-right XFF entry is used.

    When there are N trusted proxies, each proxy appends its own IP to
    X-Forwarded-For. The real client IP is the Nth entry from the right
    (index -(N+1) in the split list).

    Example: client=1.2.3.4, proxy1=10.0.0.1, proxy2=10.0.0.2
    XFF: "1.2.3.4, 10.0.0.1, 10.0.0.2"
    With proxy_count=2: take index -(2+1) = -3 = "1.2.3.4"
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_client_ip

    xff = "1.2.3.4, 10.0.0.1, 10.0.0.2"
    request = _make_request(xff=xff, client_host="10.0.0.2")

    result = _extract_client_ip(request, trusted_proxy_count=2)

    assert result == "1.2.3.4", (
        f"Expected client IP '1.2.3.4' with proxy_count=2, got {result!r}"
    )


def test_xff_fewer_entries_than_proxy_count_falls_back() -> None:
    """When XFF has fewer entries than proxy_count, fall back to socket IP.

    If an attacker sends fewer XFF entries than expected (e.g. they are
    NOT behind a proxy), the system must not use an incorrect entry.
    Fail-closed: use socket IP.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_client_ip

    # Only 1 entry in XFF, but proxy_count=2 expects 3 entries (client + 2 proxies)
    xff = "10.0.0.1"
    real_socket_ip = "10.0.0.2"
    request = _make_request(xff=xff, client_host=real_socket_ip)

    result = _extract_client_ip(request, trusted_proxy_count=2)

    assert result == real_socket_ip, (
        f"Expected socket fallback {real_socket_ip!r} when XFF undercount, got {result!r}"
    )


def test_trusted_proxy_count_rejects_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    """trusted_proxy_count must reject negative values via Pydantic validation.

    A negative trusted proxy count is nonsensical and must be caught at
    settings construction time, not silently defaulted.
    """
    from pydantic import ValidationError

    monkeypatch.setenv("CONCLAVE_TRUSTED_PROXY_COUNT", "-1")
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            from synth_engine.shared.settings import ConclaveSettings

            ConclaveSettings()
    finally:
        get_settings.cache_clear()


def test_trusted_proxy_count_rejects_absurdly_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trusted_proxy_count must reject values greater than 10 (le=10 enforcement).

    An absurdly large value would allow unbounded XFF header processing and
    could indicate a misconfiguration or attack.
    """
    from pydantic import ValidationError

    monkeypatch.setenv("CONCLAVE_TRUSTED_PROXY_COUNT", "11")
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            from synth_engine.shared.settings import ConclaveSettings

            ConclaveSettings()
    finally:
        get_settings.cache_clear()


def test_xff_with_malformed_ip_falls_back_to_socket() -> None:
    """Malformed IP in XFF (e.g. SQL injection) must fall back to socket IP.

    An attacker might inject non-IP content into XFF to cause log injection
    or trigger downstream parsing errors. Invalid IPs must be rejected.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_client_ip

    malicious_xff = "; DROP TABLE rate_limits; --"
    real_socket_ip = "172.16.0.1"
    request = _make_request(xff=malicious_xff, client_host=real_socket_ip)

    result = _extract_client_ip(request, trusted_proxy_count=1)

    assert result == real_socket_ip, (
        f"Expected socket fallback {real_socket_ip!r} for malformed XFF, got {result!r}"
    )


def test_xff_with_empty_string_entry_falls_back() -> None:
    """XFF with empty entries (e.g. leading comma) must fall back to socket IP.

    A leading comma like ',1.2.3.4' produces an empty string as the first
    split entry. Empty strings are not valid IP addresses.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_client_ip

    # With trusted_proxy_count=1, we need XFF[-(1+1)] = XFF[-2]
    # ",1.2.3.4" splits to ["", "1.2.3.4"] — index -2 = "" (invalid)
    xff = ",1.2.3.4"
    real_socket_ip = "10.10.10.10"
    request = _make_request(xff=xff, client_host=real_socket_ip)

    result = _extract_client_ip(request, trusted_proxy_count=1)

    assert result == real_socket_ip, (
        f"Expected socket fallback {real_socket_ip!r} for empty XFF entry, got {result!r}"
    )


def test_xff_with_ipv6_address_accepted() -> None:
    """IPv6 addresses in XFF must be accepted, not accidentally rejected.

    The IP validation must support both IPv4 and IPv6 addresses; rejecting
    IPv6 would cause all IPv6 clients to fall back to the proxy IP.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_client_ip

    ipv6_client = "2001:db8::1"
    proxy_ip = "10.0.0.1"
    xff = f"{ipv6_client}, {proxy_ip}"
    request = _make_request(xff=xff, client_host=proxy_ip)

    result = _extract_client_ip(request, trusted_proxy_count=1)

    assert result == ipv6_client, (
        f"Expected IPv6 address {ipv6_client!r}, got {result!r}"
    )


def test_xff_fix_does_not_affect_operator_id_extraction() -> None:
    """The XFF fix must not alter the _extract_operator_id() function.

    _extract_operator_id reads from the Authorization header, not XFF.
    Verifying isolation ensures we haven't accidentally broken the JWT
    sub-claim extraction path.
    """
    import jwt as pyjwt

    from synth_engine.bootstrapper.dependencies.rate_limit import _extract_operator_id

    token = pyjwt.encode(
        {"sub": "operator-grace", "exp": 9999999999},
        "secret",
        algorithm="HS256",
    )
    scope: dict[object, object] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/jobs",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    mock_conn = MagicMock()
    mock_conn.host = "10.0.0.1"
    scope["client"] = mock_conn  # type: ignore[assignment]
    request = Request(scope)  # type: ignore[arg-type]

    result = _extract_operator_id(request)

    assert result == "operator-grace", (
        f"Expected 'operator-grace' from JWT sub claim, got {result!r}"
    )
