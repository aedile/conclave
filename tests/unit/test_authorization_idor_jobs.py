"""Unit tests for IDOR protection on job endpoints (T39.2).

Tests cover:
- Job resource endpoints filter by owner_id.
- Job endpoints return 404 (not 403) for non-owned resources.
- Unauthenticated job requests return 401.
- list_jobs filters by owner_id.
- start_job and shred_job return 404 for other operators jobs.

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
    from synth_engine.modules.synthesizer.job_models import SynthesisJob

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


# ---------------------------------------------------------------------------
# AC6-AC9: IDOR — jobs resource access control
# ---------------------------------------------------------------------------


def test_get_job_returns_404_for_job_owned_by_other_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /jobs/{job_id} must return 404 when the job is owned by a different operator.

    Operator A requests operator B's job. IDOR protection: returns 404 not 403
    to prevent enumeration (AC2 spec requirement).

    Arrange: seed a job owned by operator B; authenticate as operator A.
    Act: GET /jobs/{job_b_id} with operator A's token.
    Assert: HTTP 404 returned.
    """
    app, engine = _make_jobs_app(monkeypatch)

    # Get operator B's job ID
    with Session(engine) as session:
        from sqlmodel import select

        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job_b = session.exec(
            select(SynthesisJob).where(SynthesisJob.owner_id == _OPERATOR_B_SUB)
        ).first()
        assert job_b is not None
        job_b_id = job_b.id

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
            f"/jobs/{job_b_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 404


def test_get_job_returns_200_for_own_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /jobs/{job_id} must return 200 when the job is owned by the requesting operator.

    Arrange: seed a job owned by operator A; authenticate as operator A.
    Act: GET /jobs/{job_a_id} with operator A's token.
    Assert: HTTP 200 returned.
    """
    app, engine = _make_jobs_app(monkeypatch)

    with Session(engine) as session:
        from sqlmodel import select

        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job_a = session.exec(
            select(SynthesisJob).where(SynthesisJob.owner_id == _OPERATOR_A_SUB)
        ).first()
        assert job_a is not None
        job_a_id = job_a.id

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
            f"/jobs/{job_a_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# AC2: 404 not 403 (enumeration prevention)
# ---------------------------------------------------------------------------


def test_get_job_returns_404_not_403_for_idor_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /jobs/{job_id} must return 404 (not 403) for IDOR attempts.

    AC2: returning 403 would confirm the resource exists and leaks
    information about other operators' resources. 404 prevents enumeration.

    Arrange: seed job owned by operator B; authenticate as operator A.
    Act: GET /jobs/{job_b_id} with operator A's token.
    Assert: status is exactly 404 (never 403).
    """
    app, engine = _make_jobs_app(monkeypatch)

    with Session(engine) as session:
        from sqlmodel import select

        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job_b = session.exec(
            select(SynthesisJob).where(SynthesisJob.owner_id == _OPERATOR_B_SUB)
        ).first()
        assert job_b is not None
        job_b_id = job_b.id

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
            f"/jobs/{job_b_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    # Exactly 404 — never 403
    assert response.status_code == 404
    assert response.status_code != 403


# ---------------------------------------------------------------------------
# AC6: IDOR — sequential ID enumeration returns 404 for non-owned resources
# ---------------------------------------------------------------------------


