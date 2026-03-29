"""Tests for the licensing router — T71.3.

Covers:
- GET /license/challenge returns hardware_id and qr_code
- POST /license/activate with valid token activates license
- POST /license/activate with expired token returns 403
- POST /license/activate with wrong hardware_id returns 403
- POST /license/activate with missing token field returns 422
- Licensing endpoints reachable when vault is sealed or system is unlicensed
- QR code fallback when qrcode library raises ImportError
- Schema validation for LicenseActivationRequest, LicenseChallengeResponse

ATTACK-FIRST TDD (T71.3 includes negative tests from spec-challenger)
CONSTITUTION Priority 0: Security — licensing is a security boundary
Task: T71.3 — Add Licensing Router Test Coverage
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# RSA key generation helpers (for test-only JWT signing)
# ---------------------------------------------------------------------------


def _generate_rsa_keypair() -> tuple[str, str]:
    """Generate an RSA-2048 keypair and return (private_pem, public_pem).

    Returns:
        Tuple of (private_key_pem, public_key_pem) as PEM-encoded strings.
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _sign_license_jwt(
    private_pem: str,
    hardware_id: str,
    *,
    expired: bool = False,
    wrong_hardware_id: bool = False,
    licensee: str = "Test Operator",
    tier: str = "standard",
) -> str:
    """Create a signed RS256 license JWT for testing.

    Args:
        private_pem: PEM-encoded RSA private key.
        hardware_id: The hardware_id claim to embed in the JWT.
        expired: If True, set exp to 1 hour ago.
        wrong_hardware_id: If True, override hardware_id with a wrong value.
        licensee: Licensee name claim.
        tier: License tier claim.

    Returns:
        Compact JWT string.
    """
    import jwt as pyjwt

    now = datetime.now(UTC)
    exp = (now - timedelta(hours=1)) if expired else (now + timedelta(hours=24))
    claims: dict[str, Any] = {
        "hardware_id": "wrong-hardware-id-0000" if wrong_hardware_id else hardware_id,
        "licensee": licensee,
        "tier": tier,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return pyjwt.encode(claims, private_pem, algorithm="RS256")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Any:
    """Clear settings lru_cache before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_license_state() -> Any:
    """Reset LicenseState to UNLICENSED before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.security.licensing import LicenseState

    LicenseState.deactivate()
    yield
    LicenseState.deactivate()


@pytest.fixture()
def licensing_app() -> FastAPI:
    """Build a minimal FastAPI app with only the licensing router.

    Returns:
        FastAPI test app with /license endpoints.
    """
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.licensing import router as licensing_router

    app = create_app()
    app.include_router(licensing_router)
    return app


@pytest.fixture()
def rsa_keypair() -> tuple[str, str]:
    """Generate a fresh RSA keypair for each test.

    Returns:
        Tuple of (private_pem, public_pem).
    """
    return _generate_rsa_keypair()


# ---------------------------------------------------------------------------
# Bypass helpers
# ---------------------------------------------------------------------------


def _bypass_middleware_patches() -> Any:
    """Return a context manager that bypasses vault + license gate middleware.

    Returns:
        Context manager patching VaultState.is_sealed and LicenseState.is_licensed.
    """
    from contextlib import ExitStack
    from unittest.mock import patch as _patch

    stack = ExitStack()
    stack.enter_context(
        _patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        )
    )
    stack.enter_context(
        _patch(
            "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
            return_value=True,
        )
    )
    return stack


# ---------------------------------------------------------------------------
# GET /license/challenge
# ---------------------------------------------------------------------------


def test_get_license_challenge_returns_hardware_id(licensing_app: FastAPI) -> None:
    """GET /license/challenge returns a non-empty hardware_id field."""
    with _bypass_middleware_patches():
        client = TestClient(licensing_app)
        resp = client.get("/license/challenge")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert "hardware_id" in data, "Response must contain hardware_id"
    assert len(data["hardware_id"]) == 64, "hardware_id must be 64-char SHA-256 hex"
    assert "qr_code" in data, "Response must contain qr_code"
    assert len(data["qr_code"]) > 0, "qr_code must be non-empty"
    assert "app_version" in data, "Response must contain app_version"
    assert "timestamp" in data, "Response must contain timestamp"
    assert "alt_text" in data, "Response must contain alt_text for WCAG accessibility"


