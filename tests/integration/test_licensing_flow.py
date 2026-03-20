"""Integration tests for the offline licensing JWT challenge/response flow.

These tests exercise the full licensing protocol end-to-end:

1. Generate a license challenge (hardware binding).
2. Sign the challenge with a test RS256 key to produce a license JWT.
3. Validate the license JWT against the test public key.
4. Verify access is granted after activation (LicenseState.is_licensed()).
5. Verify expired JWTs are rejected with LicenseError.
6. Verify tampered JWTs are rejected with LicenseError.

All tests operate fully offline — no network calls are made.  Test RSA keys are
generated fresh for each test session; production keys are never referenced.

CONSTITUTION Priority 0: Security — test keys generated in fixtures, never production keys.
CONSTITUTION Priority 3: TDD — integration gate for P26-T26.5.
Task: P26-T26.5 — Licensing + Migration + FK Masking Integration Tests
"""

from __future__ import annotations

import base64
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from synth_engine.shared.security.licensing import (
    LicenseError,
    LicenseState,
    generate_challenge,
    get_hardware_id,
    verify_license_jwt,
)

# ---------------------------------------------------------------------------
# Key generation fixtures — test-only RSA keys, never production keys
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_private_key() -> rsa.RSAPrivateKey:
    """Generate a fresh 2048-bit RSA private key for this test session.

    Returns:
        A freshly generated RSAPrivateKey.  Never reused across sessions.
    """
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )


@pytest.fixture(scope="module")
def rsa_private_key_pem(rsa_private_key: rsa.RSAPrivateKey) -> str:
    """Serialize the test RSA private key to PEM format.

    Args:
        rsa_private_key: The RSAPrivateKey fixture.

    Returns:
        PEM-encoded private key string.
    """
    return rsa_private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.fixture(scope="module")
def rsa_public_key_pem(rsa_private_key: rsa.RSAPrivateKey) -> str:
    """Serialize the test RSA public key to PEM format.

    Args:
        rsa_private_key: The RSAPrivateKey fixture from which the public key is derived.

    Returns:
        PEM-encoded public key string (SubjectPublicKeyInfo / SPKI format).
    """
    return (
        rsa_private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )


# ---------------------------------------------------------------------------
# State isolation — reset LicenseState between every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_license_state() -> Generator[None]:
    """Deactivate the license after every test to prevent state leakage.

    Yields:
        None — teardown only.
    """
    yield
    LicenseState.deactivate()


# ---------------------------------------------------------------------------
# JWT signing helper
# ---------------------------------------------------------------------------


def _sign_license_jwt(
    private_key_pem: str,
    hardware_id: str,
    *,
    exp_delta: timedelta | None = None,
) -> str:
    """Sign a license JWT with the given private key.

    Args:
        private_key_pem: PEM-encoded RSA private key.
        hardware_id: Hardware ID claim to embed.
        exp_delta: Timedelta from now for expiry.  Defaults to 24 hours if
            ``None``.  Pass a negative timedelta to produce an expired token.

    Returns:
        Compact JWT string signed with RS256.
    """
    if exp_delta is None:
        exp_delta = timedelta(hours=24)
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "hardware_id": hardware_id,
        "sub": "integration-test-license",
        "iat": int(now.timestamp()),
        "exp": int((now + exp_delta).timestamp()),
    }
    return pyjwt.encode(claims, private_key_pem, algorithm="RS256")


# ---------------------------------------------------------------------------
# AC1 integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_generate_challenge_returns_hardware_id() -> None:
    """generate_challenge() must include the local hardware_id.

    The challenge payload is the input the operator submits to the licensing
    server.  The hardware_id must match get_hardware_id() so the server can
    embed it in the signed JWT.

    Arrange/Act: call generate_challenge().
    Assert: payload['hardware_id'] == get_hardware_id().
    """
    challenge = generate_challenge()

    assert "hardware_id" in challenge, "challenge must include 'hardware_id' key"
    assert challenge["hardware_id"] == get_hardware_id(), (
        "challenge hardware_id must match local machine hardware_id"
    )
    assert "app_version" in challenge, "challenge must include 'app_version' key"
    assert "timestamp" in challenge, "challenge must include 'timestamp' key"


@pytest.mark.integration
def test_verify_license_jwt_accepts_valid_token(
    rsa_private_key_pem: str,
    rsa_public_key_pem: str,
) -> None:
    """verify_license_jwt() must return claims for a valid, unexpired RS256 JWT.

    Arrange: sign a license JWT with the test private key; hardware_id matches
        the local machine.
    Act: call verify_license_jwt(token, public_key=test_public_key).
    Assert: returned claims contain the expected hardware_id.
    """
    local_hw_id = get_hardware_id()
    token = _sign_license_jwt(rsa_private_key_pem, hardware_id=local_hw_id)

    claims = verify_license_jwt(token, public_key=rsa_public_key_pem)

    assert claims["hardware_id"] == local_hw_id, (
        "verified claims must contain the local machine hardware_id"
    )


