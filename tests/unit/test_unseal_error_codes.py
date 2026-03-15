"""Unit tests for structured error codes on the /unseal endpoint.

ADV-018: The /unseal endpoint must return structured error_code values
to allow the frontend to display context-specific error messages rather
than the raw ValueError text.

CONSTITUTION Priority 0: Security
Task: P5-T5.3 — Build Accessible React SPA & "Vault Unseal"
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture()
def _sealed_app():  # type: ignore[no-untyped-def]
    """Create a fresh FastAPI app with the vault explicitly sealed."""
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.vault import VaultState

    VaultState._is_sealed = True  # noqa: SLF001 — test access required
    VaultState._kek = None  # noqa: SLF001 — clear any residual key
    app = create_app()
    yield app
    # Restore sealed state after each test
    VaultState._is_sealed = True  # noqa: SLF001
    VaultState._kek = None  # noqa: SLF001


@pytest.mark.asyncio
async def test_unseal_empty_passphrase_returns_empty_passphrase_code(
    _sealed_app,  # type: ignore[no-untyped-def]
) -> None:
    """POST /unseal with empty passphrase returns EMPTY_PASSPHRASE error code.

    VaultState.unseal() raises ValueError('Passphrase must not be empty.')
    The endpoint maps this to error_code='EMPTY_PASSPHRASE'.
    """
    async with AsyncClient(
        transport=ASGITransport(app=_sealed_app), base_url="http://test"
    ) as client:
        response = await client.post("/unseal", json={"passphrase": ""})

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "EMPTY_PASSPHRASE"
    assert "detail" in body


@pytest.mark.asyncio
async def test_unseal_already_unsealed_returns_already_unsealed_code(
    _sealed_app,  # type: ignore[no-untyped-def]
) -> None:
    """POST /unseal when vault already unsealed returns ALREADY_UNSEALED.

    VaultState.unseal() raises ValueError('Vault is already unsealed...')
    The endpoint maps this to error_code='ALREADY_UNSEALED'.
    """
    # Simulate already-unsealed by patching VaultState.unseal to raise
    from synth_engine.shared.security.vault import VaultState

    with patch.object(
        VaultState,
        "unseal",
        side_effect=ValueError("Vault is already unsealed. Call seal() before unsealing again."),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=_sealed_app), base_url="http://test"
        ) as client:
            response = await client.post("/unseal", json={"passphrase": "some-passphrase"})

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "ALREADY_UNSEALED"
    assert "detail" in body


@pytest.mark.asyncio
async def test_unseal_missing_salt_returns_config_error_code(
    _sealed_app,  # type: ignore[no-untyped-def]
) -> None:
    """POST /unseal with no VAULT_SEAL_SALT set returns CONFIG_ERROR.

    VaultState.unseal() raises ValueError('VAULT_SEAL_SALT environment
    variable is not set. ...') The endpoint maps this to
    error_code='CONFIG_ERROR'.
    """
    from synth_engine.shared.security.vault import VaultState

    with patch.object(
        VaultState,
        "unseal",
        side_effect=ValueError(
            "VAULT_SEAL_SALT environment variable is not set. "
            "Set it to a base64-encoded 16-byte minimum salt."
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=_sealed_app), base_url="http://test"
        ) as client:
            response = await client.post("/unseal", json={"passphrase": "some-passphrase"})

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "CONFIG_ERROR"
    assert "detail" in body


@pytest.mark.asyncio
async def test_unseal_short_salt_returns_config_error_code(
    _sealed_app,  # type: ignore[no-untyped-def]
) -> None:
    """POST /unseal with a salt that is too short returns CONFIG_ERROR.

    VaultState.unseal() raises ValueError('VAULT_SEAL_SALT must decode to
    at least 16 bytes; got N bytes.') The endpoint maps this to
    error_code='CONFIG_ERROR'.
    """
    from synth_engine.shared.security.vault import VaultState

    with patch.object(
        VaultState,
        "unseal",
        side_effect=ValueError("VAULT_SEAL_SALT must decode to at least 16 bytes; got 8 bytes."),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=_sealed_app), base_url="http://test"
        ) as client:
            response = await client.post("/unseal", json={"passphrase": "some-passphrase"})

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "CONFIG_ERROR"
    assert "detail" in body


@pytest.mark.asyncio
async def test_unseal_success_returns_200_with_status_unsealed(
    _sealed_app,  # type: ignore[no-untyped-def]
) -> None:
    """POST /unseal with valid params returns 200 and status=unsealed.

    The success path must not be broken by the error code changes.
    """
    from synth_engine.shared.security.vault import VaultState

    with patch.object(VaultState, "unseal", return_value=None):
        async with AsyncClient(
            transport=ASGITransport(app=_sealed_app), base_url="http://test"
        ) as client:
            response = await client.post("/unseal", json={"passphrase": "valid-passphrase"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unsealed"
    assert "error_code" not in body


@pytest.mark.asyncio
async def test_unseal_error_response_contains_both_error_code_and_detail(
    _sealed_app,  # type: ignore[no-untyped-def]
) -> None:
    """Error responses must contain BOTH error_code and detail fields.

    The frontend relies on error_code for error type differentiation
    and detail for logging/display to admins.
    """
    async with AsyncClient(
        transport=ASGITransport(app=_sealed_app), base_url="http://test"
    ) as client:
        response = await client.post("/unseal", json={"passphrase": ""})

    assert response.status_code == 400
    body = response.json()
    assert "error_code" in body
    assert "detail" in body
    # Both must be non-empty strings
    assert isinstance(body["error_code"], str) and body["error_code"]
    assert isinstance(body["detail"], str) and body["detail"]
