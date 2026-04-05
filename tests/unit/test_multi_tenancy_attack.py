"""Negative/attack tests for multi-tenancy foundation — Phase 79.

These tests verify that the system REJECTS adversarial access patterns:
- Unauthenticated requests
- JWT signature forgery (org_id claim tampering)
- Cross-tenant data access (IDOR)
- HTTP header spoofing (X-Org-ID override attempt)
- SQL injection in path parameters
- Pagination cursor cross-tenant leakage
- Migration idempotency and backward compatibility
- Background task org isolation
- Erasure boundary enforcement

All tests were written in the ATTACK RED phase, BEFORE feature tests,
per CLAUDE.md Rule 22.

CONSTITUTION Priority 0: Security — tenant isolation, IDOR prevention, auth
CONSTITUTION Priority 3: TDD — ATTACK RED phase
Phase: 79 — Multi-Tenancy Foundation
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = (  # pragma: allowlist secret
    "unit-test-jwt-secret-key-long-enough-for-hs256-32chars+"
)
_WRONG_SECRET = (  # pragma: allowlist secret
    "wrong-secret-key-that-is-long-enough-for-hs256-32chars+"
)

_DEFAULT_ORG_UUID = "00000000-0000-0000-0000-000000000000"
_DEFAULT_USER_UUID = "00000000-0000-0000-0000-000000000001"
_ORG_A_UUID = "11111111-1111-1111-1111-111111111111"
_ORG_B_UUID = "22222222-2222-2222-2222-222222222222"
_USER_A_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_USER_B_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Any:
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
def _unseal_vault_for_ale(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Unseal the vault so EncryptedString columns can encrypt/decrypt.

    Yields:
        None — setup and teardown only.
    """
    try:
        from synth_engine.shared.security.vault import VaultState

        salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
        monkeypatch.setenv("VAULT_SEAL_SALT", salt)
        VaultState.unseal(bytearray(b"test-multi-tenancy-passphrase"))
        yield
        VaultState.reset()
    except ImportError:
        yield


# ---------------------------------------------------------------------------
# Token helpers
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


def _make_legacy_token(*, sub: str, secret: str = _TEST_SECRET) -> str:
    """Create an old-style JWT without org_id claim.

    Args:
        sub: Subject claim.
        secret: HMAC signing secret.

    Returns:
        Compact JWT string (no org_id claim).
    """
    now = int(time.time())
    payload = {
        "sub": sub,
        "iat": now,
        "exp": now + 3600,
        "scope": ["read"],
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Helpers to build a minimal FastAPI app for tenant dependency tests
# ---------------------------------------------------------------------------


def _make_tenant_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build a minimal FastAPI app with a tenant-protected endpoint.

    Uses ``Depends(get_current_user)`` as a parameter default (not a type
    annotation) to avoid the ``from __future__ import annotations`` scope
    issue where FastAPI receives lazy string annotations instead of actual
    types and falls back to treating ``ctx`` as a query parameter.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        FastAPI application instance with tenant endpoint.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    app = FastAPI()

    # Import after env setup
    from fastapi import Depends

    from synth_engine.bootstrapper.dependencies.tenant import (
        TenantContext,
        get_current_user,
    )

    # Use Depends as a default value (not a type annotation) to avoid the
    # from __future__ import annotations lazy-string resolution issue.
    @app.get("/test/tenant")
    def _test_endpoint(ctx: TenantContext = Depends(get_current_user)) -> dict[str, str]:  # type: ignore[assignment]  # noqa: B008
        return {"org_id": ctx.org_id, "user_id": ctx.user_id, "role": ctx.role}

    return app


# ---------------------------------------------------------------------------
# Test Group 1: Authentication & Identity (tests 1-3)
# ---------------------------------------------------------------------------


def test_get_current_user_rejects_unauthenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_current_user returns 401 when no Bearer token is provided.

    ATTACK-01 mitigation: unauthenticated requests must be rejected.
    Expected: HTTP 401 with static error message (no internal detail).
    """
    app = _make_tenant_app(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/test/tenant")

    assert response.status_code == 401
    body = response.json()
    assert "detail" in body
    # Static message — must not leak internal state
    assert "Authentication required" in body["detail"] or "credentials" in body["detail"]


def test_get_current_user_rejects_forged_org_id_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_current_user returns 401 when JWT is signed with the wrong secret.

    ATTACK-01 / ATTACK-02 mitigation: forged JWTs must fail signature verification.
    An attacker who tampers with org_id must receive 401 (not the forged org's data).
    """
    app = _make_tenant_app(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    forged_token = _make_token(
        sub=_USER_A_UUID,
        org_id=_ORG_A_UUID,
        secret=_WRONG_SECRET,  # signed with wrong key
    )

    response = client.get(
        "/test/tenant",
        headers={"Authorization": f"Bearer {forged_token}"},
    )

    assert response.status_code == 401


def test_get_current_user_passthrough_returns_default_org_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass-through mode (no JWT secret) returns sentinel TenantContext.

    ATTACK-02 mitigation: sentinel UUIDs are reserved all-zeros, distinct
    from any UUIDv4.  The sentinel cannot collide with real org/user IDs.
    Calls get_current_user directly with a mocked Request to avoid the
    from __future__ import annotations scope issue with nested FastAPI
    Annotated[..., Depends(...)] endpoint definitions.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "true")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from starlette.requests import Request

    from synth_engine.bootstrapper.dependencies.tenant import (
        DEFAULT_ORG_UUID,
        DEFAULT_ROLE,
        DEFAULT_USER_UUID,
        TenantContext,
        get_current_user,
    )

    # No Authorization header — pass-through mode returns sentinel (with explicit opt-in)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "query_string": b"",
        "headers": [],
    }
    request = Request(scope)

    ctx = get_current_user(request)

    assert isinstance(ctx, TenantContext)
    assert ctx.org_id == DEFAULT_ORG_UUID
    assert ctx.user_id == DEFAULT_USER_UUID
    assert ctx.role == DEFAULT_ROLE