@pytest.mark.integration
def test_full_activation_flow_grants_access(
    rsa_private_key_pem: str,
    rsa_public_key_pem: str,
) -> None:
    """Full licensing flow: verify JWT then activate LicenseState.

    Arrange: software is not licensed (deactivated by autouse fixture).
    Act: verify JWT, then call LicenseState.activate(claims).
    Assert: LicenseState.is_licensed() == True after activation.
    """
    assert not LicenseState.is_licensed(), "precondition: software must start unlicensed"

    local_hw_id = get_hardware_id()
    token = _sign_license_jwt(rsa_private_key_pem, hardware_id=local_hw_id)
    claims = verify_license_jwt(token, public_key=rsa_public_key_pem)

    LicenseState.activate(claims)

    assert LicenseState.is_licensed(), "software must be licensed after activation"
    stored_claims = LicenseState.get_claims()
    assert stored_claims["hardware_id"] == local_hw_id, "stored claims must contain the hardware_id"


@pytest.mark.integration
def test_verify_license_jwt_rejects_expired_token(
    rsa_private_key_pem: str,
    rsa_public_key_pem: str,
) -> None:
    """verify_license_jwt() must raise LicenseError for an expired JWT.

    Arrange: sign a JWT with exp set 1 second in the past (already expired).
    Act: call verify_license_jwt() with the expired token.
    Assert: LicenseError is raised with a message about expiry.
    """
    local_hw_id = get_hardware_id()
    expired_token = _sign_license_jwt(
        rsa_private_key_pem,
        hardware_id=local_hw_id,
        exp_delta=timedelta(seconds=-1),
    )

    with pytest.raises(LicenseError) as exc_info:
        verify_license_jwt(expired_token, public_key=rsa_public_key_pem)

    assert "expired" in exc_info.value.detail.lower(), (
        f"LicenseError detail must mention expiry; got: {exc_info.value.detail!r}"
    )


@pytest.mark.integration
def test_verify_license_jwt_rejects_tampered_signature(
    rsa_private_key_pem: str,
    rsa_public_key_pem: str,
) -> None:
    """verify_license_jwt() must raise LicenseError when the signature is tampered.

    Arrange: produce a valid JWT, then flip a byte in the signature segment.
    Act: call verify_license_jwt() with the corrupted token.
    Assert: LicenseError is raised (invalid signature).
    """
    local_hw_id = get_hardware_id()
    valid_token = _sign_license_jwt(rsa_private_key_pem, hardware_id=local_hw_id)

    # Tamper with the signature (third JWT segment)
    header, payload, sig = valid_token.split(".")
    # Replace signature with a known-bad value: valid base64url bytes but wrong key
    corrupted_sig = (
        base64.urlsafe_b64encode(b"definitely-not-a-valid-signature-bytes-here")
        .rstrip(b"=")
        .decode()
    )
    tampered_token = f"{header}.{payload}.{corrupted_sig}"

    with pytest.raises(LicenseError) as exc_info:
        verify_license_jwt(tampered_token, public_key=rsa_public_key_pem)

    error_detail = exc_info.value.detail.lower()
    assert "invalid" in error_detail or "signature" in error_detail, (
        f"LicenseError detail must indicate invalid signature; got: {exc_info.value.detail!r}"
    )


@pytest.mark.integration
def test_verify_license_jwt_rejects_wrong_hardware_id(
    rsa_private_key_pem: str,
    rsa_public_key_pem: str,
) -> None:
    """verify_license_jwt() must raise LicenseError when hardware_id mismatches.

    Arrange: sign a JWT with a hardware_id that is NOT this machine's ID.
    Act: call verify_license_jwt() with the token.
    Assert: LicenseError is raised mentioning hardware_id mismatch.
    """
    wrong_hw_id = "a" * 64  # valid-length SHA-256 hex but not this machine
    token = _sign_license_jwt(rsa_private_key_pem, hardware_id=wrong_hw_id)

    with pytest.raises(LicenseError) as exc_info:
        verify_license_jwt(token, public_key=rsa_public_key_pem)

    assert "hardware_id" in exc_info.value.detail.lower(), (
        f"LicenseError must mention hardware_id; got: {exc_info.value.detail!r}"
    )


@pytest.mark.integration
def test_get_claims_raises_when_not_licensed() -> None:
    """LicenseState.get_claims() must raise LicenseError when not activated.

    Arrange: software is deactivated (autouse fixture).
    Act: call LicenseState.get_claims().
    Assert: LicenseError is raised.
    """
    assert not LicenseState.is_licensed(), "precondition: software must be unlicensed"

    with pytest.raises(LicenseError):
        LicenseState.get_claims()
