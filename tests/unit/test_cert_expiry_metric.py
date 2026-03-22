"""Tests for certificate expiry Prometheus metric (T46.3).

Covers both attack/negative scenarios (committed first per Rule 22) and
feature scenarios for ``update_cert_expiry_metrics()`` in
``shared/cert_metrics.py``.

Task: T46.3 — Certificate Rotation Without Downtime
"""

from __future__ import annotations

import contextlib
import datetime
import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from synth_engine.shared.settings import get_settings

# ---------------------------------------------------------------------------
# Helpers — build in-memory certs for test fixtures
# ---------------------------------------------------------------------------


def _make_cert(days_until_expiry: int) -> bytes:
    """Build a minimal self-signed PEM certificate for testing.

    For expired certs (negative ``days_until_expiry``), the certificate's
    validity window is anchored entirely in the past so that ``not_before``
    is always before ``not_after``.

    Args:
        days_until_expiry: Days from now until the certificate expires.
            Negative values produce already-expired certificates.

    Returns:
        PEM-encoded certificate bytes.
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.datetime.now(tz=datetime.UTC)

    if days_until_expiry >= 0:
        not_before = now - datetime.timedelta(days=1)
        not_after = now + datetime.timedelta(days=days_until_expiry)
    else:
        # Expired cert: both boundaries are in the past
        # not_after = |days_until_expiry| days ago
        # not_before = 1 day before not_after
        not_after = now + datetime.timedelta(days=days_until_expiry)
        not_before = not_after - datetime.timedelta(days=1)

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .sign(private_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Any:
    """Clear the LRU settings cache before and after each test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_cert_expiry_gauge() -> Any:
    """Clear the CERT_EXPIRY_DAYS gauge between tests to avoid label cross-contamination."""
    yield
    with contextlib.suppress(Exception):
        from synth_engine.shared.cert_metrics import CERT_EXPIRY_DAYS

        CERT_EXPIRY_DAYS.clear()


# ===========================================================================
# ATTACK / NEGATIVE TESTS (committed first per Rule 22)
# ===========================================================================

# ---------------------------------------------------------------------------
# ATTACK TEST 1: expired cert metric reports negative value, not clipped to 0
# ---------------------------------------------------------------------------


def test_expired_cert_metric_reports_negative(tmp_path: Path) -> None:
    """days_until_expiry for an expired cert must be negative, not clipped to zero.

    Negative values signal an actionable alert (cert is past expiry).
    Clipping to zero would suppress the alert.
    """
    cert_file = tmp_path / "expired.crt"
    cert_file.write_bytes(_make_cert(days_until_expiry=-5))

    from synth_engine.shared.cert_metrics import CERT_EXPIRY_DAYS, update_cert_expiry_metrics

    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = True
        settings.mtls_ca_cert_path = str(cert_file)
        settings.mtls_client_cert_path = str(cert_file)
        mock_get.return_value = settings

        update_cert_expiry_metrics()

    ca_days = CERT_EXPIRY_DAYS.labels(service="ca")._value.get()
    assert ca_days < 0, f"Expected negative days for expired cert, got {ca_days}"


# ---------------------------------------------------------------------------
# ATTACK TEST 2: missing cert file when mTLS is DISABLED — must not crash
# ---------------------------------------------------------------------------


def test_missing_cert_no_crash_when_mtls_disabled() -> None:
    """When MTLS_ENABLED=false, missing cert files must not crash the metric.

    Prometheus /metrics must remain functional even when cert files are absent.
    """
    from synth_engine.shared.cert_metrics import update_cert_expiry_metrics

    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = False
        # Paths to non-existent files — should be ignored when mTLS disabled
        settings.mtls_ca_cert_path = "/nonexistent/path/ca.crt"
        settings.mtls_client_cert_path = "/nonexistent/path/app.crt"
        mock_get.return_value = settings

        # Must not raise any exception
        update_cert_expiry_metrics()


# ---------------------------------------------------------------------------
# ATTACK TEST 3: when mTLS disabled, metric is set to NaN (not a hard error)
# ---------------------------------------------------------------------------


