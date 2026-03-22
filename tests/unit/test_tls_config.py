"""Unit tests for T46.1 — Internal CA & TLS configuration helpers.

Covers attack/negative tests (negative-first per Rule 22) and feature tests
for cert loading, SAN validation, chain verification, and expiry checks.

CONSTITUTION Priority 3: TDD — RED phase
Task: T46.1 — Internal Certificate Authority & Certificate Issuance
"""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.x509.oid import NameOID

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers — build test certificate fixtures in-memory
# ---------------------------------------------------------------------------


def _generate_key() -> ec.EllipticCurvePrivateKey:
    """Generate an ECDSA P-256 private key for test use."""
    return ec.generate_private_key(ec.SECP256R1())


def _build_cert(
    subject_cn: str,
    key: ec.EllipticCurvePrivateKey,
    issuer_cn: str,
    issuer_key: ec.EllipticCurvePrivateKey,
    *,
    not_before: datetime.datetime,
    not_after: datetime.datetime,
    san_dns: list[str] | None = None,
) -> x509.Certificate:
    """Build an X.509 certificate for testing.

    Args:
        subject_cn: Common Name for the certificate subject.
        key: Private key for the certificate subject.
        issuer_cn: Common Name for the certificate issuer.
        issuer_key: Private key for signing the certificate.
        not_before: Certificate validity start.
        not_after: Certificate validity end.
        san_dns: Optional list of DNS SAN entries.

    Returns:
        A signed X.509 Certificate object.
    """
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)])

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
    )
    if san_dns:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(name) for name in san_dns]),
            critical=False,
        )
    return builder.sign(issuer_key, hashes.SHA256())


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ca_key() -> ec.EllipticCurvePrivateKey:
    """Generate a CA private key."""
    return _generate_key()


@pytest.fixture
def leaf_key() -> ec.EllipticCurvePrivateKey:
    """Generate a leaf certificate private key."""
    return _generate_key()


@pytest.fixture
def valid_ca_cert(ca_key: ec.EllipticCurvePrivateKey) -> x509.Certificate:
    """Self-signed CA certificate valid for 10 years."""
    now = _now_utc()
    return _build_cert(
        "Test CA",
        ca_key,
        "Test CA",
        ca_key,
        not_before=now - datetime.timedelta(seconds=1),
        not_after=now + datetime.timedelta(days=3650),
    )


@pytest.fixture
def valid_leaf_cert(
    ca_key: ec.EllipticCurvePrivateKey,
    leaf_key: ec.EllipticCurvePrivateKey,
    valid_ca_cert: x509.Certificate,
) -> x509.Certificate:
    """Leaf certificate signed by CA, valid for 90 days, with SANs."""
    now = _now_utc()
    return _build_cert(
        "app",
        leaf_key,
        "Test CA",
        ca_key,
        not_before=now - datetime.timedelta(seconds=1),
        not_after=now + datetime.timedelta(days=90),
        san_dns=["app", "app.synth-engine.svc.cluster.local"],
    )


@pytest.fixture
def cert_file(tmp_path: Path, valid_leaf_cert: x509.Certificate) -> Path:
    """Write a valid PEM leaf cert to a temp file."""
    path = tmp_path / "leaf.crt"
    path.write_bytes(valid_leaf_cert.public_bytes(serialization.Encoding.PEM))
    return path


@pytest.fixture
def key_file(tmp_path: Path, leaf_key: ec.EllipticCurvePrivateKey) -> Path:
    """Write a valid PEM leaf private key to a temp file."""
    path = tmp_path / "leaf.key"
    path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return path


@pytest.fixture
def ca_cert_file(tmp_path: Path, valid_ca_cert: x509.Certificate) -> Path:
    """Write a valid PEM CA cert to a temp file."""
    path = tmp_path / "ca.crt"
    path.write_bytes(valid_ca_cert.public_bytes(serialization.Encoding.PEM))
    return path


# ===========================================================================
# ATTACK / NEGATIVE TESTS — written first (Rule 22: attack-first TDD)
# ===========================================================================


