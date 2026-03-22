"""TLS configuration helpers for the Conclave Engine.

Provides certificate loading, validation, expiry checking, and chain
verification utilities for mTLS inter-container communication.

All helpers operate on PEM files on disk and use the ``cryptography``
library — no external network calls are made (air-gap compatible).

Usage example::

    from synth_engine.shared.tls.config import TLSConfig

    cert = TLSConfig.load_certificate(Path("secrets/mtls/app.crt"))
    expiry = TLSConfig.validate_certificate(Path("secrets/mtls/app.crt"))
    days = TLSConfig.days_until_expiry(Path("secrets/mtls/app.crt"))

Public API:
    - :class:`~synth_engine.shared.tls.config.TLSConfig`
    - :class:`~synth_engine.shared.tls.config.TLSCertificateError`
    - :func:`~synth_engine.shared.tls.config.validate_san_hostname`
    - :data:`~synth_engine.shared.tls.config.SERVICE_HOSTNAMES`
"""

from synth_engine.shared.tls.config import (
    SERVICE_HOSTNAMES,
    TLSCertificateError,
    TLSConfig,
    validate_san_hostname,
)

__all__ = [
    "SERVICE_HOSTNAMES",
    "TLSCertificateError",
    "TLSConfig",
    "validate_san_hostname",
]
