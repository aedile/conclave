"""Integration tests for tenant isolation — Phase 79.

These tests exercise the multi-tenancy isolation boundaries at the HTTP stack
level using TestClient with a real PostgreSQL database (pytest-postgresql).
They cover:

- User in Org A creates connection; Org B user gets 404
- User in Org A creates job; Org B user cannot see/cancel/download
- Cross-org pagination enumeration blocked
- Forged JWT org_id rejected by signature
- Header spoofing X-Org-ID cannot override JWT
- SQL injection in org_id rejected
- Pass-through mode requires explicit opt-in (P79-F12)

Security posture:
- All tests verify that cross-org access returns 404 (not 403), to avoid
  leaking the existence of resources owned by other orgs.
- JWT forging attempts return 401 from the auth layer.
- Header spoofing of X-Org-ID has no effect.

PostgreSQL requirement (RB3 — Fix Round 2):
- All tests require a real PostgreSQL database.  SQLite is NOT used.
  SQLite silently ignores FOR UPDATE locks and cannot represent the
  concurrent isolation semantics that multi-tenancy depends on.
- Uses pytest-postgresql to spawn an ephemeral PostgreSQL process.
  All tests are skipped when ``pg_ctl`` is not on PATH.

Model import order (RB2 — Fix Round 2):
- All SQLModel table classes (Connection, SynthesisJob, PrivacyLedger, etc.)
  are imported at module level BEFORE any SQLModel.metadata.create_all() call.
  This ensures the ORM registry is fully populated when tables are created.
  Importing models AFTER create_all produces "no such table" errors.

CONSTITUTION Priority 0: Security — tenant isolation, IDOR prevention
CONSTITUTION Priority 3: TDD
Task: P79-B4 — Tenant isolation integration tests
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Generator
from typing import Any

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_postgresql import factories
from pytest_postgresql.janitor import DatabaseJanitor
from sqlmodel import Session, SQLModel, create_engine

# ---------------------------------------------------------------------------
# CRITICAL: Import ALL SQLModel table classes at module level BEFORE any
# SQLModel.metadata.create_all() call.  If models are imported lazily (inside
# test bodies) after create_all has already run, the tables will not exist in
# the DB.  (RB2 — Fix Round 2)
# ---------------------------------------------------------------------------
from synth_engine.bootstrapper.schemas.connections import Connection
from synth_engine.modules.privacy.ledger import PrivacyLedger  # noqa: F401 — registers table
from synth_engine.modules.synthesizer.jobs.job_models import (
    SynthesisJob,
)
from tests.conftest_types import PostgreSQLProc

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = (  # pragma: allowlist secret
    "integration-test-multi-tenant-secret-key-long-enough-for-hs256+"
)
_WRONG_SECRET = (  # pragma: allowlist secret
    "wrong-secret-that-should-fail-hs256-validation-padded-to-length+"
)

_ORG_A_UUID = "11111111-1111-1111-1111-111111111111"
_ORG_B_UUID = "22222222-2222-2222-2222-222222222222"
_USER_A_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_USER_B_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


# ---------------------------------------------------------------------------
# pytest-postgresql process fixture
# ---------------------------------------------------------------------------

postgresql_proc = factories.postgresql_proc()


# ---------------------------------------------------------------------------
# Skip guard — runs before all tests in this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _require_postgresql() -> None:
    """Skip entire module when pg_ctl is not installed.

    pytest-postgresql spawns a real PostgreSQL process using ``pg_ctl``.
    If the binary is absent (e.g. developer laptops without a local PG
    installation), all tests would error rather than skip.
    """
    if shutil.which("pg_ctl") is None:
        pytest.skip(
            "pg_ctl not found on PATH — install PostgreSQL to run tenant "
            "isolation integration tests",
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
    """Provide a sync SQLAlchemy engine connected to the ephemeral PostgreSQL instance.

    Creates the test database via ``DatabaseJanitor``, creates all SQLModel
    tables (all models imported at module level above), yields the engine,
    and disposes it.  Uses ``psycopg2`` (the project's sync driver).

    Args:
        postgresql_proc: The running pytest-postgresql process executor
            providing host, port, user, and password.

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
    """Create a valid multi-tenant JWT for testing.

    Args:
        sub: Subject (user_id).
        org_id: Organization UUID claim.
        role: Role claim.
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


def _make_app_with_override(
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    router: Any,
    prefix: str = "/api/v1",
) -> TestClient:
    """Build a minimal FastAPI app wired to the provided engine.

    Configures JWT auth via environment variables and overrides the
    database session dependency to use the provided engine.

    Args:
        engine: SQLAlchemy engine (from pg_sync_engine fixture).
        monkeypatch: pytest monkeypatch for env var injection.
        router: FastAPI APIRouter to mount.
        prefix: URL prefix for the router.

    Returns:
        Configured :class:`TestClient`.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.dependencies.db import get_db_session

    app = FastAPI()
    app.include_router(router, prefix=prefix)

    def _session_override() -> Generator[None]:
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db_session] = _session_override
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Test Group 1: Connection isolation
# ---------------------------------------------------------------------------


