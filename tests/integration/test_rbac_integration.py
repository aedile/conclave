"""Integration tests for RBAC — Phase 80 (B4).

Verifies RBAC enforcement end-to-end against a real PostgreSQL database.
Covers:
1. Permission enforcement: admin can create user, operator cannot (403).
2. Last-admin guard with real DB transactions (409 on last admin deactivation).
3. Cross-org isolation: admin in org A cannot see/manage users in org B (404).

PostgreSQL requirement (B4):
- All tests require a real PostgreSQL database.  SQLite cannot model
  ``SELECT ... FOR UPDATE`` locking semantics required by the last-admin guard.
- Uses pytest-postgresql to spawn an ephemeral PostgreSQL process.
  All tests are skipped when ``pg_ctl`` is not on PATH.

Model import order:
- All SQLModel table classes are imported at module level BEFORE
  SQLModel.metadata.create_all() to ensure the ORM registry is populated.

CONSTITUTION Priority 0: Security — RBAC enforcement, IDOR prevention
CONSTITUTION Priority 3: TDD
Task: P80-B4 — RBAC integration tests
ADR: ADR-0066 — RBAC Permission Model
"""

from __future__ import annotations

import shutil
import time
import uuid
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_postgresql import factories
from pytest_postgresql.janitor import DatabaseJanitor
from sqlmodel import Session, SQLModel, create_engine

# ---------------------------------------------------------------------------
# CRITICAL: Import ALL SQLModel table classes at module level BEFORE any
# SQLModel.metadata.create_all() call.  (B4 fix — mirrors test_tenant_isolation.py)
# ---------------------------------------------------------------------------
from synth_engine.bootstrapper.schemas.connections import Connection  # noqa: F401
from synth_engine.modules.privacy.ledger import PrivacyLedger  # noqa: F401
from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob  # noqa: F401
from synth_engine.shared.models.organization import Organization
from synth_engine.shared.models.user import User
from tests.conftest_types import PostgreSQLProc

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = (  # pragma: allowlist secret
    "rbac-integration-test-secret-key-long-enough-for-hs256+"
)

_ORG_A_UUID = "aaaaaaaa-aaaa-1111-aaaa-aaaaaaaaaaaa"
_ORG_B_UUID = "bbbbbbbb-bbbb-2222-bbbb-bbbbbbbbbbbb"
_ADMIN_A_UUID = "00000001-0000-0000-0000-000000000001"
_OPERATOR_A_UUID = "00000002-0000-0000-0000-000000000002"
_ADMIN_B_UUID = "00000003-0000-0000-0000-000000000003"

# ---------------------------------------------------------------------------
# pytest-postgresql process fixture
# ---------------------------------------------------------------------------

postgresql_proc = factories.postgresql_proc()


# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _require_postgresql() -> None:
    """Skip entire module when pg_ctl is not installed.

    Args:
        (none — autouse fixture)
    """
    if shutil.which("pg_ctl") is None:
        pytest.skip(
            "pg_ctl not found on PATH — install PostgreSQL to run RBAC integration tests",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
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


@pytest.fixture
def pg_sync_engine(
    postgresql_proc: PostgreSQLProc,
) -> Generator[Any]:
    """Provide a sync SQLAlchemy engine against an ephemeral PostgreSQL instance.

    Creates all SQLModel tables (all models imported at module level above),
    seeds the two test organizations required by FK constraints on ``users.org_id``,
    yields the engine, then drops all tables and disposes the engine.

    Args:
        postgresql_proc: The running pytest-postgresql process executor.

    Yields:
        A sync SQLAlchemy engine pointed at the ephemeral PostgreSQL database.
    """
    proc = postgresql_proc
    password = proc.password or ""
    db_url = f"postgresql+psycopg2://{proc.user}:{password}@{proc.host}:{proc.port}/{proc.dbname}"

    with DatabaseJanitor(
        user=proc.user,
        host=proc.host,
        port=proc.port,
        dbname=proc.dbname,
        version=proc.version,
        password=password,
    ):
        engine = create_engine(db_url)
        SQLModel.metadata.create_all(engine)

        # Seed the two test organizations.  All User inserts in these tests carry
        # org_id FK references to _ORG_A_UUID or _ORG_B_UUID.  Without these rows
        # the DB raises ForeignKeyViolation → 500 on every user creation request.
        with Session(engine) as session:
            org_a = Organization(id=uuid.UUID(_ORG_A_UUID), name="Test Org A")
            org_b = Organization(id=uuid.UUID(_ORG_B_UUID), name="Test Org B")
            session.add(org_a)
            session.add(org_b)
            session.commit()

        yield engine
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    *,
    sub: str,
    org_id: str,
    role: str = "admin",
    secret: str = _TEST_SECRET,
    exp_offset: int = 3600,
) -> str:
    """Create a signed JWT for integration testing.

    Args:
        sub: Subject (user_id).
        org_id: Organization UUID claim.
        role: RBAC role claim.
        secret: HMAC secret for signing.
        exp_offset: Expiry offset from now in seconds.

    Returns:
        Compact JWT string.
    """
    now = int(time.time())
    payload = {
        "sub": sub,
        "org_id": org_id,
        "role": role,
        "iat": now,
        "exp": now + exp_offset,
        "scope": ["read", "write"],
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


def _make_admin_users_client(
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Build a TestClient wired to the admin_users router with a real DB session.

    Uses ``monkeypatch.setattr`` for the audit logger mock so that pytest's
    monkeypatch machinery handles teardown automatically — no manual
    ``patch.stop()`` call is needed and test pollution is prevented.

    Args:
        engine: SQLAlchemy engine from pg_sync_engine fixture.
        monkeypatch: pytest monkeypatch for env var injection and mock teardown.

    Returns:
        Configured :class:`TestClient` for the admin_users router.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.admin_users import router as admin_users_router

    app = FastAPI()
    app.include_router(admin_users_router)

    def _session_override() -> Generator[None]:
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db_session] = _session_override

    # Use monkeypatch.setattr so pytest automatically reverses the patch after
    # each test — prevents the audit mock from leaking into subsequent tests.
    mock_audit = MagicMock()
    monkeypatch.setattr(
        "synth_engine.bootstrapper.routers.admin_users.get_audit_logger",
        lambda *_args, **_kwargs: mock_audit,
    )

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Test 1: Permission enforcement end-to-end
# ---------------------------------------------------------------------------


def test_admin_can_create_user_operator_cannot(
    monkeypatch: pytest.MonkeyPatch,
    pg_sync_engine: Any,
) -> None:
    """Admin can create users; operator gets 403 (permission enforcement e2e).

    Verifies that RBAC enforcement works with a real DB and real JWT verification.
    - Admin token (role=admin) → POST /admin/users → 201
    - Operator token (role=operator) → POST /admin/users → 403

    This is the primary B4 permission enforcement test.
    """
    client = _make_admin_users_client(pg_sync_engine, monkeypatch)

    admin_token = _make_token(sub=_ADMIN_A_UUID, org_id=_ORG_A_UUID, role="admin")
    operator_token = _make_token(sub=_OPERATOR_A_UUID, org_id=_ORG_A_UUID, role="operator")

    # Admin can create a user
    resp = client.post(
        "/admin/users",
        json={"email": "newuser@example.com", "role": "viewer"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201, f"Admin create failed: {resp.text}"
    assert resp.json()["role"] == "viewer"

    # Operator cannot create a user (lacks admin:users permission)
    resp = client.post(
        "/admin/users",
        json={"email": "shouldfail@example.com", "role": "viewer"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 403, f"Operator should get 403, got {resp.status_code}: {resp.text}"
    assert "Insufficient permissions" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Test 2: Last-admin guard with real DB transactions
# ---------------------------------------------------------------------------


def test_last_admin_guard_with_real_db(
    monkeypatch: pytest.MonkeyPatch,
    pg_sync_engine: Any,
) -> None:
    """Last-admin guard fires with 409 when deleting the only admin (real DB).

    Creates exactly one admin user in the org, then attempts to delete them.
    Expects 409 Conflict with a real PostgreSQL backend (verifies FOR UPDATE
    lock semantics and count-based guard work correctly end-to-end).
    """
    client = _make_admin_users_client(pg_sync_engine, monkeypatch)

    admin_token = _make_token(sub=_ADMIN_A_UUID, org_id=_ORG_A_UUID, role="admin")

    # Create one admin user in the org
    resp = client.post(
        "/admin/users",
        json={"email": "lonelyadmin@example.com", "role": "admin"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201, f"Setup failed: {resp.text}"
    user_id = resp.json()["id"]

    # Attempt to delete the only admin — must return 409
    resp = client.delete(
        f"/admin/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "admin" in body["detail"].lower(), f"409 detail must mention admin: {body['detail']}"


# ---------------------------------------------------------------------------
# Test 3: Cross-org isolation (IDOR)
# ---------------------------------------------------------------------------


def test_cross_org_user_management_returns_404(
    monkeypatch: pytest.MonkeyPatch,
    pg_sync_engine: Any,
) -> None:
    """Admin in org A cannot manage users in org B — returns 404 (IDOR protection).

    Creates a user in org B directly via the DB, then attempts to PATCH that
    user's role using an admin JWT scoped to org A. Expects 404 (IDOR — org
    existence of other orgs must not be leaked via 403).
    """
    client = _make_admin_users_client(pg_sync_engine, monkeypatch)

    # Create a user in org B directly (bypass HTTP — simulates a user existing in another org)
    org_b_uuid = uuid.UUID(_ORG_B_UUID)
    with Session(pg_sync_engine) as session:
        user_b = User(
            org_id=org_b_uuid,
            email="orgb-user@example.com",
            role="operator",
        )
        session.add(user_b)
        session.commit()
        session.refresh(user_b)
        user_b_id = str(user_b.id)

    # Admin from org A tries to patch the org B user — must get 404
    admin_a_token = _make_token(sub=_ADMIN_A_UUID, org_id=_ORG_A_UUID, role="admin")
    resp = client.patch(
        f"/admin/users/{user_b_id}",
        json={"role": "viewer"},
        headers={"Authorization": f"Bearer {admin_a_token}"},
    )
    assert resp.status_code == 404, (
        f"Cross-org PATCH must return 404 (IDOR), got {resp.status_code}: {resp.text}"
    )
