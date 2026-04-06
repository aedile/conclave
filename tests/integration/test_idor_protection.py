"""Integration tests for IDOR protection on all resource endpoints.

These tests exercise the full FastAPI HTTP stack end-to-end using
httpx.AsyncClient with ASGITransport. Real middleware chain is exercised.

Tests cover:
- Operator A cannot access Operator B's job (all job endpoints).
- Operator A cannot access Operator B's connection (all connection endpoints).
- Operator A cannot download Operator B's artifact.
- Operator A cannot stream Operator B's job.
- Sequential ID enumeration returns 404 for non-owned resources.
- Unauthenticated access returns 401 on all resource endpoints.

CONSTITUTION Priority 0: Security — IDOR prevention
CONSTITUTION Priority 3: TDD
Task: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
"""

from __future__ import annotations

import base64
import os
import time
from collections.abc import Generator
from typing import Any

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = (  # pragma: allowlist secret
    "integration-test-jwt-secret-key-long-enough-for-hs256-32chars+"
)
_OPERATOR_A_SUB = "operator-alpha"
_OPERATOR_B_SUB = "operator-beta"

#: Org UUIDs for IDOR test operators — different orgs enforce org-level isolation.
#: These are deterministic fake UUIDs that cannot collide with UUIDv4 (all zeros/ones
#: in version nibble position are reserved; real UUIDv4 has version nibble = 4).
_ORG_A_UUID: str = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ORG_B_UUID: str = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

_VAULT_PATCH = "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed"
_LICENSE_PATCH = "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed"


# ---------------------------------------------------------------------------
# State isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass
    yield
    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _unseal_vault_for_ale(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Unseal the vault so EncryptedString columns can encrypt/decrypt.

    Connection.host, .database, and .schema_name use the EncryptedString
    TypeDecorator (T39.4), which calls get_fernet() on every INSERT/SELECT.
    When the vault is unsealed, get_fernet() derives the ALE key from the
    vault KEK via HKDF, avoiding the ALE_KEY env var requirement.

    This fixture mirrors the pattern in test_authorization.py and must run
    for every test in this module so that Connection seeding inside
    _make_full_app() succeeds.

    Resets (re-seals) the vault after each test for isolation.

    Args:
        monkeypatch: pytest monkeypatch for env var injection.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.security.vault import VaultState

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    VaultState.unseal(bytearray(b"test-idor-integration-passphrase"))
    yield
    VaultState.reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    sub: str,
    org_id: str = _ORG_A_UUID,
    secret: str = _TEST_SECRET,
) -> str:
    """Create a valid JWT token for the given sub and org_id.

    Args:
        sub: Operator subject identifier.
        org_id: Organization UUID embedded in the JWT org_id claim.
            Required by P79 get_current_user; defaults to org A's UUID.
        secret: HMAC secret key.

    Returns:
        Compact JWT string.
    """
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": sub,
            "org_id": org_id,
            "iat": now,
            "exp": now + 3600,
            "scope": ["read", "write"],
        },
        secret,
        algorithm="HS256",
    )


def _make_full_app(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, dict[str, Any]]:
    """Build a fully-wired integration app with two operators' resources seeded.

    Patches VaultState and LicenseState so middleware passes. Creates two
    jobs and two connections — one for each operator.

    Args:
        monkeypatch: pytest monkeypatch for env var injection.

    Returns:
        Tuple of (app, engine, seed_ids) where seed_ids maps resource keys
        to their IDs: job_a_id, job_b_id, conn_a_id, conn_b_id.
    """
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.schemas.connections import Connection
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    seed_ids: dict[str, Any] = {}

    with Session(engine) as session:
        job_a = SynthesisJob(
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            total_epochs=5,
            num_rows=100,
            owner_id=_OPERATOR_A_SUB,
            org_id=_ORG_A_UUID,
        )
        job_b = SynthesisJob(
            table_name="orders",
            parquet_path="/tmp/orders.parquet",
            total_epochs=5,
            num_rows=50,
            owner_id=_OPERATOR_B_SUB,
            org_id=_ORG_B_UUID,
        )
        conn_a = Connection(
            name="db-alpha",
            host="alpha-host",
            port=5432,
            database="alpha_db",
            owner_id=_OPERATOR_A_SUB,
            org_id=_ORG_A_UUID,
        )
        conn_b = Connection(
            name="db-beta",
            host="beta-host",
            port=5432,
            database="beta_db",
            owner_id=_OPERATOR_B_SUB,
            org_id=_ORG_B_UUID,
        )
        for obj in (job_a, job_b, conn_a, conn_b):
            session.add(obj)
        session.commit()
        for obj in (job_a, job_b, conn_a, conn_b):
            session.refresh(obj)
        seed_ids = {
            "job_a_id": job_a.id,
            "job_b_id": job_b.id,
            "conn_a_id": conn_a.id,
            "conn_b_id": conn_b.id,
        }

    app = create_app()

    from synth_engine.bootstrapper.dependencies.db import get_db_session

    def _override_session() -> Any:
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db_session] = _override_session
    return app, engine, seed_ids