class TestAttackCertFileNotFound:
    """AC1 negative — missing cert file raises a clear error."""

    def test_tls_config_raises_when_cert_file_not_found(self, tmp_path: Path) -> None:
        """Missing cert file path must raise FileNotFoundError."""
        from synth_engine.shared.tls.config import load_certificate

        with pytest.raises(FileNotFoundError, match="not found"):
            load_certificate(tmp_path / "nonexistent.crt")


class TestAttackCertExpired:
    """AC2 negative — expired certificate raises an error."""

    def test_tls_config_raises_when_cert_expired(
        self, tmp_path: Path, ca_key: ec.EllipticCurvePrivateKey
    ) -> None:
        """Expired certificate must raise TLSCertificateError."""
        from synth_engine.shared.tls.config import TLSCertificateError, validate_certificate

        now = _now_utc()
        expired_cert = _build_cert(
            "expired",
            ca_key,
            "Test CA",
            ca_key,
            not_before=now - datetime.timedelta(days=180),
            not_after=now - datetime.timedelta(days=1),
        )
        cert_path = tmp_path / "expired.crt"
        cert_path.write_bytes(expired_cert.public_bytes(serialization.Encoding.PEM))

        with pytest.raises(TLSCertificateError, match="expired"):
            validate_certificate(cert_path)


class TestAttackCertNotYetValid:
    """AC3 negative — not-yet-valid certificate raises an error."""

    def test_tls_config_raises_when_cert_not_yet_valid(
        self, tmp_path: Path, ca_key: ec.EllipticCurvePrivateKey
    ) -> None:
        """Not-yet-valid certificate must raise TLSCertificateError."""
        from synth_engine.shared.tls.config import TLSCertificateError, validate_certificate

        now = _now_utc()
        future_cert = _build_cert(
            "future",
            ca_key,
            "Test CA",
            ca_key,
            not_before=now + datetime.timedelta(days=30),
            not_after=now + datetime.timedelta(days=90),
        )
        cert_path = tmp_path / "future.crt"
        cert_path.write_bytes(future_cert.public_bytes(serialization.Encoding.PEM))

        with pytest.raises(TLSCertificateError, match="not yet valid"):
            validate_certificate(cert_path)


class TestAttackKeyCertMismatch:
    """AC4 negative — private key does not match certificate."""

    def test_tls_config_raises_when_key_cert_mismatch(
        self,
        tmp_path: Path,
        valid_leaf_cert: x509.Certificate,
    ) -> None:
        """Key/cert mismatch must raise TLSCertificateError."""
        from synth_engine.shared.tls.config import TLSCertificateError, verify_key_cert_pair

        # Generate a DIFFERENT key — not the one that signed the cert
        wrong_key = _generate_key()
        cert_path = tmp_path / "leaf.crt"
        key_path = tmp_path / "wrong.key"
        cert_path.write_bytes(valid_leaf_cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            wrong_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )

        with pytest.raises(TLSCertificateError, match="key.*mismatch|mismatch.*key"):
            verify_key_cert_pair(key_path, cert_path)


class TestAttackSANValidationRejectsEmpty:
    """AC5 negative — empty hostname SAN rejected."""

    def test_san_validation_rejects_empty_hostname(self) -> None:
        """Empty SAN hostname must raise ValueError."""
        from synth_engine.shared.tls.config import validate_san_hostname

        with pytest.raises(ValueError, match="empty"):
            validate_san_hostname("")


class TestAttackSANValidationRejectsWildcard:
    """AC6 negative — wildcard SAN rejected."""

    def test_san_validation_rejects_wildcard_sans(self) -> None:
        """Wildcard SAN must raise ValueError."""
        from synth_engine.shared.tls.config import validate_san_hostname

        with pytest.raises(ValueError, match="wildcard"):
            validate_san_hostname("*.example.com")