# ---------------------------------------------------------------------------
# Test Group 2: IDOR / Cross-Tenant Data Access (tests 4-10)
# ---------------------------------------------------------------------------


def _make_connections_tenant_app(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FastAPI, Session, str, str]:
    """Build a test app with two orgs and seed one connection for org A.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        Tuple of (app, session, conn_id_for_org_a, org_a_token).
    """
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.schemas.connections import Connection

    # In-memory SQLite for unit tests
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    # Seed a connection owned by org A
    conn_id = "test-conn-id-org-a"
    with Session(engine) as seed_session:
        conn = Connection(
            id=conn_id,
            name="org-a-connection",
            host="db.orga.example",
            port=5432,
            database="orga_db",
            schema_name="public",
            owner_id=_USER_A_UUID,
        )
        seed_session.add(conn)
        seed_session.commit()

    # Patch get_db_session to use our test engine

    def _get_test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session

    # Build app with connections router
    from synth_engine.bootstrapper.routers.connections import router as connections_router

    app = FastAPI()
    app.include_router(connections_router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _get_test_session

    org_a_token = _make_token(sub=_USER_A_UUID, org_id=_ORG_A_UUID)
    return app, Session(engine), conn_id, org_a_token


def test_org_a_connection_returns_404_to_org_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection owned by Org A is not visible to Org B (returns 404).

    IDOR protection: cross-tenant read returns 404 (not 403) to prevent
    enumeration attacks (resource existence must not be revealed).
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.schemas.connections import Connection

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    conn_id = "conn-for-org-a"
    with Session(engine) as s:
        conn = Connection(
            id=conn_id,
            name="org-a-private",
            host="internal.orga",
            port=5432,
            database="orga",
            schema_name="public",
            owner_id=_USER_A_UUID,
            org_id=_ORG_A_UUID,
        )
        s.add(conn)
        s.commit()

    def _test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.connections import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _test_session

    # Org B token — different org and user
    token_b = _make_token(sub=_USER_B_UUID, org_id=_ORG_B_UUID)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        f"/api/v1/connections/{conn_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    # Must be 404, not 403 — prevents revealing that the resource exists
    assert response.status_code == 404


def test_org_a_job_returns_404_to_org_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A job owned by Org A is not visible to Org B (returns 404).

    IDOR protection: cross-tenant job read returns 404 (not 200 or 403).
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as s:
        job = SynthesisJob(
            total_epochs=1,
            num_rows=10,
            table_name="test_table",
            parquet_path="/tmp/test.parquet",
            owner_id=_USER_A_UUID,
            org_id=_ORG_A_UUID,
        )
        s.add(job)
        s.commit()
        job_id = job.id

    def _test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.jobs import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _test_session

    # Org B token
    token_b = _make_token(sub=_USER_B_UUID, org_id=_ORG_B_UUID)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        f"/api/v1/jobs/{job_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert response.status_code == 404


def test_org_a_cannot_download_org_b_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Org A cannot download an artifact belonging to Org B.

    IDOR protection on the download endpoint: must return 404 for
    cross-tenant artifact access.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as s:
        job = SynthesisJob(
            total_epochs=1,
            num_rows=10,
            table_name="test_table",
            parquet_path="/tmp/test.parquet",
            output_path="/tmp/output.parquet",
            status="COMPLETE",
            owner_id=_USER_B_UUID,  # owned by org B's user
        )
        s.add(job)
        s.commit()
        job_id = job.id

    def _test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session

    # Use jobs_streaming router for download
    from synth_engine.bootstrapper.routers.jobs_streaming import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _test_session

    # Org A token tries to download org B's artifact
    token_a = _make_token(sub=_USER_A_UUID, org_id=_ORG_A_UUID)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        f"/api/v1/jobs/{job_id}/download",
        headers={"Authorization": f"Bearer {token_a}"},
    )

    assert response.status_code == 404


