"""Prometheus certificate expiry metrics for mTLS inter-container communication.

Exposes a ``conclave_cert_expiry_days`` gauge tracking the number of days
until each monitored mTLS certificate expires.  The metric is labelled by
logical service name (e.g. ``service="ca"``, ``service="app"``), NOT by
filesystem path, to avoid label cardinality explosion and topology leakage.

Behaviour matrix
----------------
+--------------------+-------------------+----------------------------------+
| mTLS enabled       | Cert readable     | Metric value                     |
+====================+===================+==================================+
| False              | (not checked)     | NaN  — "not applicable"          |
| True               | Yes, valid        | Integer days until expiry        |
| True               | Yes, but expired  | Negative integer days            |
| True               | Missing / unread  | -1   — "unreadable" sentinel     |
| True               | Corrupt / invalid | -1   — "unreadable" sentinel     |
+--------------------+-------------------+----------------------------------+

Negative values are intentionally passed through: they indicate an expired
certificate and should fire Prometheus alerts.  -1 is a distinct sentinel
that means "we could not read the cert at all" — operators must inspect logs
for the corresponding WARNING to diagnose the cause.

Usage
-----
Call :func:`update_cert_expiry_metrics` at application startup and
periodically (e.g. scheduled via Huey or a background thread) so that
Prometheus scrapes always see fresh values::

    from synth_engine.shared.cert_metrics import update_cert_expiry_metrics
    update_cert_expiry_metrics()

Module placement: ``shared/`` — cert expiry monitoring is a cross-cutting
observability concern consumed by the bootstrapper.  It must not live inside
any single business module.

Security notes
--------------
- Filesystem paths are never used as metric labels (privacy + cardinality).
- All exceptions are caught per-service; a read failure on one cert does not
  affect metrics for other certs.
- The function is safe to call from any thread; prometheus_client Gauge is
  thread-safe.

Task: T46.3 — Certificate Rotation Without Downtime
"""

from __future__ import annotations

import logging
from pathlib import Path

from prometheus_client import Gauge

from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus gauge — service-labelled, NOT path-labelled
# ---------------------------------------------------------------------------

CERT_EXPIRY_DAYS: Gauge = Gauge(
    "conclave_cert_expiry_days",
    "Days until mTLS certificate expires (-1 if unreadable, NaN if mTLS disabled)",
    ["service"],
)

# ---------------------------------------------------------------------------
# Internal mapping: logical service name -> settings attribute name
# ---------------------------------------------------------------------------

#: Maps service label names to the corresponding ConclaveSettings attribute that
#: holds the cert file path.  Only the CA trust anchor and the app leaf cert are
#: monitored here; PgBouncer and Redis leaf certs are served from the same CA
#: and have the same validity window as the app cert.
_CERT_SERVICES: dict[str, str] = {
    "ca": "mtls_ca_cert_path",
    "app": "mtls_client_cert_path",
}

# ---------------------------------------------------------------------------
# Sentinel value for unreadable / missing / corrupt cert files
# ---------------------------------------------------------------------------

_UNREADABLE_SENTINEL: float = -1.0


def update_cert_expiry_metrics() -> None:
    """Update certificate expiry gauges for all monitored mTLS certificates.

    Reads each monitored certificate from disk, computes the number of days
    until expiry, and updates the corresponding Prometheus gauge.

    When ``MTLS_ENABLED=false`` all gauges are set to ``float("nan")``
    (indicating "not applicable") without touching the filesystem.  This
    ensures the function is safe to call in environments where cert files
    have not been provisioned.

    When a cert file is missing, unreadable, or corrupt, the gauge is set to
    ``-1`` (sentinel for "unreadable") and a ``WARNING`` log is emitted so
    operators can diagnose the issue.

    Returns:
        None.  Side-effect: updates Prometheus gauge values.
    """
    settings = get_settings()

    if not settings.mtls_enabled:
        for service in _CERT_SERVICES:
            CERT_EXPIRY_DAYS.labels(service=service).set(float("nan"))
        return

    # Lazy import to avoid circular imports at module load time
    from synth_engine.shared.tls.config import days_until_expiry

    for service, path_attr in _CERT_SERVICES.items():
        cert_path = Path(getattr(settings, path_attr))
        try:
            days = days_until_expiry(cert_path)
            CERT_EXPIRY_DAYS.labels(service=service).set(float(days))
        except Exception:
            # Catch all — FileNotFoundError, TLSCertificateError, PermissionError, etc.
            # We intentionally log and continue rather than re-raising, because a
            # single unreadable cert must not crash the /metrics endpoint for all
            # other metrics.
            _logger.warning(
                "Cannot read certificate for service %r at %s — "
                "setting expiry metric to -1 (unreadable sentinel)",
                service,
                cert_path,
            )
            CERT_EXPIRY_DAYS.labels(service=service).set(_UNREADABLE_SENTINEL)
