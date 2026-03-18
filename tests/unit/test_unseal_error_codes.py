"""Unit tests for structured error responses on the /unseal endpoint.

ADV-018: The /unseal endpoint must return structured error responses
to allow the frontend to display context-specific error messages rather
than the raw ValueError text.

T29.3: Upgraded from legacy ``error_code``/``detail`` format to RFC 7807
Problem Details format.  The frontend ``RFC7807Toast`` component reads
``title`` (as heading) and ``detail`` (as body) — the same fields supplied
by the new format.  The legacy ``error_code`` field has been replaced by the
RFC 7807 ``title`` field which provides equivalent differentiation capability.

CONSTITUTION Priority 0: Security
Task: P5-T5.3 — Build Accessible React SPA & "Vault Unseal"
Task: P29-T29.3 — Error Message Audience Differentiation
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def sealed_app() -> Generator[Any]:
    """Create a fresh FastAPI app with the vault explicitly sealed.

    Yields:
        A FastAPI application instance with the vault in sealed state.
    """
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.vault import VaultState

    VaultState._is_sealed = True  # type: ignore[attr-defined]  # test access required
    VaultState._kek = None  # type: ignore[attr-defined]  # clear any residual key
    app = create_app()
    yield app
    # Restore sealed state after each test
    VaultState._is_sealed = True  # type: ignore[attr-defined]
    VaultState._kek = None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_unseal_empty_passphrase_returns_empty_passphrase_code(
    sealed_app: Any,
) -> None:
    """POST /unseal with empty passphrase returns RFC 7807 with 'Empty Passphrase' title.

    T29.3: Upgraded from legacy error_code='EMPTY_PASSPHRASE' to RFC 7807 format.
    VaultState.unseal() raises VaultEmptyPassphraseError.
    The endpoint maps this to title='Empty Passphrase' per OPERATOR_ERROR_MAP.
    """
    async with AsyncClient(
        transport=ASGITransport(app=sealed_app), base_url="http://test"
    ) as client:
        response = await client.post("/unseal", json={"passphrase": ""})

    assert response.status_code == 400
    body = response.json()
    # RFC 7807 format
    assert body["title"] == "Empty Passphrase"
    assert "detail" in body
    assert "type" in body
    assert "status" in body


@pytest.mark.asyncio
async def test_unseal_already_unsealed_returns_already_unsealed_code(
    sealed_app: Any,
) -> None:
    """POST /unseal when vault already unsealed returns RFC 7807 'Vault Already Unsealed'.

    T29.3: Upgraded from legacy error_code='ALREADY_UNSEALED' to RFC 7807 format.
    VaultState.unseal() raises VaultAlreadyUnsealedError.
    The endpoint returns title='Vault Already Unsealed' per RFC 7807.
    """
    from synth_engine.shared.security.vault import VaultAlreadyUnsealedError, VaultState

    with patch.object(
        VaultState,
        "unseal",
        side_effect=VaultAlreadyUnsealedError(
            "Vault is already unsealed. Call seal() before unsealing again."
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=sealed_app), base_url="http://test"
        ) as client:
            response = await client.post("/unseal", json={"passphrase": "some-passphrase"})

    assert response.status_code == 400
    body = response.json()
    assert body["title"] == "Vault Already Unsealed"
    assert "detail" in body
    assert "type" in body
    assert "status" in body


@pytest.mark.asyncio
async def test_unseal_missing_salt_returns_config_error_code(
    sealed_app: Any,
) -> None:
    """POST /unseal with no VAULT_SEAL_SALT set returns RFC 7807 'Vault Configuration Error'.

    T29.3: Upgraded from legacy error_code='CONFIG_ERROR' to RFC 7807 format.
    VaultState.unseal() raises VaultConfigError.
    The endpoint maps this to title='Vault Configuration Error' per OPERATOR_ERROR_MAP.
    """
    from synth_engine.shared.security.vault import VaultConfigError, VaultState

    with patch.object(
        VaultState,
        "unseal",
        side_effect=VaultConfigError(
            "VAULT_SEAL_SALT environment variable is not set. "
            "Set it to a base64-encoded 16-byte minimum salt."
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=sealed_app), base_url="http://test"
        ) as client:
            response = await client.post("/unseal", json={"passphrase": "some-passphrase"})

    assert response.status_code == 400
    body = response.json()
    assert body["title"] == "Vault Configuration Error"
    assert "detail" in body
    assert "type" in body
    assert "status" in body


@pytest.mark.asyncio
async def test_unseal_short_salt_returns_config_error_code(
    sealed_app: Any,
) -> None:
    """POST /unseal with a salt that is too short returns RFC 7807 'Vault Configuration Error'.

    T29.3: Upgraded from legacy error_code='CONFIG_ERROR' to RFC 7807 format.
    VaultState.unseal() raises VaultConfigError.
    The endpoint maps this to title='Vault Configuration Error' per OPERATOR_ERROR_MAP.
    """
    from synth_engine.shared.security.vault import VaultConfigError, VaultState

    with patch.object(
        VaultState,
        "unseal",
        side_effect=VaultConfigError(
            "VAULT_SEAL_SALT must decode to at least 16 bytes; got 8 bytes."
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=sealed_app), base_url="http://test"
        ) as client:
            response = await client.post("/unseal", json={"passphrase": "some-passphrase"})

    assert response.status_code == 400
    body = response.json()
    assert body["title"] == "Vault Configuration Error"
    assert "detail" in body
    assert "type" in body
    assert "status" in body


@pytest.mark.asyncio
async def test_unseal_success_returns_200_with_status_unsealed(
    sealed_app: Any,
) -> None:
    """POST /unseal with valid params returns 200 and status=unsealed.

    The success path must not be broken by the error format changes.
    """
    from synth_engine.shared.security.vault import VaultState

    with patch.object(VaultState, "unseal", return_value=None):
        async with AsyncClient(
            transport=ASGITransport(app=sealed_app), base_url="http://test"
        ) as client:
            response = await client.post("/unseal", json={"passphrase": "valid-passphrase"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unsealed"
    assert "error_code" not in body


@pytest.mark.asyncio
async def test_unseal_error_response_contains_both_error_code_and_detail(
    sealed_app: Any,
) -> None:
    """Error responses must contain RFC 7807 title and detail fields.

    T29.3: Replaces the old test that checked for 'error_code' + 'detail'.
    The frontend RFC7807Toast component reads 'title' (heading) and 'detail'
    (body) — the RFC 7807 format provides equivalent differentiation capability.
    Both must be non-empty strings.
    """
    async with AsyncClient(
        transport=ASGITransport(app=sealed_app), base_url="http://test"
    ) as client:
        response = await client.post("/unseal", json={"passphrase": ""})

    assert response.status_code == 400
    body = response.json()
    # RFC 7807 format provides title + detail instead of error_code + detail
    assert "title" in body
    assert "detail" in body
    # Both must be non-empty strings
    assert isinstance(body["title"], str)
    assert body["title"] != ""
    assert isinstance(body["detail"], str)
    assert body["detail"] != ""
    # Must NOT use legacy error_code format
    assert "error_code" not in body