def test_idor_sequential_id_enumeration_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sequential ID enumeration of non-owned jobs must return 404.

    AC6: An attacker who guesses sequential job IDs must receive 404 for
    jobs they do not own, preventing horizontal privilege escalation.

    Arrange: seed job IDs 1 and 2, owned by operators A and B respectively.
    Act: operator A requests job 2 (operator B's job).
    Assert: 404 returned — enumeration attack is neutralized.
    """
    app, engine = _make_jobs_app(monkeypatch)

    with Session(engine) as session:
        from sqlmodel import select

        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        # Find the highest-ID job owned by operator B (non-owned by A)
        job_b = session.exec(
            select(SynthesisJob).where(SynthesisJob.owner_id == _OPERATOR_B_SUB)
        ).first()
        assert job_b is not None
        non_owned_id = job_b.id

    # Authenticate as operator A and enumerate operator B's job ID
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
            f"/jobs/{non_owned_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# AC5: Unauthenticated access returns 401 (pass-through mode disabled)
# ---------------------------------------------------------------------------


def test_get_job_returns_401_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /jobs/{job_id} must return 401 for unauthenticated requests.

    AC5: When JWT_SECRET_KEY is configured, unauthenticated requests must
    be rejected with 401 Unauthorized.

    Arrange: seed a job; JWT_SECRET_KEY is set; no Authorization header.
    Act: GET /jobs/{job_id} without any token.
    Assert: HTTP 401 returned.
    """
    app, engine = _make_jobs_app(monkeypatch)

    with Session(engine) as session:
        from sqlmodel import select

        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job_a = session.exec(
            select(SynthesisJob).where(SynthesisJob.owner_id == _OPERATOR_A_SUB)
        ).first()
        assert job_a is not None
        job_a_id = job_a.id

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
        # No Authorization header
        response = client.get(f"/jobs/{job_a_id}")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# AC1: list_jobs only returns the operator's own jobs
# ---------------------------------------------------------------------------


def test_list_jobs_only_returns_own_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /jobs must only return jobs owned by the authenticated operator.

    Arrange: seed jobs for operators A and B; authenticate as operator A.
    Act: GET /jobs with operator A's token.
    Assert: only operator A's jobs are returned (not operator B's).
    """
    app, engine = _make_jobs_app(monkeypatch)
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
            "/jobs",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 200
    body = response.json()
    items = body["items"]
    # All returned jobs must be owned by operator A
    assert all(item["owner_id"] == _OPERATOR_A_SUB for item in items)
    # Operator B's jobs must not appear
    assert not any(item["owner_id"] == _OPERATOR_B_SUB for item in items)


# ---------------------------------------------------------------------------
# AC1: POST /jobs/{job_id}/start — IDOR protection
# ---------------------------------------------------------------------------


def test_start_job_returns_404_for_other_operators_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /jobs/{job_id}/start must return 404 for a job owned by a different operator.

    Arrange: seed a job owned by operator B; authenticate as operator A.
    Act: POST /jobs/{job_b_id}/start with operator A's token.
    Assert: HTTP 404 returned (IDOR protection).
    """
    app, engine = _make_jobs_app(monkeypatch)

    with Session(engine) as session:
        from sqlmodel import select

        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job_b = session.exec(
            select(SynthesisJob).where(SynthesisJob.owner_id == _OPERATOR_B_SUB)
        ).first()
        assert job_b is not None
        job_b_id = job_b.id

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
            f"/jobs/{job_b_id}/start",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# AC1: POST /jobs/{job_id}/shred — IDOR protection
# ---------------------------------------------------------------------------


def test_shred_job_returns_404_for_other_operators_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /jobs/{job_id}/shred must return 404 for a job owned by a different operator.

    Arrange: seed a COMPLETE job owned by operator B; authenticate as operator A.
    Act: POST /jobs/{job_b_id}/shred with operator A's token.
    Assert: HTTP 404 returned (IDOR protection).
    """
    from sqlalchemy.pool import StaticPool

    from synth_engine.bootstrapper.errors import register_error_handlers
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.bootstrapper.routers.jobs import router as jobs_router
    from synth_engine.modules.synthesizer.job_models import SynthesisJob

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

    with Session(engine) as session:
        job_b_complete = SynthesisJob(
            table_name="orders",
            parquet_path="/tmp/orders.parquet",
            total_epochs=5,
            num_rows=50,
            status="COMPLETE",
            output_path="/tmp/orders-synthetic.parquet",
            owner_id=_OPERATOR_B_SUB,
        )
        session.add(job_b_complete)
        session.commit()
        session.refresh(job_b_complete)
        job_b_id = job_b_complete.id

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
            f"/jobs/{job_b_id}/shred",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# AC1: GET /connections/{connection_id} — IDOR protection
# ---------------------------------------------------------------------------