class TestAttackSANValidationRejectsExcessiveLength:
    """AC7 negative — hostname exceeding 253 characters rejected."""

    def test_san_validation_rejects_hostname_exceeding_max_length(self) -> None:
        """Hostname > 253 chars must raise ValueError."""
        from synth_engine.shared.tls.config import validate_san_hostname

        long_hostname = "a" * 254
        with pytest.raises(ValueError, match="too long"):
            validate_san_hostname(long_hostname)


class TestAttackMalformedCert:
    """AC8 negative — garbage data passed as cert raises an error."""

    def test_tls_config_raises_when_cert_is_malformed(self, tmp_path: Path) -> None:
        """Malformed PEM data must raise TLSCertificateError."""
        from synth_engine.shared.tls.config import TLSCertificateError, load_certificate

        malformed_path = tmp_path / "garbage.crt"
        malformed_path.write_bytes(b"this is not a valid PEM certificate\n")

        with pytest.raises(TLSCertificateError, match="malformed|invalid|parse"):
            load_certificate(malformed_path)


class TestAttackPermissionDenied:
    """AC9 negative — cert file exists but is unreadable."""

    def test_tls_helpers_handle_permission_denied(
        self, tmp_path: Path, valid_leaf_cert: x509.Certificate
    ) -> None:
        """Permission-denied on cert file must raise PermissionError."""
        from synth_engine.shared.tls.config import load_certificate

        cert_path = tmp_path / "unreadable.crt"
        cert_path.write_bytes(valid_leaf_cert.public_bytes(serialization.Encoding.PEM))
        cert_path.chmod(0o000)

        try:
            with pytest.raises(PermissionError):
                load_certificate(cert_path)
        finally:
            # Restore permissions so tmp_path cleanup does not fail
            cert_path.chmod(0o644)


class TestAttackKeyFileNotFound:
    """QA-F1 — missing key file raises FileNotFoundError with clear message."""

    def test_verify_key_cert_pair_raises_when_key_file_missing(
        self, tmp_path: Path, valid_leaf_cert: x509.Certificate
    ) -> None:
        """Missing key file must raise FileNotFoundError, not propagate unchecked."""
        from synth_engine.shared.tls.config import verify_key_cert_pair

        cert_path = tmp_path / "leaf.crt"
        cert_path.write_bytes(valid_leaf_cert.public_bytes(serialization.Encoding.PEM))
        missing_key_path = tmp_path / "nonexistent.key"

        with pytest.raises(FileNotFoundError, match="not found"):
            verify_key_cert_pair(missing_key_path, cert_path)


# ===========================================================================
# FEATURE TESTS — happy-path and functional coverage
# ===========================================================================


class TestLoadCertificate:
    """Feature — load_certificate happy path."""

    def test_load_certificate_returns_cert_object(self, cert_file: Path) -> None:
        """load_certificate must return an x509.Certificate."""
        from synth_engine.shared.tls.config import load_certificate

        cert = load_certificate(cert_file)
        assert isinstance(cert, x509.Certificate)

    def test_load_certificate_has_expected_subject(
        self,
        cert_file: Path,
    ) -> None:
        """Loaded certificate subject CN must match 'app'."""
        from synth_engine.shared.tls.config import load_certificate

        cert = load_certificate(cert_file)
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        assert cn == "app"


class TestValidateCertificate:
    """Feature — validate_certificate on valid cert."""

    def test_validate_certificate_passes_for_valid_cert(self, cert_file: Path) -> None:
        """validate_certificate must not raise for a valid, in-window cert."""
        from synth_engine.shared.tls.config import validate_certificate

        # Should complete without raising
        validate_certificate(cert_file)

    def test_validate_certificate_returns_expiry_datetime(self, cert_file: Path) -> None:
        """validate_certificate must return the not_valid_after datetime."""
        from synth_engine.shared.tls.config import validate_certificate

        expiry = validate_certificate(cert_file)
        assert isinstance(expiry, datetime.datetime)
        assert expiry > _now_utc()


class TestVerifyKeyCertPair:
    """Feature — verify_key_cert_pair happy path."""

    def test_verify_key_cert_pair_passes_for_matching_pair(
        self, key_file: Path, cert_file: Path
    ) -> None:
        """verify_key_cert_pair must not raise when key matches cert."""
        from synth_engine.shared.tls.config import verify_key_cert_pair

        # Should complete without raising
        verify_key_cert_pair(key_file, cert_file)