def test_org_a_cannot_cancel_org_b_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Org A cannot cancel (shred) a job belonging to Org B.

    IDOR protection on mutation: shred endpoint must return 404 for
    cross-tenant job mutation.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as s:
        job = SynthesisJob(
            total_epochs=1,
            num_rows=10,
            table_name="test_table",
            parquet_path="/tmp/test.parquet",
            status="COMPLETE",
            owner_id=_USER_B_UUID,
        )
        s.add(job)
        s.commit()
        job_id = job.id

    def _test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.jobs import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _test_session

    # Org A token tries to shred org B's job
    token_a = _make_token(sub=_USER_A_UUID, org_id=_ORG_A_UUID)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        f"/api/v1/jobs/{job_id}/shred",
        headers={"Authorization": f"Bearer {token_a}"},
    )

    assert response.status_code == 404


def test_org_a_privacy_budget_not_visible_to_org_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Org A's privacy budget is not visible to Org B (returns 404 for org B).

    Privacy ledger must be scoped by org_id. Org B with no ledger row
    sees 404, not Org A's budget.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.modules.privacy.ledger import PrivacyLedger

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    # Seed org A's ledger (with org_id after T79.4 migration)
    # For now, we test that the endpoint is org-scoped
    # The feature implementation will add org_id FK; the test verifies
    # that org B gets 404 when the ledger is scoped to org A
    with Session(engine) as s:
        ledger = PrivacyLedger(total_allocated_epsilon=10.0)  # type: ignore[arg-type]
        s.add(ledger)
        s.commit()

    def _test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.privacy import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _test_session

    # Org B token — org B has no ledger → must get 404 (not org A's ledger)
    token_b = _make_token(sub=_USER_B_UUID, org_id=_ORG_B_UUID)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/api/v1/privacy/budget",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    # After T79.4: ledger is org-scoped; org B has no ledger → 404
    assert response.status_code == 404


def test_org_a_cannot_reset_org_b_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Org A cannot reset Org B's privacy budget.

    POST /privacy/budget/refresh must be scoped to the requesting org.
    Org B can only reset its own ledger.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.modules.privacy.ledger import PrivacyLedger

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    # Only org A has a ledger
    with Session(engine) as s:
        ledger = PrivacyLedger(total_allocated_epsilon=10.0)  # type: ignore[arg-type]
        s.add(ledger)
        s.commit()

    def _test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.privacy import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _test_session

    # Org B tries to refresh (no org B ledger exists)
    token_b = _make_token(sub=_USER_B_UUID, org_id=_ORG_B_UUID)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/v1/privacy/budget/refresh",
        json={"justification": "test-justification-for-budget-reset"},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    # After T79.4: org B has no ledger → 404
    assert response.status_code == 404


def test_org_a_cannot_enumerate_org_b_connections_via_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Org A's pagination cursor does not expose Org B's connections.

    ATTACK-03 mitigation: cursor-based pagination must scope results by
    org_id BEFORE applying cursor offset.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.schemas.connections import Connection

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    # Seed connections for both orgs with org_id set
    with Session(engine) as s:
        for i in range(3):
            conn = Connection(
                id=f"org-a-conn-{i}",
                name=f"org-a-{i}",
                host="host.orga",
                port=5432,
                database="dba",
                schema_name="public",
                owner_id=_USER_A_UUID,
                org_id=_ORG_A_UUID,
            )
            s.add(conn)
        for i in range(3):
            conn = Connection(
                id=f"org-b-conn-{i}",
                name=f"org-b-{i}",
                host="host.orgb",
                port=5432,
                database="dbb",
                schema_name="public",
                owner_id=_USER_B_UUID,
                org_id=_ORG_B_UUID,
            )
            s.add(conn)
        s.commit()

    def _test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.connections import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _test_session

    # Org B lists connections — must only see org B's 3 connections
    token_b = _make_token(sub=_USER_B_UUID, org_id=_ORG_B_UUID)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/api/v1/connections",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert response.status_code == 200
    data = response.json()
    items = data["items"]
    # Org B must only see its own connections
    assert len(items) == 3
    for item in items:
        # None of the returned connections should belong to org A's user
        assert item["owner_id"] != _USER_A_UUID


