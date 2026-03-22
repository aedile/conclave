"""TLS certificate loading, validation, and chain verification.

This module provides utilities for mTLS inter-container communication
certificate management. It is intentionally dependency-free beyond the
``cryptography`` library (already a project dependency) and the standard
library — ensuring full air-gap compatibility.

Security properties
-------------------
- ECDSA P-256 leaf certificates are loaded and validated.
- Validity windows are checked against the current UTC clock.
- Key/certificate pair matching is verified via public-key byte comparison.
- Chain verification uses the cryptography library's issuer/signature APIs.
- SAN hostnames are validated for format correctness (RFC 1035 character set,
  length limit, no wildcards). Allowlist enforcement is a generation-time
  concern handled by the CA shell script, not a load-time concern.

Module placement: ``shared/tls/`` — consumed by bootstrapper startup
validation and potentially by ingestion/synthesizer health checks. Lives in
``shared/`` because it is a cross-cutting concern, not owned by any single
business module.

Exemptions
----------
Prometheus, Alertmanager, Grafana, and MinIO are monitoring/ephemeral
services exempt from mTLS. They are NOT included in ``SERVICE_HOSTNAMES``
and MUST NOT be issued leaf certificates by the internal CA script.
This is consistent with ADR-0029 (Gap 7) which limits mTLS to containers
carrying application or database traffic.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDSA,
    EllipticCurvePublicKey,
)

from synth_engine.shared.exceptions import TLSCertificateError

# ---------------------------------------------------------------------------
# Public re-export — TLSCertificateError lives in shared/exceptions.py but
# is re-exported here so that callers who import from this module directly
# continue to work without modification.
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Hardcoded allowlist of service hostnames eligible for mTLS leaf certificates.
#:
#: Monitoring services (prometheus, alertmanager, grafana, minio) are
#: intentionally excluded — they are exempt from mTLS per ADR-0029 (Gap 7).
#: Both Docker Compose hostnames (short form) and Kubernetes service names
#: (FQDN form) are included.
SERVICE_HOSTNAMES: tuple[str, ...] = (
    # Docker Compose short hostnames
    "app",
    "postgres",
    "pgbouncer",
    "redis",
    # Kubernetes service FQDNs (default namespace: synth-engine)
    "app.synth-engine.svc.cluster.local",
    "postgres.synth-engine.svc.cluster.local",
    "pgbouncer.synth-engine.svc.cluster.local",
    "redis.synth-engine.svc.cluster.local",
    # Kubernetes short-form (within same namespace)
    "app.synth-engine",
    "postgres.synth-engine",
    "pgbouncer.synth-engine",
    "redis.synth-engine",
)

#: Maximum allowed hostname length (RFC 1035 §2.3.4).
_MAX_HOSTNAME_LEN: int = 253

#: Pattern for valid hostname characters (letters, digits, hyphens, dots).
_VALID_HOSTNAME_RE: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?$")


# ---------------------------------------------------------------------------
# SAN validation
# ---------------------------------------------------------------------------


def validate_san_hostname(hostname: str) -> None:
    """Validate a SAN DNS hostname entry against security constraints.

    Rejects empty strings, wildcards, hostnames exceeding RFC 1035 length
    limits, and hostnames containing invalid characters. This is a format
    validation only — allowlist enforcement is a generation-time concern
    handled by the CA shell script.

    Args:
        hostname: The DNS hostname string to validate.

    Raises:
        ValueError: If the hostname is empty, a wildcard, too long, or
            contains invalid characters.
    """
    if not hostname:
        raise ValueError("SAN hostname must not be empty")

    if hostname.startswith("*"):
        raise ValueError(f"wildcard SANs are not permitted: {hostname!r}")

    if len(hostname) > _MAX_HOSTNAME_LEN:
        raise ValueError(
            f"SAN hostname too long ({len(hostname)} chars, max {_MAX_HOSTNAME_LEN}): {hostname!r}"
        )

    if not _VALID_HOSTNAME_RE.match(hostname):
        raise ValueError(f"SAN hostname contains invalid characters: {hostname!r}")


# ---------------------------------------------------------------------------
# Certificate helpers (module-level functions)
# ---------------------------------------------------------------------------


def load_certificate(cert_path: Path) -> x509.Certificate:
    """Load a PEM-encoded X.509 certificate from disk.

    Args:
        cert_path: Absolute or relative path to the PEM certificate file.

    Returns:
        The parsed X.509 Certificate object.

    Raises:
        FileNotFoundError: If the file does not exist at ``cert_path``.
        TLSCertificateError: If the file contents cannot be parsed as a
            valid PEM certificate.
    """
    if not cert_path.exists():
        raise FileNotFoundError(f"Certificate file not found: {cert_path}")

    # read_bytes() propagates PermissionError if the file is not readable.
    pem_data = cert_path.read_bytes()

    try:
        return x509.load_pem_x509_certificate(pem_data)
    except Exception as exc:
        raise TLSCertificateError(f"Failed to parse certificate at {cert_path}: {exc}") from exc


def validate_certificate(cert_path: Path) -> datetime.datetime:
    """Validate a certificate's temporal validity window.

    Loads the certificate and checks that the current UTC time falls
    within the ``not_valid_before`` / ``not_valid_after`` window.

    Args:
        cert_path: Absolute or relative path to the PEM certificate file.

    Returns:
        The ``not_valid_after`` datetime (UTC-aware) for the certificate.

    Raises:
        TLSCertificateError: If the certificate is malformed, already
            expired, or not yet valid.
    """
    cert = load_certificate(cert_path)
    now = datetime.datetime.now(tz=datetime.UTC)

    not_before = _ensure_utc(cert.not_valid_before_utc)
    not_after = _ensure_utc(cert.not_valid_after_utc)

    if now < not_before:
        raise TLSCertificateError(
            f"Certificate at {cert_path} is not yet valid (valid from {not_before.isoformat()})"
        )

    if now > not_after:
        raise TLSCertificateError(
            f"Certificate at {cert_path} has expired (expired {not_after.isoformat()})"
        )

    return not_after


def verify_key_cert_pair(key_path: Path, cert_path: Path) -> None:
    """Verify that a private key matches a certificate's public key.

    Compares the DER-encoded public key bytes of the private key against
    those embedded in the certificate.

    Args:
        key_path: Path to the PEM-encoded private key file.
        cert_path: Path to the PEM-encoded certificate file.

    Raises:
        FileNotFoundError: If ``key_path`` does not exist on disk.
        TLSCertificateError: If the private key does not correspond to
            the certificate's public key, or if either file is malformed.
    """
    cert = load_certificate(cert_path)

    if not key_path.exists():
        raise FileNotFoundError(f"Private key file not found: {key_path}")

    # read_bytes() propagates PermissionError if the file is not readable.
    key_pem = key_path.read_bytes()

    try:
        private_key = serialization.load_pem_private_key(key_pem, password=None)
    except Exception as exc:
        raise TLSCertificateError(f"Failed to load private key at {key_path}: {exc}") from exc

    cert_pub_bytes = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_pub_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    if cert_pub_bytes != key_pub_bytes:
        raise TLSCertificateError(
            f"Private key at {key_path} does not match "
            f"the certificate at {cert_path} (key/cert mismatch)"
        )


def verify_chain(leaf_cert_path: Path, ca_cert_path: Path) -> None:
    """Verify that a leaf certificate was signed by the given CA certificate.

    Uses the CA certificate's public key to verify the leaf certificate's
    signature. This is a single-hop verification (leaf -> CA) — it does not
    walk multi-level chains.

    Args:
        leaf_cert_path: Path to the PEM-encoded leaf certificate.
        ca_cert_path: Path to the PEM-encoded CA certificate.

    Raises:
        TLSCertificateError: If the leaf certificate was not signed by
            the CA, if either file is malformed, or if the CA uses a
            non-ECDSA key type.
    """
    leaf_cert = load_certificate(leaf_cert_path)
    ca_cert = load_certificate(ca_cert_path)

    ca_public_key = ca_cert.public_key()

    if not isinstance(ca_public_key, EllipticCurvePublicKey):
        raise TLSCertificateError(
            f"CA certificate at {ca_cert_path} uses an unsupported key type "
            f"(expected ECDSA, got {type(ca_public_key).__name__})"
        )

    try:
        ca_public_key.verify(
            leaf_cert.signature,
            leaf_cert.tbs_certificate_bytes,
            ECDSA(hashes.SHA256()),
        )
    except InvalidSignature as exc:
        raise TLSCertificateError(
            f"Certificate chain verification failed: leaf at {leaf_cert_path} "
            f"was not signed by CA at {ca_cert_path}"
        ) from exc
    except Exception as exc:
        raise TLSCertificateError(f"Certificate chain verification error: {exc}") from exc

    # Also verify the issuer name matches the CA subject name
    if leaf_cert.issuer != ca_cert.subject:
        raise TLSCertificateError(
            f"Issuer name mismatch: leaf issuer={leaf_cert.issuer.rfc4514_string()!r} "
            f"does not match CA subject={ca_cert.subject.rfc4514_string()!r}"
        )


def days_until_expiry(cert_path: Path) -> int:
    """Return the number of days until a certificate expires.

    The value is negative if the certificate is already expired.

    Args:
        cert_path: Path to the PEM-encoded certificate file.

    Returns:
        Integer number of days until (or since) expiry. Negative means
        already expired.
    """
    cert = load_certificate(cert_path)
    now = datetime.datetime.now(tz=datetime.UTC)
    not_after = _ensure_utc(cert.not_valid_after_utc)
    delta = not_after - now
    return delta.days


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_utc(dt: datetime.datetime) -> datetime.datetime:
    """Ensure a datetime is UTC-aware.

    The cryptography library's ``not_valid_before_utc`` / ``not_valid_after_utc``
    properties always return timezone-aware UTC datetimes since v42. This helper
    guards against naive datetimes in edge cases.

    Args:
        dt: A datetime object, possibly naive (no tzinfo).

    Returns:
        A UTC-aware datetime.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.UTC)
    return dt