# ---------------------------------------------------------------------------
# Integration: GET /jobs/{job_id} IDOR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_get_job_idor_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: operator A cannot fetch operator B's job — receives 404.

    Exercises the full middleware chain (auth gate, vault, license) with a
    real JWT and real database query filtered by owner_id.

    Arrange: seed job for operator B; authenticate as operator A.
    Act: GET /jobs/{job_b_id}.
    Assert: 404 returned — not 200 or 403.
    """
    from unittest.mock import patch

    app, engine, seed_ids = _make_full_app(monkeypatch)
    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/jobs/{seed_ids['job_b_id']}",
                headers={"Authorization": f"Bearer {token_a}"},
            )

    assert response.status_code == 404
    assert response.status_code != 403


@pytest.mark.asyncio
async def test_integration_get_job_own_resource_returns_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: operator A can fetch their own job — receives 200.

    Exercises the full middleware chain with a real JWT and owner_id filter.

    Arrange: seed job for operator A; authenticate as operator A.
    Act: GET /jobs/{job_a_id}.
    Assert: 200 returned.
    """
    from unittest.mock import patch

    app, engine, seed_ids = _make_full_app(monkeypatch)
    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/jobs/{seed_ids['job_a_id']}",
                headers={"Authorization": f"Bearer {token_a}"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["owner_id"] == _OPERATOR_A_SUB


# ---------------------------------------------------------------------------
# Integration: POST /jobs/{job_id}/start IDOR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_start_job_idor_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: operator A cannot start operator B's job — receives 404.

    Arrange: seed job for operator B; authenticate as operator A.
    Act: POST /jobs/{job_b_id}/start.
    Assert: 404 returned.
    """
    from unittest.mock import patch

    app, engine, seed_ids = _make_full_app(monkeypatch)
    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/jobs/{seed_ids['job_b_id']}/start",
                headers={"Authorization": f"Bearer {token_a}"},
            )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Integration: POST /jobs/{job_id}/shred IDOR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_shred_job_idor_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: operator A cannot shred operator B's job — receives 404.

    Arrange: seed a COMPLETE job for operator B; authenticate as operator A.
    Act: POST /jobs/{job_b_id}/shred.
    Assert: 404 returned.
    """
    from unittest.mock import patch

    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.main import create_app
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        job_b = SynthesisJob(
            table_name="orders",
            parquet_path="/tmp/orders.parquet",
            total_epochs=5,
            num_rows=50,
            status="COMPLETE",
            output_path="/tmp/orders-synthetic.parquet",
            owner_id=_OPERATOR_B_SUB,
            org_id=_ORG_B_UUID,
        )
        session.add(job_b)
        session.commit()
        session.refresh(job_b)
        job_b_id = job_b.id

    app = create_app()

    from synth_engine.bootstrapper.dependencies.db import get_db_session

    def _override_session() -> Any:
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db_session] = _override_session
    token_a = _make_token(_OPERATOR_A_SUB, org_id=_ORG_A_UUID)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/jobs/{job_b_id}/shred",
                headers={"Authorization": f"Bearer {token_a}"},
            )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Integration: GET /jobs/{job_id}/stream IDOR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_stream_job_idor_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: operator A cannot stream operator B's job — receives 404.

    Arrange: seed job for operator B; authenticate as operator A.
    Act: GET /jobs/{job_b_id}/stream.
    Assert: 404 returned.
    """
    from unittest.mock import patch

    app, engine, seed_ids = _make_full_app(monkeypatch)
    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/jobs/{seed_ids['job_b_id']}/stream",
                headers={"Authorization": f"Bearer {token_a}"},
            )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Integration: GET /jobs/{job_id}/download IDOR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_download_job_idor_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: operator A cannot download operator B's artifact — receives 404.

    Arrange: seed a COMPLETE job with output_path for operator B; auth as operator A.
    Act: GET /jobs/{job_b_id}/download.
    Assert: 404 returned.
    """
    from unittest.mock import patch

    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.main import create_app
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        job_b = SynthesisJob(
            table_name="orders",
            parquet_path="/tmp/orders.parquet",
            total_epochs=5,
            num_rows=50,
            status="COMPLETE",
            output_path="/tmp/orders-synthetic.parquet",
            owner_id=_OPERATOR_B_SUB,
            org_id=_ORG_B_UUID,
        )
        session.add(job_b)
        session.commit()
        session.refresh(job_b)
        job_b_id = job_b.id

    app = create_app()

    from synth_engine.bootstrapper.dependencies.db import get_db_session

    def _override_session() -> Any:
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db_session] = _override_session
    token_a = _make_token(_OPERATOR_A_SUB, org_id=_ORG_A_UUID)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/jobs/{job_b_id}/download",
                headers={"Authorization": f"Bearer {token_a}"},
            )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Integration: GET /connections/{connection_id} IDOR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_get_connection_idor_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: operator A cannot fetch operator B's connection — receives 404.

    Arrange: seed connection for operator B; authenticate as operator A.
    Act: GET /connections/{conn_b_id}.
    Assert: 404 returned.
    """
    from unittest.mock import patch

    app, engine, seed_ids = _make_full_app(monkeypatch)
    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/connections/{seed_ids['conn_b_id']}",
                headers={"Authorization": f"Bearer {token_a}"},
            )

    assert response.status_code == 404
    assert response.status_code != 403


@pytest.mark.asyncio
async def test_integration_get_connection_own_resource_returns_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: operator A can fetch their own connection — receives 200.

    Arrange: seed connection for operator A; authenticate as operator A.
    Act: GET /connections/{conn_a_id}.
    Assert: 200 returned with correct owner_id.
    """
    from unittest.mock import patch

    app, engine, seed_ids = _make_full_app(monkeypatch)
    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/connections/{seed_ids['conn_a_id']}",
                headers={"Authorization": f"Bearer {token_a}"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["owner_id"] == _OPERATOR_A_SUB


# ---------------------------------------------------------------------------
# Integration: DELETE /connections/{connection_id} IDOR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_delete_connection_idor_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: operator A cannot delete operator B's connection — receives 404.

    Arrange: seed connection for operator B; authenticate as operator A.
    Act: DELETE /connections/{conn_b_id}.
    Assert: 404 returned.
    """
    from unittest.mock import patch

    app, engine, seed_ids = _make_full_app(monkeypatch)
    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete(
                f"/api/v1/connections/{seed_ids['conn_b_id']}",
                headers={"Authorization": f"Bearer {token_a}"},
            )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Integration: Unauthenticated access returns 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_unauthenticated_get_job_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: unauthenticated GET /jobs/{id} returns 401.

    Arrange: JWT_SECRET_KEY configured; no Authorization header.
    Act: GET /jobs/{job_a_id} without token.
    Assert: 401 returned.
    """
    from unittest.mock import patch

    app, engine, seed_ids = _make_full_app(monkeypatch)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/jobs/{seed_ids['job_a_id']}")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_integration_unauthenticated_get_connection_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: unauthenticated GET /connections/{id} returns 401.

    Arrange: JWT_SECRET_KEY configured; no Authorization header.
    Act: GET /connections/{conn_a_id} without token.
    Assert: 401 returned.
    """
    from unittest.mock import patch

    app, engine, seed_ids = _make_full_app(monkeypatch)

    with (
        patch(_VAULT_PATCH, return_value=False),
        patch(_LICENSE_PATCH, return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/connections/{seed_ids['conn_a_id']}")

    assert response.status_code == 401