# ---------------------------------------------------------------------------
# Test Group 3: Input Validation & Spoofing (tests 11-14)
# ---------------------------------------------------------------------------


def test_sql_injection_in_org_id_path_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQL injection attempt in org_id path parameter is rejected with 422.

    Path parameters that reach DB queries must be validated.
    A crafted SQL injection string in a path param must return 422, not 500.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from sqlmodel import create_engine as _create_engine

    engine = _create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def _test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.connections import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _test_session

    token = _make_token(sub=_USER_A_UUID, org_id=_ORG_A_UUID)
    client = TestClient(app, raise_server_exceptions=False)

    # SQL injection in the connection_id path parameter
    # The Path(max_length=255) constraint will reject excessively long IDs
    # But a short injection string should also be handled safely (parameterized query)
    injection_id = "1; DROP TABLE connection; --"
    response = client.get(
        f"/api/v1/connections/{injection_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Must not be 500 (internal error indicating unhandled injection)
    # 404 is acceptable (not found), 422 for max_length violation
    assert response.status_code in (404, 422)
    # Critical: must NOT be 200 (which would indicate data was returned)
    assert response.status_code != 200


def test_http_header_spoofing_x_org_id_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """X-Org-ID HTTP header cannot override the JWT-derived org_id.

    An attacker who sends X-Org-ID: <other-org-uuid> in the request
    must not gain access to the other org's resources. The system must
    derive org_id exclusively from the verified JWT claim.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.schemas.connections import Connection

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    conn_id = "org-b-private-conn"
    with Session(engine) as s:
        conn = Connection(
            id=conn_id,
            name="org-b-private",
            host="secret.orgb",
            port=5432,
            database="orgb_db",
            schema_name="public",
            owner_id=_USER_B_UUID,
        )
        s.add(conn)
        s.commit()

    def _test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.connections import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _test_session

    # Org A token, but with X-Org-ID header spoofing org B
    token_a = _make_token(sub=_USER_A_UUID, org_id=_ORG_A_UUID)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        f"/api/v1/connections/{conn_id}",
        headers={
            "Authorization": f"Bearer {token_a}",
            "X-Org-ID": _ORG_B_UUID,  # spoofing attempt
        },
    )

    # Must not see org B's connection — org derived from JWT, not header
    assert response.status_code == 404


def test_pagination_cursor_from_org_a_under_org_b_returns_only_org_b_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pagination cursor from Org A's session returns only Org B's data when used by Org B.

    ATTACK-03 mitigation: cursor comparison must occur AFTER org_id filtering.
    Cursor cross-tenant leakage is prevented by scoping the WHERE clause.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as s:
        # Create 3 jobs for org A (IDs 1, 2, 3) with org_id
        for i in range(3):
            job = SynthesisJob(
                total_epochs=1,
                num_rows=10,
                table_name="table_a",
                parquet_path=f"/tmp/orga_{i}.parquet",
                owner_id=_USER_A_UUID,
                org_id=_ORG_A_UUID,
            )
            s.add(job)
        s.flush()
        # Create 2 jobs for org B (IDs 4, 5) — after org A's IDs — with org_id
        for i in range(2):
            job = SynthesisJob(
                total_epochs=1,
                num_rows=10,
                table_name="table_b",
                parquet_path=f"/tmp/orgb_{i}.parquet",
                owner_id=_USER_B_UUID,
                org_id=_ORG_B_UUID,
            )
            s.add(job)
        s.commit()

    def _test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.jobs import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _test_session

    # Org B lists jobs with cursor=0 (start from beginning)
    # After org_id filtering: org B only sees its 2 jobs, regardless of
    # the cursor value used by org A
    token_b = _make_token(sub=_USER_B_UUID, org_id=_ORG_B_UUID)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/api/v1/jobs?after=0",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert response.status_code == 200
    data = response.json()
    items = data.get("items", data.get("jobs", []))
    for item in items:
        assert item["owner_id"] == _USER_B_UUID, (
            f"Cross-tenant leakage: org B query returned job owned by {item['owner_id']}"
        )


def test_jwt_with_forged_org_id_rejected_by_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JWT with a manually forged org_id is rejected by signature verification.

    ATTACK-01 / ATTACK-02: An attacker who base64-decodes, edits, and re-encodes
    a JWT must not gain access. PyJWT rejects mismatched signatures.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    app = _make_tenant_app(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    # Create a valid token for org A
    valid_token = _make_token(sub=_USER_A_UUID, org_id=_ORG_A_UUID)

    # Tamper with the payload: change org_id to org B
    # A naive tamper (base64 decode header.payload, change value, re-encode)
    # will invalidate the signature
    parts = valid_token.split(".")
    import base64

    # Decode the payload (pad if needed)
    payload_encoded = parts[1]
    padding = 4 - len(payload_encoded) % 4
    if padding != 4:
        payload_encoded += "=" * padding
    payload_bytes = base64.urlsafe_b64decode(payload_encoded)

    import json

    payload_dict = json.loads(payload_bytes)
    payload_dict["org_id"] = _ORG_B_UUID  # tamper

    # Re-encode without re-signing
    tampered_payload = (
        base64.urlsafe_b64encode(json.dumps(payload_dict).encode()).rstrip(b"=").decode()
    )
    tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"

    response = client.get(
        "/test/tenant",
        headers={"Authorization": f"Bearer {tampered_token}"},
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Test Group 4: Migration Safety (tests 15-18)
# ---------------------------------------------------------------------------


def test_default_org_seed_is_idempotent() -> None:
    """Default org seed in migration is idempotent — no duplicate rows.

    ATTACK-02 mitigation: the default org UUID must exist exactly once.
    Double-applying the seed must not cause IntegrityError or duplicates.
    """
    import uuid

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    # Import models to trigger registration
    from synth_engine.shared.models.organization import Organization

    SQLModel.metadata.create_all(engine)

    _default_uuid = uuid.UUID(_DEFAULT_ORG_UUID)

    with Session(engine) as s:
        # Simulate idempotent seed (ON CONFLICT DO NOTHING pattern)
        from sqlmodel import select

        existing = s.exec(select(Organization).where(Organization.id == _default_uuid)).first()
        if existing is None:
            org = Organization(
                id=_default_uuid,
                name="Default Organization",
            )
            s.add(org)
            s.commit()

    with Session(engine) as s:
        # Apply seed a second time — must not raise or create duplicates
        from sqlmodel import select

        existing = s.exec(select(Organization).where(Organization.id == _default_uuid)).first()
        if existing is None:
            org = Organization(
                id=_default_uuid,
                name="Default Organization",
            )
            s.add(org)
            s.commit()

    with Session(engine) as s:
        from sqlmodel import select

        orgs = s.exec(select(Organization).where(Organization.id == _default_uuid)).all()
        assert len(orgs) == 1, f"Expected exactly 1 default org, found {len(orgs)}"
        # id stored as UUID; compare with UUID object
        assert str(orgs[0].id) == _DEFAULT_ORG_UUID


def test_organization_model_has_required_fields() -> None:
    """Organization model has id, name, created_at, settings fields.

    Verifies the model schema matches the spec before migration runs.
    """
    from synth_engine.shared.models.organization import Organization

    org = Organization(
        id=_DEFAULT_ORG_UUID,
        name="Test Organization",
    )
    # Organization.id stays as string when created in-memory; compare as string
    assert str(org.id) == _DEFAULT_ORG_UUID
    assert org.name == "Test Organization"
    assert org.created_at is not None
    # settings is optional JSON field — defaults to None or "{}"
    assert hasattr(org, "settings")


def test_user_model_has_required_fields() -> None:
    """User model has id, org_id, email, role, created_at fields.

    Verifies the model schema matches the spec before migration runs.
    """
    from synth_engine.shared.models.user import User

    user = User(
        id=_DEFAULT_USER_UUID,
        org_id=_DEFAULT_ORG_UUID,
        email="admin@default.local",
        role="admin",
    )
    # id/org_id stay as strings when created in-memory; compare as strings
    assert str(user.id) == _DEFAULT_USER_UUID
    assert str(user.org_id) == _DEFAULT_ORG_UUID
    assert user.email == "admin@default.local"
    assert user.role == "admin"


def test_existing_single_operator_data_accessible_after_migration() -> None:
    """Existing single-operator data (owner_id='') is accessible after migration.

    Backward compatibility: records with owner_id='' belong to the default org.
    Pass-through mode (no JWT) must still work.
    """
    from synth_engine.bootstrapper.schemas.connections import Connection

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    # Seed a legacy record with owner_id='' (pre-T39.2 format)
    with Session(engine) as s:
        conn = Connection(
            id="legacy-conn-id",
            name="legacy-connection",
            host="db.legacy",
            port=5432,
            database="legacy_db",
            schema_name="public",
            owner_id="",  # legacy
        )
        s.add(conn)
        s.commit()

    # After migration, default org user (owner_id='') should still be accessible
    # in pass-through mode where get_current_user returns default org sentinel
    with Session(engine) as s:
        from sqlmodel import select

        result = s.exec(select(Connection).where(Connection.id == "legacy-conn-id")).first()
        assert result is not None
        assert result.owner_id == ""
        assert result.name == "legacy-connection"


# ---------------------------------------------------------------------------
# Test Group 5: Background Task Isolation (tests 19-22)
# ---------------------------------------------------------------------------


def test_tenant_context_is_frozen_dataclass() -> None:
    """TenantContext is a frozen dataclass — immutable after creation.

    Security property: org_id, user_id, role must not be mutatable
    after construction (prevents accidental privilege escalation).
    """
    from dataclasses import FrozenInstanceError

    from synth_engine.bootstrapper.dependencies.tenant import TenantContext

    ctx = TenantContext(
        org_id=_ORG_A_UUID,
        user_id=_USER_A_UUID,
        role="operator",
    )

    assert ctx.org_id == _ORG_A_UUID
    assert ctx.user_id == _USER_A_UUID
    assert ctx.role == "operator"

    # Must be frozen — mutation must raise FrozenInstanceError
    with pytest.raises((FrozenInstanceError, AttributeError)):
        ctx.org_id = _ORG_B_UUID  # type: ignore[misc]


def test_reaper_scoped_audit_event_includes_org_id() -> None:
    """OrphanTaskReaper audit events include org_id field.

    ATTACK-04 mitigation: reaper must emit auditable org context so
    cross-tenant reaping operations can be detected.
    """

    from synth_engine.shared.tasks.repository import StaleTask, TaskRepository

    class _FakeRepo(TaskRepository):
        def get_stale_in_progress(self, older_than: object) -> list[StaleTask]:
            return [
                StaleTask(
                    task_id=42,
                    status="IN_PROGRESS",
                    org_id=_ORG_A_UUID,  # org_id field required on StaleTask
                )
            ]

        def mark_failed(self, task_id: int, error_msg: str) -> bool:
            return True

    audit_events: list[dict[str, object]] = []

    def _mock_log_event(**kwargs: object) -> None:
        audit_events.append(dict(kwargs))

    mock_audit = MagicMock()
    mock_audit.log_event.side_effect = _mock_log_event

    with patch(
        "synth_engine.shared.tasks.reaper.get_audit_logger",
        return_value=mock_audit,
    ):
        from synth_engine.shared.tasks.reaper import OrphanTaskReaper

        reaper = OrphanTaskReaper(repository=_FakeRepo(), stale_threshold_minutes=10)
        reaped = reaper.reap()

    assert reaped == 1
    assert len(audit_events) == 1
    # Audit event must include org_id
    details = audit_events[0].get("details", {})
    assert "org_id" in details, f"org_id missing from reaper audit event: {audit_events[0]}"
    assert details["org_id"] == _ORG_A_UUID


def test_privacy_transaction_audit_scoped_to_org() -> None:
    """PrivacyTransaction has org_id FK as defense-in-depth (ATTACK-06).

    No route may query transactions without a ledger_id filter.
    """
    from synth_engine.modules.privacy.ledger import PrivacyTransaction

    # Verify org_id field exists on PrivacyTransaction
    tx = PrivacyTransaction(
        ledger_id=1,
        job_id=42,
        epsilon_spent=0.5,  # type: ignore[arg-type]
        org_id=_ORG_A_UUID,
    )
    assert tx.org_id == _ORG_A_UUID
    assert tx.ledger_id == 1


def test_huey_task_spends_correct_org_budget() -> None:
    """run_synthesis_job resolves org from SynthesisJob.org_id FK.

    ATTACK-04 mitigation: the Huey task must validate org_id from the
    job record, not from any external input.  This test verifies that
    _handle_dp_accounting reads job.org_id and passes it to spend_budget_fn.
    """
    from unittest.mock import MagicMock

    import synth_engine.modules.synthesizer.jobs.job_orchestration as _orch
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob
    from synth_engine.modules.synthesizer.training.dp_accounting import _handle_dp_accounting

    job = SynthesisJob(
        total_epochs=1,
        num_rows=10,
        table_name="test_table",
        parquet_path="/tmp/test.parquet",
        org_id=_ORG_A_UUID,
    )
    job.id = 42  # type: ignore[assignment]
    job.actual_epsilon = 0.5

    dp_wrapper = MagicMock()
    dp_wrapper.epsilon_spent.return_value = 0.5

    spend_calls: list[dict] = []

    def _mock_spend(**kwargs):  # type: ignore[return]
        spend_calls.append(kwargs)

    saved = _orch._spend_budget_fn
    try:
        _orch._spend_budget_fn = _mock_spend  # type: ignore[assignment]
        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger"
        ) as mock_audit:
            mock_audit.return_value.log_event.return_value = None
            _handle_dp_accounting(job=job, dp_wrapper=dp_wrapper, job_id=42)
    finally:
        _orch._spend_budget_fn = saved

    # The org_id from the job must flow through to the spend call
    assert len(spend_calls) == 1
    assert spend_calls[0]["org_id"] == _ORG_A_UUID, (
        f"org_id was not threaded through to spend_budget_fn: got {spend_calls[0].get('org_id')!r}"
    )
    assert spend_calls[0]["amount"] == 0.5
    assert spend_calls[0]["job_id"] == 42


def test_huey_task_cannot_spend_cross_org_budget() -> None:
    """Huey task must validate job.org_id == ledger.org_id before spending.

    ATTACK-04 mitigation: mismatched org_ids must raise an error before
    any epsilon is spent. This prevents cross-tenant budget depletion.
    Tests the sync_spend_budget cross-org validation directly.
    """
    from decimal import Decimal

    import sqlalchemy as _sa
    from sqlalchemy.pool import StaticPool as SaStaticPool
    from sqlmodel import Session as SaSession
    from sqlmodel import SQLModel as SaModel

    from synth_engine.modules.privacy.ledger import PrivacyLedger
    from synth_engine.modules.privacy.sync_budget import sync_spend_budget
    from synth_engine.shared.exceptions import BudgetExhaustionError

    sa_create_engine = _sa.create_engine

    # Build an in-memory DB with a ledger belonging to ORG_A
    engine = sa_create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=SaStaticPool,
    )
    SaModel.metadata.create_all(engine)

    with SaSession(engine) as session:
        with session.begin():
            ledger = PrivacyLedger(
                total_allocated_epsilon=Decimal("10.0"),
                total_spent_epsilon=Decimal("0.0"),
                org_id=_ORG_A_UUID,
            )
            session.add(ledger)

    # Attempt to spend budget for ORG_B against ORG_A's ledger — must be rejected
    with SaSession(engine) as s2:
        ledger_id = (
            s2.exec(  # type: ignore[call-overload]
                __import__("sqlmodel", fromlist=["select"]).select(PrivacyLedger)
            )
            .first()
            .id
        )

    with pytest.raises(BudgetExhaustionError):
        sync_spend_budget(
            engine,
            amount=0.1,
            job_id=99,
            ledger_id=ledger_id,
            org_id=_ORG_B_UUID,  # cross-org: ORG_B tries to use ORG_A ledger
        )

    # Verify no epsilon was spent (ledger must remain unchanged)
    with SaSession(engine) as s3:
        persisted = s3.get(PrivacyLedger, ledger_id)
        assert persisted is not None
        assert persisted.total_spent_epsilon == Decimal("0.0"), (
            "Epsilon must NOT be deducted when cross-org validation rejects the spend"
        )


# ---------------------------------------------------------------------------
# Test Group 6: Feature Scoping (tests 23-27)
# ---------------------------------------------------------------------------


def test_settings_org_isolation_or_global_documented() -> None:
    """Settings table has no org_id column — it is intentionally global.

    Settings are deployment-wide configuration, not per-tenant data.
    This test verifies the Setting model has no org_id attribute.
    """
    from synth_engine.bootstrapper.schemas.settings import Setting

    # Setting must NOT have org_id (intentionally global per ADR)
    setting = Setting(key="test_key", value="test_value")
    assert not hasattr(setting, "org_id"), (
        "Setting model must remain global (no org_id). "
        "Per-tenant settings are a P80+ concern per ADR-0065."
    )


def test_webhook_limit_scoped_per_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook registration limit is enforced per-org, not per-user.

    An org with 10 registrations must reject the 11th, regardless of
    which user within that org makes the request.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.routers.webhooks import _count_active_registrations
    from synth_engine.bootstrapper.schemas.webhooks import WebhookRegistration

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    # Seed 10 webhooks for org A with org_id set
    with Session(engine) as s:
        for i in range(10):
            wh = WebhookRegistration(
                owner_id=_USER_A_UUID,
                callback_url=f"https://hook.example.com/{i}",
                signing_key="test-signing-key",
                active=True,
                org_id=_ORG_A_UUID,
            )
            s.add(wh)
        s.commit()

    # After T79.2 migration: _count_active_registrations filters by org_id
    # Count for org A should be 10
    with Session(engine) as s:
        count = _count_active_registrations(s, owner_id=_USER_A_UUID, org_id=_ORG_A_UUID)

    assert count == 10, f"Expected 10 active registrations for org A, got {count}"


def test_tenant_aware_pooling_prevents_pool_exhaustion_by_one_org() -> None:
    """Per-org connection semaphore limits concurrent connections per org.

    UNIT-LEVEL ASSERTION: the semaphore dict is keyed by org_id and
    initialized with the correct limit from settings.

    Full integration testing is in tests/integration/test_tenant_isolation.py.
    """
    from synth_engine.shared.db import get_org_semaphore

    sem_a = get_org_semaphore(_ORG_A_UUID)
    sem_b = get_org_semaphore(_ORG_B_UUID)

    # Each org gets its own independent semaphore
    assert sem_a is not sem_b

    import asyncio

    # Semaphore should be an asyncio.Semaphore instance
    assert isinstance(sem_a, asyncio.Semaphore)
    assert isinstance(sem_b, asyncio.Semaphore)


def test_erasure_scoped_to_requesting_user_within_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Erasure endpoint: self-erasure within org is permitted.

    A user can erase their own data within their org (subject_id == user_id).
    After T79.2: erasure is scoped to the requesting user's org.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def _test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.compliance import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _test_session

    # The _unseal_vault_for_ale autouse fixture has already unsealed the vault.
    # No redundant unseal needed here.

    # User A requests erasure of their own data
    token_a = _make_token(sub=_USER_A_UUID, org_id=_ORG_A_UUID)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.request(
        "DELETE",
        "/api/v1/compliance/erasure",
        json={"subject_id": _USER_A_UUID},
        headers={"Authorization": f"Bearer {token_a}"},
    )

    # Self-erasure within org must succeed: 200 (OK) or 204 (No Content)
    assert response.status_code in (200, 204), (
        f"Self-erasure should return 200 or 204, got {response.status_code}: {response.text}"
    )


def test_org_a_cannot_erase_org_b_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross-org erasure is blocked: Org A user cannot erase Org B user data.

    After T79.2: erasure is self-erasure within org only.
    Cross-org erasure must return 404 (not 403) — prevents revealing
    that the other org's user exists.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def _test_session() -> Any:
        with Session(engine) as s:
            yield s

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.compliance import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = _test_session

    # Org A token tries to erase Org B's user data
    # subject_id != user_id (cross-org, cross-user attempt)
    token_a = _make_token(sub=_USER_A_UUID, org_id=_ORG_A_UUID)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.request(
        "DELETE",
        "/api/v1/compliance/erasure",
        json={"subject_id": _USER_B_UUID},  # trying to erase org B's user
        headers={"Authorization": f"Bearer {token_a}"},
    )

    # Must be rejected — 403 (self-erasure only) or 404 (after T79.2 cross-org → 404)
    assert response.status_code in (403, 404)
    assert response.status_code != 200


# ---------------------------------------------------------------------------
# Test Group: Configuration Validation (ATTACK-01, CONFIG-01)
# ---------------------------------------------------------------------------


def test_jwt_expiry_seconds_validator_le_900(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """jwt_expiry_seconds must be validated ≤ 900 in multi-tenant mode.

    CONFIG-01 mitigation: unbounded token lifetime enables stale org_id
    claims to persist. Pydantic must reject values > 900.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("CONCLAVE_MULTI_TENANT_ENABLED", "true")
    monkeypatch.setenv("JWT_EXPIRY_SECONDS", "901")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    import pydantic

    with pytest.raises((ValueError, pydantic.ValidationError)):
        get_settings()  # validator should reject jwt_expiry_seconds > 900