def test_mtls_disabled_sets_nan_not_crash() -> None:
    """When MTLS_ENABLED=false, all cert expiry gauges must be set to NaN.

    NaN indicates "not applicable" in a clean, non-alerting way.
    """
    from synth_engine.shared.cert_metrics import CERT_EXPIRY_DAYS, update_cert_expiry_metrics

    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = False
        settings.mtls_ca_cert_path = "/nonexistent/ca.crt"
        settings.mtls_client_cert_path = "/nonexistent/app.crt"
        mock_get.return_value = settings

        update_cert_expiry_metrics()

    for service in ("ca", "app"):
        value = CERT_EXPIRY_DAYS.labels(service=service)._value.get()
        assert math.isnan(value), f"Expected NaN for {service} when mTLS disabled, got {value}"


# ---------------------------------------------------------------------------
# ATTACK TEST 4: missing cert file when mTLS is ENABLED — metric = -1 sentinel
# ---------------------------------------------------------------------------


def test_missing_cert_when_mtls_enabled_sets_sentinel(tmp_path: Path) -> None:
    """When mTLS is enabled but cert file is missing, metric must report -1.

    -1 is a sentinel value indicating "unreadable cert" rather than "expired cert".
    This must not crash; it must emit a warning log.
    """
    valid_cert = tmp_path / "ca.crt"
    valid_cert.write_bytes(_make_cert(days_until_expiry=30))
    missing_cert = tmp_path / "missing.crt"
    # Do NOT create missing_cert

    from synth_engine.shared.cert_metrics import CERT_EXPIRY_DAYS, update_cert_expiry_metrics

    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = True
        settings.mtls_ca_cert_path = str(valid_cert)
        settings.mtls_client_cert_path = str(missing_cert)  # doesn't exist
        mock_get.return_value = settings

        update_cert_expiry_metrics()

    app_days = CERT_EXPIRY_DAYS.labels(service="app")._value.get()
    assert app_days == -1, f"Expected -1 sentinel for missing cert, got {app_days}"


# ---------------------------------------------------------------------------
# ATTACK TEST 5: invalid/corrupt cert file — no crash, log warning
# ---------------------------------------------------------------------------


def test_corrupt_cert_file_no_crash(tmp_path: Path) -> None:
    """A corrupt cert file must not crash /metrics — must log warning and set -1.

    Malformed PEM content (truncated, random bytes) must be handled gracefully.
    """
    corrupt_cert = tmp_path / "corrupt.crt"
    corrupt_cert.write_bytes(b"NOT A VALID CERTIFICATE")

    from synth_engine.shared.cert_metrics import CERT_EXPIRY_DAYS, update_cert_expiry_metrics

    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = True
        settings.mtls_ca_cert_path = str(corrupt_cert)
        settings.mtls_client_cert_path = str(corrupt_cert)
        mock_get.return_value = settings

        # Must not raise
        update_cert_expiry_metrics()

    ca_days = CERT_EXPIRY_DAYS.labels(service="ca")._value.get()
    assert ca_days == -1, f"Expected -1 for corrupt cert, got {ca_days}"


# ---------------------------------------------------------------------------
# ATTACK TEST 6: zero-byte cert file — handled gracefully, sets -1
# ---------------------------------------------------------------------------


def test_zero_byte_cert_file_no_crash(tmp_path: Path) -> None:
    """A zero-byte cert file must not crash /metrics — must set -1 sentinel.

    This covers incomplete writes or accidental truncation of cert files.
    """
    empty_cert = tmp_path / "empty.crt"
    empty_cert.write_bytes(b"")

    from synth_engine.shared.cert_metrics import CERT_EXPIRY_DAYS, update_cert_expiry_metrics

    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = True
        settings.mtls_ca_cert_path = str(empty_cert)
        settings.mtls_client_cert_path = str(empty_cert)
        mock_get.return_value = settings

        update_cert_expiry_metrics()

    ca_days = CERT_EXPIRY_DAYS.labels(service="ca")._value.get()
    assert ca_days == -1, f"Expected -1 for zero-byte cert, got {ca_days}"