def test_org_a_connection_not_visible_to_org_b(
    monkeypatch: pytest.MonkeyPatch,
    pg_sync_engine: Any,
) -> None:
    """User in Org A creates a connection; Org B user gets 404.

    Tenant isolation: org_id filters prevent cross-org resource access.
    The 404 (not 403) response avoids leaking resource existence.
    """
    import base64
    import os

    # Unseal vault so EncryptedString columns work.
    from synth_engine.shared.security.vault import VaultState

    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    if VaultState.is_sealed():
        VaultState.unseal(bytearray(b"test-tenant-isolation-passphrase"))

    try:
        # Seed a connection for Org A
        with Session(pg_sync_engine) as session:
            conn_a = Connection(
                name="org-a-conn",
                host="localhost",
                port=5432,
                database="org_a_db",
                username="org_a_user",
                password="secret",  # pragma: allowlist secret
                owner_id=_USER_A_UUID,
                org_id=_ORG_A_UUID,
            )
            session.add(conn_a)
            session.commit()
            session.refresh(conn_a)
            conn_a_id = conn_a.id

        from synth_engine.bootstrapper.routers.connections import router as connections_router

        client = _make_app_with_override(pg_sync_engine, monkeypatch, connections_router)
        token_b = _make_token(sub=_USER_B_UUID, org_id=_ORG_B_UUID)

        # Org B tries to access Org A's connection — must return 404
        response = client.get(
            f"/api/v1/connections/{conn_a_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == 404, (
            f"Cross-org connection access must return 404, got {response.status_code}"
        )
    finally:
        VaultState.reset()


# ---------------------------------------------------------------------------
# Test Group 2: Synthesis job isolation
# ---------------------------------------------------------------------------


