"""Unit tests for IDOR protection on connection endpoints and owner_id creation (T39.2).

Tests cover:
- Connection resource endpoints filter by owner_id.
- Connection endpoints return 404 (not 403) for non-owned resources.
- list_connections filters by owner_id.
- create_job sets owner_id from JWT sub.
- create_connection sets owner_id from JWT sub.

Split from test_authorization.py (T56.3).

CONSTITUTION Priority 0: Security — IDOR prevention
Task: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = (  # pragma: allowlist secret
    "unit-test-jwt-secret-key-long-enough-for-hs256-32chars+"
)
_OPERATOR_A_SUB = "operator-alpha"
_OPERATOR_B_SUB = "operator-beta"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _unseal_vault_for_ale(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Unseal the vault so EncryptedString columns can encrypt/decrypt.

    Connection.host, .database, and .schema_name use the EncryptedString
    TypeDecorator (T39.4), which calls get_fernet() on every INSERT/SELECT.
    When the vault is unsealed, get_fernet() derives the ALE key from the
    vault KEK via HKDF, avoiding the ALE_KEY env var requirement.

    This fixture mirrors the pattern in test_connections_router.py and
    test_connection_encryption.py and must run for every test in this module
    so that Connection seeding inside _make_connections_app() succeeds.

    Resets (re-seals) the vault after each test for isolation.
    """
    from synth_engine.shared.security.vault import VaultState

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    VaultState.unseal("test-authorization-passphrase")
    yield
    VaultState.reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(sub: str, secret: str = _TEST_SECRET) -> str:
    """Create a valid JWT token for the given sub claim.

    Args:
        sub: The operator subject identifier.
        secret: HMAC secret key.

    Returns:
        Compact JWT string.
    """
    import jwt as pyjwt

    now = int(time.time())
    return pyjwt.encode(
        {"sub": sub, "iat": now, "exp": now + 3600, "scope": ["read", "write"]},
        secret,
        algorithm="HS256",
    )


def _make_jobs_app(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Any]:
    """Build a test FastAPI app with the jobs router and in-memory SQLite.

    Args:
        monkeypatch: pytest monkeypatch for env var injection.

    Returns:
        Tuple of (app, engine) for test use.
    """
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    # Seed two jobs with different owners
    with Session(engine) as session:
        job_a = SynthesisJob(
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            total_epochs=10,
            num_rows=100,
            owner_id=_OPERATOR_A_SUB,
        )
        job_b = SynthesisJob(
            table_name="orders",
            parquet_path="/tmp/orders.parquet",
            total_epochs=5,
            num_rows=50,
            owner_id=_OPERATOR_B_SUB,
        )
        session.add(job_a)
        session.add(job_b)
        session.commit()

    app = create_app()
    register_error_handlers(app)
    app.include_router(jobs_router)

    from synth_engine.bootstrapper.dependencies.db import get_db_session

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    return app, engine


def _make_connections_app(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Any, str, str]:
    """Build a test FastAPI app with the connections router and in-memory SQLite.

    Args:
        monkeypatch: pytest monkeypatch for env var injection.

    Returns:
        Tuple of (app, engine, conn_a_id, conn_b_id) for test use.
    """
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.connections import router as connections_router
    from synth_engine.bootstrapper.schemas.connections import Connection

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    # Seed two connections with different owners
    with Session(engine) as session:
        conn_a = Connection(
            name="db-alpha",
            host="alpha-host",
            port=5432,
            database="alpha_db",
            owner_id=_OPERATOR_A_SUB,
        )
        conn_b = Connection(
            name="db-beta",
            host="beta-host",
            port=5432,
            database="beta_db",
            owner_id=_OPERATOR_B_SUB,
        )
        session.add(conn_a)
        session.add(conn_b)
        session.commit()
        session.refresh(conn_a)
        session.refresh(conn_b)
        conn_a_id: str = conn_a.id
        conn_b_id: str = conn_b.id

    app = create_app()
    register_error_handlers(app)
    app.include_router(connections_router)

    from synth_engine.bootstrapper.dependencies.db import get_db_session

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session
    return app, engine, conn_a_id, conn_b_id


# ---------------------------------------------------------------------------
# AC1/AC5: get_current_operator dependency
# ---------------------------------------------------------------------------