# ---------------------------------------------------------------------------
# ATTACK TEST 7: metric uses 'service' label, NOT 'path' or 'file'
# ---------------------------------------------------------------------------


def test_metric_uses_service_label_not_path(tmp_path: Path) -> None:
    """Cert expiry gauge must label by service name, NOT by filesystem path.

    Using filesystem paths as labels leaks deployment topology and can cause
    label cardinality explosion (Prometheus anti-pattern).
    """
    cert_file = tmp_path / "app.crt"
    cert_file.write_bytes(_make_cert(days_until_expiry=45))

    from synth_engine.shared.cert_metrics import CERT_EXPIRY_DAYS, update_cert_expiry_metrics

    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = True
        settings.mtls_ca_cert_path = str(cert_file)
        settings.mtls_client_cert_path = str(cert_file)
        mock_get.return_value = settings

        update_cert_expiry_metrics()

    # The gauge must have 'service' label, not 'path' or 'file'
    label_names = list(CERT_EXPIRY_DAYS._labelnames)
    assert "service" in label_names, f"Expected 'service' label, got {label_names}"
    assert "path" not in label_names, f"'path' label must not be present, got {label_names}"
    assert "file" not in label_names, f"'file' label must not be present, got {label_names}"

    # Verify we can query by service name, not path
    value = CERT_EXPIRY_DAYS.labels(service="app")._value.get()
    assert not math.isnan(value), "Expected a numeric value for 'app' service label"


# ---------------------------------------------------------------------------
# ATTACK TEST 8: corrupt cert logs a WARNING (not just silently ignoring)
# ---------------------------------------------------------------------------