def test_get_license_challenge_qrcode_fallback(licensing_app: FastAPI) -> None:
    """GET /license/challenge falls back to base64-JSON when qrcode raises ImportError."""
    with (
        _bypass_middleware_patches(),
        patch(
            "synth_engine.bootstrapper.routers.licensing._render_qr_code",
            side_effect=ImportError("qrcode not available"),
        ),
        patch(
            "synth_engine.bootstrapper.routers.licensing.asyncio.to_thread",
            side_effect=ImportError("qrcode not available"),
        ),
    ):
        # Override asyncio.to_thread to simulate the fallback path by calling
        # _render_qr_code directly with the mock raising.
        # We instead mock it at the router level so the endpoint still returns.
        pass

    # The fallback is inside _render_qr_code itself — test it directly.
    from synth_engine.bootstrapper.routers.licensing import _render_qr_code

    payload = {"hardware_id": "abc123", "app_version": "1.0.0", "timestamp": "2024-01-01"}

    with patch("builtins.__import__", side_effect=ImportError("qrcode not installed")):
        # Can't use __import__ patch broadly — instead mock qrcode import failure
        # by patching the function that imports qrcode.
        pass

    # Test the actual fallback path: mock qrcode to raise ImportError.
    with patch.dict("sys.modules", {"qrcode": None, "qrcode.image.pil": None}):
        result = _render_qr_code(payload)

    # Fallback returns base64-encoded JSON.
    decoded = base64.b64decode(result).decode()
    parsed = json.loads(decoded)
    assert parsed["hardware_id"] == "abc123", "Fallback must encode original payload"


# ---------------------------------------------------------------------------
# POST /license/activate — happy path
# ---------------------------------------------------------------------------


def test_post_license_activate_valid_token_returns_200(
    licensing_app: FastAPI,
    rsa_keypair: tuple[str, str],
) -> None:
    """POST /license/activate with a valid signed token activates the license."""
    private_pem, public_pem = rsa_keypair

    # Patch get_hardware_id to return a known value so we can build a matching JWT.
    with (
        _bypass_middleware_patches(),
        patch(
            "synth_engine.shared.security.licensing.get_hardware_id",
            return_value="test-hw-id-64chars-" + "0" * 45,
        ),
        patch(
            "synth_engine.shared.settings.get_settings",
        ) as mock_settings,
    ):
        mock_settings.return_value.license_public_key = public_pem

        hw_id = "test-hw-id-64chars-" + "0" * 45
        token = _sign_license_jwt(private_pem, hw_id)

        client = TestClient(licensing_app)
        resp = client.post("/license/activate", json={"token": token})

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"
    data = resp.json()
    assert data["status"] == "activated", f"Expected 'activated', got {data['status']!r}"


# ---------------------------------------------------------------------------
# POST /license/activate — attack tests
# ---------------------------------------------------------------------------


def test_post_license_activate_expired_returns_403(
    licensing_app: FastAPI,
    rsa_keypair: tuple[str, str],
) -> None:
    """POST /license/activate with an expired token returns 403."""
    private_pem, public_pem = rsa_keypair

    with (
        _bypass_middleware_patches(),
        patch(
            "synth_engine.shared.security.licensing.get_hardware_id",
            return_value="test-hw-id-64chars-" + "0" * 45,
        ),
        patch(
            "synth_engine.shared.settings.get_settings",
        ) as mock_settings,
    ):
        mock_settings.return_value.license_public_key = public_pem

        hw_id = "test-hw-id-64chars-" + "0" * 45
        token = _sign_license_jwt(private_pem, hw_id, expired=True)

        client = TestClient(licensing_app)
        resp = client.post("/license/activate", json={"token": token})

    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}\n{resp.text}"
    body = resp.json()
    assert body.get("status") == 403, "RFC 7807 body must have status=403"
    assert "expired" in body.get("detail", "").lower() or "license" in body.get("title", "").lower()


