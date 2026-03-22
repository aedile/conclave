"""TLS configuration helpers for the Conclave Engine.

Provides certificate loading, validation, expiry checking, and chain
verification utilities for mTLS inter-container communication.

All helpers operate on PEM files on disk and use the ``cryptography``
library — no external network calls are made (air-gap compatible).

Usage example::

    from pathlib import Path
    from synth_engine.shared.tls.config import (
        days_until_expiry,
        load_certificate,
        validate_certificate,
    )

    cert = load_certificate(Path("secrets/mtls/app.crt"))
    expiry = validate_certificate(Path("secrets/mtls/app.crt"))
    days = days_until_expiry(Path("secrets/mtls/app.crt"))

Public API:
    - :func:`~synth_engine.shared.tls.config.load_certificate`
    - :func:`~synth_engine.shared.tls.config.validate_certificate`
    - :func:`~synth_engine.shared.tls.config.verify_key_cert_pair`
    - :func:`~synth_engine.shared.tls.config.verify_chain`
    - :func:`~synth_engine.shared.tls.config.days_until_expiry`
    - :class:`~synth_engine.shared.exceptions.TLSCertificateError`
    - :func:`~synth_engine.shared.tls.config.validate_san_hostname`
    - :data:`~synth_engine.shared.tls.config.SERVICE_HOSTNAMES`
"""

from synth_engine.shared.tls.config import (
    SERVICE_HOSTNAMES,
    TLSCertificateError,
    days_until_expiry,
    load_certificate,
    validate_certificate,
    validate_san_hostname,
    verify_chain,
    verify_key_cert_pair,
)

__all__ = [
    "SERVICE_HOSTNAMES",
    "TLSCertificateError",
    "days_until_expiry",
    "load_certificate",
    "validate_certificate",
    "validate_san_hostname",
    "verify_chain",
    "verify_key_cert_pair",
]
