"""Unit tests for T39.2 Authorization & IDOR Protection.

Tests cover:
- get_current_operator() dependency extracts sub claim from valid JWT.
- get_current_operator() raises HTTPException(401) when no Authorization header.
- get_current_operator() raises HTTPException(401) for invalid token.
- get_current_operator() raises HTTPException(401) when sub claim is empty string.
- Resource endpoints filter by owner_id — operator A cannot access operator B's job.
- Resource endpoints return 404 (not 403) for non-owned resources.
- Unauthenticated requests return 401.
- SynthesisJob model has owner_id field.
- Connection model has owner_id field.
- create_job sets owner_id from current operator's sub claim.
- create_connection sets owner_id from current operator's sub claim.
- list_jobs filters by owner_id.
- list_connections filters by owner_id.

CONSTITUTION Priority 0: Security — IDOR prevention
CONSTITUTION Priority 3: TDD — RED phase
Task: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit

# Pass-through mode org sentinel (matches DEFAULT_ORG_UUID from tenant.py)
_DEFAULT_ORG_UUID: str = "00000000-0000-0000-0000-000000000000"

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
    VaultState.unseal(bytearray(b"test-authorization-passphrase"))
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
            org_id=_DEFAULT_ORG_UUID,
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


def test_get_current_operator_extracts_sub_from_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_current_operator() must return the sub claim from a valid Bearer token.

    Arrange: set JWT_SECRET_KEY; build a valid token with a known sub.
    Act: call get_current_operator() via a mocked Request.
    Assert: returns the sub string.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator

    token = _make_token(_OPERATOR_A_SUB)
    mock_request = MagicMock()
    mock_request.headers = {"Authorization": f"Bearer {token}"}

    result = get_current_operator(mock_request)
    assert result == _OPERATOR_A_SUB


# Authorization header values that get_current_operator() must reject with 401
_BAD_AUTH_HEADERS = [
    pytest.param({}, id="no_auth_header"),
    pytest.param({"Authorization": "Bearer this-is-not-a-valid-jwt"}, id="invalid_token"),
    pytest.param({"Authorization": "Token not-bearer"}, id="wrong_scheme"),
]


@pytest.mark.parametrize("headers", _BAD_AUTH_HEADERS)
def test_get_current_operator_raises_401_for_bad_auth(
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_current_operator() raises HTTPException(401) for missing or invalid auth.

    Args:
        headers: Request headers dict to pass (may be empty or malformed).
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from fastapi import HTTPException

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator

    mock_request = MagicMock()
    mock_request.headers = headers

    with pytest.raises(HTTPException) as exc_info:
        get_current_operator(mock_request)

    assert exc_info.value.status_code == 401


def test_get_current_operator_raises_401_for_empty_sub_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_current_operator() raises HTTPException(401) when token sub claim is empty string.

    An empty sub claim is structurally valid JWT but semantically invalid for
    resource ownership — it would collide with the pass-through sentinel value
    and grant access to all pre-T39.2 resources owned by any legacy operator.

    Arrange: set JWT_SECRET_KEY; build a valid token with sub="".
    Act: call get_current_operator() via a mocked Request.
    Assert: HTTPException with status_code=401 is raised with an informative detail.
    """
    import jwt as pyjwt

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from fastapi import HTTPException

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    now = int(time.time())
    token_empty_sub = pyjwt.encode(
        {"sub": "", "iat": now, "exp": now + 3600, "scope": []},
        _TEST_SECRET,
        algorithm="HS256",
    )

    mock_request = MagicMock()
    mock_request.headers = {"Authorization": f"Bearer {token_empty_sub}"}

    with pytest.raises(HTTPException) as exc_info:
        get_current_operator(mock_request)

    assert exc_info.value.status_code == 401
    assert "empty" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# AC3: SynthesisJob model has owner_id field
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AC3: Resource models have owner_id field
# ---------------------------------------------------------------------------


def test_synthesis_job_has_owner_id_field() -> None:
    """SynthesisJob must have an owner_id field that stores the value correctly."""
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    job = SynthesisJob(
        table_name="test",
        parquet_path="/tmp/test.parquet",
        total_epochs=1,
        num_rows=10,
        owner_id="test-operator",
    )
    assert job.owner_id == "test-operator"


def test_synthesis_job_owner_id_defaults_to_empty_string() -> None:
    """SynthesisJob.owner_id must default to empty string for backward compatibility."""
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    job = SynthesisJob(
        table_name="test",
        parquet_path="/tmp/test.parquet",
        total_epochs=1,
        num_rows=10,
    )
    assert job.owner_id == ""


def test_connection_has_owner_id_field() -> None:
    """Connection must have an owner_id field that stores the value correctly."""
    from synth_engine.bootstrapper.schemas.connections import Connection

    conn = Connection(
        name="test-conn",
        host="localhost",
        port=5432,
        database="testdb",
        owner_id="test-operator",
    )
    assert conn.owner_id == "test-operator"


def test_connection_owner_id_defaults_to_empty_string() -> None:
    """Connection.owner_id must default to empty string for backward compatibility."""
    from synth_engine.bootstrapper.schemas.connections import Connection

    conn = Connection(
        name="test-conn",
        host="localhost",
        port=5432,
        database="testdb",
    )
    assert conn.owner_id == ""


# ---------------------------------------------------------------------------
# AC1/AC2: GET /jobs/{job_id} — IDOR protection
# ---------------------------------------------------------------------------