def test_get_connection_returns_404_for_other_operators_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /connections/{id} must return 404 for a connection owned by another operator.

    Arrange: seed a connection owned by operator B; authenticate as operator A.
    Act: GET /connections/{conn_b_id} with operator A's token.
    Assert: HTTP 404 returned.
    """
    app, engine, conn_a_id, conn_b_id = _make_connections_app(monkeypatch)
    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ),
        patch(
            "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
            return_value=True,
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"/api/v1/connections/{conn_b_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 404


def test_get_connection_returns_200_for_own_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /connections/{id} must return 200 for a connection owned by the requesting operator.

    Arrange: seed a connection owned by operator A; authenticate as operator A.
    Act: GET /connections/{conn_a_id} with operator A's token.
    Assert: HTTP 200 returned.
    """
    app, engine, conn_a_id, conn_b_id = _make_connections_app(monkeypatch)
    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ),
        patch(
            "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
            return_value=True,
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"/api/v1/connections/{conn_a_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# AC1: DELETE /connections/{connection_id} — IDOR protection
# ---------------------------------------------------------------------------


def test_delete_connection_returns_404_for_other_operators_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE /connections/{id} must return 404 for a connection owned by another operator.

    Arrange: seed a connection owned by operator B; authenticate as operator A.
    Act: DELETE /connections/{conn_b_id} with operator A's token.
    Assert: HTTP 404 returned (not 204).
    """
    app, engine, conn_a_id, conn_b_id = _make_connections_app(monkeypatch)
    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ),
        patch(
            "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
            return_value=True,
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.delete(
            f"/api/v1/connections/{conn_b_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# AC1: list_connections only returns the operator's own connections
# ---------------------------------------------------------------------------


def test_list_connections_only_returns_own_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /connections must only return connections owned by the authenticated operator.

    Arrange: seed connections for operators A and B; authenticate as operator A.
    Act: GET /connections with operator A's token.
    Assert: only operator A's connections are returned.
    """
    app, engine, conn_a_id, conn_b_id = _make_connections_app(monkeypatch)
    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ),
        patch(
            "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
            return_value=True,
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/api/v1/connections",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 200
    body = response.json()
    items = body["items"]
    # All returned connections must be owned by operator A
    assert all(item["owner_id"] == _OPERATOR_A_SUB for item in items)
    assert not any(item["owner_id"] == _OPERATOR_B_SUB for item in items)


# ---------------------------------------------------------------------------
# AC1/AC4: POST /jobs — create_job sets owner_id from operator sub
# ---------------------------------------------------------------------------


def test_create_job_sets_owner_id_from_jwt_sub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /jobs must set owner_id from the authenticated operator's sub claim.

    Arrange: authenticate as operator A.
    Act: POST /jobs with a valid job body.
    Assert: created job has owner_id == operator A's sub.
    """
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()
    register_error_handlers(app)
    app.include_router(jobs_router)

    from synth_engine.bootstrapper.dependencies.db import get_db_session

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session

    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ),
        patch(
            "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
            return_value=True,
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/jobs",
            json={
                "table_name": "customers",
                "parquet_path": "/tmp/customers.parquet",
                "total_epochs": 5,
                "num_rows": 100,
            },
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["owner_id"] == _OPERATOR_A_SUB


# ---------------------------------------------------------------------------
# AC4: POST /connections — create_connection sets owner_id from operator sub
# ---------------------------------------------------------------------------


def test_create_connection_sets_owner_id_from_jwt_sub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /connections must set owner_id from the authenticated operator's sub claim.

    Arrange: authenticate as operator A.
    Act: POST /connections with a valid body.
    Assert: created connection has owner_id == operator A's sub.
    """
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.connections import router as connections_router

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = create_app()
    register_error_handlers(app)
    app.include_router(connections_router)

    from synth_engine.bootstrapper.dependencies.db import get_db_session

    def _override_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_session

    token_a = _make_token(_OPERATOR_A_SUB)

    with (
        patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ),
        patch(
            "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
            return_value=True,
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/connections",
            json={
                "name": "new-conn",
                "host": "localhost",
                "port": 5432,
                "database": "mydb",
                "schema_name": "public",
            },
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["owner_id"] == _OPERATOR_A_SUB