class TestVerifyChain:
    """Feature — verify_chain validates leaf-to-CA chain."""

    def test_verify_chain_passes_for_valid_chain(self, cert_file: Path, ca_cert_file: Path) -> None:
        """verify_chain must not raise for a valid leaf-to-CA chain."""
        from synth_engine.shared.tls.config import verify_chain

        verify_chain(leaf_cert_path=cert_file, ca_cert_path=ca_cert_file)

    def test_verify_chain_raises_for_mismatched_chain(
        self,
        tmp_path: Path,
        valid_leaf_cert: x509.Certificate,
    ) -> None:
        """verify_chain must raise TLSCertificateError for mismatched chain."""
        from synth_engine.shared.tls.config import TLSCertificateError, verify_chain

        # Create a completely separate CA — NOT the one that signed valid_leaf_cert
        different_ca_key = _generate_key()
        now = _now_utc()
        different_ca_cert = _build_cert(
            "Different CA",
            different_ca_key,
            "Different CA",
            different_ca_key,
            not_before=now - datetime.timedelta(seconds=1),
            not_after=now + datetime.timedelta(days=3650),
        )

        leaf_path = tmp_path / "leaf.crt"
        wrong_ca_path = tmp_path / "wrong_ca.crt"
        leaf_path.write_bytes(valid_leaf_cert.public_bytes(serialization.Encoding.PEM))
        wrong_ca_path.write_bytes(different_ca_cert.public_bytes(serialization.Encoding.PEM))

        with pytest.raises(TLSCertificateError, match="chain|issuer|verify"):
            verify_chain(leaf_cert_path=leaf_path, ca_cert_path=wrong_ca_path)

    def test_verify_chain_raises_on_generic_verify_exception(
        self, cert_file: Path, ca_cert_file: Path
    ) -> None:
        """Generic exception from verify() must be wrapped in TLSCertificateError.

        QA-F3: exercises the ``except Exception`` fallback path in verify_chain
        by patching load_certificate to return a mock CA cert whose EC public key
        raises TypeError from verify(). MagicMock(spec=EllipticCurvePublicKey)
        passes the isinstance() check inside verify_chain.
        """
        from synth_engine.shared.tls.config import (
            TLSCertificateError,
            load_certificate,
            verify_chain,
        )

        # Build a mock CA cert whose public_key().verify() raises TypeError.
        # spec=EllipticCurvePublicKey ensures isinstance(mock, EllipticCurvePublicKey) is True.
        mock_ec_key = MagicMock(spec=EllipticCurvePublicKey)
        mock_ec_key.verify.side_effect = TypeError("unexpected internal error")

        mock_ca_cert = MagicMock(spec=x509.Certificate)
        mock_ca_cert.public_key.return_value = mock_ec_key

        # load_certificate is called twice in verify_chain: leaf first, then CA.
        # Pre-load the real leaf cert before patching.
        real_leaf = load_certificate(cert_file)

        with patch(
            "synth_engine.shared.tls.config.load_certificate",
            side_effect=[real_leaf, mock_ca_cert],
        ):
            with pytest.raises(TLSCertificateError, match="chain verification error"):
                verify_chain(leaf_cert_path=cert_file, ca_cert_path=ca_cert_file)


class TestDaysUntilExpiry:
    """Feature — days_until_expiry."""

    def test_days_until_expiry_returns_positive_for_valid_cert(self, cert_file: Path) -> None:
        """days_until_expiry must return a positive integer for a valid cert."""
        from synth_engine.shared.tls.config import days_until_expiry

        days = days_until_expiry(cert_file)
        assert days > 0

    def test_days_until_expiry_returns_negative_for_expired_cert(
        self, tmp_path: Path, ca_key: ec.EllipticCurvePrivateKey
    ) -> None:
        """days_until_expiry must return negative for an expired cert."""
        from synth_engine.shared.tls.config import days_until_expiry

        now = _now_utc()
        expired_cert = _build_cert(
            "expired",
            ca_key,
            "Test CA",
            ca_key,
            not_before=now - datetime.timedelta(days=180),
            not_after=now - datetime.timedelta(days=1),
        )
        cert_path = tmp_path / "expired.crt"
        cert_path.write_bytes(expired_cert.public_bytes(serialization.Encoding.PEM))

        days = days_until_expiry(cert_path)
        assert days < 0