def test_corrupt_cert_emits_warning_log(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A corrupt cert must emit a WARNING log — silent failure is not acceptable.

    Operators need visibility into cert read failures to take corrective action.
    """
    import logging

    corrupt_cert = tmp_path / "corrupt.crt"
    corrupt_cert.write_bytes(b"GARBAGE")

    from synth_engine.shared.cert_metrics import update_cert_expiry_metrics

    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = True
        settings.mtls_ca_cert_path = str(corrupt_cert)
        settings.mtls_client_cert_path = str(corrupt_cert)
        mock_get.return_value = settings

        with caplog.at_level(logging.WARNING, logger="synth_engine.shared.cert_metrics"):
            update_cert_expiry_metrics()

    assert len(caplog.records) > 0, "Expected at least one WARNING log for corrupt cert"
    assert any(r.levelno >= logging.WARNING for r in caplog.records), "Expected WARNING level log"


# ===========================================================================
# FEATURE TESTS
# ===========================================================================

# ---------------------------------------------------------------------------
# FEATURE TEST 1: valid cert reports correct positive days
# ---------------------------------------------------------------------------


def test_valid_cert_reports_positive_days(tmp_path: Path) -> None:
    """A valid cert expiring in ~45 days must produce a metric close to 45.

    The exact value may differ by 1 due to timedelta truncation, so we allow
    a ±2 day tolerance.
    """
    cert_file = tmp_path / "app.crt"
    cert_file.write_bytes(_make_cert(days_until_expiry=45))

    from synth_engine.shared.cert_metrics import CERT_EXPIRY_DAYS, update_cert_expiry_metrics

    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = True
        settings.mtls_ca_cert_path = str(cert_file)
        settings.mtls_client_cert_path = str(cert_file)
        mock_get.return_value = settings

        update_cert_expiry_metrics()

    ca_days = CERT_EXPIRY_DAYS.labels(service="ca")._value.get()
    assert 43 <= ca_days <= 47, f"Expected ~45 days, got {ca_days}"


# ---------------------------------------------------------------------------
# FEATURE TEST 2: both 'ca' and 'app' services are populated
# ---------------------------------------------------------------------------


def test_both_services_populated(tmp_path: Path) -> None:
    """update_cert_expiry_metrics must populate both 'ca' and 'app' service labels."""
    ca_cert = tmp_path / "ca.crt"
    app_cert = tmp_path / "app.crt"
    ca_cert.write_bytes(_make_cert(days_until_expiry=300))
    app_cert.write_bytes(_make_cert(days_until_expiry=60))

    from synth_engine.shared.cert_metrics import CERT_EXPIRY_DAYS, update_cert_expiry_metrics

    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = True
        settings.mtls_ca_cert_path = str(ca_cert)
        settings.mtls_client_cert_path = str(app_cert)
        mock_get.return_value = settings

        update_cert_expiry_metrics()

    ca_days = CERT_EXPIRY_DAYS.labels(service="ca")._value.get()
    app_days = CERT_EXPIRY_DAYS.labels(service="app")._value.get()

    assert ca_days > 0, f"CA days must be positive, got {ca_days}"
    assert app_days > 0, f"App days must be positive, got {app_days}"
    # CA cert should have more days than app cert
    assert ca_days > app_days, f"CA ({ca_days}) should outlast app ({app_days})"


# ---------------------------------------------------------------------------
# FEATURE TEST 3: gauge metric name and description are correct
# ---------------------------------------------------------------------------


def test_metric_name_and_description() -> None:
    """The Prometheus gauge must have the correct metric name and description."""
    from synth_engine.shared.cert_metrics import CERT_EXPIRY_DAYS

    assert CERT_EXPIRY_DAYS._name == "conclave_cert_expiry_days"
    assert "expir" in CERT_EXPIRY_DAYS._documentation.lower(), (
        "Metric description must mention expiry"
    )


# ---------------------------------------------------------------------------
# FEATURE TEST 4: idempotent — calling twice with same certs gives same result
# ---------------------------------------------------------------------------


def test_update_is_idempotent(tmp_path: Path) -> None:
    """Calling update_cert_expiry_metrics() twice must not error or diverge."""
    cert_file = tmp_path / "app.crt"
    cert_file.write_bytes(_make_cert(days_until_expiry=30))

    from synth_engine.shared.cert_metrics import CERT_EXPIRY_DAYS, update_cert_expiry_metrics

    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = True
        settings.mtls_ca_cert_path = str(cert_file)
        settings.mtls_client_cert_path = str(cert_file)
        mock_get.return_value = settings

        update_cert_expiry_metrics()
        first = CERT_EXPIRY_DAYS.labels(service="ca")._value.get()

        update_cert_expiry_metrics()
        second = CERT_EXPIRY_DAYS.labels(service="ca")._value.get()

    # Values should be the same (both calls within same second)
    assert abs(first - second) <= 1, f"Idempotent calls diverged: {first} vs {second}"


# ---------------------------------------------------------------------------
# FEATURE TEST 5: mTLS re-enabled after disabled resets NaN to real value
# ---------------------------------------------------------------------------


def test_mtls_disabled_then_enabled(tmp_path: Path) -> None:
    """After mTLS is disabled (NaN), re-enabling must reset metric to real days."""
    cert_file = tmp_path / "app.crt"
    cert_file.write_bytes(_make_cert(days_until_expiry=20))

    from synth_engine.shared.cert_metrics import CERT_EXPIRY_DAYS, update_cert_expiry_metrics

    # First call with mTLS disabled — should set NaN
    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = False
        settings.mtls_ca_cert_path = str(cert_file)
        settings.mtls_client_cert_path = str(cert_file)
        mock_get.return_value = settings
        update_cert_expiry_metrics()

    assert math.isnan(CERT_EXPIRY_DAYS.labels(service="ca")._value.get())

    # Second call with mTLS enabled — should overwrite NaN with real value
    with patch("synth_engine.shared.cert_metrics.get_settings") as mock_get:
        settings = MagicMock()
        settings.mtls_enabled = True
        settings.mtls_ca_cert_path = str(cert_file)
        settings.mtls_client_cert_path = str(cert_file)
        mock_get.return_value = settings
        update_cert_expiry_metrics()

    ca_days = CERT_EXPIRY_DAYS.labels(service="ca")._value.get()
    assert not math.isnan(ca_days), "Expected real days after re-enabling mTLS"
    assert ca_days > 0, f"Expected positive days, got {ca_days}"