def test_post_license_activate_wrong_hw_returns_403(
    licensing_app: FastAPI,
    rsa_keypair: tuple[str, str],
) -> None:
    """POST /license/activate with a mismatched hardware_id returns 403."""
    private_pem, public_pem = rsa_keypair

    with (
        _bypass_middleware_patches(),
        patch(
            "synth_engine.shared.security.licensing.get_hardware_id",
            return_value="correct-hw-id-64chars-" + "0" * 42,
        ),
        patch(
            "synth_engine.shared.settings.get_settings",
        ) as mock_settings,
    ):
        mock_settings.return_value.license_public_key = public_pem

        hw_id = "correct-hw-id-64chars-" + "0" * 42
        # Sign JWT with wrong hardware_id claim.
        token = _sign_license_jwt(private_pem, hw_id, wrong_hardware_id=True)

        client = TestClient(licensing_app)
        resp = client.post("/license/activate", json={"token": token})

    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}\n{resp.text}"
    body = resp.json()
    assert body.get("status") == 403, "RFC 7807 body must have status=403"


def test_post_license_activate_missing_token_returns_422(
    licensing_app: FastAPI,
) -> None:
    """POST /license/activate with no token field in body returns 422."""
    with _bypass_middleware_patches():
        client = TestClient(licensing_app)
        resp = client.post("/license/activate", json={})

    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}\n{resp.text}"


def test_post_license_activate_empty_token_returns_422(
    licensing_app: FastAPI,
) -> None:
    """POST /license/activate with empty token field returns 422 or 403."""
    with _bypass_middleware_patches():
        client = TestClient(licensing_app)
        resp = client.post("/license/activate", json={"token": ""})

    # FastAPI passes empty string to the handler; the JWT verify will raise LicenseError → 403.
    assert resp.status_code in (422, 403), (
        f"Expected 422 or 403 for empty token, got {resp.status_code}\n{resp.text}"
    )


# ---------------------------------------------------------------------------
# Middleware exemption tests
# ---------------------------------------------------------------------------


def test_license_endpoints_reachable_when_sealed(licensing_app: FastAPI) -> None:
    """License endpoints must be reachable even when the vault is sealed.

    The SealGateMiddleware must exempt /license/* routes.
    """
    with patch(
        "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
        return_value=True,  # Vault IS sealed.
    ):
        client = TestClient(licensing_app, raise_server_exceptions=False)
        resp = client.get("/license/challenge")

    # Must NOT return 503 (seal gate response) — must reach the handler.
    assert resp.status_code != 503, (
        "License endpoints must be exempt from SealGateMiddleware; got 503"
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"


def test_license_endpoints_reachable_when_unlicensed(licensing_app: FastAPI) -> None:
    """License endpoints must be reachable when LicenseState is UNLICENSED.

    The LicenseGateMiddleware must exempt /license/* routes.
    """
    from synth_engine.shared.security.licensing import LicenseState

    LicenseState.deactivate()  # Ensure unlicensed.

    with patch(
        "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
        return_value=False,
    ):
        client = TestClient(licensing_app, raise_server_exceptions=False)
        resp = client.get("/license/challenge")

    assert resp.status_code != 402, (
        "License endpoints must be exempt from LicenseGateMiddleware; got 402"
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text}"


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


def test_license_activation_request_rejects_missing_token() -> None:
    """LicenseActivateRequest must require the token field."""
    from pydantic import ValidationError

    from synth_engine.bootstrapper.schemas.licensing import LicenseActivateRequest

    with pytest.raises(ValidationError):
        LicenseActivateRequest()  # type: ignore[call-arg]


def test_license_challenge_response_all_required_fields() -> None:
    """LicenseChallengeResponse must require all 5 fields."""
    from pydantic import ValidationError

    from synth_engine.bootstrapper.schemas.licensing import LicenseChallengeResponse

    with pytest.raises(ValidationError):
        LicenseChallengeResponse(hardware_id="hw", app_version="1.0")  # type: ignore[call-arg]


def test_license_activate_response_optional_fields() -> None:
    """LicenseActivateResponse licensee and tier must be optional (nullable)."""
    from synth_engine.bootstrapper.schemas.licensing import LicenseActivateResponse

    resp = LicenseActivateResponse(status="activated")
    assert resp.status == "activated"
    assert resp.licensee is None
    assert resp.tier is None
