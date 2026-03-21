"""Unit tests for Cryptographic Shredding & Re-Keying API router.

Tests verify:
- POST /security/shred — zeroizes the master KEK, renders all ciphertext unrecoverable.
- POST /security/keys/rotate — enqueues a Huey task, returns 202 Accepted.
- Audit events are emitted on both operations.
- RFC 7807 error responses on failure paths.
- Both handlers are async def.
- DATABASE_URL="" edge case: 202 still returned, task still enqueued (ADV-055).

CONSTITUTION Priority 3: TDD — Red Phase
Task: P5-T5.5 — Cryptographic Shredding & Re-Keying API
"""

from __future__ import annotations

import base64
import os
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from synth_engine.shared.security.vault import VaultState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_vault() -> Generator[None]:
    """Seal and clear vault KEK after every test."""
    yield
    VaultState.reset()


@pytest.fixture
def vault_salt(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provision VAULT_SEAL_SALT for vault unseal tests."""
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    return salt


@pytest.fixture
def unsealed_vault(vault_salt: str) -> None:
    """Unseal the vault with a known passphrase."""
    VaultState.unseal("shred-test-passphrase")


@pytest.fixture
def security_client() -> TestClient:
    """Build a minimal FastAPI app with only the security router."""
    from synth_engine.bootstrapper.routers.security import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# /security/shred — seal (shred) endpoint
# ---------------------------------------------------------------------------


def test_shred_seals_vault(
    security_client: TestClient,
    unsealed_vault: None,
) -> None:
    """POST /security/shred must zeroize the KEK and seal the vault.

    Arrange: unseal the vault.
    Act: call POST /security/shred.
    Assert: vault is sealed afterwards; response body confirms the shred.
    """
    assert not VaultState.is_sealed(), "vault must be unsealed before shred"

    response = security_client.post("/security/shred")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "shredded"
    assert VaultState.is_sealed(), "vault must be sealed after shred"


def test_shred_already_sealed_returns_200(security_client: TestClient) -> None:
    """POST /security/shred on an already-sealed vault must still return 200.

    VaultState.seal() is idempotent-safe; calling it while already sealed is
    a no-op.  The endpoint must handle this gracefully.
    """
    assert VaultState.is_sealed(), "vault must be sealed for this test"

    response = security_client.post("/security/shred")

    assert response.status_code == 200
    assert response.json()["status"] == "shredded"


def test_shred_emits_audit_event(
    security_client: TestClient,
    unsealed_vault: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /security/shred must emit a CRYPTO_SHRED audit event."""
    audit_key = os.urandom(32).hex()
    monkeypatch.setenv("AUDIT_KEY", audit_key)

    mock_audit = MagicMock()
    with patch(
        "synth_engine.bootstrapper.routers.security.get_audit_logger",
        return_value=mock_audit,
    ):
        response = security_client.post("/security/shred")

    assert response.status_code == 200
    mock_audit.log_event.assert_called_once()
    call_kwargs = mock_audit.log_event.call_args.kwargs
    assert call_kwargs["event_type"] == "CRYPTO_SHRED"
    assert call_kwargs["action"] == "shred"


def test_shred_audit_failure_does_not_block(
    security_client: TestClient,
    unsealed_vault: None,
) -> None:
    """POST /security/shred must complete even if audit logging raises.

    Audit is best-effort; a misconfigured AUDIT_KEY must not prevent shredding.
    """
    with patch(
        "synth_engine.bootstrapper.routers.security.get_audit_logger",
        side_effect=ValueError("AUDIT_KEY not configured"),
    ):
        response = security_client.post("/security/shred")

    assert response.status_code == 200
    assert VaultState.is_sealed()


# ---------------------------------------------------------------------------
# /security/keys/rotate — key rotation endpoint
# ---------------------------------------------------------------------------


def test_rotate_enqueues_huey_task_and_returns_202(
    security_client: TestClient,
    unsealed_vault: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /security/keys/rotate must enqueue a Huey task and return 202.

    Arrange: unsealed vault + valid rotate request body.
    Act: call POST /security/keys/rotate with new_passphrase.
    Assert: HTTP 202, status == "accepted", task enqueued.
    """
    monkeypatch.setenv("VAULT_SEAL_SALT", base64.urlsafe_b64encode(os.urandom(16)).decode())

    with patch("synth_engine.bootstrapper.routers.security.rotate_ale_keys_task") as mock_task:
        mock_result = MagicMock()
        mock_task.return_value = mock_result

        response = security_client.post(
            "/security/keys/rotate",
            json={"new_passphrase": "new-secure-passphrase"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    mock_task.assert_called_once()


def test_rotate_sealed_vault_returns_423(security_client: TestClient) -> None:
    """POST /security/keys/rotate with a sealed vault must return 423.

    Key rotation requires an unsealed vault — without the current KEK,
    existing ciphertext cannot be re-encrypted.
    """
    assert VaultState.is_sealed()

    response = security_client.post(
        "/security/keys/rotate",
        json={"new_passphrase": "some-passphrase"},
    )

    assert response.status_code == 423


def test_rotate_missing_passphrase_returns_422(
    security_client: TestClient,
    unsealed_vault: None,
) -> None:
    """POST /security/keys/rotate without new_passphrase must return 422."""
    response = security_client.post("/security/keys/rotate", json={})

    assert response.status_code == 422


def test_rotate_emits_audit_event(
    security_client: TestClient,
    unsealed_vault: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /security/keys/rotate must emit a KEY_ROTATION_REQUESTED audit event."""
    audit_key = os.urandom(32).hex()
    monkeypatch.setenv("AUDIT_KEY", audit_key)

    mock_audit = MagicMock()
    with (
        patch(
            "synth_engine.bootstrapper.routers.security.get_audit_logger",
            return_value=mock_audit,
        ),
        patch("synth_engine.bootstrapper.routers.security.rotate_ale_keys_task"),
    ):
        response = security_client.post(
            "/security/keys/rotate",
            json={"new_passphrase": "new-passphrase"},
        )

    assert response.status_code == 202
    mock_audit.log_event.assert_called_once()
    call_kwargs = mock_audit.log_event.call_args.kwargs
    assert call_kwargs["event_type"] == "KEY_ROTATION_REQUESTED"
    assert call_kwargs["action"] == "rotate"


def test_rotate_audit_failure_does_not_block(
    security_client: TestClient,
    unsealed_vault: None,
) -> None:
    """POST /security/keys/rotate must complete even if audit logging raises."""
    with (
        patch(
            "synth_engine.bootstrapper.routers.security.get_audit_logger",
            side_effect=ValueError("AUDIT_KEY not configured"),
        ),
        patch("synth_engine.bootstrapper.routers.security.rotate_ale_keys_task"),
    ):
        response = security_client.post(
            "/security/keys/rotate",
            json={"new_passphrase": "new-passphrase"},
        )

    assert response.status_code == 202


def test_rotate_empty_database_url_returns_202_and_enqueues_task(
    security_client: TestClient,
    unsealed_vault: None,
) -> None:
    """POST /security/keys/rotate with DATABASE_URL="" must still return 202.

    When DATABASE_URL is empty or unset, the endpoint logs a warning and
    still enqueues the Huey task (the task will fail in the worker, but the
    HTTP response is 202 — the failure is a worker concern, not an API
    concern).

    This is a defense-in-depth edge case: air-gapped deployments always have
    DATABASE_URL configured, but the endpoint must not panic if it is absent.

    ADV-055 drain: exercises the DATABASE_URL="" branch in security.py.
    """
    # Patch get_settings at the source to return a mock with database_url="".
    # Using monkeypatch.delenv is insufficient because pydantic-settings also
    # reads from the .env file; patching the function bypasses both sources.
    mock_settings = MagicMock()
    mock_settings.database_url = ""

    with (
        patch(
            "synth_engine.shared.settings.get_settings",
            return_value=mock_settings,
        ),
        patch("synth_engine.bootstrapper.routers.security.rotate_ale_keys_task") as mock_task,
    ):
        response = security_client.post(
            "/security/keys/rotate",
            json={"new_passphrase": "new-secure-passphrase"},
        )

    # The endpoint must return 202 — the missing DATABASE_URL is a warning, not an error
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"

    # The Huey task must still be enqueued — the worker handles the failure
    mock_task.assert_called_once()
    call_args = mock_task.call_args
    # First positional arg is database_url — it must be the empty string
    database_url_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("database_url")
    assert database_url_arg == "", (
        "database_url passed to task must be '' when DATABASE_URL is unset, "
        f"got {database_url_arg!r}"
    )


# ---------------------------------------------------------------------------
# Route handler async verification (Known Failure Pattern #3)
# ---------------------------------------------------------------------------


def test_shred_handler_is_async() -> None:
    """The shred route handler must be an async function (not sync def)."""
    import asyncio

    from synth_engine.bootstrapper.routers import security as sec_mod

    # Find the route function — it's the route function on the router
    shred_routes = [r for r in sec_mod.router.routes if getattr(r, "path", "") == "/security/shred"]
    assert shred_routes, "shred route must be registered"
    handler = shred_routes[0].endpoint  # type: ignore[attr-defined]
    assert asyncio.iscoroutinefunction(handler), "POST /security/shred handler must be async def"


def test_rotate_handler_is_async() -> None:
    """The rotate route handler must be an async function (not sync def)."""
    import asyncio

    from synth_engine.bootstrapper.routers import security as sec_mod

    rotate_routes = [
        r for r in sec_mod.router.routes if getattr(r, "path", "") == "/security/keys/rotate"
    ]
    assert rotate_routes, "rotate route must be registered"
    handler = rotate_routes[0].endpoint  # type: ignore[attr-defined]
    assert asyncio.iscoroutinefunction(handler), (
        "POST /security/keys/rotate handler must be async def"
    )