class TestSANValidation:
    """Feature — validate_san_hostname allowlist."""

    @pytest.mark.parametrize(
        "hostname",
        [
            "app",
            "postgres",
            "pgbouncer",
            "redis",
            "app.synth-engine.svc.cluster.local",
            "postgres.synth-engine.svc.cluster.local",
        ],
    )
    def test_san_validation_accepts_valid_hostnames(self, hostname: str) -> None:
        """validate_san_hostname must not raise for valid, known hostnames."""
        from synth_engine.shared.tls.config import validate_san_hostname

        # Should complete without raising
        validate_san_hostname(hostname)

    def test_san_validation_rejects_hostname_with_spaces(self) -> None:
        """Hostname with spaces must raise ValueError."""
        from synth_engine.shared.tls.config import validate_san_hostname

        with pytest.raises(ValueError, match="invalid"):
            validate_san_hostname("host name with spaces")

    def test_san_validation_rejects_null_byte(self) -> None:
        """Hostname with null byte must raise ValueError."""
        from synth_engine.shared.tls.config import validate_san_hostname

        with pytest.raises(ValueError, match="invalid"):
            validate_san_hostname("host\x00name")


class TestServiceHostnames:
    """Feature — SERVICE_HOSTNAMES allowlist completeness."""

    def test_service_hostnames_contains_required_services(self) -> None:
        """SERVICE_HOSTNAMES must include all four mTLS services."""
        from synth_engine.shared.tls.config import SERVICE_HOSTNAMES

        required = {"app", "postgres", "pgbouncer", "redis"}
        assert required.issubset(set(SERVICE_HOSTNAMES))

    def test_service_hostnames_does_not_contain_monitoring_services(
        self,
    ) -> None:
        """Monitoring services (prometheus, grafana, minio) must NOT be in SERVICE_HOSTNAMES."""
        from synth_engine.shared.tls.config import SERVICE_HOSTNAMES

        exempt = {"prometheus", "alertmanager", "grafana", "minio"}
        overlap = exempt.intersection(set(SERVICE_HOSTNAMES))
        assert not overlap, f"Monitoring services found in allowlist: {overlap}"


class TestTLSCertificateError:
    """Feature — TLSCertificateError is a proper domain exception."""

    def test_tls_certificate_error_is_synth_engine_error(self) -> None:
        """TLSCertificateError must inherit from SynthEngineError (ADR-0037)."""
        from synth_engine.shared.exceptions import SynthEngineError
        from synth_engine.shared.tls.config import TLSCertificateError

        err = TLSCertificateError("test message")
        assert isinstance(err, SynthEngineError)
        assert isinstance(err, Exception)
        assert "test message" in str(err)

    def test_tls_certificate_error_importable_from_shared_exceptions(self) -> None:
        """TLSCertificateError must be importable from shared.exceptions directly."""
        from synth_engine.shared.exceptions import TLSCertificateError

        err = TLSCertificateError("from exceptions module")
        assert "from exceptions module" in str(err)


# ===========================================================================
# EDGE-CASE / COVERAGE TESTS — branch paths not covered by negative/feature
# ===========================================================================


