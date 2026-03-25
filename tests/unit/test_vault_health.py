"""Unit tests for vault seal status in readiness probe and /health/vault endpoint.

Covers:
- Attack/negative tests: sealed vault returns 503, vault_sealed: true
- Feature tests: after unseal returns 200, vault_sealed: false
- Re-seal: returns 503 again
- /health/vault endpoint reports correct seal state and opaque worker_id UUID (ADV-P55-01)

CONSTITUTION Priority 0: Security — vault state MUST block readiness when sealed
CONSTITUTION Priority 3: TDD — attack tests first
Task: T55.1 — Vault State Health Endpoint & Multi-Worker Coordination
"""

from __future__ import annotations

import base64
import os
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_vault_state() -> Generator[None]:
    """Reset VaultState class-level state after each test for isolation."""
    yield
    try:
        from synth_engine.shared.security.vault import VaultState

        VaultState.reset()
    except ImportError:
        pass


@pytest.fixture
def vault_salt_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provision VAULT_SEAL_SALT in the environment and return the raw value."""
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    return salt


@pytest.fixture
def app() -> Any:
    """Create a minimal FastAPI test application with the health router mounted."""
    from fastapi import FastAPI

    from synth_engine.bootstrapper.routers.health import router

    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


def _mock_all_deps_ok() -> Any:
    """Return a context manager that patches all dependency checks to succeed."""
    return patch(
        "synth_engine.bootstrapper.routers.health._run_check_with_timeout",
        new_callable=lambda: lambda: AsyncMock(return_value=True),
    )


# ---------------------------------------------------------------------------
# Attack tests — SEALED state MUST block readiness (commit these first)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ready_returns_503_when_vault_is_sealed(app: Any) -> None:
    """Sealed worker MUST return 503 on /ready even if all deps are healthy.

    A sealed worker has no access to encryption keys and MUST NOT accept
    traffic.  The readiness probe must advertise this to the load balancer
    so sealed workers are excluded from the pool.
    """
    # VaultState starts sealed by default
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed(), "Pre-condition: vault must be sealed"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                return_value=True,
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                return_value=True,
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_minio",
                return_value=None,
            ),
        ):
            response = await client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["vault_sealed"] is True


@pytest.mark.asyncio
async def test_ready_response_body_contains_vault_sealed_true_when_sealed(
    app: Any,
) -> None:
    """The /ready 503 body MUST include vault_sealed: true.

    Orchestrators and operators need to distinguish a sealed-worker 503
    from a dependency-failure 503.
    """
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed(), "Pre-condition: vault must be sealed"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                return_value=True,
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                return_value=True,
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_minio",
                return_value=None,
            ),
        ):
            response = await client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert "vault_sealed" in body
    assert body["vault_sealed"] is True
    assert body["status"] == "degraded"


@pytest.mark.asyncio
async def test_health_vault_returns_vault_sealed_true_when_sealed(app: Any) -> None:
    """/health/vault MUST return vault_sealed: true when vault is sealed."""
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed(), "Pre-condition: vault must be sealed"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/vault")

    assert response.status_code == 200
    body = response.json()
    assert body["vault_sealed"] is True


@pytest.mark.asyncio
async def test_health_vault_returns_worker_id(app: Any) -> None:
    """/health/vault MUST include worker_id as an opaque UUID string (ADV-P55-01).

    The worker_id is a UUID generated once at import time. It is stable
    within a process lifetime but opaque — it does not leak process topology.
    """
    import uuid as uuid_module

    from synth_engine.bootstrapper.routers.health import _WORKER_ID

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/vault")

    assert response.status_code == 200
    body = response.json()
    assert "worker_id" in body
    assert body["worker_id"] == _WORKER_ID
    # Verify it is a valid UUID string
    parsed = uuid_module.UUID(body["worker_id"])
    assert str(parsed) == body["worker_id"]


@pytest.mark.asyncio
async def test_ready_returns_503_again_after_reseal(app: Any, vault_salt_env: str) -> None:
    """After re-sealing, /ready MUST return 503 again.

    A worker that was unsealed and then re-sealed is no longer safe to
    accept traffic.
    """
    from synth_engine.shared.security.vault import VaultState

    # Unseal first
    VaultState.unseal("correct-horse-battery-staple")
    assert not VaultState.is_sealed(), "Pre-condition: vault must be unsealed"

    # Re-seal
    VaultState.seal()
    assert VaultState.is_sealed(), "Pre-condition: vault must be sealed after re-seal"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                return_value=True,
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                return_value=True,
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_minio",
                return_value=None,
            ),
        ):
            response = await client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["vault_sealed"] is True


# ---------------------------------------------------------------------------
# Feature tests — UNSEALED state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ready_returns_200_when_vault_unsealed_and_deps_healthy(
    app: Any, vault_salt_env: str
) -> None:
    """After unseal, /ready MUST return 200 when all deps are healthy."""
    from synth_engine.shared.security.vault import VaultState

    VaultState.unseal("correct-horse-battery-staple")
    assert not VaultState.is_sealed(), "Pre-condition: vault must be unsealed"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                return_value=True,
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                return_value=True,
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_minio",
                return_value=None,
            ),
        ):
            response = await client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["vault_sealed"] is False
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_ready_response_body_contains_vault_sealed_false_when_unsealed(
    app: Any, vault_salt_env: str
) -> None:
    """The /ready 200 body MUST include vault_sealed: false after unseal."""
    from synth_engine.shared.security.vault import VaultState

    VaultState.unseal("correct-horse-battery-staple")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with (
            patch(
                "synth_engine.bootstrapper.routers.health._check_database",
                return_value=True,
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_redis",
                return_value=True,
            ),
            patch(
                "synth_engine.bootstrapper.routers.health._check_minio",
                return_value=None,
            ),
        ):
            response = await client.get("/ready")

    body = response.json()
    assert "vault_sealed" in body
    assert body["vault_sealed"] is False


@pytest.mark.asyncio
async def test_health_vault_returns_vault_sealed_false_after_unseal(
    app: Any, vault_salt_env: str
) -> None:
    """/health/vault MUST return vault_sealed: false after unseal."""
    from synth_engine.shared.security.vault import VaultState

    VaultState.unseal("correct-horse-battery-staple")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/vault")

    assert response.status_code == 200
    body = response.json()
    assert body["vault_sealed"] is False


# ---------------------------------------------------------------------------
# Exempt paths — /health/vault must bypass seal and auth gates
# ---------------------------------------------------------------------------


def test_health_vault_in_common_infra_exempt_paths() -> None:
    """/health/vault MUST be in COMMON_INFRA_EXEMPT_PATHS to bypass auth gate."""
    from synth_engine.bootstrapper.dependencies._exempt_paths import (
        COMMON_INFRA_EXEMPT_PATHS,
    )

    assert "/health/vault" in COMMON_INFRA_EXEMPT_PATHS
    assert len(COMMON_INFRA_EXEMPT_PATHS) == 10  # P55 count


def test_health_vault_in_seal_exempt_paths() -> None:
    """/health/vault MUST be in SEAL_EXEMPT_PATHS to bypass seal gate."""
    from synth_engine.bootstrapper.dependencies._exempt_paths import SEAL_EXEMPT_PATHS

    assert "/health/vault" in SEAL_EXEMPT_PATHS
    assert len(SEAL_EXEMPT_PATHS) == 11  # P55 count