def test_org_a_job_not_visible_to_org_b(
    monkeypatch: pytest.MonkeyPatch,
    pg_sync_engine: Any,
) -> None:
    """User in Org A creates a job; Org B user gets 404.

    Tenant isolation: org_id scoping on synthesis_job prevents cross-org access.
    """
    with Session(pg_sync_engine) as session:
        job_a = SynthesisJob(
            table_name="persons",
            parquet_path="/data/persons.parquet",
            total_epochs=5,
            num_rows=100,
            owner_id=_USER_A_UUID,
            org_id=_ORG_A_UUID,
        )
        session.add(job_a)
        session.commit()
        session.refresh(job_a)
        job_a_id = job_a.id

    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    client = _make_app_with_override(pg_sync_engine, monkeypatch, jobs_router)
    token_b = _make_token(sub=_USER_B_UUID, org_id=_ORG_B_UUID)

    # Org B tries to access Org A's job — must return 404
    response = client.get(
        f"/api/v1/jobs/{job_a_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404, (
        f"Cross-org job access must return 404, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Test Group 3: JWT security — forged org_id rejected by signature
# ---------------------------------------------------------------------------


def test_forged_jwt_org_id_rejected(
    monkeypatch: pytest.MonkeyPatch,
    pg_sync_engine: Any,
) -> None:
    """Forged JWT with wrong signature is rejected with 401.

    An attacker cannot forge an org_id claim without the JWT secret.
    """
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    client = _make_app_with_override(pg_sync_engine, monkeypatch, jobs_router)

    # Attacker forges a token claiming Org A, but uses the wrong signing secret
    forged_token = _make_token(sub=_USER_A_UUID, org_id=_ORG_A_UUID, secret=_WRONG_SECRET)

    response = client.get(
        "/api/v1/jobs/1",
        headers={"Authorization": f"Bearer {forged_token}"},
    )
    assert response.status_code == 401, (
        f"Forged JWT must be rejected with 401, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Test Group 4: Header spoofing — X-Org-ID cannot override JWT
# ---------------------------------------------------------------------------


def test_x_org_id_header_spoofing_rejected(
    monkeypatch: pytest.MonkeyPatch,
    pg_sync_engine: Any,
) -> None:
    """X-Org-ID header spoofing cannot override JWT org_id.

    ATTACK-02 mitigation from ADR-0065: the org_id must come exclusively
    from the verified JWT claim, never from HTTP headers.
    """
    with Session(pg_sync_engine) as session:
        job_a = SynthesisJob(
            table_name="persons",
            parquet_path="/data/persons.parquet",
            total_epochs=5,
            num_rows=100,
            owner_id=_USER_A_UUID,
            org_id=_ORG_A_UUID,
        )
        session.add(job_a)
        session.commit()
        session.refresh(job_a)
        job_a_id = job_a.id

    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    client = _make_app_with_override(pg_sync_engine, monkeypatch, jobs_router)

    # User B has a valid JWT for Org B but sets X-Org-ID: Org A (header spoof)
    token_b = _make_token(sub=_USER_B_UUID, org_id=_ORG_B_UUID)

    response = client.get(
        f"/api/v1/jobs/{job_a_id}",
        headers={
            "Authorization": f"Bearer {token_b}",
            "X-Org-ID": _ORG_A_UUID,  # Header spoof — must be ignored
        },
    )
    # The org comes from JWT (Org B), so Org A's job must still return 404
    assert response.status_code == 404, (
        f"X-Org-ID header must not override JWT org_id. Got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Test Group 5: SQL injection in org_id
# ---------------------------------------------------------------------------


def test_sql_injection_in_job_creation_rejected(
    monkeypatch: pytest.MonkeyPatch,
    pg_sync_engine: Any,
) -> None:
    """SQL injection via org_id claim in JWT is rejected.

    The org_id must be validated as a UUID, preventing injection via
    a crafted JWT claim containing SQL special characters.
    """
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    client = _make_app_with_override(pg_sync_engine, monkeypatch, jobs_router)

    # Craft a JWT with a SQL-injection-style org_id (not a valid UUID)
    malicious_org_id = "'; DROP TABLE synthesis_job; --"
    injection_token = _make_token(sub=_USER_A_UUID, org_id=malicious_org_id)

    response = client.get(
        "/api/v1/jobs/1",
        headers={"Authorization": f"Bearer {injection_token}"},
    )
    # UUID validation must reject the non-UUID org_id claim with 401
    assert response.status_code == 401, (
        f"SQL injection in org_id must be rejected with 401, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Test Group 6: Unauthenticated access
# ---------------------------------------------------------------------------


def test_unauthenticated_request_rejected(
    monkeypatch: pytest.MonkeyPatch,
    pg_sync_engine: Any,
) -> None:
    """Unauthenticated requests return 401 on all tenant-scoped endpoints.

    The presence of JWT_SECRET_KEY triggers authentication enforcement.
    """
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    client = _make_app_with_override(pg_sync_engine, monkeypatch, jobs_router)

    # No Authorization header
    response = client.get("/api/v1/jobs/1")
    assert response.status_code == 401, (
        f"Unauthenticated request must return 401, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Test Group 7: Cross-org pagination enumeration blocked
# ---------------------------------------------------------------------------


def test_cross_org_pagination_returns_only_own_data(
    monkeypatch: pytest.MonkeyPatch,
    pg_sync_engine: Any,
) -> None:
    """Cross-org pagination enumeration is blocked.

    When Org A and Org B each have jobs, a paginated list from Org B's token
    must only return Org B's jobs, not Org A's.
    """
    with Session(pg_sync_engine) as session:
        # Create 2 jobs for Org A
        for i in range(2):
            session.add(
                SynthesisJob(
                    table_name=f"org_a_table_{i}",
                    parquet_path="/data/test.parquet",
                    total_epochs=1,
                    num_rows=10,
                    owner_id=_USER_A_UUID,
                    org_id=_ORG_A_UUID,
                )
            )
        # Create 1 job for Org B
        session.add(
            SynthesisJob(
                table_name="org_b_table",
                parquet_path="/data/test.parquet",
                total_epochs=1,
                num_rows=10,
                owner_id=_USER_B_UUID,
                org_id=_ORG_B_UUID,
            )
        )
        session.commit()

    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    client = _make_app_with_override(pg_sync_engine, monkeypatch, jobs_router)

    # Org B lists jobs — must only see their own 1 job
    token_b = _make_token(sub=_USER_B_UUID, org_id=_ORG_B_UUID)

    response = client.get(
        "/api/v1/jobs",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 200, f"List jobs must return 200, got {response.status_code}"
    data = response.json()
    items = data.get("items", [])
    # Org B must only see their own jobs, not Org A's
    assert len(items) == 1, f"Org B must see only 1 job, got {len(items)}: {items}"
    assert all(j.get("org_id") == _ORG_B_UUID for j in items if "org_id" in j), (
        "All returned jobs must belong to Org B"
    )


# ---------------------------------------------------------------------------
# Test Group 8: Pass-through mode requires explicit opt-in (P79-F12)
# ---------------------------------------------------------------------------


def test_pass_through_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    pg_sync_engine: Any,
) -> None:
    """Pass-through mode requires CONCLAVE_PASS_THROUGH_ENABLED=true.

    When JWT_SECRET_KEY is empty but CONCLAVE_PASS_THROUGH_ENABLED is not set,
    requests must return 401 with a helpful message.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("CONCLAVE_PASS_THROUGH_ENABLED", raising=False)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    app = FastAPI()
    app.include_router(jobs_router, prefix="/api/v1")

    def _session_override() -> Generator[None]:
        with Session(pg_sync_engine) as s:
            yield s

    app.dependency_overrides[get_db_session] = _session_override

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/v1/jobs/1")
    assert response.status_code == 401, (
        f"Pass-through without opt-in must return 401, got {response.status_code}"
    )
    # Response must contain helpful message about CONCLAVE_PASS_THROUGH_ENABLED
    assert "CONCLAVE_PASS_THROUGH_ENABLED" in response.text, (
        "401 response must explain how to enable pass-through mode"
    )


def test_pass_through_works_with_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    pg_sync_engine: Any,
) -> None:
    """Pass-through mode works when CONCLAVE_PASS_THROUGH_ENABLED=true.

    When JWT_SECRET_KEY is empty AND CONCLAVE_PASS_THROUGH_ENABLED=true
    in non-production mode, the sentinel identity is returned.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "true")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router

    app = FastAPI()
    app.include_router(jobs_router, prefix="/api/v1")

    def _session_override() -> Generator[None]:
        with Session(pg_sync_engine) as s:
            yield s

    app.dependency_overrides[get_db_session] = _session_override

    client = TestClient(app, raise_server_exceptions=False)
    # With pass-through enabled, request should proceed (200 or 404 for missing job)
    response = client.get("/api/v1/jobs/999")
    # Must NOT be 401 — pass-through is active
    assert response.status_code != 401, (
        f"Pass-through with opt-in must not return 401, got {response.status_code}"
    )
    # Expect 404 since job 999 doesn't exist
    assert response.status_code == 404, (
        f"Expected 404 for non-existent job in pass-through mode, got {response.status_code}"
    )