class TestVerifyKeyCertPairMalformedKey:
    """Coverage — malformed private key file raises TLSCertificateError."""

    def test_verify_key_cert_pair_raises_for_malformed_key(
        self, tmp_path: Path, valid_leaf_cert: x509.Certificate
    ) -> None:
        """Malformed private key PEM must raise TLSCertificateError."""
        from synth_engine.shared.tls.config import TLSCertificateError, verify_key_cert_pair

        cert_path = tmp_path / "leaf.crt"
        key_path = tmp_path / "bad.key"
        cert_path.write_bytes(valid_leaf_cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(b"this is not a valid private key\n")

        with pytest.raises(TLSCertificateError, match="Failed to load private key"):
            verify_key_cert_pair(key_path, cert_path)


class TestVerifyChainNonECDSACa:
    """Coverage — non-ECDSA CA key type raises TLSCertificateError."""

    def test_verify_chain_raises_for_rsa_ca(self, tmp_path: Path) -> None:
        """Chain verify with RSA CA must raise TLSCertificateError (ECDSA only)."""
        from cryptography.hazmat.primitives.asymmetric import rsa

        from synth_engine.shared.tls.config import TLSCertificateError, verify_chain

        # Build an RSA-2048 CA cert
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ec_leaf_key = _generate_key()
        now = _now_utc()

        rsa_ca_cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "RSA CA")]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "RSA CA")]))
            .public_key(rsa_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(seconds=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .sign(rsa_key, hashes.SHA256())
        )

        leaf_cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "app")]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "RSA CA")]))
            .public_key(ec_leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(seconds=1))
            .not_valid_after(now + datetime.timedelta(days=90))
            .sign(rsa_key, hashes.SHA256())
        )

        ca_path = tmp_path / "rsa_ca.crt"
        leaf_path = tmp_path / "leaf.crt"
        ca_path.write_bytes(rsa_ca_cert.public_bytes(serialization.Encoding.PEM))
        leaf_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))

        with pytest.raises(TLSCertificateError, match="unsupported key type"):
            verify_chain(leaf_cert_path=leaf_path, ca_cert_path=ca_path)


class TestEnsureUTCNaiveDatetime:
    """Coverage — _ensure_utc handles naive datetimes."""

    def test_ensure_utc_adds_timezone_to_naive_datetime(self) -> None:
        """_ensure_utc must add UTC tzinfo to a naive datetime."""
        from synth_engine.shared.tls.config import _ensure_utc

        naive = datetime.datetime(2025, 6, 1, 12, 0, 0)
        aware = _ensure_utc(naive)
        assert aware.tzinfo is datetime.UTC
        assert aware.year == 2025

    def test_ensure_utc_preserves_already_aware_datetime(self) -> None:
        """_ensure_utc must pass through a datetime that already has tzinfo."""
        from synth_engine.shared.tls.config import _ensure_utc

        aware = datetime.datetime(2025, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)
        result = _ensure_utc(aware)
        assert result is aware


class TestVerifyChainIssuerMismatch:
    """Coverage — leaf issuer name mismatch with CA subject raises TLSCertificateError."""

    def test_verify_chain_raises_for_issuer_name_mismatch(self, tmp_path: Path) -> None:
        """Leaf cert with different issuer name must raise TLSCertificateError."""
        from synth_engine.shared.tls.config import TLSCertificateError, verify_chain

        # Build a CA cert
        ca_key = _generate_key()
        leaf_key = _generate_key()
        now = _now_utc()

        ca_cert = _build_cert(
            "Real CA",
            ca_key,
            "Real CA",
            ca_key,
            not_before=now - datetime.timedelta(seconds=1),
            not_after=now + datetime.timedelta(days=3650),
        )

        # Build a leaf cert where issuer says "Different CA" but we try to verify
        # against "Real CA" — both signature AND issuer name mismatch.
        # We construct the leaf signed by ca_key but with issuer name != CA subject.
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "app")])
        wrong_issuer_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Different CA")])
        leaf_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(wrong_issuer_name)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(seconds=1))
            .not_valid_after(now + datetime.timedelta(days=90))
            .sign(ca_key, hashes.SHA256())
        )

        ca_path = tmp_path / "ca.crt"
        leaf_path = tmp_path / "leaf.crt"
        ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        leaf_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))

        with pytest.raises(TLSCertificateError, match="mismatch"):
            verify_chain(leaf_cert_path=leaf_path, ca_cert_path=ca_path)
