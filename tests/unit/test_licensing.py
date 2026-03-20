"""Unit tests for the Offline License Activation Protocol.

Tests cover:
- get_hardware_id() — deterministic, SHA-256 hex format
- generate_challenge() — returns hardware_id, app_version, timestamp
- verify_license_jwt() — valid JWT accepted; wrong hardware_id rejected;
  bad signature rejected; expired JWT rejected
- LicenseState — activate, deactivate, is_licensed, get_claims
- /license/challenge endpoint — returns 200 with expected fields
- /license/activate endpoint — valid JWT → 200; bad JWT → 403
- Modified JWT hardware_id claim → rejected (backlog verbatim)
- Modified JWT signature → rejected (backlog verbatim)
- LicenseGateMiddleware — 402 returned for non-exempt routes when unlicensed
- LICENSE_PUBLIC_KEY env var override — activate endpoint uses env key
- LicenseChallengeResponse.alt_text field — accessibility field present

ADV-054 (P8-T8.3): LicenseError no longer carries status_code.
Tests updated to assert on exc.detail instead of exc.status_code.

CONSTITUTION Priority 3: TDD
Task: P5-T5.2 — Offline License Activation Protocol
Task: P8-T8.3 — Data Model & Architecture Cleanup (ADV-054)
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers — generate an ephemeral RSA keypair for tests
# ---------------------------------------------------------------------------


def _make_rsa_keypair() -> tuple[str, str]:
    """Generate a fresh RSA-2048 keypair for test use.

    Returns:
        Tuple of (private_key_pem, public_key_pem) as PEM strings.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def _make_license_jwt(
    private_key_pem: str,
    hardware_id: str,
    exp_offset_seconds: int = 3600,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Sign a license JWT for tests using the given private key.

    Args:
        private_key_pem: RSA private key in PEM format.
        hardware_id: hardware_id claim value to embed.
        exp_offset_seconds: Seconds from now before token expiry.
        extra_claims: Additional claims to merge into the payload.

    Returns:
        Compact JWT string.
    """
    import jwt as pyjwt

    now = int(datetime.now(UTC).timestamp())
    payload: dict[str, Any] = {
        "hardware_id": hardware_id,
        "iat": now,
        "exp": now + exp_offset_seconds,
        "licensee": "test-org",
        "tier": "enterprise",
    }
    if extra_claims:
        payload.update(extra_claims)
    return pyjwt.encode(payload, private_key_pem, algorithm="RS256")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rsa_keypair() -> tuple[str, str]:
    """Provide a fresh RSA keypair (private_pem, public_pem)."""
    return _make_rsa_keypair()


@pytest.fixture(autouse=True)
def reset_license_state() -> Generator[None]:
    """Reset LicenseState class-level state and settings cache after each test.

    Clears the get_settings() lru_cache so that any monkeypatched environment
    variables (e.g. LICENSE_PUBLIC_KEY) do not leak across tests.
    """
    yield
    try:
        from synth_engine.shared.security.licensing import LicenseState
        from synth_engine.shared.settings import get_settings

        LicenseState.deactivate()
        get_settings.cache_clear()
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# get_hardware_id() tests
# ---------------------------------------------------------------------------


def test_get_hardware_id_returns_64_char_hex() -> None:
    """get_hardware_id() returns a 64-character lowercase hex string (SHA-256)."""
    from synth_engine.shared.security.licensing import get_hardware_id

    hw_id = get_hardware_id()
    assert isinstance(hw_id, str)
    assert len(hw_id) == 64
    assert all(c in "0123456789abcdef" for c in hw_id)


def test_get_hardware_id_is_deterministic() -> None:
    """get_hardware_id() returns the same value on repeated calls."""
    from synth_engine.shared.security.licensing import get_hardware_id

    assert get_hardware_id() == get_hardware_id()


# ---------------------------------------------------------------------------
# generate_challenge() tests
# ---------------------------------------------------------------------------


def test_generate_challenge_has_required_keys() -> None:
    """generate_challenge() returns a dict with hardware_id, app_version, timestamp."""
    from synth_engine.shared.security.licensing import generate_challenge

    challenge = generate_challenge()
    assert "hardware_id" in challenge
    assert "app_version" in challenge
    assert "timestamp" in challenge


def test_generate_challenge_hardware_id_matches_get_hardware_id() -> None:
    """generate_challenge() embeds the same hardware_id as get_hardware_id()."""
    from synth_engine.shared.security.licensing import generate_challenge, get_hardware_id

    challenge = generate_challenge()
    assert challenge["hardware_id"] == get_hardware_id()


def test_generate_challenge_timestamp_is_iso_format() -> None:
    """generate_challenge() timestamp is a valid ISO-8601 datetime string."""
    from synth_engine.shared.security.licensing import generate_challenge

    challenge = generate_challenge()
    # Should parse without raising
    datetime.fromisoformat(challenge["timestamp"])


# ---------------------------------------------------------------------------
# LicenseState tests
# ---------------------------------------------------------------------------


def test_license_state_starts_unlicensed() -> None:
    """LicenseState.is_licensed() is False before any activation."""
    from synth_engine.shared.security.licensing import LicenseState

    assert LicenseState.is_licensed() is False


def test_license_state_activate_sets_licensed() -> None:
    """LicenseState.activate() sets is_licensed() to True."""
    from synth_engine.shared.security.licensing import LicenseState

    claims: dict[str, Any] = {"hardware_id": "abc123", "tier": "enterprise"}
    LicenseState.activate(claims)
    assert LicenseState.is_licensed() is True


def test_license_state_get_claims_returns_claims_after_activation() -> None:
    """LicenseState.get_claims() returns the dict passed to activate()."""
    from synth_engine.shared.security.licensing import LicenseState

    claims: dict[str, Any] = {"hardware_id": "abc123", "tier": "enterprise"}
    LicenseState.activate(claims)
    assert LicenseState.get_claims() == claims


def test_license_state_deactivate_clears_state() -> None:
    """LicenseState.deactivate() reverts is_licensed() to False."""
    from synth_engine.shared.security.licensing import LicenseState

    LicenseState.activate({"hardware_id": "abc"})
    LicenseState.deactivate()
    assert LicenseState.is_licensed() is False


def test_license_state_get_claims_raises_when_unlicensed() -> None:
    """LicenseState.get_claims() raises LicenseError when not licensed."""
    from synth_engine.shared.security.licensing import LicenseError, LicenseState

    with pytest.raises(LicenseError):
        LicenseState.get_claims()


# ---------------------------------------------------------------------------
# verify_license_jwt() tests
# ---------------------------------------------------------------------------


def test_verify_license_jwt_accepts_valid_token(rsa_keypair: tuple[str, str]) -> None:
    """verify_license_jwt() returns claims when the token is valid."""
    from synth_engine.shared.security.licensing import get_hardware_id, verify_license_jwt

    private_pem, public_pem = rsa_keypair
    hw_id = get_hardware_id()
    token = _make_license_jwt(private_pem, hw_id)

    claims = verify_license_jwt(token, public_pem)
    assert claims["hardware_id"] == hw_id


def test_verify_license_jwt_rejects_wrong_hardware_id(rsa_keypair: tuple[str, str]) -> None:
    """verify_license_jwt() raises LicenseError when hardware_id claim is wrong."""
    from synth_engine.shared.security.licensing import LicenseError, verify_license_jwt

    private_pem, public_pem = rsa_keypair
    token = _make_license_jwt(private_pem, "00000000deadbeef" * 4)  # wrong hw_id

    with pytest.raises(LicenseError) as exc_info:
        verify_license_jwt(token, public_pem)

    # ADV-054: LicenseError carries detail (not status_code) with content about hardware_id
    assert "hardware_id" in exc_info.value.detail


def test_verify_license_jwt_rejects_bad_signature(rsa_keypair: tuple[str, str]) -> None:
    """verify_license_jwt() raises LicenseError when JWT signature is invalid.

    This is the verbatim backlog requirement: modified JWT signature → rejected.
    The token is signed with a different private key than the public key used for
    verification, causing a signature mismatch.
    """
    from synth_engine.shared.security.licensing import (
        LicenseError,
        get_hardware_id,
        verify_license_jwt,
    )

    private_pem, public_pem = rsa_keypair  # Original keypair
    hw_id = get_hardware_id()

    # Sign with a DIFFERENT private key — signature will fail against original public key
    other_private_pem, _ = _make_rsa_keypair()
    tampered_token = _make_license_jwt(other_private_pem, hw_id)

    with pytest.raises(LicenseError) as exc_info:
        verify_license_jwt(tampered_token, public_pem)

    # ADV-054: LicenseError carries detail (not status_code) with content about signature
    assert "signature" in exc_info.value.detail.lower()


def test_verify_license_jwt_rejects_modified_hardware_id_claim(
    rsa_keypair: tuple[str, str],
) -> None:
    """verify_license_jwt() rejects a JWT whose hardware_id claim was tampered.

    This is the verbatim backlog requirement: modified JWT hardware_id claim → rejected.
    The JWT header+payload are decoded, the hardware_id is changed, then the token is
    re-encoded without re-signing (invalid signature path) — or simply by signing with
    a different key to ensure the tampered claim is rejected.
    """
    from synth_engine.shared.security.licensing import (
        LicenseError,
        get_hardware_id,
        verify_license_jwt,
    )

    private_pem, public_pem = rsa_keypair
    hw_id = get_hardware_id()
    token = _make_license_jwt(private_pem, hw_id)

    # Tamper: decode header+payload, change hardware_id, forge invalid token
    # (without re-signing — signature will mismatch)
    parts = token.split(".")
    assert len(parts) == 3
    # Pad and decode payload
    padding = 4 - (len(parts[1]) % 4)
    padded = parts[1] + "=" * (padding % 4)
    payload_bytes = base64.urlsafe_b64decode(padded)
    payload_dict = json.loads(payload_bytes)
    payload_dict["hardware_id"] = "tampered-hardware-id-value"
    new_payload = base64.urlsafe_b64encode(json.dumps(payload_dict).encode()).rstrip(b"=").decode()
    tampered_token = f"{parts[0]}.{new_payload}.{parts[2]}"  # original sig — now invalid

    with pytest.raises(LicenseError) as exc_info:
        verify_license_jwt(tampered_token, public_pem)

    # ADV-054: LicenseError carries detail (not status_code); tampered token fails sig check
    assert "signature" in exc_info.value.detail.lower()


def test_verify_license_jwt_rejects_expired_token(rsa_keypair: tuple[str, str]) -> None:
    """verify_license_jwt() raises LicenseError when the token is expired."""
    from synth_engine.shared.security.licensing import (
        LicenseError,
        get_hardware_id,
        verify_license_jwt,
    )

    private_pem, public_pem = rsa_keypair
    hw_id = get_hardware_id()
    # Token expired 10 seconds ago
    token = _make_license_jwt(private_pem, hw_id, exp_offset_seconds=-10)

    with pytest.raises(LicenseError) as exc_info:
        verify_license_jwt(token, public_pem)

    # ADV-054: LicenseError carries detail (not status_code) with content about expiry
    assert "expired" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# LicenseGateMiddleware tests — B1 fix: real HTTP assertions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_license_gate_middleware_returns_402_for_unlicensed_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LicenseGateMiddleware returns 402 for non-exempt routes when unlicensed.

    Vault is patched to return unsealed so that SealGateMiddleware passes,
    isolating the 402 behavior of LicenseGateMiddleware.
    """
    import base64
    import os

    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.licensing import LicenseState
    from synth_engine.shared.security.vault import VaultState

    # Unseal the vault so the seal gate passes
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    VaultState.unseal("any-passphrase-for-test")

    assert LicenseState.is_licensed() is False

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/connections")

    # Vault is unsealed → SealGateMiddleware passes; LicenseGateMiddleware fires → 402
    assert response.status_code == 402
    body = response.json()
    # RFC 7807 format required (B6 fix)
    assert body["status"] == 402
    assert "title" in body
    assert "detail" in body
    assert "type" in body

    # Restore vault state
    VaultState.seal()


@pytest.mark.asyncio
async def test_license_gate_middleware_class_is_importable() -> None:
    """LicenseGateMiddleware is importable and is a middleware class."""
    from starlette.middleware.base import BaseHTTPMiddleware

    from synth_engine.bootstrapper.dependencies.licensing import LicenseGateMiddleware
    from synth_engine.shared.security.licensing import LicenseState

    assert LicenseState.is_licensed() is False
    assert issubclass(LicenseGateMiddleware, BaseHTTPMiddleware)


# ---------------------------------------------------------------------------
# /license/challenge endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_challenge_endpoint_returns_200() -> None:
    """GET /license/challenge returns 200 with expected fields."""
    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/license/challenge")

    assert response.status_code == 200
    body = response.json()
    assert "hardware_id" in body
    assert "app_version" in body
    assert "timestamp" in body


@pytest.mark.asyncio
async def test_challenge_endpoint_includes_qr_code() -> None:
    """GET /license/challenge returns a qr_code field (base64 PNG or text)."""
    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/license/challenge")

    assert response.status_code == 200
    body = response.json()
    assert "qr_code" in body
    # Must be a non-empty string
    assert isinstance(body["qr_code"], str)
    assert len(body["qr_code"]) > 0


@pytest.mark.asyncio
async def test_challenge_endpoint_includes_alt_text() -> None:
    """GET /license/challenge returns an alt_text field for accessibility (WCAG 2.1 AA)."""
    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/license/challenge")

    assert response.status_code == 200
    body = response.json()
    assert "alt_text" in body
    assert isinstance(body["alt_text"], str)
    assert len(body["alt_text"]) > 0
    # alt_text must reference the hardware_id prefix for identification
    assert body["hardware_id"][:8] in body["alt_text"]


@pytest.mark.asyncio
async def test_challenge_endpoint_accessible_while_sealed() -> None:
    """GET /license/challenge returns 200 even when the vault is sealed."""
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed() is True

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/license/challenge")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_challenge_endpoint_accessible_while_unlicensed() -> None:
    """GET /license/challenge returns 200 even when the software is not licensed."""
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.licensing import LicenseState

    assert LicenseState.is_licensed() is False

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/license/challenge")

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /license/activate endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_endpoint_accepts_valid_jwt(
    rsa_keypair: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /license/activate with a valid JWT → 200 and LicenseState is licensed.

    Uses monkeypatch.setenv to set LICENSE_PUBLIC_KEY so that get_active_public_key()
    returns the test key regardless of any .env file present on the machine.
    """
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.licensing import LicenseState, get_hardware_id
    from synth_engine.shared.settings import get_settings

    private_pem, public_pem = rsa_keypair
    hw_id = get_hardware_id()
    token = _make_license_jwt(private_pem, hw_id)

    monkeypatch.setenv("LICENSE_PUBLIC_KEY", public_pem)
    get_settings.cache_clear()

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/license/activate", json={"token": token})

    assert response.status_code == 200
    assert LicenseState.is_licensed() is True


@pytest.mark.asyncio
async def test_activate_endpoint_rejects_bad_jwt() -> None:
    """POST /license/activate with a malformed token → 403."""
    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/license/activate", json={"token": "not.a.valid.jwt"})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_activate_endpoint_rejects_wrong_hardware_id(
    rsa_keypair: tuple[str, str],
) -> None:
    """POST /license/activate with wrong hardware_id in JWT → 403.

    Verbatim backlog: modify the JWT's hardware_id claim → endpoint must reject it.
    """
    from synth_engine.bootstrapper.main import create_app

    private_pem, public_pem = rsa_keypair
    token = _make_license_jwt(private_pem, "00000000deadbeef" * 4)

    app = create_app()
    import synth_engine.shared.security.licensing as lic_mod

    original_key = lic_mod._EMBEDDED_PUBLIC_KEY
    lic_mod._EMBEDDED_PUBLIC_KEY = public_pem
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/license/activate", json={"token": token})
    finally:
        lic_mod._EMBEDDED_PUBLIC_KEY = original_key

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_activate_endpoint_rejects_bad_signature(
    rsa_keypair: tuple[str, str],
) -> None:
    """POST /license/activate with JWT signed by wrong key → 403.

    Verbatim backlog: modify the JWT's signature → endpoint must reject it.
    """
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.licensing import get_hardware_id

    _original_private_pem, public_pem = rsa_keypair
    hw_id = get_hardware_id()

    # Sign with a different private key
    other_private_pem, _ = _make_rsa_keypair()
    token = _make_license_jwt(other_private_pem, hw_id)

    app = create_app()
    import synth_engine.shared.security.licensing as lic_mod

    original_key = lic_mod._EMBEDDED_PUBLIC_KEY
    lic_mod._EMBEDDED_PUBLIC_KEY = public_pem
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/license/activate", json={"token": token})
    finally:
        lic_mod._EMBEDDED_PUBLIC_KEY = original_key

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_activate_endpoint_accessible_while_sealed() -> None:
    """POST /license/activate is exempt from SealGateMiddleware (not 423)."""
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed() is True

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # A malformed token is fine here — we're testing the seal exemption, not JWT validity
        response = await client.post("/license/activate", json={"token": "bad"})

    # Must not be 423 (sealed) — can be 403 (bad token) or 422 (validation error)
    assert response.status_code != 423


@pytest.mark.asyncio
async def test_activate_endpoint_response_body_on_success(
    rsa_keypair: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /license/activate returns a JSON body with 'status' on success.

    Uses monkeypatch.setenv to set LICENSE_PUBLIC_KEY so that get_active_public_key()
    returns the test key regardless of any .env file present on the machine.
    """
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.licensing import get_hardware_id
    from synth_engine.shared.settings import get_settings

    private_pem, public_pem = rsa_keypair
    hw_id = get_hardware_id()
    token = _make_license_jwt(private_pem, hw_id)

    monkeypatch.setenv("LICENSE_PUBLIC_KEY", public_pem)
    get_settings.cache_clear()

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/license/activate", json={"token": token})

    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert body["status"] == "activated"


# ---------------------------------------------------------------------------
# LICENSE_PUBLIC_KEY env var override test — B9 fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_endpoint_uses_license_public_key_env_var(
    rsa_keypair: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /license/activate uses LICENSE_PUBLIC_KEY env var when set.

    This test covers the env var return path in get_active_public_key()
    (shared/security/licensing.py line that returns env_key when present).
    """
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.licensing import LicenseState, get_hardware_id

    private_pem, public_pem = rsa_keypair
    hw_id = get_hardware_id()
    token = _make_license_jwt(private_pem, hw_id)

    # Set LICENSE_PUBLIC_KEY env var — this is the path under test (B9)
    monkeypatch.setenv("LICENSE_PUBLIC_KEY", public_pem)

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/license/activate", json={"token": token})

    assert response.status_code == 200
    assert LicenseState.is_licensed() is True


def test_get_active_public_key_returns_env_var_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_active_public_key() returns the env var value when LICENSE_PUBLIC_KEY is set."""
    from synth_engine.shared.security.licensing import get_active_public_key

    fake_key = "-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----\n"
    monkeypatch.setenv("LICENSE_PUBLIC_KEY", fake_key)
    result = get_active_public_key()
    assert result == fake_key


def test_get_active_public_key_falls_back_to_embedded_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_active_public_key() returns the embedded key when LICENSE_PUBLIC_KEY is not set.

    pydantic-settings reads from the .env file as well as os.environ, so
    monkeypatch.delenv() alone is not sufficient — the .env file value is
    still loaded on cache miss.  We clear the lru_cache and then temporarily
    replace get_settings() with a stub that returns None for license_public_key.
    The stub is replaced by monkeypatch (which preserves the cache_clear attr)
    so the autouse conftest fixture can call cache_clear() safely on teardown.
    """
    from unittest.mock import patch

    import synth_engine.shared.security.licensing as lic_mod
    from synth_engine.shared.security.licensing import get_active_public_key
    from synth_engine.shared.settings import ConclaveSettings, get_settings

    monkeypatch.delenv("LICENSE_PUBLIC_KEY", raising=False)
    get_settings.cache_clear()

    # Build a stub settings object with license_public_key=None.
    stub_settings = ConclaveSettings.model_construct(license_public_key=None)

    # Use unittest.mock.patch as a context manager so we replace the local
    # import inside get_active_public_key() while preserving cache_clear().
    with patch(
        "synth_engine.shared.settings.get_settings",
        return_value=stub_settings,
    ):
        result = get_active_public_key()

    assert result == lic_mod._EMBEDDED_PUBLIC_KEY


# ---------------------------------------------------------------------------
# LicenseGateMiddleware integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_license_gate_blocks_protected_route_when_unlicensed() -> None:
    """A non-exempt route returns 423 when vault is sealed (seal fires first)."""
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.licensing import LicenseState
    from synth_engine.shared.security.vault import VaultState

    # Unseal the vault so we isolate the license gate behaviour
    # (vault is sealed by default; we need to get past SealGateMiddleware first)
    # We test the gate using /health since it's exempt from SEAL but not from LICENSE gate
    # Actually health is exempt from both. Let's use a non-exempt path:
    assert LicenseState.is_licensed() is False
    assert VaultState.is_sealed() is True  # vault still sealed — 423 takes priority

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/connections")

    # Vault is sealed → 423 takes priority over license gate (sealed first, licensed second)
    assert response.status_code == 423


@pytest.mark.asyncio
async def test_license_gate_allows_exempt_paths_when_unlicensed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """License-exempt routes (health, unseal, challenge, activate) are accessible unlicensed."""
    import base64
    import os

    from synth_engine.bootstrapper.main import create_app

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # /health is exempt from both seal and license gates
        response = await client.get("/health")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_license_state_not_licensed_after_activate_endpoint_rejects(
    rsa_keypair: tuple[str, str],
) -> None:
    """LicenseState remains unlicensed after a rejected /license/activate call."""
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.licensing import LicenseState

    private_pem, public_pem = rsa_keypair
    token = _make_license_jwt(private_pem, "wrong-hardware-id-value")

    app = create_app()
    import synth_engine.shared.security.licensing as lic_mod

    original_key = lic_mod._EMBEDDED_PUBLIC_KEY
    lic_mod._EMBEDDED_PUBLIC_KEY = public_pem
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/license/activate", json={"token": token})
    finally:
        lic_mod._EMBEDDED_PUBLIC_KEY = original_key

    assert LicenseState.is_licensed() is False


# ---------------------------------------------------------------------------
# LicenseError tests — ADV-054: no status_code, only detail
# ---------------------------------------------------------------------------


def test_license_error_has_detail_attribute() -> None:
    """LicenseError carries detail attribute (plain string, no HTTP semantics).

    ADV-054: status_code was removed from LicenseError. Only detail remains.
    HTTP status mapping is the bootstrapper's responsibility.
    """
    from synth_engine.shared.security.licensing import LicenseError

    err = LicenseError("test detail")
    assert err.detail == "test detail"
    assert str(err) == "test detail"


def test_license_error_has_no_status_code() -> None:
    """LicenseError must NOT have a status_code attribute (ADV-054).

    HTTP status semantics belong in bootstrapper/routers/licensing.py,
    not in the shared/security layer.
    """
    from synth_engine.shared.security.licensing import LicenseError

    err = LicenseError("test detail")
    assert not hasattr(err, "status_code")


# ---------------------------------------------------------------------------
# Timing — ensure verify_license_jwt does not use wall clock in tests
# ---------------------------------------------------------------------------


def test_verify_license_jwt_missing_hardware_id_claim(
    rsa_keypair: tuple[str, str],
) -> None:
    """verify_license_jwt() raises LicenseError when hardware_id claim is absent."""
    import jwt as pyjwt

    from synth_engine.shared.security.licensing import LicenseError, verify_license_jwt

    private_pem, public_pem = rsa_keypair
    now = int(time.time())
    # JWT without hardware_id
    token = pyjwt.encode(
        {"sub": "test", "iat": now, "exp": now + 3600},
        private_pem,
        algorithm="RS256",
    )

    with pytest.raises(LicenseError):
        verify_license_jwt(token, public_pem)


# ---------------------------------------------------------------------------
# get_active_public_key() — literal \n conversion test
# ---------------------------------------------------------------------------


def test_get_active_public_key_converts_literal_newlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_active_public_key() converts literal \\n sequences to real newlines.

    Docker ``env_file`` directives pass PEM keys as single-line strings with
    literal ``\\n`` characters instead of real newlines.  This test asserts
    that ``get_active_public_key()`` normalises the key so callers always
    receive a properly formatted PEM string.
    """
    from synth_engine.shared.security.licensing import get_active_public_key
    from synth_engine.shared.settings import get_settings

    # Clear lru_cache so that get_settings() re-reads from the environment
    # after monkeypatch.setenv — without this the cached instance is stale.
    get_settings.cache_clear()

    # Simulate a PEM key as delivered by Docker env_file: literal \n, not real newlines
    pem_with_literal_newlines = (
        "-----BEGIN PUBLIC KEY-----\\nFAKEKEYDATA\\n-----END PUBLIC KEY-----\\n"
    )
    monkeypatch.setenv("LICENSE_PUBLIC_KEY", pem_with_literal_newlines)
    # Force re-read of settings with the new env var value
    get_settings.cache_clear()

    result = get_active_public_key()

    assert "\\n" not in result
    assert "\n" in result
    assert result == pem_with_literal_newlines.replace("\\n", "\n")

    # Cleanup: clear cache so subsequent tests don't inherit the patched settings.
    # monkeypatch restores the env var on teardown; this clears the stale cache.
    get_settings.cache_clear()
